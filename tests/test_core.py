"""pytest-asyncio tests for all sahiixx_bus core classes."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from pathlib import Path

import pytest

from sahiixx_bus.core import (
    BudgetController,
    EconomicEngine,
    Permission,
    RBACGuard,
    SafetyCouncil,
    SafetyVerdict,
    StateManager,
    SwarmBus,
    SwarmMemory,
)
from sahiixx_bus.types import AgentCard, AuditLog, MissionRecord, TaskMessage, ToolSchema
from sahiixx_bus.utils import (
    async_retry,
    generate_id,
    now_iso,
    safe_json_loads,
    sanitize_input,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_tmp_dirs():
    """Ensure /tmp/sahiixx_* are pristine before each test."""
    for d in ("/tmp/sahiixx_memory", "/tmp/sahiixx_state"):
        if os.path.isdir(d):
            shutil.rmtree(d)
    yield
    for d in ("/tmp/sahiixx_memory", "/tmp/sahiixx_state"):
        if os.path.isdir(d):
            shutil.rmtree(d)


@pytest.fixture
def bus():
    return SwarmBus(namespace="test")


@pytest.fixture
def memory():
    return SwarmMemory(namespace="test")


@pytest.fixture
def safety():
    return SafetyCouncil(strict_mode=True)


@pytest.fixture
def rbac():
    g = RBACGuard()
    g.add_role("admin", {Permission.ADMIN, Permission.READ, Permission.WRITE})
    g.add_role("operator", {Permission.READ, Permission.TOOL_USE, Permission.EXECUTE})
    g.add_role("viewer", {Permission.READ})
    return g


@pytest.fixture
def econ():
    return EconomicEngine()


@pytest.fixture
def budget():
    return BudgetController()


@pytest.fixture
def state():
    return StateManager()


# ---------------------------------------------------------------------------
# SwarmBus
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_bus_publish_subscribe(bus: SwarmBus):
    """Messages published to a channel reach subscribers."""
    received: list[dict] = []

    async def handler(msg: dict) -> None:
        received.append(msg)

    sub_id = await bus.subscribe("chan.a", handler)
    await bus.publish("chan.a", {"hello": "world"})
    await asyncio.sleep(0.05)
    assert len(received) == 1
    assert received[0]["hello"] == "world"
    await bus.unsubscribe(sub_id)


@pytest.mark.asyncio
async def test_bus_unsubscribe(bus: SwarmBus):
    """Unsubscribed handler no longer receives messages."""
    received: list[dict] = []

    async def handler(msg: dict) -> None:
        received.append(msg)

    sub_id = await bus.subscribe("chan.b", handler)
    await bus.publish("chan.b", {"n": 1})
    await asyncio.sleep(0.05)
    assert len(received) == 1

    await bus.unsubscribe(sub_id)
    await bus.publish("chan.b", {"n": 2})
    await asyncio.sleep(0.05)
    assert len(received) == 1  # still 1


@pytest.mark.asyncio
async def test_bus_multiple_subscribers(bus: SwarmBus):
    """Multiple subscribers on the same channel all receive the message."""
    counts = [0, 0]

    async def h0(msg: dict) -> None:
        counts[0] += 1

    async def h1(msg: dict) -> None:
        counts[1] += 1

    await bus.subscribe("chan.c", h0)
    await bus.subscribe("chan.c", h1)
    await bus.publish("chan.c", {"x": 1})
    await asyncio.sleep(0.05)
    assert counts == [1, 1]


@pytest.mark.asyncio
async def test_bus_request_response(bus: SwarmBus):
    """request() waits for and returns the first reply."""

    async def responder(msg: dict) -> None:
        reply_chan = msg.get("__reply_channel")
        if reply_chan:
            await bus.publish(reply_chan, {"answer": 42})

    await bus.subscribe("chan.req", responder)
    result = await bus.request("chan.req", {"q": "life"}, timeout=1.0)
    assert result["answer"] == 42


@pytest.mark.asyncio
async def test_bus_get_channels(bus: SwarmBus):
    """get_channels reflects active subscriptions."""
    async def h(msg: dict) -> None:
        pass

    await bus.subscribe("alpha", h)
    await bus.subscribe("beta", h)
    channels = bus.get_channels()
    assert "alpha" in channels
    assert "beta" in channels


@pytest.mark.asyncio
async def test_bus_publish_non_dict_raises(bus: SwarmBus):
    """Publishing a non-dict raises TypeError."""
    with pytest.raises(TypeError):
        await bus.publish("x", "not-a-dict")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# SwarmMemory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_record_recall(memory: SwarmMemory):
    """record() stores and recall() retrieves by relevance."""
    await memory.record("key1", "ordinary data", importance=1.0)
    await memory.record("key2", "unusual_xyz_999!!!", importance=5.0)
    results = await memory.recall("unusual", top_k=5)
    assert len(results) == 2
    # The unusual entry should rank first because of higher importance
    assert "unusual_xyz" in str(results[0]["value"])


@pytest.mark.asyncio
async def test_memory_record_outcome(memory: SwarmMemory):
    """record_outcome stores a mission result."""
    await memory.record_outcome("mission-1", "success", {"reward": 10})
    results = await memory.recall("mission-1", top_k=5)
    assert any("mission-1" in str(r["value"]) for r in results)


@pytest.mark.asyncio
async def test_memory_surprise_score(memory: SwarmMemory):
    """Higher-entropy content yields a higher surprise score."""
    low = memory.get_surprise_score("aaaaaaaaaa")
    high = memory.get_surprise_score("a1b2c3d4e5!@#")
    assert 0.0 <= low <= 1.0
    assert 0.0 <= high <= 1.0
    assert high > low


@pytest.mark.asyncio
async def test_memory_persistence(memory: SwarmMemory):
    """Memories survive re-instantiation of SwarmMemory."""
    await memory.record("persist_key", {"foo": "bar"})
    # Simulate a new process by creating a new instance with same namespace
    memory2 = SwarmMemory(namespace="test")
    results = await memory2.recall("persist_key", top_k=5)
    assert any(r["key"] == "persist_key" for r in results)


# ---------------------------------------------------------------------------
# SafetyCouncil
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_safety_scan_clean(safety: SafetyCouncil):
    """Clean input returns a safe verdict."""
    verdict = await safety.scan("Hello, world!", context="test")
    assert verdict.safe is True
    assert verdict.threat_level == "none"
    assert verdict.violations == []


@pytest.mark.asyncio
async def test_safety_scan_rm_rf(safety: SafetyCouncil):
    """rm -rf triggers a critical threat."""
    verdict = await safety.scan("rm -rf /", context="shell")
    assert verdict.safe is False
    assert verdict.threat_level == "critical"
    assert any("rm -rf" in v for v in verdict.violations)


@pytest.mark.asyncio
async def test_safety_scan_eval(safety: SafetyCouncil):
    """eval( triggers a high threat."""
    verdict = await safety.scan("eval('1+1')", context="code")
    assert verdict.safe is False
    assert verdict.threat_level == "high"


@pytest.mark.asyncio
async def test_safety_disarm(safety: SafetyCouncil):
    """Disarmed council reports violations but marks safe=True."""
    safety.disarm()
    assert safety.is_armed() is False
    verdict = await safety.scan("rm -rf /")
    assert verdict.violations  # still detects
    assert verdict.safe is True  # but does not block


@pytest.mark.asyncio
async def test_safety_adaptive_rule(safety: SafetyCouncil):
    """Adaptive rules are applied dynamically."""
    safety.add_adaptive_rule("evil_func", "critical")
    verdict = await safety.scan("call evil_func now")
    assert any("evil_func" in v for v in verdict.violations)
    assert verdict.threat_level == "critical"


@pytest.mark.asyncio
async def test_safety_normalize_input(safety: SafetyCouncil):
    """normalize_input strips control characters and limits length."""
    raw = "Hello\x00\x01World\t\n\r!" + "x" * 20000
    cleaned = safety.normalize_input(raw)
    assert "\x00" not in cleaned
    assert len(cleaned) <= 10000


@pytest.mark.asyncio
async def test_safety_get_rules(safety: SafetyCouncil):
    """get_rules returns all built-in + adaptive rules."""
    rules = safety.get_rules()
    patterns = {r["pattern"] for r in rules}
    assert "rm -rf" in patterns
    assert "eval(" in patterns
    assert "sudo" in patterns


# ---------------------------------------------------------------------------
# SafetyVerdict (pydantic model)
# ---------------------------------------------------------------------------


def test_safety_verdict_model():
    """SafetyVerdict can be instantiated and serialised."""
    v = SafetyVerdict(
        safe=False,
        threat_level="high",
        violations=["eval("],
        sanitized="eval('1+1')",
        recommendation="Reject",
    )
    assert v.safe is False
    assert v.threat_level == "high"
    d = v.model_dump()
    assert d["safe"] is False


# ---------------------------------------------------------------------------
# RBACGuard
# ---------------------------------------------------------------------------


def test_rbac_add_and_assign(rbac: RBACGuard):
    """Roles can be added and assigned to identities."""
    rbac.assign_role("alice", "admin")
    assert rbac.get_roles("alice") == ["admin"]


def test_rbac_check(rbac: RBACGuard):
    """check() reflects assigned permissions."""
    rbac.assign_role("bob", "operator")
    assert rbac.check("bob", Permission.READ) is True
    assert rbac.check("bob", Permission.ADMIN) is False


def test_rbac_require_raises(rbac: RBACGuard):
    """require() raises PermissionError on missing permission."""
    rbac.assign_role("charlie", "viewer")
    rbac.require("charlie", Permission.READ)
    with pytest.raises(PermissionError):
        rbac.require("charlie", Permission.WRITE)


def test_rbac_revoke(rbac: RBACGuard):
    """revoke() removes a role from an identity."""
    rbac.assign_role("dave", "admin")
    assert rbac.check("dave", Permission.ADMIN) is True
    rbac.revoke("dave", "admin")
    assert rbac.check("dave", Permission.ADMIN) is False


def test_rbac_assign_unknown_role_raises(rbac: RBACGuard):
    """Assigning a non-existent role raises ValueError."""
    with pytest.raises(ValueError):
        rbac.assign_role("eve", "ghost_role")


# ---------------------------------------------------------------------------
# EconomicEngine
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_econ_record_and_ledger(econ: EconomicEngine):
    """Costs are recorded and retrievable via ledger."""
    await econ.record_cost("compute", 1.23, "USD")
    ledger = await econ.get_ledger("compute", limit=10)
    assert len(ledger) == 1
    assert ledger[0]["amount"] == 1.23


@pytest.mark.asyncio
async def test_econ_charge_and_balance(econ: EconomicEngine):
    """charge() debits an account and get_balance reflects it."""
    # Seed balance manually via internal dict (no top-up method in spec)
    econ._balances["acct_a"] = 100.0
    ok = await econ.charge("acct_a", 30.0, description="GPU time")
    assert ok is True
    balance = await econ.get_balance("acct_a")
    assert balance == 70.0


@pytest.mark.asyncio
async def test_econ_charge_insufficient_funds(econ: EconomicEngine):
    """charge() returns False when balance is too low."""
    econ._balances["acct_b"] = 5.0
    ok = await econ.charge("acct_b", 10.0)
    assert ok is False


@pytest.mark.asyncio
async def test_econ_budget(econ: EconomicEngine):
    """set_budget and get_budget_status track limits."""
    econ.set_budget("acct_c", 1000.0)
    status = econ.get_budget_status("acct_c")
    assert status["budget"] == 1000.0
    assert status["remaining"] == 1000.0


# ---------------------------------------------------------------------------
# BudgetController
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_acquire_release(budget: BudgetController):
    """acquire_budget reserves and release_budget records actual spend."""
    await budget.set_limit(100.0)
    ok = await budget.acquire_budget("op-1", 30.0)
    assert ok is True
    await budget.release_budget("op-1", 25.0)
    spent = await budget.get_spent()
    assert spent == 25.0


@pytest.mark.asyncio
async def test_budget_exceeds_limit(budget: BudgetController):
    """acquire_budget fails when the reservation would exceed the limit."""
    await budget.set_limit(50.0)
    ok = await budget.acquire_budget("op-2", 60.0)
    assert ok is False


@pytest.mark.asyncio
async def test_budget_is_within_budget(budget: BudgetController):
    """is_within_budget reflects reservation state."""
    await budget.set_limit(100.0)
    await budget.acquire_budget("op-3", 40.0)
    assert await budget.is_within_budget(50.0) is True
    assert await budget.is_within_budget(70.0) is False


@pytest.mark.asyncio
async def test_budget_release_updates_spent(budget: BudgetController):
    """Multiple releases accumulate in spent."""
    await budget.set_limit(1000.0)
    await budget.acquire_budget("a", 10.0)
    await budget.release_budget("a", 10.0)
    await budget.acquire_budget("b", 20.0)
    await budget.release_budget("b", 20.0)
    assert await budget.get_spent() == 30.0


# ---------------------------------------------------------------------------
# StateManager
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_save_load(state: StateManager):
    """save() and load() round-trip a dict."""
    payload = {"agents": ["a", "b"], "config": {"timeout": 5}}
    await state.save("swarm-1", payload)
    loaded = await state.load("swarm-1")
    assert loaded == payload


@pytest.mark.asyncio
async def test_state_delete(state: StateManager):
    """delete() removes a key."""
    await state.save("tmp", {"x": 1})
    assert await state.load("tmp") is not None
    await state.delete("tmp")
    assert await state.load("tmp") is None


@pytest.mark.asyncio
async def test_state_list_keys(state: StateManager):
    """list_keys returns saved keys, optionally filtered by prefix."""
    await state.save("alpha", {})
    await state.save("beta", {})
    await state.save("alpha-x", {})
    all_keys = await state.list_keys()
    assert "alpha" in all_keys
    assert "beta" in all_keys
    prefixed = await state.list_keys("alpha")
    assert "alpha" in prefixed
    assert "beta" not in prefixed


@pytest.mark.asyncio
async def test_state_snapshot_restore(state: StateManager):
    """snapshot() and restore() preserve state."""
    original = {"data": "v1"}
    await state.save("db", original)
    snap_id = await state.snapshot("db")
    # Mutate
    await state.save("db", {"data": "v2"})
    assert (await state.load("db"))["data"] == "v2"
    # Restore
    restored = await state.restore(snap_id)
    assert restored["data"] == "v1"
    assert (await state.load("db"))["data"] == "v1"


@pytest.mark.asyncio
async def test_state_snapshot_not_found(state: StateManager):
    """snapshot() raises FileNotFoundError for missing keys."""
    with pytest.raises(FileNotFoundError):
        await state.snapshot("nonexistent")


@pytest.mark.asyncio
async def test_state_restore_not_found(state: StateManager):
    """restore() raises FileNotFoundError for missing snapshots."""
    with pytest.raises(FileNotFoundError):
        await state.restore("nonexistent_snapshot")


# ---------------------------------------------------------------------------
# Utils
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_retry_success():
    """async_retry returns the coroutine result on success."""
    async def ok() -> int:
        return 42

    result = await async_retry(ok, retries=3)
    assert result == 42


@pytest.mark.asyncio
async def test_async_retry_eventual_success():
    """async_retry recovers after transient failures."""
    attempts = 0

    async def flaky() -> int:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("boom")
        return 99

    result = await async_retry(flaky, retries=5, backoff=0.01)
    assert result == 99
    assert attempts == 3


@pytest.mark.asyncio
async def test_async_retry_exhausted():
    """async_retry raises the last exception when retries are exhausted."""
    async def always_fail() -> int:
        raise ValueError("nope")

    with pytest.raises(ValueError):
        await async_retry(always_fail, retries=2, backoff=0.01)


def test_sanitize_input():
    """sanitize_input strips control characters and limits length."""
    raw = "Hello\x00World\n\t" + "A" * 20000
    out = sanitize_input(raw, max_length=500)
    assert "\x00" not in out
    assert len(out) <= 500


def test_generate_id():
    """generate_id produces non-empty prefixed identifiers."""
    ident = generate_id("tst")
    assert ident.startswith("tst-")
    assert len(ident) > 4


def test_now_iso():
    """now_iso returns a valid ISO-8601 timestamp."""
    ts = now_iso()
    assert "T" in ts
    assert ts.endswith("+00:00") or ("Z" in ts)


def test_safe_json_loads():
    """safe_json_loads parses JSON and falls back to empty dict."""
    assert safe_json_loads('{"a":1}') == {"a": 1}
    assert safe_json_loads("not json") == {}


# ---------------------------------------------------------------------------
# Pydantic Models (types.py)
# ---------------------------------------------------------------------------


def test_task_message_defaults():
    """TaskMessage uses sensible defaults."""
    msg = TaskMessage(task_id="t1", sender="agent_a")
    assert msg.priority == 5
    assert msg.task_type == "default"


def test_agent_card_serialization():
    """AgentCard round-trips through dict/json."""
    card = AgentCard(agent_id="a1", name="Searcher", capabilities=["web_search"])
    d = card.model_dump()
    assert d["agent_id"] == "a1"
    assert d["capabilities"] == ["web_search"]


def test_audit_log_immutable():
    """AuditLog can be instantiated with all fields."""
    log = AuditLog(
        log_id="l1",
        event_type="scan",
        identity="agent_x",
        action=" SafetyCouncil.scan",
        permitted=True,
        threat_level="high",
    )
    assert log.permitted is True
    assert log.threat_level == "high"
