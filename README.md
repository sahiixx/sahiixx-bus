# SAHIIXX Bus 🚌

> **Unified orchestration layer for the SAHIIXX ecosystem.**  
> Async pub/sub message bus · A2A bridge · MCP gateway · inter-agent routing backbone

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│                   sahiixx-agency (OPA)                        │
│        Auto-discovery · Smart routing · CLI/API/MCP           │
└───────────────────────────┬──────────────────────────────────┘
                            │ orchestrates
┌───────────────────────────▼──────────────────────────────────┐
│                      sahiixx-bus  ← YOU ARE HERE              │
│          SwarmBus · A2ARouter · MCPGateway · Bridge           │
│                    pub/sub messaging backbone                  │
└──┬──────────┬────────────┬────────────┬───────────┬──────────┘
   │          │            │            │           │
┌──▼──────┐ ┌─▼──────┐ ┌──▼───────┐ ┌──▼──────┐ ┌─▼──────────┐
│sovereign│ │friday  │ │clearwing │ │saas-    │ │sahiix-agi  │
│-swarm   │ │-os     │ │(pentest) │ │agent-   │ │(AGI layer) │
│-v2      │ │(voice) │ │          │ │platform │ │            │
└──┬──────┘ └──┬─────┘ └──┬───────┘ └─────────┘ └────────────┘
   │           │           │
   │      ┌────▼──────┐    │
   │      │titans-    │    │
   │      │memory     │◄───┘  (shared memory layer)
   │      └───────────┘
   │
   └──► sahiixx-graph-sight (Neo4j trust graph + code context)
              │
              └──► sahiixx-geoflow-agent (Dubai RE content)
```

---

## Ecosystem Modules

| Module | Role | Bus Channel | Protocol |
|--------|------|-------------|----------|
| [sovereign-swarm-v2](https://github.com/sahiixx/sovereign-swarm-v2) | Multi-agent OS runtime | `swarm.*` | A2A |
| [friday-os](https://github.com/sahiixx/friday-os) | Voice AI + personal assistant | `friday.*` | MCP |
| [sahiixx-clearwing](https://github.com/sahiixx/sahiixx-clearwing) | Pentesting swarm | `security.*` | A2A |
| [saas-agent-platform](https://github.com/sahiixx/saas-agent-platform) | Multi-tenant SaaS | `saas.*` | REST+MCP |
| [sahiix-agi](https://github.com/sahiixx/sahiix-agi) | AGI coordination | `agi.*` | A2A |
| [sahiixx-titans-memory](https://github.com/sahiixx/sahiixx-titans-memory) | Surprise-weighted memory | `memory.*` | Python lib |
| [sahiixx-graph-sight](https://github.com/sahiixx/sahiixx-graph-sight) | Neo4j trust graph + context | `graph.*` | Python lib |
| [sahiixx-geoflow-agent](https://github.com/sahiixx/sahiixx-geoflow-agent) | Dubai RE GEO optimization | `geo.*` | A2A |
| [sahiixx-agency](https://github.com/sahiixx/sahiixx-agency) | OPA orchestrator | `agency.*` | MCP+REST |

---

## Quick Start

```bash
pip install -e .
# Start the bus server
sahiixx_bus
```

Default port: `8000`  
MCP gateway: `8001`

## Bus API

```python
from sahiixx_bus.core import SwarmBus

bus = SwarmBus(namespace="sahiixx")

# Subscribe an agent
await bus.subscribe("friday.voice", handle_voice_event)

# Publish from any module
await bus.publish("friday.voice", {"text": "Hello", "agent": "friday-os"})

# Request/response pattern
result = await bus.request("memory.recall", {"query": "last meeting"})
```

## Module Registration

Any module can register itself on startup:

```python
from sahiixx_bus.bridge import AgentBridge

bridge = AgentBridge(bus, agent_id="my-module", channels=["my.*"])
await bridge.connect()
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `SAHIIXX_BUS_HOST` | `0.0.0.0` | Bind host |
| `SAHIIXX_BUS_PORT` | `8000` | HTTP/WS port |
| `SAHIIXX_BUS_MCP_PORT` | `8001` | MCP gateway port |
| `SAHIIXX_BUS_NAMESPACE` | `sahiixx` | Bus namespace |

---

## Related

- [sahiixx-agency](https://github.com/sahiixx/sahiixx-agency) — OPA orchestrator that sits above the bus
- [sovereign-swarm-v2](https://github.com/sahiixx/sovereign-swarm-v2) — primary swarm consumer
- [friday-os](https://github.com/sahiixx/friday-os) — voice interface connected via MCP
