"""MCP Gateway — MCP stdio/SSE gateway with unified tool schema."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import AsyncIterator, Callable
from typing import Any

from sahiixx_bus.core import RBACGuard, SafetyCouncil, SwarmBus, Permission


class MCPGateway:
    """MCP gateway that exposes tools via a unified schema and proxies
    stdio / SSE transports.

    Tools are registered with a JSON schema and a handler callable.
    Execution is guarded by RBAC and the SafetyCouncil.  The gateway
    also pre-registers five example tools for common operations.
    """

    def __init__(self, bus: SwarmBus, safety: SafetyCouncil, rbac: RBACGuard) -> None:
        """Initialise the gateway.

        Args:
            bus: Shared ``SwarmBus`` instance.
            safety: ``SafetyCouncil`` for scanning tool parameters.
            rbac: ``RBACGuard`` for permission enforcement.
        """
        self.bus = bus
        self.safety = safety
        self.rbac = rbac
        self._tools: dict[str, dict[str, Any]] = {}
        self._processes: dict[str, subprocess.Popen] = {}

        # Pre-register example tools
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Register the five example tools required by the spec."""
        self.register_tool_sync(
            "web_search",
            {
                "description": "Perform a web search for the given query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query"}
                    },
                    "required": ["query"],
                },
            },
            lambda params: {"results": [f"Mock result for {params.get('query', '')}"]},
        )
        self.register_tool_sync(
            "read_file",
            {
                "description": "Read a file from disk.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Absolute file path"}
                    },
                    "required": ["path"],
                },
            },
            self._handler_read_file,
        )
        self.register_tool_sync(
            "write_output",
            {
                "description": "Write content to a file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            },
            self._handler_write_output,
        )
        self.register_tool_sync(
            "memory_recall",
            {
                "description": "Recall relevant memories by query.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "top_k": {"type": "integer", "default": 5},
                    },
                    "required": ["query"],
                },
            },
            self._handler_memory_recall,
        )
        self.register_tool_sync(
            "get_datetime",
            {
                "description": "Return the current date and time.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "timezone": {"type": "string", "default": "UTC"}
                    },
                },
            },
            self._handler_get_datetime,
        )

    # ------------------------------------------------------------------
    # Default handlers
    # ------------------------------------------------------------------
    def _handler_read_file(self, params: dict[str, Any]) -> dict[str, Any]:
        """Read a file from disk."""
        path = params.get("path", "")
        try:
            with open(path, "r", encoding="utf-8") as f:
                return {"content": f.read()}
        except Exception as exc:
            return {"error": str(exc)}

    def _handler_write_output(self, params: dict[str, Any]) -> dict[str, Any]:
        """Write content to a file."""
        path = params.get("path", "")
        content = params.get("content", "")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return {"written": True, "path": path, "bytes": len(content.encode("utf-8"))}
        except Exception as exc:
            return {"error": str(exc)}

    def _handler_memory_recall(self, params: dict[str, Any]) -> dict[str, Any]:
        """Recall memories via the bus."""
        query = params.get("query", "")
        top_k = params.get("top_k", 5)
        # In a real implementation this would query SwarmMemory
        return {"memories": [{"key": query, "score": 1.0}] * top_k}

    def _handler_get_datetime(self, params: dict[str, Any]) -> dict[str, Any]:
        """Return current datetime."""
        import datetime
        tz = params.get("timezone", "UTC")
        now = datetime.datetime.now(datetime.timezone.utc)
        return {"datetime": now.isoformat(), "timezone": tz}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    async def register_tool(self, name: str, schema: dict, handler: Callable[..., Any]) -> None:
        """Register a tool asynchronously (delegates to sync helper)."""
        self.register_tool_sync(name, schema, handler)

    def register_tool_sync(self, name: str, schema: dict, handler: Callable[..., Any]) -> None:
        """Register a tool with name, JSON schema and handler.

        The schema must contain ``description`` and ``parameters`` keys.

        Args:
            name: Unique tool name.
            schema: JSON-schema dict describing the tool.
            handler: Callable that receives a params dict and returns a result.

        Raises:
            ValueError: If the schema is missing required keys.
        """
        if "description" not in schema:
            raise ValueError("Schema must contain 'description'")
        if "parameters" not in schema:
            raise ValueError("Schema must contain 'parameters'")
        self._tools[name] = {
            "name": name,
            "schema": schema,
            "handler": handler,
        }

    async def list_tools(self) -> list[dict]:
        """Return all registered tools as MCP-compatible schema list.

        Returns:
            List of ``{"name": ..., "description": ..., "inputSchema": ...}`` dicts.
        """
        return [
            {
                "name": meta["name"],
                "description": meta["schema"]["description"],
                "inputSchema": meta["schema"]["parameters"],
            }
            for meta in self._tools.values()
        ]

    async def execute_tool(self, name: str, params: dict, identity: str = "anonymous") -> dict:
        """Execute a registered tool.

        Steps:
        1. RBAC check — identity must have ``Permission.TOOL_USE``.
        2. Safety scan on the JSON-serialised parameters.
        3. Invoke the handler with *params*.
        4. Return ``{"success": True/False, "result": result, "tool": name}``.

        Args:
            name: Registered tool name.
            params: Tool arguments.
            identity: Calling identity for RBAC.

        Returns:
            Execution result dict.
        """
        # 1. RBAC
        if not self.rbac.check(identity, Permission.TOOL_USE):
            return {
                "success": False,
                "error": f"Identity '{identity}' lacks tool_use permission",
                "tool": name,
            }

        if name not in self._tools:
            return {"success": False, "error": f"Tool '{name}' not found", "tool": name}

        # 2. Safety scan
        verdict = await self.safety.scan(json.dumps(params))
        if not verdict.safe:
            return {
                "success": False,
                "error": "Safety scan failed",
                "violations": verdict.violations,
                "tool": name,
            }

        # 3. Execute
        try:
            handler = self._tools[name]["handler"]
            if asyncio.iscoroutinefunction(handler):
                result = await handler(params)
            else:
                result = handler(params)
            await self.bus.publish("mcp.executed", {"tool": name, "identity": identity})
            return {"success": True, "result": result, "tool": name}
        except Exception as exc:
            return {"success": False, "error": str(exc), "tool": name}

    async def proxy_stdio(self, command: list[str], env: dict | None = None) -> None:
        """Launch a subprocess and stream stdout/stderr to the bus channel
        ``mcp.stdio.{tool_name}``.

        Args:
            command: Shell command as list of strings.
            env: Optional environment variables dict.
        """
        tool_name = command[0] if command else "unknown"
        channel = f"mcp.stdio.{tool_name}"
        import os
        proc_env = {**os.environ, **(env or {})} if env else None
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=proc_env,
        )
        self._processes[tool_name] = process  # type: ignore[arg-type]

        async def _drain(stream: asyncio.StreamReader | None, tag: str) -> None:
            if stream is None:
                return
            while True:
                line = await stream.readline()
                if not line:
                    break
                msg = {"tag": tag, "line": line.decode("utf-8", errors="replace").rstrip()}
                await self.bus.publish(channel, msg)

        await asyncio.gather(
            _drain(process.stdout, "stdout"),
            _drain(process.stderr, "stderr"),
        )

    async def proxy_sse(self, url: str) -> AsyncIterator[str]:
        """Connect to an SSE endpoint, yield events, and publish them to the bus.

        Args:
            url: SSE endpoint URL.

        Yields:
            Raw SSE event strings (lines beginning with ``data:`` stripped).
        """
        import httpx

        channel = f"mcp.sse.{url.replace('://', '_').replace('/', '_')}"
        async with httpx.AsyncClient() as client:
            async with client.stream("GET", url, timeout=60.0) as response:
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        event = line[6:]
                        await self.bus.publish(channel, {"event": event})
                        yield event

    def get_unified_schema(self) -> dict:
        """Return an OpenAPI-like schema of all registered tools.

        Returns:
            Dict with ``openapi`` version, ``info`` and ``paths`` keys.
        """
        paths: dict[str, dict] = {}
        for name, meta in self._tools.items():
            schema = meta["schema"]
            paths[f"/tools/{name}"] = {
                "post": {
                    "summary": schema.get("description", ""),
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": schema.get("parameters", {}),
                            }
                        },
                    },
                    "responses": {
                        "200": {
                            "description": "Tool execution result",
                        }
                    },
                }
            }
        return {
            "openapi": "3.1.0",
            "info": {
                "title": "SAHIIXX MCP Unified Schema",
                "version": "0.1.0",
            },
            "paths": paths,
        }
