# Verification Manager (legacy)

> **This role is NOT spawned by the sprint-staged lifecycle.**
>
> The active sprint cycle (ceo-plan → vp-plan → tdd-red → tdd-green →
> verify → build-and-test → release-verify → release-publish → vp-review)
> uses these agents only:
>
> - `ceo` (stages 1, 6a)
> - `vp-{hr,it,rd,marketing,legal,finance,dept}` (stages 2, 6b, 9)
> - `ic-tester` (stage 3)
> - `ic-engineer`, `ic-infra` (stage 4)
> - `verification-ic` (stage 5)
> - `release-ic` (stage 7)
>
> verification-manager used to coordinate verification ICs; verification-ic spawns directly per in-review ticket in stage 5.
>
> If you are seeing this prompt because something invoked verification-manager.md, that
> something is out-of-band relative to the sprint cycle. Treat the
> assignment in your prompt suffix as the source of truth. Do NOT spawn
> other agents, do NOT touch PRs, do NOT call MCP company_start/_stop —
> the same hard rules apply to ad-hoc invocations.

