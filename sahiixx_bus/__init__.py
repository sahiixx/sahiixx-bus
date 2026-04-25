"""sahiixx_bus — Unified orchestration layer for the SAHIIXX ecosystem."""

from .core import (
    BudgetController,
    EconomicEngine,
    RBACGuard,
    Permission,
    SafetyCouncil,
    SafetyVerdict,
    StateManager,
    SwarmBus,
    SwarmMemory,
)
from .bridge import (
    AgencyBridge,
    BaseBridge,
    FixfizxBridge,
    FridayBridge,
    GooseBridge,
    MoltBridge,
)
from .a2a_router import A2ARouter
from .mcp_gateway import MCPGateway
from .types import (
    AgentCard,
    AuditLog,
    MissionRecord,
    TaskMessage,
    ToolSchema,
)
from .utils import (
    async_retry,
    generate_id,
    now_iso,
    safe_json_loads,
    sanitize_input,
)

__version__ = "0.1.0"

__all__ = [
    # Core orchestration
    "SwarmBus",
    "SwarmMemory",
    "SafetyCouncil",
    "SafetyVerdict",
    "RBACGuard",
    "Permission",
    "EconomicEngine",
    "BudgetController",
    "StateManager",
    # Bridges
    "BaseBridge",
    "AgencyBridge",
    "FridayBridge",
    "GooseBridge",
    "FixfizxBridge",
    "MoltBridge",
    # Router & Gateway
    "A2ARouter",
    "MCPGateway",
    # Types
    "TaskMessage",
    "AgentCard",
    "ToolSchema",
    "MissionRecord",
    "AuditLog",
    # Utils
    "async_retry",
    "sanitize_input",
    "generate_id",
    "now_iso",
    "safe_json_loads",
]
