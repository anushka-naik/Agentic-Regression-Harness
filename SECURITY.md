# Security policy

## Scope

AgentCheck version 1 is a **local, single-user test environment** with synthetic data:

- The dashboard binds to `127.0.0.1` and has no authentication. Do not expose it publicly.
- Tools are read-only functions over an in-memory fictional store. No email, shell,
  browser, payment or customer system is connected.
- Model output, tool notes and imported traces are treated as untrusted and rendered
  escaped (Jinja2 autoescape for HTML, escaping for Markdown, ElementTree for JUnit).
- The model never names a Python function; unknown tool names are logged and rejected,
  never imported or executed.
- Configuration is parsed with `yaml.safe_load`; case data with JSON parsers. Dataset
  paths are confined beneath the dataset directory.

Prompt-injection cases in the dataset (`untrusted_tool_note`) exist to *measure* whether an
agent follows instructions in tool data. They are test fixtures, not attack tooling.

## Reporting

Please report vulnerabilities privately through GitHub's "Report a vulnerability"
(Security Advisories) on this repository rather than in a public issue. Include steps to
reproduce, the affected version or commit, and impact. Expect an acknowledgement within a
week.
