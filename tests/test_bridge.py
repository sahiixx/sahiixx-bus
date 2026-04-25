#!/usr/bin/env python3
"""
tests/test_bridge.py — pytest-asyncio tests for all SAHIIXX bridge classes.

Uses ``respx`` to mock ``httpx.AsyncClient`` HTTP calls and ``unittest.mock``
for WebSocket/SSE streaming primitives.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import respx
from httpx import Response

from sahiixx_bus.bridge import (
    AgencyBridge,
    BaseBridge,
    FixfizxBridge,
    FridayBridge,
    GooseBridge,
    MoltBridge,
)


# ---------------------------------------------------------------------------
# BaseBridge
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_base_bridge_health() -> None:
    """BaseBridge.health returns JSON from /health."""
    route = respx.get("http://localhost:9999/health").mock(
        return_value=Response(200, json={"status": "ok", "healthy": True})
    )
    bridge = BaseBridge("http://localhost:9999")
    result = await bridge.health()
    assert result["status"] == "ok"
    assert result["healthy"] is True
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_base_bridge_discover() -> None:
    """BaseBridge.discover parses agent card from /.well-known/agent.json."""
    card = {"name": "TestAgent", "version": "1.0.0"}
    route = respx.get("http://localhost:9999/.well-known/agent.json").mock(
        return_value=Response(200, json=card)
    )
    bridge = BaseBridge("http://localhost:9999")
    result = await bridge.discover()
    assert result == card
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_base_bridge_health_fallback_to_wellknown() -> None:
    """BaseBridge.health falls back to /.well-known/agent.json when /health 500s."""
    respx.get("http://localhost:9999/health").mock(return_value=Response(503))
    route = respx.get("http://localhost:9999/.well-known/agent.json").mock(
        return_value=Response(200, json={"status": "degraded"})
    )
    bridge = BaseBridge("http://localhost:9999")
    result = await bridge.health()
    assert result["status"] == "degraded"
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_base_bridge_retry_on_connect_error() -> None:
    """BaseBridge.discover retries on transient network errors (single URL)."""
    route = respx.get("http://localhost:9999/.well-known/agent.json")
    route.side_effect = [
        httpx.ConnectError("conn refused"),
        Response(200, json={"status": "ok"}),
    ]
    bridge = BaseBridge("http://localhost:9999")
    result = await bridge.discover()
    assert result["status"] == "ok"
    assert route.call_count == 2
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_base_bridge_close_idempotent() -> None:
    """Calling BaseBridge.close does not raise."""
    bridge = BaseBridge("http://localhost:9999")
    await bridge.close()
    await bridge.close()


# ---------------------------------------------------------------------------
# AgencyBridge
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_agency_bridge_send_task() -> None:
    """AgencyBridge.send_task posts correct JSON-RPC 2.0 envelope."""
    def match_jsonrpc(request: httpx.Request) -> Response:
        body = json.loads(request.content)
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "tasks/send"
        assert body["params"]["message"]["parts"][0]["text"] == "hello"
        return Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"taskId": "t-123"}})

    route = respx.post("http://localhost:8100/tasks/send").mock(side_effect=match_jsonrpc)
    bridge = AgencyBridge()
    result = await bridge.send_task("agency_default", "hello", context_id="ctx-1")
    assert result["taskId"] == "t-123"
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_agency_bridge_get_task() -> None:
    """AgencyBridge.get_task posts JSON-RPC tasks/get."""
    route = respx.post("http://localhost:8100/tasks/get").mock(
        return_value=Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"status": "done"}})
    )
    bridge = AgencyBridge()
    result = await bridge.get_task("agency_default", "task-42")
    assert result["status"] == "done"
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_agency_bridge_cancel_task() -> None:
    """AgencyBridge.cancel_task posts JSON-RPC tasks/cancel."""
    route = respx.post("http://localhost:8100/tasks/cancel").mock(
        return_value=Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"cancelled": True}})
    )
    bridge = AgencyBridge()
    result = await bridge.cancel_task("agency_default", "task-42")
    assert result["cancelled"] is True
    assert route.called
    await bridge.close()


@pytest.mark.asyncio
async def test_agency_bridge_list_agents() -> None:
    """AgencyBridge.list_agents returns names from the local port_map."""
    bridge = AgencyBridge()
    agents = bridge.list_agents()
    assert isinstance(agents, list)
    assert "agency_default" in agents
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_agency_bridge_stream_task() -> None:
    """AgencyBridge.stream_task yields SSE data lines."""
    sse_body = "data: chunk1\n\ndata: chunk2\n\n"
    route = respx.get("http://localhost:8100/stream?taskId=task-99").mock(
        return_value=Response(200, text=sse_body, headers={"content-type": "text/event-stream"})
    )
    bridge = AgencyBridge()
    chunks = [chunk async for chunk in bridge.stream_task("agency_default", "task-99")]
    assert chunks == ["chunk1", "chunk2"]
    assert route.called
    await bridge.close()


# ---------------------------------------------------------------------------
# FridayBridge
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_friday_bridge_invoke() -> None:
    """FridayBridge.invoke POSTs skill + params."""
    def match(request: httpx.Request) -> Response:
        body = json.loads(request.content)
        assert body["skill"] == "summarise"
        assert body["params"]["text"] == "long article"
        return Response(200, json={"result": "short summary"})

    route = respx.post("http://localhost:8000/a2a/invoke").mock(side_effect=match)
    bridge = FridayBridge()
    result = await bridge.invoke("summarise", {"text": "long article"})
    assert result["result"] == "short summary"
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_friday_bridge_list_skills() -> None:
    """FridayBridge.list_skills returns a list of skill names."""
    route = respx.get("http://localhost:8000/a2a/skills").mock(
        return_value=Response(200, json=["summarise", "translate", "code"])
    )
    bridge = FridayBridge()
    skills = await bridge.list_skills()
    assert skills == ["summarise", "translate", "code"]
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_friday_bridge_chat_stream() -> None:
    """FridayBridge.chat_stream yields SSE chunks."""
    sse = "data: hello\n\ndata: world\n\n"
    route = respx.get("http://localhost:8000/chat/stream?message=hi").mock(
        return_value=Response(200, text=sse, headers={"content-type": "text/event-stream"})
    )
    bridge = FridayBridge()
    chunks = [c async for c in bridge.chat_stream("hi")]
    assert chunks == ["hello", "world"]
    assert route.called
    await bridge.close()


# ---------------------------------------------------------------------------
# GooseBridge
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_goose_bridge_chat() -> None:
    """GooseBridge.chat POSTs message + optional history."""
    def match(request: httpx.Request) -> Response:
        body = json.loads(request.content)
        assert body["message"] == "hello"
        assert body["history"][0]["role"] == "user"
        return Response(200, json={"reply": "hi there"})

    route = respx.post("http://localhost:8001/chat").mock(side_effect=match)
    bridge = GooseBridge()
    result = await bridge.chat("hello", history=[{"role": "user", "content": "hello"}])
    assert result["reply"] == "hi there"
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_goose_bridge_list_agents() -> None:
    """GooseBridge.list_agents returns a list of agent dicts."""
    agents = [{"id": "a1", "name": "Alpha"}, {"id": "a2", "name": "Beta"}]
    route = respx.get("http://localhost:8001/agents").mock(return_value=Response(200, json=agents))
    bridge = GooseBridge()
    result = await bridge.list_agents()
    assert len(result) == 2
    assert result[0]["name"] == "Alpha"
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_goose_bridge_delegate() -> None:
    """GooseBridge.delegate POSTs to /agents/{id}/delegate."""
    def match(request: httpx.Request) -> Response:
        body = json.loads(request.content)
        assert body["task"] == "deploy"
        return Response(200, json={"delegated": True})

    route = respx.post("http://localhost:8001/agents/a1/delegate").mock(side_effect=match)
    bridge = GooseBridge()
    result = await bridge.delegate("a1", "deploy")
    assert result["delegated"] is True
    assert route.called
    await bridge.close()


@pytest.mark.asyncio
async def test_goose_bridge_ws_chat() -> None:
    """GooseBridge.ws_chat yields text frames from a mocked WebSocket."""
    bridge = GooseBridge()
    mock_ws = MagicMock()
    mock_ws.send_str = AsyncMock()
    mock_ws.__aenter__ = AsyncMock(return_value=mock_ws)
    mock_ws.__aexit__ = AsyncMock(return_value=False)

    fake_msg = MagicMock()
    fake_msg.type = 1  # aiohttp.WSMsgType.TEXT
    fake_msg.data = "ws reply"

    closed_msg = MagicMock()
    closed_msg.type = 257  # aiohttp.WSMsgType.CLOSED
    closed_msg.data = None

    async def fake_iter():
        yield fake_msg
        yield closed_msg

    mock_ws.__aiter__ = lambda self: fake_iter()

    # ws_connect must return an awaitable that produces an async-context-manager
    async_ws_ctx = MagicMock()
    async_ws_ctx.__aenter__ = AsyncMock(return_value=mock_ws)
    async_ws_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.ws_connect = MagicMock(return_value=async_ws_ctx)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    with patch("aiohttp.ClientSession", return_value=mock_session):
        chunks = [c async for c in bridge.ws_chat("hello")]

    assert chunks == ["ws reply"]
    await bridge.close()


# ---------------------------------------------------------------------------
# FixfizxBridge
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_fixfizx_bridge_enhanced_chat() -> None:
    """FixfizxBridge.enhanced_chat POSTs message + context."""
    route = respx.post("http://localhost:8002/api/ai/advanced/enhanced-chat").mock(
        return_value=Response(200, json={"reply": "Dubai is booming"})
    )
    bridge = FixfizxBridge(jwt="secret-jwt")
    result = await bridge.enhanced_chat("How is the market?", context={"user_id": "u1"})
    assert result["reply"] == "Dubai is booming"
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer secret-jwt"
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_fixfizx_bridge_qualify_lead() -> None:
    """FixfizxBridge.qualify_lead POSTs lead data."""
    route = respx.post("http://localhost:8002/api/agents/sales/qualify-lead").mock(
        return_value=Response(200, json={"qualified": True, "score": 0.92})
    )
    bridge = FixfizxBridge(jwt="secret-jwt")
    result = await bridge.qualify_lead({"name": "Alice", "email": "alice@example.com"})
    assert result["qualified"] is True
    assert result["score"] == 0.92
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_fixfizx_bridge_analyze_market() -> None:
    """FixfizxBridge.analyze_market POSTs market name."""
    route = respx.post("http://localhost:8002/api/ai/advanced/dubai-market-analysis").mock(
        return_value=Response(200, json={"trend": "upward"})
    )
    bridge = FixfizxBridge(jwt="secret-jwt")
    result = await bridge.analyze_market("dubai")
    assert result["trend"] == "upward"
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_fixfizx_bridge_create_campaign() -> None:
    """FixfizxBridge.create_campaign POSTs campaign config."""
    route = respx.post("http://localhost:8002/api/agents/marketing/create-campaign").mock(
        return_value=Response(200, json={"campaign_id": "c-77"})
    )
    bridge = FixfizxBridge(jwt="secret-jwt")
    result = await bridge.create_campaign({"name": "Summer Sale", "budget": 5000})
    assert result["campaign_id"] == "c-77"
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_fixfizx_bridge_agent_status() -> None:
    """FixfizxBridge.agent_status GETs /api/health with Bearer header."""
    route = respx.get("http://localhost:8002/api/health").mock(
        return_value=Response(200, json={"status": "healthy"})
    )
    bridge = FixfizxBridge(jwt="secret-jwt")
    result = await bridge.agent_status()
    assert result["status"] == "healthy"
    request = route.calls[0].request
    assert request.headers["Authorization"] == "Bearer secret-jwt"
    await bridge.close()


# ---------------------------------------------------------------------------
# MoltBridge
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_molt_bridge_sandbox_health() -> None:
    """MoltBridge.sandbox_health GETs /sandbox-health with token header."""
    route = respx.get("http://localhost:8787/sandbox-health").mock(
        return_value=Response(200, json={"sandbox": "ok"})
    )
    bridge = MoltBridge(token="molt-token")
    result = await bridge.sandbox_health()
    assert result["sandbox"] == "ok"
    request = route.calls[0].request
    assert request.headers["X-Moltbot-Token"] == "molt-token"
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_molt_bridge_trigger_mission() -> None:
    """MoltBridge.trigger_mission POSTs channel + message + payload."""
    def match(request: httpx.Request) -> Response:
        body = json.loads(request.content)
        assert body["channel"] == "alerts"
        assert body["message"] == "disk full"
        assert body["payload"]["severity"] == "high"
        return Response(200, json={"triggered": True})

    route = respx.post("http://localhost:8787/api/gateway/trigger").mock(side_effect=match)
    bridge = MoltBridge(token="molt-token")
    result = await bridge.trigger_mission(
        "alerts", "disk full", payload={"severity": "high"}
    )
    assert result["triggered"] is True
    assert route.called
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_molt_bridge_admin_status() -> None:
    """MoltBridge.admin_status GETs /_admin/ with token header."""
    route = respx.get("http://localhost:8787/_admin/").mock(
        return_value=Response(200, json={"uptime": 1234})
    )
    bridge = MoltBridge(token="molt-token")
    result = await bridge.admin_status()
    assert result["uptime"] == 1234
    request = route.calls[0].request
    assert request.headers["X-Moltbot-Token"] == "molt-token"
    await bridge.close()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

@respx.mock
@pytest.mark.asyncio
async def test_bridge_raises_on_500() -> None:
    """All bridges propagate HTTP 500 as httpx.HTTPStatusError."""
    respx.get("http://localhost:8000/a2a/skills").mock(return_value=Response(500, text="boom"))
    bridge = FridayBridge()
    with pytest.raises(httpx.HTTPStatusError):
        await bridge.list_skills()
    await bridge.close()


@respx.mock
@pytest.mark.asyncio
async def test_bridge_jsonrpc_error_raises() -> None:
    """AgencyBridge raises on JSON-RPC error field."""
    respx.post("http://localhost:8100/tasks/send").mock(
        return_value=Response(
            200,
            json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad req"}},
        )
    )
    bridge = AgencyBridge()
    with pytest.raises(httpx.HTTPStatusError):
        await bridge.send_task("agency_default", "hello")
    await bridge.close()
