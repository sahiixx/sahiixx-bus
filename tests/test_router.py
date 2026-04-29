"""Tests for sahiixx_bus.a2a_router using mocked HTTP."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from sahiixx_bus.a2a_router import A2ARouter
from sahiixx_bus.core import SafetyCouncil, SwarmBus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def bus() -> SwarmBus:
    return SwarmBus(namespace="test")


@pytest.fixture
def safety() -> SafetyCouncil:
    return SafetyCouncil(strict_mode=True)


@pytest.fixture
def router(bus: SwarmBus, safety: SafetyCouncil) -> A2ARouter:
    return A2ARouter(bus=bus, safety=safety)


# ---------------------------------------------------------------------------
# register_service
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_register_service_stores_metadata(router: A2ARouter) -> None:
    """register_service() stores name, url, capabilities and last_seen."""
    await router.register_service("svc-1", "http://svc1.local", ["search", "read"])
    meta = router._registry["svc-1"]
    assert meta["name"] == "svc-1"
    assert meta["url"] == "http://svc1.local"
    assert meta["capabilities"] == ["search", "read"]
    assert meta["last_seen"] > 0


@pytest.mark.asyncio
async def test_register_service_overwrites_existing(router: A2ARouter) -> None:
    """Re-registering updates the entry."""
    await router.register_service("svc-1", "http://old.local", ["a"])
    await router.register_service("svc-1", "http://new.local", ["b"])
    assert router._registry["svc-1"]["url"] == "http://new.local"


# ---------------------------------------------------------------------------
# discover_all
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_discover_all_fetches_agent_cards(router: A2ARouter) -> None:
    """discover_all() calls GET /.well-known/agent.json and returns cards."""
    await router.register_service("svc-1", "http://svc1.local", ["search"])

    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "Agent One", "skills": ["search"]}
    mock_response.raise_for_status = MagicMock()

    with patch.object(router._http, "get", new=AsyncMock(return_value=mock_response)):
        cards = await router.discover_all()

    assert len(cards) == 1
    assert cards[0]["name"] == "Agent One"
    assert "svc-1" in router._agent_cards


@pytest.mark.asyncio
async def test_discover_all_updates_last_seen(router: A2ARouter) -> None:
    """discover_all() updates last_seen for services that respond."""
    await router.register_service("svc-1", "http://svc1.local", ["search"])
    old_ts = router._registry["svc-1"]["last_seen"]
    time.sleep(0.05)

    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "Agent One"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(router._http, "get", new=AsyncMock(return_value=mock_response)):
        await router.discover_all()

    assert router._registry["svc-1"]["last_seen"] > old_ts


@pytest.mark.asyncio
async def test_discover_all_skips_unreachable(router: A2ARouter) -> None:
    """Unreachable services are skipped but kept in registry."""
    await router.register_service("bad", "http://bad.local", ["x"])

    with patch.object(router._http, "get", new=AsyncMock(side_effect=httpx.ConnectError("fail"))):
        cards = await router.discover_all()

    assert len(cards) == 1
    assert cards[0]["name"] == "bad"
    assert cards[0]["status"] == "unreachable"
    assert "bad" in router._registry


# ---------------------------------------------------------------------------
# route_task — safety
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_route_task_rejects_unsafe_input(router: A2ARouter) -> None:
    """If safety.scan() returns unsafe, route_task returns an error dict."""
    result = await router.route_task("rm -rf /")
    assert result["routed"] is False
    assert "Safety scan failed" in result["error"]
    assert "violations" in result


@pytest.mark.asyncio
async def test_route_task_accepts_safe_input(router: A2ARouter) -> None:
    """Safe input continues to routing logic."""
    await router.register_service("svc-1", "http://svc1.local", ["search"])

    with patch.object(router, "_send_via_bridge", new=AsyncMock(return_value={"routed": True})):
        result = await router.route_task("hello world", required_capabilities=["search"])
    assert result["routed"] is True


# ---------------------------------------------------------------------------
# route_task — scoring & tie-break
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_route_task_scores_by_capability(router: A2ARouter) -> None:
    """Highest capability-match score wins."""
    await router.register_service("a", "http://a.local", ["read"])
    await router.register_service("b", "http://b.local", ["read", "write"])

    with patch.object(router, "_send_via_bridge", new=AsyncMock(return_value={"routed": True})) as mock_send:
        await router.route_task("task", required_capabilities=["read", "write"])

    args, _ = mock_send.call_args
    assert args[0] == "b"


@pytest.mark.asyncio
async def test_route_task_tie_break_by_last_seen(router: A2ARouter) -> None:
    """When scores tie, most recent last_seen wins."""
    await router.register_service("a", "http://a.local", ["read"])
    await router.register_service("b", "http://b.local", ["read"])
    router._registry["b"]["last_seen"] = time.time() + 10

    with patch.object(router, "_send_via_bridge", new=AsyncMock(return_value={"routed": True})) as mock_send:
        await router.route_task("task", required_capabilities=["read"])

    args, _ = mock_send.call_args
    assert args[0] == "b"


# ---------------------------------------------------------------------------
# route_task — fallback chain
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_route_task_fallback_chain(router: A2ARouter) -> None:
    """If no service matches capabilities, fallback chain is used."""
    await router.register_service("agency-agents", "http://agency.local", ["chat"])

    with patch.object(router, "_send_via_bridge", new=AsyncMock(return_value={"routed": True})) as mock_send:
        await router.route_task("task", required_capabilities=["nonexistent"])

    args, _ = mock_send.call_args
    assert args[0] == "agency-agents"


@pytest.mark.asyncio
async def test_route_task_exhausted_fallback_returns_error(router: A2ARouter) -> None:
    """When fallback chain is exhausted, return an error."""
    result = await router.route_task("safe task", required_capabilities=["x"])
    assert result["routed"] is False
    assert "fallback chain exhausted" in result["error"]


# ---------------------------------------------------------------------------
# get_agent_card
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_get_agent_card_cached(router: A2ARouter) -> None:
    """Return cached card when available."""
    router._agent_cards["svc-1"] = {"name": "Cached"}
    card = await router.get_agent_card("svc-1")
    assert card["name"] == "Cached"


@pytest.mark.asyncio
async def test_get_agent_card_fetches_missing(router: A2ARouter) -> None:
    """Fetch card from service when not cached."""
    await router.register_service("svc-1", "http://svc1.local", ["read"])

    mock_response = MagicMock()
    mock_response.json.return_value = {"name": "Fresh"}
    mock_response.raise_for_status = MagicMock()

    with patch.object(router._http, "get", new=AsyncMock(return_value=mock_response)):
        card = await router.get_agent_card("svc-1")

    assert card["name"] == "Fresh"
    assert router._agent_cards["svc-1"]["name"] == "Fresh"


@pytest.mark.asyncio
async def test_get_agent_card_not_found(router: A2ARouter) -> None:
    """Unknown service returns error dict."""
    card = await router.get_agent_card("unknown")
    assert "error" in card
    assert card["service"] == "unknown"


# ---------------------------------------------------------------------------
# get_fallback_chain
# ---------------------------------------------------------------------------
def test_get_fallback_chain(router: A2ARouter) -> None:
    """Returns the expected fallback chain."""
    chain = router.get_fallback_chain()
    assert chain == ["friday-os", "goose-aios", "agency-agents"]
