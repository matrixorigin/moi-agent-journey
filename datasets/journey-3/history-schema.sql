-- Run only in the QA journey-3 workspace. UTC timestamps; NULL means unscored.
CREATE DATABASE IF NOT EXISTS journey3_benchmark;
CREATE TABLE IF NOT EXISTS journey3_benchmark.dataset_snapshots (
  snapshot_id VARCHAR(128) PRIMARY KEY, state VARCHAR(32), manifest_sha256 VARCHAR(64),
  manifest_json LONGTEXT, reference_archive_file_id VARCHAR(128), reference_archive_sha256 VARCHAR(64)
);
CREATE TABLE IF NOT EXISTS journey3_benchmark.case_specs (
  case_id VARCHAR(32), revision VARCHAR(128), sha256 VARCHAR(64), spec_json LONGTEXT,
  PRIMARY KEY(case_id, revision)
);
CREATE TABLE IF NOT EXISTS journey3_benchmark.private_evidence_files (
  file_id VARCHAR(128) PRIMARY KEY, sha256 VARCHAR(64)
);
CREATE TABLE IF NOT EXISTS journey3_benchmark.results (
  result_key VARCHAR(64) PRIMARY KEY,
  case_id VARCHAR(32), question LONGTEXT, agent_name VARCHAR(256),
  duration_seconds DOUBLE, quality_score DOUBLE, evaluated_at DATETIME(6),
  tool_call_count BIGINT, model_round_count BIGINT, failure_reason LONGTEXT,
  trace_id LONGTEXT, session_id LONGTEXT,
  run_id VARCHAR(128), run_attempt INT, case_revision VARCHAR(128), snapshot_id VARCHAR(128),
  task_state VARCHAR(32), evaluation_state VARCHAR(256), harness_state VARCHAR(32),
  benchmark_state VARCHAR(32),
  model VARCHAR(128), qa_version LONGTEXT, workflow_commit VARCHAR(64), evaluator_version VARCHAR(128),
  evidence_file_id VARCHAR(128), evidence_sha256 VARCHAR(64), report_json LONGTEXT
);

-- Select one stable case/snapshot/evaluator series before drawing a curve.
-- Agent failures and evaluator failures remain visible; never drop them to
-- improve the average. Model invocations used only for scoring are excluded.
SELECT case_id, evaluated_at, duration_seconds, quality_score, task_state,
       evaluation_state, tool_call_count, model_round_count, failure_reason
FROM journey3_benchmark.results
ORDER BY case_id, evaluated_at;
