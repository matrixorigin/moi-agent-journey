# Journey-3 operations replay and history

Journey-3 has 57 document cases and 60 operations cases (J3-58–J3-117). Two standard Ubuntu runners execute the cases concurrently. `cases (documents)` runs J3-01–J3-57. `cases (operations)` runs both operations agents’ questions, J3-58–J3-117. Each runner has up to 10 isolated case processes. Each process has its own conversation, files, event recorder, deadlines and report. One failure does not skip queued cases. The QA workflow remains manual; schedules are paused.

## Selection and replay

The original selection used the 30 slowest distinct questions for each source agent in the seven days before collection. Successful, failed and canceled requests shared the ranking. Deduplication normalizes whitespace and bot mentions, retaining the slowest occurrence. Latency runs from the original user-message timestamp to the trace terminal timestamp; mutable task `updated_at` is not a latency source.

Each replay creates a fresh conversation. Earlier user/assistant text is supplied as quoted background to resolve follow-ups, not replayed as additional agent tasks and not trusted as a reference answer. Attachment transport credentials are omitted. Relative dates use the original question's UTC/Beijing clock, not the workflow date. Queries are read-only. This is a fixed-snapshot question benchmark, not a reproduction of the original model, cache or complete IM session.

The copied agents have no IM/channel bindings. Runtime preflight checks their active status, exact knowledge-base bindings, required skills/tools and the live set of enabled, successfully registered table sources. Importing table declarations alone leaves them legacy-unbound in QA; the supported backfill API registered the copied sources, and their effective active sets now match production (77/64/64/64). The three originally unbound declarations remain disabled. A missing production dependency is `blocked`, not a quality failure for the partially copied agent. The public-cloud replica binds five MCP tools and its custom skill to a Journey-3-only adapter for the QA semantic API. Read, list and validate target the copied snapshot. Create and update target an unbound scratch model. Case revision r8 records the QA bindings while preserving the reference questions and answers. See [the adapter](../tools/journey3-semantic-mcp/README.md).

Seven original requests depended on monitoring, GitHub, image delivery, old-task introspection or customer business tables outside the copied knowledge bases. Revision r9 replaces them in the same case slots while preserving their historical records under the old revisions. Four replacements use archived production questions from the 15-day candidate pool with explicit metric definitions. Three use answerable questions based on the same operations themes and fixed snapshots. The public-cloud agent retains 30 cases and the private-cloud agent retains 30 cases. Each replacement has its own independently computed reference and case-specific criteria. The original 91-row uploaded list for a separate identity-mapping question remains checksum-verified and stored in QA with its independently calculated account mappings.

## Golden and quality

Historical answers are never goldens. Operations replays that read benchmark control tables or registered reference/evidence archives are explicitly unscored; reading a file listing alone is not treated as reading an archive. Reviewed SQL calculations run independently in DuckDB against the immutable exported TSV snapshot, with source checksums, row counts, explicit time units and a frozen clock. This keeps QA SQL-engine failures from corrupting the reference calculation. Historical SQL is only a review input: timestamp units, time zones, attribution, deduplication and requested populations are checked and corrected before independent execution.

Complete SQL, calculated values, criteria and limitations stay in QA. Only the tested prompt/context reaches the agent; the golden reaches a separate tool-free model grader. Quality is `100 × satisfied reference criteria / evaluated criteria`. Equivalent wording and reasonable rounding are accepted. This is criterion coverage, with a coarse score for small rubrics, not an assertion of exact factual accuracy to one percentage point. Missing/invalid grading yields `NULL`, never zero or a pass.

Correctness, observed grounding, citation integrity and citation support remain separate. Native SQL selection receipts plus the fixed table catalog attest source identity; they do not prove answer correctness. Without the source body, citation support and grounding can remain unavailable. SQL found in a citation is never executed by the validator. Document cases retain their existing independently opened source/chunk checks.

The dataset manifest records expected source checksums, schemas, semantics and copy receipts. The migration seals it only after final QA row-count verification. Runtime checks manifest identity and completion state; this is not a fresh checksum of every QA cell. Snapshot tables must stay fixed. Data/schema/semantic changes require a new snapshot and golden revision; the historical series must not be spliced together.

Journey-3 gives every case a 30-minute task budget through its suite-wide timeout override. The planner and the runner apply the same budget to both public case definitions and verified private specs. Evidence validation and grading have separate budgets. The planner accounts for private-history persistence and rejects groups exceeding the GitHub-hosted job limit. Green means the framework collected and reported the outcome. Agent failure, missing benchmark dependencies, no delivery or poor quality remain explicit report outcomes.

## Private history and public downloads

The runtime schema is in [history-schema.sql](../datasets/journey-3/history-schema.sql). QA `journey3_benchmark` contains:

| Table | Contents |
|---|---|
| `case_specs` | Versioned private questions, context, goldens and checksums |
| `case_goldens` | QA catalog reference answers, rubric and evidence, grouped by catalog revision |
| `dataset_snapshots` | Snapshot manifest, hash and readiness state |
| `private_evidence_files` | Reference/evidence archive identities used to detect answer contamination |
| `results` | One case result per workflow run/attempt/revision, plus the full report and private evidence-file reference |

`results` includes `case_id`, `question`, `agent_name`, `duration_seconds`, `quality_score`, `evaluated_at` (UTC), `tool_call_count`, `model_round_count`, `failure_reason`, `trace_id` and `session_id`. It also records case/snapshot/evaluator versions, requested model, observed QA version, workflow commit and benchmark readiness. Model rounds exclude grader calls. Unobserved counts and scores are `NULL`. A case that never started an agent task has no task-latency measurement.

History writes are idempotent by run ID, attempt, case ID and revision. Reusing that key with a different report is an error. Successful tasks with no reported failure have an empty failure reason. Multi-turn cases store all task/session identifiers. Evidence archives contain only that case's files, refuse symlinks and are uploaded to the QA workspace; a missing persistence receipt causes an explicit framework error.

Every Journey-3 case uses the same publication boundary, including the old synthetic cases, because they now share a workspace containing business data. Public downloads include only fixed identifiers, states and numeric metrics. Questions, business agent names, answers, citations, SQL results, tool names/arguments/errors, trace IDs, session IDs and raw files stay in QA. Child-process stdout/stderr also stays private; runner logs report opaque case IDs and framework status.

`history/history.html` opens in **All cases** and overlays individual case lines on a shared UTC axis for each metric. Choose **Job-1-文档知识库问答** or **Job-2-运营知识库问答** to show only that job. Select a case from the menu or legend to inspect a fixed case revision, data snapshot, evaluator and model. Distinct versions remain separate lines in the overview. Unscored results leave gaps, and dependency-blocked records stay visible in the table. `history.csv` and `history.json` contain the same metric allowlist. After both case jobs finish, the report job exports history once and regenerates the chart from validated metrics.

## Validation status

All 272 copied tables passed final QA row-count verification (35,470,556 rows), including the independently sealed 192-table private-cloud subset. The MCP adapter and bindings were verified in QA. The 60 active operations cases now have independent knowledge-answer references. The r9 replacements were checked against their bound source tables and evaluated independently against the frozen data. The manual QA workflow can report model completion and answer quality separately.
