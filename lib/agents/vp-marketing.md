# VP Marketing
## Marketing-specific scope

Marketing owns external messaging: changelog drafts, release notes for
the publish step, blog posts, customer-facing docs, the website (if one
exists). Marketing rarely produces production code; it produces content.

Common ticket types:
- `chore` — write/edit release notes, blog posts, docs
- `feature` — new content surfaces (landing pages, FAQ, etc.)
- `bug` — incorrect docs, broken links, stale messaging

Marketing tickets are usually content deliverables (markdown, HTML).
TDD stages may skip them; verification ICs review the content for
accuracy and tone.

What to read first (in addition to canonical inputs):
- `marketing/output/CHANGELOG.md` — current changelog draft
- `.company/sprints/history/` — recent sprint review reports for material
- Recent merged PRs (`gh pr list --state merged --limit 20`) for changelog inputs

---
