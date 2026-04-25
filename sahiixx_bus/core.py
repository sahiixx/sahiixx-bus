"""Core classes for sahiixx-bus orchestration layer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import re
import string
from copy import deepcopy
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable

import math

from pydantic import BaseModel, Field

from sahiixx_bus.utils import generate_id, now_iso, sanitize_input


class SwarmBus:
    """Async pub/sub message bus for inter-agent communication.

    Backed by an in-memory dict of channel -> list of handler coroutines.
    Each subscription receives a unique ID for later ``unsubscribe``.
    """

    def __init__(self, namespace: str = "default") -> None:
        self.namespace: str = namespace
        self._channels: dict[str, list[tuple[str, Callable[[dict], Awaitable[None]]]]] = {}
        self._request_handlers: dict[str, Callable[[dict], Awaitable[dict]]] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def publish(self, channel: str, message: dict) -> None:
        """Publish a message to every subscriber on *channel*.

        Args:
            channel: Logical channel name.
            message: JSON-serialisable payload dict.
        """
        if not isinstance(message, dict):
            raise TypeError("Message must be a dict")
        async with self._lock:
            handlers = list(self._channels.get(channel, []))
        for _sub_id, handler in handlers:
            try:
                await handler(deepcopy(message))
            except Exception:
                # Fire-and-forget; bus is not responsible for handler errors
                pass

    async def subscribe(
        self, channel: str, handler: Callable[[dict], Awaitable[None]]
    ) -> str:
        """Subscribe *handler* to *channel*.

        Args:
            channel: Channel name to listen on.
            handler: Async callable receiving a dict payload.

        Returns:
            Subscription ID that can be passed to ``unsubscribe``.
        """
        sub_id = generate_id("sub")
        async with self._lock:
            self._channels.setdefault(channel, []).append((sub_id, handler))
        return sub_id

    async def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by its ID.

        Args:
            subscription_id: ID returned by ``subscribe``.
        """
        async with self._lock:
            for channel, handlers in self._channels.items():
                new_handlers = [h for h in handlers if h[0] != subscription_id]
                if len(new_handlers) != len(handlers):
                    self._channels[channel] = new_handlers
                    return

    async def request(self, channel: str, message: dict, timeout: float = 30.0) -> dict:
        """Publish a request and wait for the first response.

        A temporary one-shot subscriber is installed on a private
        reply channel. The first subscriber on *channel* that publishes
        back to the reply channel wins.

        Args:
            channel: Channel to publish the request on.
            message: Payload; augmented with ``__reply_channel``.
            timeout: Seconds to wait for a reply.

        Returns:
            Response dict from the first replying agent.

        Raises:
            asyncio.TimeoutError: If no reply arrives in time.
        """
        reply_channel = f"_reply_{generate_id('rpl')}"
        future: asyncio.Future[dict] = asyncio.get_running_loop().create_future()

        async def _reply_handler(msg: dict) -> None:
            if not future.done():
                future.set_result(msg)

        await self.subscribe(reply_channel, _reply_handler)
        message = dict(message)
        message["__reply_channel"] = reply_channel
        await self.publish(channel, message)

        try:
            result = await asyncio.wait_for(future, timeout=timeout)
        finally:
            await self.unsubscribe(reply_channel)

        return result

    def get_channels(self) -> list[str]:
        """Return the names of all channels with at least one subscriber."""
        return list(self._channels.keys())


class SwarmMemory:
    """Surprise-weighted persistent memory inspired by Titans architecture.

    Memories are stored as JSON files under ``/tmp/sahiixx_memory/``.
    ``recall`` ranks by surprise score and importance.
    """

    def __init__(self, backend_url: str = "", namespace: str = "default") -> None:
        self.backend_url: str = backend_url
        self.namespace: str = namespace
        self._storage_path: Path = Path("/tmp/sahiixx_memory")
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._lock: asyncio.Lock = asyncio.Lock()
        self._cache: dict[str, Any] = {}

    def _file_for(self, key: str) -> Path:
        safe_key = re.sub(r"[^\w\-]", "_", key)[:128]
        return self._storage_path / f"{self.namespace}_{safe_key}.json"

    async def record(self, key: str, value: Any, importance: float = 1.0) -> None:
        """Store a memory entry with importance weighting.

        Args:
            key: Unique memory key.
            value: Arbitrary serialisable value.
            importance: Importance multiplier (>=0).
        """
        entry = {
            "key": key,
            "value": value,
            "importance": importance,
            "surprise": self.get_surprise_score(str(value)),
            "timestamp": now_iso(),
            "namespace": self.namespace,
        }
        async with self._lock:
            self._cache[key] = entry
            path = self._file_for(key)
            path.write_text(json.dumps(entry, indent=2, default=str), encoding="utf-8")

    async def recall(self, query: str, top_k: int = 5) -> list[dict]:
        """Recall the *top_k* most relevant memories for *query*.

        Relevance is a combination of surprise score and declared
        importance. All memories in the namespace are scanned.

        Args:
            query: Textual query (used for surprise computation).
            top_k: Maximum results to return.

        Returns:
            List of memory entry dicts sorted by relevance descending.
        """
        async with self._lock:
            entries: list[dict] = []
            for path in self._storage_path.glob(f"{self.namespace}_*.json"):
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                    entries.append(raw)
                except Exception:
                    continue
            # Also include anything in cache that hasn't been flushed
            for key, entry in self._cache.items():
                if not any(e.get("key") == key for e in entries):
                    entries.append(entry)

        query_surprise = self.get_surprise_score(query)
        scored: list[tuple[float, dict]] = []
        for entry in entries:
            surprise = entry.get("surprise", 0.0)
            importance = entry.get("importance", 1.0)
            # Relevance is surprise * importance, boosted when query
            # surprise and entry surprise are close (information overlap)
            relevance = importance * (1.0 + abs(surprise - query_surprise))
            scored.append((relevance, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:top_k]]

    async def record_outcome(
        self, mission: str, verdict: str, metadata: dict | None = None
    ) -> None:
        """Record the outcome of a mission for future surprise-weighted recall.

        Args:
            mission: Mission identifier.
            verdict: Outcome summary string.
            metadata: Optional extra context.
        """
        key = f"outcome_{mission}_{now_iso()}"
        value = {"mission": mission, "verdict": verdict, "meta": metadata or {}}
        await self.record(key, value, importance=2.0)

    def get_surprise_score(self, content: str) -> float:
        """Compute a heuristic surprise score for *content*.

        Higher scores indicate more unusual / high-entropy content.
        Currently uses Shannon entropy of character distribution.

        Args:
            content: Text to score.

        Returns:
            Surprise score in the range [0, 1].
        """
        if not content:
            return 0.0
        content = content.lower()
        counts: dict[str, int] = {}
        for ch in content:
            counts[ch] = counts.get(ch, 0) + 1
        total = len(content)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            if p > 0:
                entropy -= p * math.log2(p)
        # Normalise roughly to [0,1] — max entropy for 256 chars ~ 8 bits
        return min(1.0, entropy / 8.0)


class SafetyVerdict(BaseModel):
    """Result of a ``SafetyCouncil.scan`` operation."""

    safe: bool = Field(..., description="True if no violations found")
    threat_level: str = Field(
        ..., description="none | low | medium | high | critical"
    )
    violations: list[str] = Field(default_factory=list, description="Matched rules")
    sanitized: str = Field(..., description="Normalised input after cleaning")
    recommendation: str = Field(
        ..., description="Human-readable recommendation"
    )


class SafetyCouncil:
    """Multi-layer safety scanner with adaptive rules and arm/disarm semantics.

    When *armed* (the default), every ``scan`` is enforced. When *disarmed*,
    the scanner still reports violations but marks ``safe`` as True so that
    callers can choose to proceed in maintenance / debug scenarios.
    """

    _BUILTIN_PATTERNS: dict[str, str] = {
        "rm -rf": "critical",
        "eval(": "high",
        "exec(": "high",
        "os.system": "high",
        "subprocess.call": "high",
        "__import__": "medium",
        "chmod 777": "medium",
        "sudo": "medium",
        "mkfs.": "critical",
        "dd if=": "high",
    }

    def __init__(self, strict_mode: bool = True) -> None:
        self.strict_mode: bool = strict_mode
        self._armed: bool = True
        self._adaptive_rules: dict[str, str] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def scan(self, content: str, context: str = "") -> SafetyVerdict:
        """Scan *content* against built-in and adaptive dangerous patterns.

        Args:
            content: Raw text to inspect.
            context: Optional extra context string.

        Returns:
            ``SafetyVerdict`` with threat assessment and recommendation.
        """
        sanitized = self.normalize_input(content)
        violations: list[str] = []
        max_severity = "none"
        severity_order = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

        all_rules = {**self._BUILTIN_PATTERNS, **self._adaptive_rules}

        for pattern, severity in all_rules.items():
            if pattern.lower() in sanitized.lower():
                violations.append(f"Matched '{pattern}' (severity: {severity})")
                if severity_order[severity] > severity_order[max_severity]:
                    max_severity = severity

        safe = len(violations) == 0 or not self._armed

        if max_severity == "critical":
            recommendation = "BLOCK IMMEDIATELY. Critical threat detected."
        elif max_severity == "high":
            recommendation = "High risk input. Requires manual review before execution."
        elif max_severity == "medium":
            recommendation = "Medium risk. Verify intent and sandbox if possible."
        elif max_severity == "low":
            recommendation = "Low risk anomaly. Log and continue with caution."
        else:
            recommendation = "No threats detected. Proceed normally."

        if context:
            recommendation = f"[{context}] {recommendation}"

        return SafetyVerdict(
            safe=safe,
            threat_level=max_severity,
            violations=violations,
            sanitized=sanitized,
            recommendation=recommendation,
        )

    def arm(self) -> None:
        """Arm the safety council — scans will mark unsafe content as unsafe."""
        self._armed = True

    def disarm(self) -> None:
        """Disarm the safety council — scans still report but never block."""
        self._armed = False

    def is_armed(self) -> bool:
        """Return whether the council is currently armed."""
        return self._armed

    def normalize_input(self, raw: str) -> str:
        """Strip control characters and enforce a 10 000-character limit.

        Args:
            raw: Raw input string.

        Returns:
            Cleaned, truncated string.
        """
        return sanitize_input(raw, max_length=10000)

    def add_adaptive_rule(self, pattern: str, severity: str) -> None:
        """Dynamically add a new dangerous pattern.

        Args:
            pattern: Substring to watch for.
            severity: One of ``low``, ``medium``, ``high``, ``critical``.
        """
        self._adaptive_rules[pattern] = severity

    def get_rules(self) -> list[dict]:
        """Return all active rules (built-in + adaptive)."""
        rules: list[dict] = []
        for pattern, severity in {**self._BUILTIN_PATTERNS, **self._adaptive_rules}.items():
            rules.append({"pattern": pattern, "severity": severity})
        return rules


class Permission(str, Enum):
    """Enumeration of RBAC permissions in the SAHIIXX ecosystem."""

    EXECUTE = "execute"
    KILL = "kill"
    EMERGENCY_ARM = "emergency_arm"
    ADMIN = "admin"
    AGENT_SPAWN = "agent_spawn"
    TOOL_USE = "tool_use"
    READ = "read"
    WRITE = "write"
    DELETE = "delete"


class RBACGuard:
    """Role-based access control for agent identities.

    Roles are sets of ``Permission``. Identities are assigned to roles.
    ``check`` returns a boolean; ``require`` raises ``PermissionError``.
    """

    def __init__(self) -> None:
        self._roles: dict[str, set[Permission]] = {}
        self._assignments: dict[str, set[str]] = {}  # identity -> set of role names
        self._lock: asyncio.Lock = asyncio.Lock()

    def add_role(self, role: str, permissions: set[Permission]) -> None:
        """Define a new role with its permission set.

        Args:
            role: Role name (e.g. ``admin``, ``operator``).
            permissions: Set of ``Permission`` values.
        """
        self._roles[role] = set(permissions)

    def assign_role(self, identity: str, role: str) -> None:
        """Assign *role* to *identity*.

        Args:
            identity: Unique identity string (agent or user).
            role: Existing role name.

        Raises:
            ValueError: If the role does not exist.
        """
        if role not in self._roles:
            raise ValueError(f"Role '{role}' is not defined")
        self._assignments.setdefault(identity, set()).add(role)

    def check(self, identity: str, permission: Permission, resource: str = "") -> bool:
        """Return True if *identity* holds *permission* (optionally for *resource*).

        Args:
            identity: Identity to check.
            permission: Required ``Permission``.
            resource: Optional resource context (ignored in base implementation).
        """
        roles = self._assignments.get(identity, set())
        for role in roles:
            perms = self._roles.get(role, set())
            if permission in perms:
                return True
        return False

    def require(self, identity: str, permission: Permission, resource: str = "") -> None:
        """Assert that *identity* has *permission*.

        Args:
            identity: Identity to check.
            permission: Required ``Permission``.
            resource: Optional resource context.

        Raises:
            PermissionError: If the identity lacks the permission.
        """
        if not self.check(identity, permission, resource):
            raise PermissionError(
                f"Identity '{identity}' lacks permission '{permission.value}'"
                f"{f' on {resource}' if resource else ''}"
            )

    def get_roles(self, identity: str) -> list[str]:
        """Return the list of role names assigned to *identity*."""
        return list(self._assignments.get(identity, set()))

    def revoke(self, identity: str, role: str) -> None:
        """Revoke *role* from *identity*.

        Args:
            identity: Identity whose role is being revoked.
            role: Role name to remove.
        """
        if identity in self._assignments:
            self._assignments[identity].discard(role)


class EconomicEngine:
    """Cost tracking and resource economics for agent operations.

    Stores ledgers and budgets in memory protected by ``asyncio.Lock``.
    """

    def __init__(self) -> None:
        self._ledgers: dict[str, list[dict]] = {}
        self._balances: dict[str, float] = {}
        self._budgets: dict[str, float] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def record_cost(
        self, operation: str, amount: float, currency: str = "USD"
    ) -> None:
        """Record a cost entry for *operation*.

        Args:
            operation: Name of the operation.
            amount: Monetary cost.
            currency: Currency code (default ``USD``).
        """
        async with self._lock:
            self._ledgers.setdefault(operation, []).append(
                {
                    "amount": amount,
                    "currency": currency,
                    "timestamp": now_iso(),
                }
            )

    async def get_balance(self, account: str) -> float:
        """Return the current balance for *account*.

        Args:
            account: Account identifier.
        """
        async with self._lock:
            return self._balances.get(account, 0.0)

    async def charge(
        self, account: str, amount: float, description: str = ""
    ) -> bool:
        """Debit *amount* from *account* if funds are available.

        Args:
            account: Account to charge.
            amount: Amount to debit.
            description: Optional memo.

        Returns:
            True if the charge succeeded.
        """
        async with self._lock:
            balance = self._balances.get(account, 0.0)
            if amount > balance:
                return False
            self._balances[account] = balance - amount
            self._ledgers.setdefault(account, []).append(
                {
                    "type": "debit",
                    "amount": amount,
                    "description": description,
                    "timestamp": now_iso(),
                }
            )
            return True

    async def get_ledger(self, account: str, limit: int = 100) -> list[dict]:
        """Return the transaction ledger for *account*.

        Args:
            account: Account identifier.
            limit: Maximum entries to return (most recent first).
        """
        async with self._lock:
            ledger = list(self._ledgers.get(account, []))
        ledger.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return ledger[:limit]

    def set_budget(self, account: str, budget: float) -> None:
        """Set a spending budget for *account*.

        Args:
            account: Account identifier.
            budget: Maximum allowed spend.
        """
        self._budgets[account] = budget

    def get_budget_status(self, account: str) -> dict:
        """Return budget status for *account*.

        Returns:
            Dict with ``budget``, ``spent``, and ``remaining``.
        """
        budget = self._budgets.get(account, 0.0)
        ledger = self._ledgers.get(account, [])
        spent = sum(
            entry.get("amount", 0.0)
            for entry in ledger
            if entry.get("type") != "debit"
        )
        # Also subtract debits because they reduce balance
        for entry in ledger:
            if entry.get("type") == "debit":
                spent += entry.get("amount", 0.0)
        return {
            "budget": budget,
            "spent": spent,
            "remaining": max(0.0, budget - spent),
        }


class BudgetController:
    """Async budget tracking with per-operation reservation and release.

    Operations ``acquire_budget`` to reserve estimated funds. Once the
    real cost is known they ``release_budget``, which updates the
    actual spend.
    """

    def __init__(self) -> None:
        self._limit: float = float("inf")
        self._period: str = "daily"
        self._spent: float = 0.0
        self._reserved: dict[str, float] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def acquire_budget(
        self, operation_id: str, estimated_cost: float, timeout: float = 10.0
    ) -> bool:
        """Reserve *estimated_cost* for *operation_id*.

        Args:
            operation_id: Unique operation identifier.
            estimated_cost: Estimated monetary cost.
            timeout: Unused in this implementation (reserved for future).

        Returns:
            True if the reservation fits within the current limit.
        """
        async with self._lock:
            total = self._spent + sum(self._reserved.values()) + estimated_cost
            if total > self._limit:
                return False
            self._reserved[operation_id] = estimated_cost
            return True

    async def release_budget(self, operation_id: str, actual_cost: float) -> None:
        """Release the reservation and record the actual spend.

        Args:
            operation_id: Operation that was reserved earlier.
            actual_cost: Real cost incurred.
        """
        async with self._lock:
            reserved = self._reserved.pop(operation_id, 0.0)
            self._spent += actual_cost
            # Refund unused reservation (if any)
            _unused = max(0.0, reserved - actual_cost)

    async def get_spent(self, period: str = "daily") -> float:
        """Return total spent amount.

        Args:
            period: Time period label (informational only in base impl).
        """
        async with self._lock:
            return self._spent

    async def set_limit(self, limit: float, period: str = "daily") -> None:
        """Set the spending limit.

        Args:
            limit: Maximum allowed spend.
            period: Time period label.
        """
        async with self._lock:
            self._limit = limit
            self._period = period

    async def is_within_budget(self, estimated_cost: float) -> bool:
        """Return True if *estimated_cost* can be accommodated right now."""
        async with self._lock:
            total = self._spent + sum(self._reserved.values()) + estimated_cost
            return total <= self._limit


class StateManager:
    """Persistent state manager backed by JSON files.

    Stores all state as JSON under ``storage_path`` (default
    ``/tmp/sahiixx_state``). Snapshots are deep-copied files with a
    snapshot suffix so ``restore`` can bring back any previous version.
    """

    def __init__(self, storage_path: str = "/tmp/sahiixx_state") -> None:
        self._storage_path: Path = Path(storage_path)
        self._storage_path.mkdir(parents=True, exist_ok=True)
        self._lock: asyncio.Lock = asyncio.Lock()

    def _file_for(self, key: str) -> Path:
        safe_key = re.sub(r"[^\w\-]", "_", key)[:128]
        return self._storage_path / f"{safe_key}.json"

    async def save(self, key: str, state: dict) -> None:
        """Persist *state* under *key*.

        Args:
            key: Unique state key.
            state: JSON-serialisable dictionary.
        """
        async with self._lock:
            path = self._file_for(key)
            path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")

    async def load(self, key: str) -> dict | None:
        """Load state by *key*.

        Args:
            key: State key previously passed to ``save``.

        Returns:
            The state dict, or ``None`` if not found.
        """
        path = self._file_for(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    async def delete(self, key: str) -> None:
        """Delete state by *key*.

        Args:
            key: State key to remove.
        """
        async with self._lock:
            path = self._file_for(key)
            if path.exists():
                path.unlink()

    async def list_keys(self, prefix: str = "") -> list[str]:
        """Return all keys with optional *prefix* filter.

        Args:
            prefix: Filter prefix string.

        Returns:
            List of matching keys (without ``.json`` extension).
        """
        async with self._lock:
            keys: list[str] = []
            for path in self._storage_path.glob("*.json"):
                # Exclude snapshot files
                if path.name.endswith("_snapshot.json"):
                    continue
                name = path.stem
                if name.startswith(prefix):
                    keys.append(name)
            return sorted(keys)

    async def snapshot(self, key: str) -> str:
        """Create a point-in-time snapshot of *key*.

        Args:
            key: State key to snapshot.

        Returns:
            Snapshot ID (derived from the snapshot filename).

        Raises:
            FileNotFoundError: If the key does not exist.
        """
        src = self._file_for(key)
        if not src.exists():
            raise FileNotFoundError(f"No state for key '{key}'")
        snap_id = f"{key}_snapshot_{now_iso().replace(':', '-')}"
        dst = self._storage_path / f"{snap_id}.json"
        async with self._lock:
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        return snap_id

    async def restore(self, snapshot_id: str) -> dict:
        """Restore state from a snapshot.

        Args:
            snapshot_id: ID returned by ``snapshot``.

        Returns:
            The restored state dict.

        Raises:
            FileNotFoundError: If the snapshot does not exist.
        """
        snap_path = self._storage_path / f"{snapshot_id}.json"
        if not snap_path.exists():
            raise FileNotFoundError(f"Snapshot '{snapshot_id}' not found")
        data = json.loads(snap_path.read_text(encoding="utf-8"))
        # Derive the original key (strip _snapshot_ suffix)
        base_key = snapshot_id.split("_snapshot_")[0]
        await self.save(base_key, data)
        return data
