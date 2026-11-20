# Journey-3 semantic MCP adapter

This adapter exposes five operations for the public-cloud replica in a Journey-3 workspace. It uses that environment's semantic models API and preserves the MCP operation names and successful result shapes.

Read, list, and validate use the copied public-cloud model or the scratch model. Create and update require the dedicated scratch model. The adapter enforces the exact workspace and model IDs and rejects arbitrary URLs, methods, argument fields, and snapshot writes. Keep the scratch model unbound from benchmark agents.

Run `python3 -B bridge.py` behind the environment's HTTPS ingress. The service listens on port 8080. Mount the five tool definitions as `tools.json` next to the script and advertise the allowed model IDs in each input schema. The authenticated POST path is `/mcp` in QA or `/journey-3-semantic-mcp` in production. The pod health check uses GET `/healthz`.

| Setting | Purpose |
|---|---|
| `J3_ENVIRONMENT` | `qa` (default) or `production` |
| `J3_WORKSPACE_ID` | Journey-3 workspace ID in the selected environment |
| `J3_READ_MODEL_IDS` | Comma-separated read-only model IDs |
| `J3_SCRATCH_MODEL_ID` | Separate model for write tests |
| `J3_QA_BASE_URL` | Fixed QA HTTPS product API base, ending in `/newmoi` |
| `J3_QA_API_KEY` | QA key stored in the deployment Secret |
| `J3_PROD_BASE_URL` | Production HTTPS product API base |
| `J3_PROD_API_KEY` | Production key stored in the deployment Secret |
| `J3_BRIDGE_KEY` | Dedicated inbound key stored in the deployment Secret and MOI MCP connection |

Validate all five operations in the target environment. Read the snapshot, check that attempted snapshot writes return a tool error, and create, update, read back, then remove a synthetic rule in the scratch model. Verify the bound tool and skill IDs before using the adapter in a benchmark run.
