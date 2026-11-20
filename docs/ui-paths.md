# A2UI and MCP App UI paths

J2-61, J2-62, J2-69, J2-74, J2-75 and J2-94 accept either `a2ui` or `mcp_app`.

## Path contract

The case-level additive contract is:

```json
"ui_contract": {
  "allowed_paths": ["a2ui", "mcp_app"],
  "selection": "first_valid_evidence",
  "mixed_path": "fail"
}
```

The runner uses task events, not the Agent's prose. A valid A2UI event selects `a2ui`; a successful `present_mcp_app` result with a `ui://` resource selects `mcp_app`. Both paths in one attempt are `mixed_path` and fail. No valid UI evidence is `unsupported_path` and fails. API, credential and resource-read failures remain framework errors.

A2UI keeps its original checks. MCP App checks cover:

- successful presentation and a unique `ui://` resource;
- resource identity, MIME type and SHA-256;
- self-contained HTML under 2 MiB with no unresolved placeholders or required external scripts/styles;
- real DOM components and exact case data;
- actual option clicks, text input and form submission in a headless DOM;
- correct results and locked inputs without creating a replacement app.

Single, multiple-choice and text questions may complete their judgment inside the MCP App, so the second Agent turn is not required. The result records `ui_path_expected_turns` accordingly. J2-94 still sends the submitted values to the next Agent turn and requires the final JSON response.

## Timeout behavior

These six cases use a 600-second execution budget because building a self-contained MCP App may take more than five minutes. Other Journey-2 cases retain their existing budgets.

Timeout retains its execution status. The runner detects the UI path from available evidence and evaluates the MCP resource when it exists. Missing resources or unreachable checkpoints fail instead of being synthesized as passes. MCP checks are never merged with A2UI checks, and `ui_path_expected_checks` records the path-specific denominator.

## DOM execution

MCP App checks require Node and `jsdom`. Install development dependencies with `npm ci`. The DOM execution copy changes `<script type="module">` to a classic script because jsdom does not execute module scripts. Resource bytes and the recorded hash are not changed. Headless DOM verifies interaction logic, not the visual rendering or CSP behavior of the actual MOI host.

## Evidence

Each MCP App result records `ui_path`, `ui_path_expected_checks`, `ui_path_expected_turns` where applicable, the resource URI and its SHA-256. Historical results and A2UI scores are not rewritten. Passing an MCP App does not imply that the same Agent behavior is correct on every run or on a different UI path.
