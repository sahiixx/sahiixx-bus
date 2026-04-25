#!/usr/bin/env python3
"""
sahiixx_bus.bridge — Bridge classes for connecting to external SAHIIXX ecosystem services.

All HTTP traffic uses `httpx.AsyncClient` with automatic retry logic, timeout handling,
and graceful degradation.  WebSocket/SSE endpoints use the most appropriate async primitive.
"""
from __future__ import annotations

import asyncio
import functools
import json
import logging
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any, Callable

import aiohttp
import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry helper
# ---------------------------------------------------------------------------

def async_retry(
    retries: int = 3,
    base_delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.NetworkError,
        asyncio.TimeoutError,
    ),
) -> Callable[..., Any]:
    """Decorator that retries an async function with exponential backoff."""

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            last_exc: Exception | None = None
            for attempt in range(1, retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < retries:
                        logger.warning(
                            "%s attempt %d/%d failed: %s — retrying in %.1fs",
                            func.__name__,
                            attempt,
                            retries,
                            exc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        delay *= backoff
                    else:
                        break
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# BaseBridge
# ---------------------------------------------------------------------------

class BaseBridge(ABC):
    """Abstract base for every SAHIIXX service bridge.

    Provides a shared ``httpx.AsyncClient``, common health-check / discovery helpers,
    and a configurable request timeout.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url: str = base_url.rstrip("/")
        self.timeout: float = timeout
        self._client: httpx.AsyncClient = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            headers={"Accept": "application/json"},
        )

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def health(self) -> dict:
        """GET ``{base_url}/health`` (falls back to ``/.well-known/agent.json``).

        Returns a dict with at least ``status`` and ``healthy`` keys when the
        service responds successfully.
        """
        urls_to_try = [f"{self.base_url}/health", f"{self.base_url}/.well-known/agent.json"]
        last_error: Exception | None = None
        for url in urls_to_try:
            try:
                response = await self._client.get(url)
                if response.status_code < 500:
                    try:
                        return response.json()
                    except Exception:
                        return {"status": response.status_code, "text": response.text}
            except Exception as exc:
                last_error = exc
                continue
        raise last_error or httpx.ConnectError(f"Health check failed for {self.base_url}")

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def discover(self) -> dict:
        """GET ``/.well-known/agent.json`` (A2A agent card discovery).

        Returns the parsed JSON agent card, or an empty dict on non-JSON response.
        """
        url = f"{self.base_url}/.well-known/agent.json"
        response = await self._client.get(url)
        response.raise_for_status()
        try:
            return response.json()
        except Exception:
            return {}

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} url={self.base_url}>"


# ---------------------------------------------------------------------------
# AgencyBridge
# ---------------------------------------------------------------------------

class AgencyBridge(BaseBridge):
    """Bridge to agency-agents A2A endpoints (default port 8100).

    Communicates via JSON-RPC 2.0 over HTTP POST for task lifecycle methods,
    and Server-Sent Events (SSE) for streaming results.
    """

    def __init__(self, base_url: str = "http://localhost:8100", timeout: float = 30.0) -> None:
        super().__init__(base_url, timeout)
        self._port_map: dict[str, int] = {
            "agency_default": 8100,
        }

    def _agent_url(self, agent_name: str) -> str:
        """Resolve an agent name to a concrete service URL."""
        port = self._port_map.get(agent_name, 8100)
        return f"http://localhost:{port}"

    def list_agents(self) -> list[str]:
        """Return the list of locally-known agent names."""
        return list(self._port_map.keys())

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def send_task(
        self, agent_name: str, text: str, context_id: str | None = None
    ) -> dict:
        """Send a task to *agent_name* via JSON-RPC 2.0 ``tasks/send``.

        Args:
            agent_name: Logical name of the target agent.
            text: User message text.
            context_id: Optional conversation context identifier.

        Returns:
            The JSON-RPC ``result`` field as a dict.
        """
        url = self._agent_url(agent_name)
        task_id = str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tasks/send",
            "params": {
                "id": task_id,
                "contextId": context_id,
                "message": {
                    "role": "user",
                    "parts": [{"text": text}],
                },
            },
        }
        response = await self._client.post(
            f"{url}/tasks/send",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise httpx.HTTPStatusError(
                f"JSON-RPC error: {data['error']}",
                request=response.request,
                response=response,
            )
        return data.get("result", {})

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def get_task(self, agent_name: str, task_id: str) -> dict:
        """Retrieve a task status via JSON-RPC 2.0 ``tasks/get``."""
        url = self._agent_url(agent_name)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        response = await self._client.post(
            f"{url}/tasks/get",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise httpx.HTTPStatusError(
                f"JSON-RPC error: {data['error']}",
                request=response.request,
                response=response,
            )
        return data.get("result", {})

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def cancel_task(self, agent_name: str, task_id: str) -> dict:
        """Cancel a task via JSON-RPC 2.0 ``tasks/cancel``."""
        url = self._agent_url(agent_name)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tasks/cancel",
            "params": {"id": task_id},
        }
        response = await self._client.post(
            f"{url}/tasks/cancel",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise httpx.HTTPStatusError(
                f"JSON-RPC error: {data['error']}",
                request=response.request,
                response=response,
            )
        return data.get("result", {})

    async def stream_task(self, agent_name: str, task_id: str) -> AsyncIterator[str]:
        """Stream task output via SSE GET ``/stream?taskId=...``.

        Yields each non-empty SSE data line as a decoded string.
        """
        url = self._agent_url(agent_name)
        stream_url = f"{url}/stream?taskId={task_id}"
        async with self._client.stream("GET", stream_url, timeout=self.timeout) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    chunk = line[len("data: "):].strip()
                    if chunk:
                        yield chunk


# ---------------------------------------------------------------------------
# FridayBridge
# ---------------------------------------------------------------------------

class FridayBridge(BaseBridge):
    """Bridge to friday-os A2A server (default port 8000).

    Supports skill invocation, chat streaming, and skill enumeration.
    """

    def __init__(self, base_url: str = "http://localhost:8000", timeout: float = 30.0) -> None:
        super().__init__(base_url, timeout)

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def invoke(self, skill: str, params: dict) -> dict:
        """POST ``/a2a/invoke`` to trigger a skill with the supplied parameters.

        Args:
            skill: Skill identifier (e.g. ``"summarise"``).
            params: Arbitrary keyword parameters for the skill.

        Returns:
            Parsed JSON response dict.
        """
        response = await self._client.post(
            f"{self.base_url}/a2a/invoke",
            json={"skill": skill, "params": params},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    async def chat_stream(self, message: str) -> AsyncIterator[str]:
        """SSE GET ``/chat/stream?message=...`` — yields response tokens.

        Args:
            message: User utterance to stream a reply for.
        """
        stream_url = f"{self.base_url}/chat/stream"
        async with self._client.stream(
            "GET",
            stream_url,
            params={"message": message},
            timeout=self.timeout,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    chunk = line[len("data: "):].strip()
                    if chunk:
                        yield chunk

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def list_skills(self) -> list[str]:
        """GET ``/a2a/skills`` — returns a list of available skill names."""
        response = await self._client.get(f"{self.base_url}/a2a/skills")
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        return data.get("skills", [])


# ---------------------------------------------------------------------------
# GooseBridge
# ---------------------------------------------------------------------------

class GooseBridge(BaseBridge):
    """Bridge to goose-aios FastAPI (default port 8001).

    Provides chat, agent listing, delegation, and WebSocket chat.
    """

    def __init__(self, base_url: str = "http://localhost:8001", timeout: float = 30.0) -> None:
        super().__init__(base_url, timeout)

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def chat(self, message: str, history: list | None = None) -> dict:
        """POST ``/chat`` with a message and optional conversation history.

        Args:
            message: User message.
            history: List of prior messages (``{"role": ..., "content": ...}``).

        Returns:
            Parsed JSON response dict.
        """
        payload: dict[str, Any] = {"message": message}
        if history is not None:
            payload["history"] = history
        response = await self._client.post(
            f"{self.base_url}/chat",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def list_agents(self) -> list[dict]:
        """GET ``/agents`` — returns a list of agent descriptor dicts."""
        response = await self._client.get(f"{self.base_url}/agents")
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            return data
        return data.get("agents", [])

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def delegate(self, agent_id: str, task: str) -> dict:
        """POST ``/agents/{agent_id}/delegate`` to assign a task to a specific agent."""
        response = await self._client.post(
            f"{self.base_url}/agents/{agent_id}/delegate",
            json={"task": task},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    async def ws_chat(self, message: str) -> AsyncIterator[str]:
        """Send *message* over WebSocket ``/ws`` and yield every text frame received.

        Uses ``aiohttp`` for the WebSocket transport because ``httpx`` does not
        support WebSockets natively.
        """
        ws_url = self.base_url.replace("http://", "ws://").replace("https://", "wss://") + "/ws"
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(ws_url) as ws:
                await ws.send_str(json.dumps({"message": message}))
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        yield msg.data
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break


# ---------------------------------------------------------------------------
# FixfizxBridge
# ---------------------------------------------------------------------------

class FixfizxBridge(BaseBridge):
    """Bridge to Fixfizx API (default port 8002).

    All requests carry ``Authorization: Bearer {jwt}``.  Provides lead qualification,
    market analysis, campaign creation, and agent status.
    """

    def __init__(
        self, base_url: str = "http://localhost:8002", jwt: str = "", timeout: float = 30.0
    ) -> None:
        super().__init__(base_url, timeout)
        self.jwt: str = jwt
        self._client.headers["Authorization"] = f"Bearer {jwt}"

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def enhanced_chat(self, message: str, context: dict | None = None) -> dict:
        """POST ``/api/ai/advanced/enhanced-chat``.

        Args:
            message: User message.
            context: Optional structured context dict.
        """
        payload: dict[str, Any] = {"message": message}
        if context is not None:
            payload["context"] = context
        response = await self._client.post(
            f"{self.base_url}/api/ai/advanced/enhanced-chat",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def qualify_lead(self, lead_data: dict) -> dict:
        """POST ``/api/agents/sales/qualify-lead``.

        Args:
            lead_data: Dict describing the lead (name, email, company, etc.).
        """
        response = await self._client.post(
            f"{self.base_url}/api/agents/sales/qualify-lead",
            json=lead_data,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def analyze_market(self, market: str = "dubai") -> dict:
        """POST ``/api/ai/advanced/dubai-market-analysis`` (parametrized by *market*)."""
        response = await self._client.post(
            f"{self.base_url}/api/ai/advanced/dubai-market-analysis",
            json={"market": market},
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def create_campaign(self, campaign_config: dict) -> dict:
        """POST ``/api/agents/marketing/create-campaign``.

        Args:
            campaign_config: Campaign parameters (name, budget, audience, etc.).
        """
        response = await self._client.post(
            f"{self.base_url}/api/agents/marketing/create-campaign",
            json=campaign_config,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def agent_status(self) -> dict:
        """GET ``/api/health`` — returns the agent / API health status."""
        response = await self._client.get(f"{self.base_url}/api/health")
        response.raise_for_status()
        return response.json()


# ---------------------------------------------------------------------------
# MoltBridge
# ---------------------------------------------------------------------------

class MoltBridge(BaseBridge):
    """Bridge to moltworker Cloudflare Worker (default port 8787).

    Requests carry ``X-Moltbot-Token`` for lightweight auth.  Supports sandbox health,
    mission triggers, and admin dashboard status.
    """

    def __init__(
        self, base_url: str = "http://localhost:8787", token: str = "", timeout: float = 30.0
    ) -> None:
        super().__init__(base_url, timeout)
        self.token: str = token
        self._client.headers["X-Moltbot-Token"] = token

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def sandbox_health(self) -> dict:
        """GET ``/sandbox-health`` — returns sandbox runtime health."""
        response = await self._client.get(f"{self.base_url}/sandbox-health")
        response.raise_for_status()
        return response.json()

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def trigger_mission(
        self, channel: str, message: str, payload: dict | None = None
    ) -> dict:
        """POST ``/api/gateway/trigger`` to broadcast a mission into a channel.

        Args:
            channel: Target channel name.
            message: Human-readable mission text.
            payload: Optional structured payload dict.
        """
        body: dict[str, Any] = {"channel": channel, "message": message}
        if payload is not None:
            body["payload"] = payload
        response = await self._client.post(
            f"{self.base_url}/api/gateway/trigger",
            json=body,
            headers={"Content-Type": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    @async_retry(retries=3, base_delay=1.0, backoff=2.0)
    async def admin_status(self) -> dict:
        """GET ``/_admin/`` — returns admin dashboard status."""
        response = await self._client.get(f"{self.base_url}/_admin/")
        response.raise_for_status()
        return response.json()
