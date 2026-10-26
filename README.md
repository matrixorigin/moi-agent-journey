# moi-agent-journey
End-to-end regression journeys for MOI agents: task completion, efficiency, and stability.

## Momo hello

This first journey calls a deployed MOI product API. It discovers the active
`journey-1` workspace and Momo (`source_ref=agent-zero-builder`), creates a fresh
conversation, and sends a random greeting challenge using `deepseek-v4.1-flash`.
It reads the persisted A2A task and requires both `completed` and the challenge
in the agent's text reply. HTTP 200 alone is not a pass.

The run reports task time, total time, request count, failure stage, and cleanup
outcome. It archives its conversation after completion. A known unfinished task
is canceled before archiving; an unknown send outcome is reported as incomplete
cleanup, never retried. Locate that conversation by the `journey-momo-hello-<run_id>`
title for manual inspection. Cleanup failure also fails the journey.

This is a connectivity and basic execution smoke test, not a long-task benchmark.
There is a 180-second journey budget and a separate 20-second cleanup budget;
the CI job has a five-minute hard limit. Requests are not automatically retried.

## Run locally

Python 3.10+ and the standard library are sufficient. Provide these environment
variables from a private secret source, outside the checkout:

| Variable | Meaning |
| --- | --- |
| `MOI_BASE_URL` | HTTPS product API URL, including its `/newmoi` prefix |
| `MOI_PAT` | QA personal access token with access to the test workspace |
| `MOI_WORKSPACE` | Exact workspace name (`journey-1`) |
| `MOI_MODEL` | Model identifier (`deepseek-v4.1-flash`) |

```sh
python3 -m unittest discover -s tests -v
python3 -m journey.hello
```

The command exits nonzero on failure. It emits only a sanitized JSON report;
server response bodies, credentials, endpoint URLs, workspace/task IDs, and
conversation content are never included. Archived conversations remain in QA.

## GitHub Actions

Create the `qa` environment and set its `MOI_BASE_URL` and `MOI_PAT` secrets.
The workflow uses a standard `ubuntu-latest` runner. It runs on main pushes,
same-repository PRs, manual dispatch, and every six hours (00:17, 06:17, 12:17,
18:17 UTC). Scheduled/manual runs become available once the workflow is on main;
GitHub schedules may start late. Until then, the draft PR validates the runner.

Fork PRs run only offline tests. Credentialed runs never use `pull_request_target`,
never checkout an arbitrary input ref, and do not upload raw artifacts. Repository
writers can change code that receives secrets; restrict write access accordingly.
The `qa` concurrency group prevents overlapping live runs.

On a failure, inspect the Actions summary's stage and error. Do not interpret a
later successful rerun as erasing the original failure. GitHub Actions failure
notifications follow each subscriber's GitHub notification settings.
