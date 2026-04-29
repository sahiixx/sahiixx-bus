"""A2A Router — Unified A2A discovery and capability-based routing."""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from sahiixx_bus.bridge import AgencyBridge, GooseBridge
from sahiixx_bus.core import SafetyCouncil, SwarmBus


class A2ARouter:
    """Unified A2A discovery and routing across the SAHIIXX ecosystem.

    Services are registered with their URL and capabilities.  The router
    discovers agent cards via the ``/.well-known/agent.json`` endpoint,
    scores candidates by capability overlap, and sends tasks through the
    appropriate bridge class.  A built-in fallback chain ensures tasks are
    never dropped when primary targets are unavailable.
    """

    def __init__(self, bus: SwarmBus, safety: SafetyCouncil) -> None:
        """Initialise the router.

        Args:
            bus: Shared ``SwarmBus`` instance for pub/sub.
            safety: ``SafetyCouncil`` instance for pre-flight scanning.
        """
        self.bus = bus
        self.safety = safety
        self._registry: dict[str, dict[str, Any]] = {}
        self._agent_cards: dict[str, dict] = {}
        self._fallback_chain = ["friday-os", "goose-aios", "agency-agents"]
        self._http = httpx.AsyncClient(timeout=30.0)

    async def register_service(self, name: str, url: str, capabilities: list[str]) -> None:
        """Register a service in the internal registry.

        Args:
            name: Human-readable service identifier (e.g. *goose-aios*).
            url: Base URL of the service.
            capabilities: List of capability strings the service advertises.
        """
        self._registry[name] = {
            "name": name,
            "url": url,
            "capabilities": list(capabilities),
            "last_seen": time.time(),
        }

    async def discover_all(self) -> list[dict]:
        """Discover all registered services by fetching ``/.well-known/agent.json``.

        Returns:
            List of agent-card dicts.  The ``last_seen`` timestamp is updated
            for every service that responds successfully.
        """
        cards: list[dict] = []
        for name, meta in self._registry.items():
            try:
                resp = await self._http.get(f"{meta['url']}/.well-known/agent.json")
                resp.raise_for_status()
                card = resp.json()
                self._agent_cards[name] = card
                meta["last_seen"] = time.time()
                cards.append(card)
            except Exception:
                # Service unreachable — emit synthetic card from registry
                card = {
                    "name": meta["name"],
                    "url": meta["url"],
                    "capabilities": meta["capabilities"],
                    "last_seen": meta["last_seen"],
                    "status": "unreachable",
                }
                self._agent_cards[name] = card
                cards.append(card)
        return cards

    async def route_task(self, task: str, required_capabilities: list[str] | None = None) -> dict:
        """Route *task* to the best matching service.

        Routing steps:
        1. Safety-scan the task.  If unsafe, return an error dict immediately.
        2. Score every registered service by the count of overlapping
           *required_capabilities*.
        3. Pick the highest score.  Tie-break by ``last_seen`` (most recent
           first).
        4. If no match, walk the fallback chain
           ``agency-agents → goose-aios → sovereign-swarm``.
        5. Send the task via the bridge class from ``sahiixx_bus.bridge``.
        6. Return ``{"service": name, "result": result, "routed": True}``.

        Args:
            task: Plain-text task description.
            required_capabilities: Capabilities the target must possess.

        Returns:
            Routing result dict.
        """
        required_capabilities = required_capabilities or []

        # 1. Safety scan
        verdict = await self.safety.scan(task)
        if not verdict.safe:
            return {
                "error": "Safety scan failed",
                "violations": verdict.violations,
                "threat_level": verdict.threat_level,
                "routed": False,
            }

        # 2. Score services
        scored: list[tuple[str, int, float]] = []
        for name, meta in self._registry.items():
            caps = set(meta.get("capabilities", []))
            score = sum(1 for req in required_capabilities if req in caps)
            if required_capabilities and score == 0:
                continue
            scored.append((name, score, meta["last_seen"]))

        # 3. Pick best — highest score, then most recent last_seen
        if scored:
            scored.sort(key=lambda x: (-x[1], -x[2]))
            target_name = scored[0][0]
            return await self._send_via_bridge(target_name, task)

        # 4. Fallback chain
        for fallback in self.get_fallback_chain():
            if fallback in self._registry:
                return await self._send_via_bridge(fallback, task)

        # Nothing available — return error
        return {
            "error": "No matching service and fallback chain exhausted",
            "routed": False,
        }

    async def _send_via_bridge(self, service_name: str, task: str) -> dict:
        """Send *task* to *service_name* using the correct bridge class."""
        meta = self._registry[service_name]
        url = meta["url"]

        # For all services, try A2A invoke first, then /task, then bridge methods
        endpoints = [
            f"{url}/a2a/invoke",
            f"{url}/task",
            f"{url}/chat",
        ]
        payloads = [
            {"input": task},        # A2A invoke
            {"text": task},          # /task
            {"message": task},       # /chat
        ]

        # Try bridge methods for known service types
        if service_name == "goose-aios":
            endpoints = [
                f"{url}/api/chat",
                f"{url}/chat",
                f"{url}/a2a/invoke",
                f"{url}/task",
            ]
            payloads = [
                {"message": task},
                {"message": task},
                {"input": task},
                {"text": task},
            ]
        elif service_name == "agency-agents":
            try:
                bridge = AgencyBridge(url)
                result = await bridge.send_task(agent_name="jarvis", text=task)
                await self.bus.publish("a2a.routed", {"service": service_name, "task": task})
                return {"service": service_name, "result": result, "routed": True}
            except Exception as exc:
                # Fall through to generic endpoints
                pass

        for endpoint, payload in zip(endpoints, payloads):
            try:
                resp = await self._http.post(endpoint, json=payload, timeout=15.0)
                if resp.status_code < 500:
                    try:
                        result = resp.json()
                    except Exception:
                        result = {"text": resp.text[:1000]}
                    await self.bus.publish("a2a.routed", {"service": service_name, "task": task})
                    return {"service": service_name, "result": result, "routed": True}
            except Exception:
                continue

        result = {"error": f"All endpoints unreachable for {service_name} at {url}"}
        await self.bus.publish("a2a.routed", {"service": service_name, "task": task})
        return {"service": service_name, "result": result, "routed": True}

    async def get_agent_card(self, service_name: str) -> dict:
        """Return the cached agent card for *service_name* or fetch it.

        Args:
            service_name: Registered service name.

        Returns:
            Agent card dict, or ``{"error": "not found"}``.
        """
        if service_name in self._agent_cards:
            return self._agent_cards[service_name]

        meta = self._registry.get(service_name)
        if not meta:
            return {"error": "not found", "service": service_name}

        try:
            resp = await self._http.get(f"{meta['url']}/.well-known/agent.json")
            resp.raise_for_status()
            card = resp.json()
            self._agent_cards[service_name] = card
            meta["last_seen"] = time.time()
            return card
        except Exception as exc:
            return {"error": str(exc), "service": service_name}

    def get_fallback_chain(self) -> list[str]:
        """Return the built-in fallback chain.

        Returns:
            ``["friday-os", "goose-aios", "agency-agents"]``
        """
        return list(self._fallback_chain)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
