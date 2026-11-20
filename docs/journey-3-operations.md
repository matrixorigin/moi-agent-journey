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
| `results` | Metrics, lookup IDs and HTML report details for each workflow run/attempt/revision |

`results` includes `case_id`, `question`, `agent_name`, `duration_seconds`, `quality_score`, `evaluated_at` (UTC), `tool_call_count`, `model_round_count`, `failure_reason`, `trace_id` and `session_id`. It also records case/snapshot/evaluator versions, requested model, observed QA version, workflow commit and benchmark readiness. Model rounds exclude grader calls. Unobserved counts and scores are `NULL`. A case that never started an agent task has no task-latency measurement.

History writes are idempotent by run ID, attempt, case ID and revision. Reusing that key for a different execution is an error. Successful tasks with no reported failure have an empty failure reason. Multi-turn cases keep the task, session and trace IDs needed to locate the original activity.

Every Journey-3 case stores its metrics and report details in the selected QA or production workspace. Answers, retrieved content, event bodies and scorer requests are used while evaluating the case. New runs keep only the data needed to locate the original task and render the report. Historical records remain readable.

After the two Journey-3 jobs finish, the report job saves
`journey-3-report-<run-id>-attempt-<attempt>.html` to the CI account's private Catalog files
in the selected `journey-3` workspace. The workflow also uploads a single HTML file to
GitHub Actions with one-day retention. The Actions Summary links to the download. The
production GitHub copy contains metrics and coverage. Its Catalog copy includes the four detail panels.

The HTML shows history for all cases, documents, operations and individual cases.
The horizontal axis spaces GitHub run numbers evenly. Reruns have an attempt suffix such as
`#96.2`. Hover a point or tick to see its time in Asia/Shanghai. If GitHub metadata is
unavailable, the chart uses the run ID and the earliest recorded QA timestamp.
Completion and timeout rates use the eligible case outcomes of a run with all configured records.
Completion requires an observed final answer body. Charted completion timings, model rounds and
scores use completed answers. Tool failure rate uses failed calls divided by succeeded plus failed calls
for every outcome. Case revision, data snapshot, evaluator and model must match for case lines to connect.
The charts read the task time from `results.duration_seconds` for both new and historical runs.
New runs write completion time to this column and keep the outcome, model and tool observations in `report_json.history_observations`.
Older records use the saved report. A historical multi-turn case with an answer and a later failure
can lack a per-answer model-round count, so that point is left blank.

The HTML adds the current attempt's recorded task errors, task timeouts, failed tool
calls and deductions for scores below 50. It reads the saved reports without contacting
the model. Collection timeouts after a server-side completion do not enter the task timeout list.
The report shows the number of recorded cases and any missing case IDs. Case groups report their
own framework errors. The Journey-3 report job succeeds once every case record is present
and the HTML has been saved and uploaded.

## Validation status

All 272 copied tables passed final QA row-count verification (35,470,556 rows), including the independently sealed 192-table private-cloud subset. The MCP adapter and bindings were verified in QA. The 60 active operations cases now have independent knowledge-answer references. The r9 replacements were checked against their bound source tables and evaluated independently against the frozen data. The manual QA workflow can report model completion and answer quality separately.
