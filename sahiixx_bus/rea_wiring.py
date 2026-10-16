"""
Wire REA handler into the real SwarmBus.

The REABusHandler class already lives in sahiixx_bus.rea_handler.
This module provides the subscription glue that was missing.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sahiixx_bus.core import SwarmBus

logger = logging.getLogger(__name__)


async def _rea_handler_adapter(message: dict) -> None:
    """Adapt SwarmBus message shape to REABusHandler.handle()."""
    from sahiixx_bus.rea_handler import handler

    event = message if "payload" in message or "type" in message else {"payload": message}
    try:
        result = await handler.handle(event)
        logger.info(
            "REA handler processed analysis_id=%s competitor=%s risk=%s",
            result.get("analysis_id"),
            result.get("fanout", {}).get("swarm", {}).get("competitor"),
            result.get("fanout", {}).get("swarm", {}).get("risk", {}).get("risk_level"),
        )
    except Exception:
        logger.exception("REA handler failed")


async def wire_rea_handler(bus: "SwarmBus") -> str:
    """Subscribe REABusHandler to channel 'rea.analysis.completed'.

    Returns the subscription ID so it can be unsubscribed later.
    """
    sub_id = await bus.subscribe("rea.analysis.completed", _rea_handler_adapter)
    logger.info("Wired REA handler to 'rea.analysis.completed' (sub_id=%s)", sub_id)
    return sub_id


__all__ = ["wire_rea_handler"]
