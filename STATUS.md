# STATUS

**Label:** `canonical`  
**Role:** Event transport for SAHIIXX production workflows.

Must implement: [BUS_REQUIREMENTS.md](./BUS_REQUIREMENTS.md)  
Envelope: [event-envelope.json](https://github.com/sahiixx/sahiixx-production-hardening/blob/main/contracts/event-envelope.json)

Revenue and agent events must not bypass this bus for FirstCall workflows.
