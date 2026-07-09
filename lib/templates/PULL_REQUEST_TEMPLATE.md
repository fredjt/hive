<!-- This template is auto-installed by hive's gh-init.sh. Customize as needed. -->

## Linked hive ticket
<!-- Hive auto-fills this when the PR is opened by gh-pr-open.sh. If you opened the PR by hand, replace `TICKET-ID` with the actual id. -->
- Ticket: `TICKET-ID`
- Department: <!-- e.g. backend-api, frontend-web, it -->

## TDD evidence
<!-- Hive enforces test-first via the tdd-order gate. The first commit on this branch must touch a test file. -->
- [ ] First commit (`[TDD-RED]`) adds a failing test
- [ ] Second commit (`[TDD-GREEN]`) makes that test pass
- [ ] Test suite passes locally before push

## Summary
<!-- One paragraph: what does this change do, and why? -->

## Verification checklist
<!-- The hive verification IC reads `gh pr checks` before merging. CI must be green. -->
- [ ] All CI checks passing
- [ ] No new lint errors
- [ ] No new security findings (high+)
- [ ] Acceptance criteria from the ticket are satisfied
