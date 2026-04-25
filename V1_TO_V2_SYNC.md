# Sovereign Swarm v1.4 → v2 Sync Guide

This document tracks which features from the hardened v1.4 package
(`/mnt/c/Users/Sahil Khan/Downloads/sovereign_swarm/`) need to be
merged into sovereign-swarm-v2 (`/home/sahiix/sovereign-swarm-v2/`).

## Features to Merge

### 1. RBACGuard + Permission Enum
- **v1.4 file:** `sovereign_swarm/protocols.py` lines 184-210
- **v2 target:** `sovereign_swarm/protocols/` (new file `rbac.py`)
- **Status:** v2 completely missing RBAC

### 2. ClusterNode + ClusterManager
- **v1.4 file:** `sovereign_swarm/protocols.py` lines 212-240
- **v2 target:** `sovereign_swarm/protocols/` (new file `cluster.py`)
- **Status:** v2 completely missing clustering

### 3. StateManager (Persistence)
- **v1.4 file:** `sovereign_swarm/state.py`
- **v2 target:** `sovereign_swarm/infra/state.py`
- **Status:** v2 completely missing state persistence

### 4. SafetyCouncil Hardening
- **v1.4 features:** arm_emergency/disarm_emergency with auth, input normalization, adaptive rules enforcement, 30-char truncation, bounded deques
- **v2 target:** `sovereign_swarm/safety/`
- **Status:** v2 has basic SafetyCouncil without auth gating or input normalization

### 5. BudgetController Async + Lock
- **v1.4 feature:** asyncio.Lock, async charge/remaining/kill_switch_armed/report
- **v2 target:** `sovereign_swarm/safety/cost.py`
- **Status:** v2 BudgetController is sync without lock

### 6. HITLCheckpoint asyncio.Event + MIN_TIMEOUT
- **v1.4 feature:** Event-based wait instead of polling, MIN_TIMEOUT=1.0
- **v2 target:** `sovereign_swarm/agents/hitl.py`
- **Status:** Unknown — need to check v2's HITL implementation

### 7. SQLite Atomicity (execute+commit in single to_thread)
- **v1.4 feature:** Combined execute+commit into single to_thread calls
- **v2 target:** `sovereign_swarm/infra/bus.py`, `sovereign_swarm/infra/memory.py`
- **Status:** v2 may not have this fix

### 8. SQL LIKE Injection Fix (ESCAPE clause)
- **v1.4 feature:** _escape_like() method, ESCAPE '\\' clause
- **v2 target:** `sovereign_swarm/infra/memory.py`
- **Status:** Unknown

### 9. A2ACardServer Localhost Default
- **v1.4 feature:** host defaults to "127.0.0.1" instead of "0.0.0.0"
- **v2 target:** `sovereign_swarm/protocols/a2a.py`
- **Status:** Unknown

### 10. MCPServer MAX_MESSAGE_SIZE
- **v1.4 feature:** 1MB message size limit on stdio_loop
- **v2 target:** `sovereign_swarm/protocols/mcp.py`
- **Status:** Unknown

### 11. Shared aiohttp Session
- **v1.4 feature:** Session parameter passed to LLMClient, SwarmBridge, OpenClawGateway
- **v2 target:** Multiple protocol files
- **Status:** Unknown

### 12. Bus Subscriber Lock
- **v1.4 feature:** Lock held during subscribe()
- **v2 target:** `sovereign_swarm/infra/bus.py`
- **Status:** Unknown

### 13. Session Leak Fix (if self._session / else aiohttp.ClientSession)
- **v1.4 feature:** Proper session lifecycle management
- **v2 target:** LLMClient, protocol clients
- **Status:** Unknown

### 14. Async Callback Exception Logging
- **v1.4 feature:** add_done_callback on create_task, logger.exception on sync callback errors
- **v2 target:** `sovereign_swarm/infra/bus.py`
- **Status:** Unknown

### 15. 76 Tests (v1.4) vs v2 Tests
- **v1.4:** 76 tests including expanded unit tests + security tests
- **v2 target:** `sovereign_swarm/tests.py`
- **Status:** Need to verify v2 test count and add missing tests

## How to Sync

```bash
# 1. Copy new modules
cp /mnt/c/Users/Sahil\ Khan/Downloads/sovereign_swarm/state.py \
   /home/sahiix/sovereign-swarm-v2/sovereign_swarm/infra/state.py

# 2. Create RBAC and Cluster modules
# (Need to adapt v1.4 code to v2's import structure)

# 3. Update v2's __init__.py to export new classes

# 4. Run v2 tests to verify nothing broke
cd /home/sahiix/sovereign-swarm-v2 && python -m pytest

# 5. Add missing test cases from v1.4
```

## Priority Order

1. StateManager (data loss on restart)
2. RBACGuard (security)
3. SafetyCouncil hardening (security)
4. BudgetController async (race condition)
5. SQLite atomicity (data corruption)
6. Cluster management (operational)
7. Remaining fixes (quality)
8. Test sync (verification)