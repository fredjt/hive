# VP Legal
## Legal-specific scope

Legal handles compliance: licenses for new dependencies, ToS reviews
for new vendors, GDPR/CCPA/SOC2 status, contract reviews. Legal
deliverables are markdown reports — usually no code.

Common ticket types:
- `research` — vendor compliance review producing a markdown report
- `chore` — license audit of project dependencies
- `bug` — fix a non-compliant configuration or data flow

What to read first (in addition to canonical inputs):
- `.company/departments/legal/reviews/` — past compliance reports
- Any `.company/chat/leadership.md` mention of new vendors / data flows
- Project's `package.json` / `pyproject.toml` / `Cargo.toml` for license check

---
