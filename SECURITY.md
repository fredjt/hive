# Security Policy

Hive stores and passes LLM provider API keys and, in some configurations,
other credentials (GitHub tokens for release publishing). If you believe
you have found a security vulnerability, please read this page before
reporting.

## Report a vulnerability — privately, never publicly

**Do not open a public issue for security problems.** Public issues are
readable by everyone, and a vulnerability report is itself sensitive
information.

Report privately via GitHub's security advisory form:

- <https://github.com/fredjt/hive/security/advisories/new>

(or the repo's **Security** tab → **Report a vulnerability**).

We ask that you:

- give us a reasonable time (we aim to acknowledge within 7 days and ship a
  fix before any disclosure) to fix it before you disclose publicly;
- include the smallest repro that demonstrates the issue;
- **never paste the actual value of a leaked secret, token, or key** —
  reference it by prefix only (e.g. `gho_0NFF…`). A report that contains a
  live credential makes the leak worse, not better.

## Secret leaks in particular

If you believe Hive leaked a secret (into a log, a ticket, chat history,
or the repository itself), report it privately the same way. The response
is: revoke the credential immediately, then scrub it from wherever it
landed. Revocation and scrubbing are independent — both happen.

## What to report

- Credential/API-key exposure: keys written to logs, tickets, chat, or the
  repo; secrets returned in error output; insecure storage or transit of
  provider keys.
- Injection or command-execution paths: the harness runs `bash:` blocks and
  MCP tools; a way to escape the intended sandbox or force-execute
  unintended commands.
- Quality-gate bypasses: paths that let a ticket reach `done/` without the
  required gates or IC approval.
- MCP server exposure: the MCP HTTP endpoint is meant to be localhost-only;
  report anything that widens its reach.

## Supported versions

The latest stable release only. Older releases are not patched.

## Scope notes

Hive's agent harness executes the project's own code by design — running
arbitrary code is the product, not a vulnerability. The threat model covers
unintended execution, credential handling, and gate integrity, not "an agent
can run code in the project."
