"""FastAPI server exposing A2A routing, MCP gateway, health, and WebSocket endpoints."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import uvicorn

from sahiixx_bus.a2a_router import A2ARouter
from sahiixx_bus.core import RBACGuard, SafetyCouncil, SwarmBus
from sahiixx_bus.mcp_gateway import MCPGateway

# ---------------------------------------------------------------------------
# Shared singletons
# ---------------------------------------------------------------------------
_bus = SwarmBus(namespace="sahiixx-bus")
_safety = SafetyCouncil(strict_mode=True)
_rbac = RBACGuard()
_router = A2ARouter(bus=_bus, safety=_safety)
_mcp = MCPGateway(bus=_bus, safety=_safety, rbac=_rbac)

# Seed RBAC so "admin" can use tools
from sahiixx_bus.core import Permission

_rbac.add_role("admin", {Permission.TOOL_USE, Permission.EXECUTE, Permission.READ, Permission.WRITE})
_rbac.assign_role("admin", "admin")
_rbac.assign_role("friday-os", "admin")
_rbac.add_role("user", {Permission.TOOL_USE, Permission.READ})
_rbac.assign_role("anonymous", "user")

app = FastAPI(title="SAHIIXX Bus", version="0.1.0")


def main() -> None:
    """Entry point for `sahiixx_bus` console script."""
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="info")


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------
class RouteTaskRequest(BaseModel):
    task: str
    capabilities: list[str] | None = None


class ExecuteToolRequest(BaseModel):
    tool: str
    params: dict[str, Any] = {}
    identity: str = "anonymous"


class RegisterServiceRequest(BaseModel):
    name: str
    url: str
    capabilities: list[str]


# ---------------------------------------------------------------------------
# A2A endpoints
# ---------------------------------------------------------------------------
@app.post("/a2a/route")
async def a2a_route(body: RouteTaskRequest) -> JSONResponse:
    """Route a task to the best matching A2A service."""
    result = await _router.route_task(body.task, body.capabilities)
    return JSONResponse(content=result)


@app.post("/a2a/register")
async def a2a_register(body: RegisterServiceRequest) -> JSONResponse:
    """Register a new A2A service."""
    await _router.register_service(body.name, body.url, body.capabilities)
    return JSONResponse(content={"registered": body.name, "capabilities": len(body.capabilities)})


@app.get("/a2a/discover")
async def a2a_discover() -> JSONResponse:
    """Discover all registered A2A services."""
    cards = await _router.discover_all()
    return JSONResponse(content={"services": cards, "count": len(cards)})


# ---------------------------------------------------------------------------
# MCP endpoints
# ---------------------------------------------------------------------------
@app.get("/mcp/tools")
async def mcp_list_tools() -> JSONResponse:
    """List all MCP tools."""
    tools = await _mcp.list_tools()
    return JSONResponse(content={"tools": tools, "count": len(tools)})


@app.post("/mcp/execute")
async def mcp_execute(body: ExecuteToolRequest) -> JSONResponse:
    """Execute an MCP tool."""
    result = await _mcp.execute_tool(body.tool, body.params, body.identity)
    return JSONResponse(content=result)


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> JSONResponse:
    """Health check returning all registered services status."""
    services = []
    for name, meta in _router._registry.items():
        services.append(
            {
                "name": name,
                "url": meta["url"],
                "capabilities": meta["capabilities"],
                "last_seen": meta["last_seen"],
            }
        )
    return JSONResponse(
        content={
            "status": "ok",
            "router_services": services,
            "mcp_tools": list(_mcp._tools.keys()),
            "bus_channels": _bus.get_channels(),
        }
    )


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time bus events."""
    await websocket.accept()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    async def _handler(msg: dict) -> None:
        await queue.put(msg)

    sub_id = await _bus.subscribe("bus.broadcast", _handler)
    try:
        while True:
            # Send any queued messages
            try:
                msg = queue.get_nowait()
                await websocket.send_json(msg)
            except asyncio.QueueEmpty:
                pass

            # Receive from client (echo / command dispatch)
            try:
                data = await asyncio.wait_for(websocket.receive_json(), timeout=0.5)
                if isinstance(data, dict) and data.get("action") == "publish":
                    channel = data.get("channel", "bus.broadcast")
                    payload = data.get("payload", {})
                    await _bus.publish(channel, payload)
                else:
                    await websocket.send_json({"echo": data})
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                break
    finally:
        await _bus.unsubscribe(sub_id)
