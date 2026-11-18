# Journey-3 semantic MCP adapter

QA disables the global publication of product REST routes as MCP tools. This adapter exposes five specific operations for the public-cloud replica in the Journey-3 workspace. It calls the existing QA semantic models API and preserves its MCP operation names and successful result shapes.

Read, list, and validate use the copied public-cloud model or the scratch model. Create and update require the dedicated scratch model. The adapter enforces the exact workspace and model IDs and rejects arbitrary URLs, methods, argument fields, and snapshot writes. Keep the scratch model unbound from benchmark agents.

Run `python3 -B bridge.py` behind the QA HTTPS ingress. The service listens on port 8080. Mount the five QA tool definitions as `tools.json` next to the script. Advertise the allowed model IDs in each input schema. The endpoints are authenticated POST `/mcp` and GET `/healthz`.

| Setting | Purpose |
|---|---|
| `J3_WORKSPACE_ID` | Journey-3 QA workspace |
| `J3_READ_MODEL_IDS` | Comma-separated read-only model IDs |
| `J3_SCRATCH_MODEL_ID` | Separate model for write tests |
| `J3_QA_BASE_URL` | Fixed QA HTTPS product API base, ending in `/newmoi` |
| `J3_QA_API_KEY` | QA key stored in the deployment Secret |
| `J3_BRIDGE_KEY` | Dedicated inbound key stored in the deployment Secret and MOI MCP connection |

Validate five operations end to end in QA. Read the snapshot, check that attempted snapshot writes return a tool error, and create, update, read back, then remove a synthetic rule in the scratch model. Verify the bound tool and skill IDs before registering a new case revision.
