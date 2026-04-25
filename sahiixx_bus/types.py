"""Pydantic models for sahiixx-bus data structures."""

from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskMessage(BaseModel):
    """Represents a task sent between agents via the message bus."""

    task_id: str = Field(..., description="Unique identifier for the task")
    sender: str = Field(..., description="Identity of the sending agent")
    recipient: str = Field(default="", description="Target agent or channel")
    task_type: str = Field(default="default", description="Classification of the task")
    payload: dict = Field(default_factory=dict, description="Task data")
    timestamp: str = Field(default_factory=_utc_iso)
    priority: int = Field(default=5, ge=1, le=10, description="Priority 1-10")
    timeout: Optional[float] = Field(default=30.0, description="Seconds before timeout")


class AgentCard(BaseModel):
    """Describes an agent's capabilities and metadata for A2A discovery."""

    agent_id: str = Field(..., description="Unique agent identifier")
    name: str = Field(..., description="Human-readable agent name")
    version: str = Field(default="0.1.0", description="Agent version string")
    capabilities: list[str] = Field(default_factory=list, description="List of skills")
    endpoint: str = Field(default="", description="URL for A2A communication")
    status: str = Field(default="idle", description="Current agent status")
    metadata: dict = Field(default_factory=dict, description="Extra agent info")
    registered_at: str = Field(default_factory=_utc_iso)


class ToolSchema(BaseModel):
    """Unified schema for a tool exposed through the MCP gateway."""

    name: str = Field(..., description="Tool identifier")
    description: str = Field(default="", description="What the tool does")
    parameters: dict = Field(default_factory=dict, description="JSON Schema for params")
    required_permissions: list[str] = Field(default_factory=list, description="RBAC perms")
    version: str = Field(default="0.1.0", description="Schema version")
    handler_ref: str = Field(default="", description="Internal handler reference")


class MissionRecord(BaseModel):
    """Record of a completed or failed mission for SwarmMemory."""

    mission_id: str = Field(..., description="Unique mission identifier")
    mission_name: str = Field(..., description="Human-readable mission name")
    status: str = Field(..., description="success | failure | aborted | pending")
    verdict: str = Field(default="", description="Outcome summary / reasoning")
    agents_involved: list[str] = Field(default_factory=list, description="Agent IDs")
    cost: float = Field(default=0.0, description="Economic cost of the mission")
    duration_seconds: float = Field(default=0.0, description="Wall-clock duration")
    metadata: dict = Field(default_factory=dict, description="Arbitrary extra data")
    recorded_at: str = Field(default_factory=_utc_iso)


class AuditLog(BaseModel):
    """Immutable audit entry for safety and compliance tracking."""

    log_id: str = Field(..., description="Unique log entry identifier")
    event_type: str = Field(..., description="Category of the event")
    identity: str = Field(default="anonymous", description="Actor identity")
    action: str = Field(..., description="What was attempted")
    resource: str = Field(default="", description="Target resource")
    permitted: bool = Field(..., description="Whether the action was allowed")
    details: dict = Field(default_factory=dict, description="Contextual details")
    threat_level: str = Field(default="none", description="none | low | medium | high | critical")
    timestamp: str = Field(default_factory=_utc_iso)
