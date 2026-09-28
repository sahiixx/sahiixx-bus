# sahiixx-bus Production Requirements

Canonical checklist from **[sahiixx-production-hardening](https://github.com/sahiixx/sahiixx-production-hardening/blob/main/contracts/bus-requirements.md)**.

## Must-have

| Capability | Requirement |
|------------|-------------|
| Durable storage | Survive restarts; Postgres or equivalent |
| Event versioning | `event_version` on every event |
| Idempotency | Reject/dedupe by `idempotency_key` |
| Consumer offsets | At-least-once + explicit ack |
| Retry + DLQ | Backoff then dead-letter |
| Replay | By time range or correlation_id |
| Ordering | Per correlation_id / partition |
| Correlation & causation | Required on envelope |
| Tenant isolation | Scope all ops by tenant_id |
| Inter-service auth | mTLS or signed JWT |
| Schema registry | Validate against event-envelope + payload schemas |
| Backpressure | Throttle publishers on lag |
| Metrics | published / delivered / failed / DLQ / lag |

## Canonical envelope

All producers MUST use:
https://github.com/sahiixx/sahiixx-production-hardening/blob/main/contracts/event-envelope.json

## Anti-patterns

- Fire-and-forget without persistence
- In-memory queues for revenue events
- Letting n8n/Activepieces own the primary FirstCall event stream
