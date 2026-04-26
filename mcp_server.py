#!/usr/bin/env python3
"""MCP stdio server wrapping sahiixx-bus capabilities.

Usage:
    uv run python mcp_server.py

Or for Hermes native MCP client:
    command: uv
    args: [run, --directory, /home/sahiix/sahiixx-bus, mcp_server.py]
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MCP_TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "Search the web via curl + DuckDuckGo",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "read_file",
        "description": "Read a file from disk",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute file path",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "Write content to a file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_datetime",
        "description": "Return current date and time",
        "inputSchema": {
            "type": "object",
            "properties": {
                "timezone": {"type": "string", "default": "UTC"},
            },
        },
    },
    {
        "name": "bus_health",
        "description": "Check sahiixx-bus health and registered services",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "a2a_discover",
        "description": "Discover registered A2A services",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "a2a_route",
        "description": "Route a task to an A2A service",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Task to route",
                },
                "capabilities": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Required capabilities",
                },
            },
            "required": ["task"],
        },
    },
]


def handle_tool(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Execute a tool and return MCP-compatible result."""
    if name == "web_search":
        query = args.get("query", "")
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-s",
                    "-L",
                    f"https://lite.duckduckgo.com/lite/?q={query}",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            return {"result": result.stdout[:5000]}
        except Exception as e:
            return {"result": f"Search failed: {e}"}

    elif name == "read_file":
        path = args.get("path", "")
        try:
            content = Path(path).read_text(encoding="utf-8")
            return {"result": content}
        except Exception as e:
            return {"error": str(e)}

    elif name == "write_file":
        path = args.get("path", "")
        content = args.get("content", "")
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_text(content, encoding="utf-8")
            return {"result": f"Written {len(content)} bytes to {path}"}
        except Exception as e:
            return {"error": str(e)}

    elif name == "get_datetime":
        now = datetime.now(timezone.utc)
        return {"result": {"datetime": now.isoformat(), "timezone": "UTC"}}

    elif name == "bus_health":
        try:
            import httpx

            r = httpx.get("http://127.0.0.1:8090/health", timeout=5)
            return {"result": r.json()}
        except Exception as e:
            return {"result": f"Bus not reachable: {e}"}

    elif name == "a2a_discover":
        try:
            import httpx

            r = httpx.get("http://127.0.0.1:8090/a2a/discover", timeout=5)
            return {"result": r.json()}
        except Exception as e:
            return {"result": f"Bus not reachable: {e}"}

    elif name == "a2a_route":
        try:
            import httpx

            r = httpx.post(
                "http://127.0.0.1:8090/a2a/route",
                json={
                    "task": args.get("task", ""),
                    "capabilities": args.get("capabilities", []),
                },
                timeout=10,
            )
            return {"result": r.json()}
        except Exception as e:
            return {"result": f"Routing failed: {e}"}

    return {"error": f"Unknown tool: {name}"}


# ---------------------------------------------------------------------------
# MCP stdio protocol implementation (JSON-RPC over stdin/stdout)
# ---------------------------------------------------------------------------

def send(msg: dict) -> None:
    line = json.dumps(msg)
    sys.stdout.write(f"Content-Length: {len(line)}\r\n\r\n{line}")
    sys.stdout.flush()


def main() -> None:
    buffer = ""
    content_length = 0
    in_headers = True

    while True:
        chunk = sys.stdin.read(1)
        if not chunk:
            break
        buffer += chunk

        if in_headers:
            if "\r\n\r\n" in buffer:
                headers, body = buffer.split("\r\n\r\n", 1)
                buffer = body
                in_headers = False
                # Parse Content-Length
                for hline in headers.split("\r\n"):
                    if hline.lower().startswith("content-length:"):
                        content_length = int(hline.split(":", 1)[1].strip())
                        break
                if content_length <= 0:
                    in_headers = True
                    buffer = ""
                    continue

        if not in_headers and len(buffer) >= content_length:
            raw_body = buffer[:content_length].strip()
            buffer = buffer[content_length:]
            in_headers = True
            content_length = 0

            if not raw_body:
                continue

            try:
                msg = json.loads(raw_body)
            except json.JSONDecodeError:
                continue

            msg_id = msg.get("id")
            method = msg.get("method")
            params = msg.get("params", {})

            if method == "initialize":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {"tools": {}},
                            "serverInfo": {
                                "name": "sahiixx-bus-mcp",
                                "version": "0.1.0",
                            },
                        },
                    }
                )

            elif method == "tools/list":
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"tools": MCP_TOOLS},
                    }
                )

            elif method == "tools/call":
                name = params.get("name", "")
                arguments = params.get("arguments", {})
                result = handle_tool(name, arguments)
                if "error" in result:
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "error": {
                                "code": -32000,
                                "message": result["error"],
                            },
                        }
                    )
                else:
                    send(
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": result,
                        }
                    )

            elif method == "notifications/initialized":
                # Ack — no response needed
                pass

            else:
                send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "error": {
                            "code": -32601,
                            "message": f"Method not found: {method}",
                        },
                    }
                )


if __name__ == "__main__":
    main()
