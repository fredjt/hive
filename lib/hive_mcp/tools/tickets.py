"""Ticket lifecycle tools."""

from __future__ import annotations

from hive_mcp.server_compat import Server

from hive_mcp.shell import run


def register(server: Server) -> None:
    @server.tool()
    def ticket_create(
        dept: str,
        type: str,
        priority: str,
        title: str,
        description: str,
        created_by: str = "user",
        source: str = "manual",
        estimate: str | None = None,
        labels: list[str] | None = None,
        acceptance_criteria: list[str] | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Create a new ticket in the dept's backlog.

        Args (all lowercase; the script normalizes case + a few synonyms):
          type:     feature | bug | improvement | chore | infrastructure | research
          priority: critical | high | medium | low
          estimate: trivial | small | medium | large | xlarge   (optional, default medium)
          labels:   optional list of short tags (stored on the ticket)
          acceptance_criteria: optional list of criteria, one string each
        """
        args = [dept, type, priority, title, description, created_by, source]
        if estimate:
            args.append(estimate)
        if labels:
            args += ["--labels", ",".join(labels)]
        if acceptance_criteria:
            args += ["--ac", "\n".join(acceptance_criteria)]
        return run("ticket-create", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def ticket_get(dept: str, ticket_id: str, company_dir: str | None = None) -> dict:
        """Read a ticket's full JSON."""
        return run("ticket-get", dept, ticket_id, "--json", company_dir=company_dir).to_dict()

    @server.tool()
    def ticket_list(
        dept: str,
        status: str | None = None,
        priority: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """List tickets in a department. Optional status filter
        (backlog, sprint-current, in-progress, in-review, done, archive).
        """
        args = [dept]
        if status:
            args.append(status)
        if priority:
            args.extend(["--priority", priority])
        args.append("--json")
        return run("ticket-list", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def ticket_move(
        dept: str,
        ticket_id: str,
        new_status: str,
        comment: str,
        author: str,
        reason: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Move a ticket between statuses.

        ⚠️  VERIFICATION ICs: do NOT call this for approve / bounce. Use
        `ticket_approve` (atomic: stamps verification + moves to done +
        merges PR — ONE call) or `ticket_bounce` (atomic: writes rationale
        + moves to in-progress — ONE call). Calling ticket_move directly
        will return an error redirecting you back to those tools.

        Most ticket moves under the sprint-staged lifecycle are SCRIPTED
        (sprint-lifecycle moves sprint-current → in-progress; engineer's exit-0
        moves in-progress → in-review; crash recovery bounces unverified
        tickets). The only agent-allowed move via ticket_move is:

          - VP rejection: new_status="done", reason="rejected" — only
            during stages vp-plan, vp-review, ceo-bugfix-plan, vp-bugfix-plan.
            Closes the linked PR (if any) and notifies the GH issue.

        For `done`, `reason` is required: `implemented` | `rejected` | `done`.
        Verified-merge requires gates_passed=true AND verified_by set
        (i.e., ticket_approve was called first).
        """
        # Active redirect for the most common confused-caller pattern.
        # Verification ICs that call ticket_move directly get a clear
        # "use this instead" response, not a cryptic gate failure.
        # Detect verification context from author OR from comment language —
        # some agents omit the author param entirely.
        author_l = (author or "").lower()
        comment_l = (comment or "").lower()
        looks_like_verifier = (
            "verif" in author_l
            or "rejected by verification" in comment_l
            or "rejected by verification-ic" in comment_l
            or comment_l.startswith("rejected by ")
            or comment_l.startswith("approved by verification")
        )
        if looks_like_verifier:
            if new_status == "done" and reason in (None, "implemented", "done"):
                return {
                    "ok": False,
                    "error": "redirect",
                    "stderr": (
                        "ticket_move denied: verification ICs do NOT call ticket_move "
                        "to approve a ticket. Use ticket_approve instead — it is atomic "
                        "(stamps verification + moves to done + merges PR + closes the "
                        "linked GH issue all in one call).\n\n"
                        f"  ticket_approve(dept=\"{dept}\", ticket_id=\"{ticket_id}\", "
                        f"verifier=\"Verification-IC\", rationale=\"<≥30 chars: which AC verified, "
                        f"which edge cases probed, what you tried to break>\")\n\n"
                        "Do NOT also call ticket_move afterwards."
                    ),
                }
            if new_status == "in-progress":
                return {
                    "ok": False,
                    "error": "redirect",
                    "stderr": (
                        "ticket_move denied: verification ICs do NOT call ticket_move "
                        "to bounce a ticket. Use ticket_bounce instead — it is atomic "
                        "(writes rationale comment + moves to in-progress + posts a "
                        "'bounced' comment to the linked GH issue all in one call).\n\n"
                        f"  ticket_bounce(dept=\"{dept}\", ticket_id=\"{ticket_id}\", "
                        f"verifier=\"Verification-IC\", rationale=\"<≥80 chars: numbered list "
                        f"of every issue with file:line refs>\", skip_tdd_red=False)\n\n"
                        "Pass skip_tdd_red=True ONLY when tests are sound and adding more "
                        "tests wouldn't help (rare — defaults to re-running TDD-RED)."
                    ),
                }

        args = [dept, ticket_id, new_status, comment, author]
        if reason:
            args.append(reason)
        return run("ticket-move", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def ticket_claim(
        dept: str,
        ticket_id: str,
        role: str,
        agent_id: str,
        agent_name: str,
        worktree_path: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Claim a ticket — sets assignee, links worktree, moves to in-progress."""
        args = [dept, ticket_id, role, agent_id, agent_name]
        if worktree_path:
            args.append(worktree_path)
        return run("ticket-claim", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def ticket_comment(
        dept: str,
        ticket_id: str,
        author: str,
        comment: str,
        company_dir: str | None = None,
    ) -> dict:
        """Add a comment to a ticket."""
        return run(
            "ticket-comment", dept, ticket_id, author, comment, company_dir=company_dir
        ).to_dict()

    @server.tool()
    def ticket_approve(
        dept: str,
        ticket_id: str,
        verifier: str,
        rationale: str,
        company_dir: str | None = None,
    ) -> dict:
        """Verification IC's APPROVE path — atomic. Stamps the verification
        fields (verified_by, verified_at, verification_rationale) AND moves
        the ticket to done with reason=implemented in one call.

        Side-effects of the done move:
          - Triggers `gh-pr-merge.sh` if a PR is open for feature/<ticket>
          - Closes the linked GitHub issue (if the ticket has
            (external_id field preserved for legacy tickets but no longer used)
          - Posts a "verified" comment on the GH issue
          - On PR merge conflict: rolls the ticket back to in-progress with
            skip_tdd_red=true so the engineer rebases next loop. The verifier's
            turn still exits 0 — the conflict is the ticket's problem, not
            the verifier's.

        Rationale must be at least 30 characters and describe what was
        checked: which acceptance criteria you verified, which edge cases
        you probed, what you tried to break. One-word approvals are
        rejected to discourage rubber-stamping.

        After this returns successfully, you do NOT need to call
        `ticket_move` separately. The approve IS the move.
        """
        return run(
            "ticket-approve", dept, ticket_id, verifier, rationale, company_dir=company_dir
        ).to_dict()

    @server.tool()
    def ticket_bounce(
        dept: str,
        ticket_id: str,
        verifier: str,
        rationale: str,
        skip_tdd_red: bool = False,
        company_dir: str | None = None,
    ) -> dict:
        """Verification IC's BOUNCE path — return the ticket to the engineer
        for another loop iteration. Atomically:
          1. Moves the ticket from in-review back to in-progress, writing
             the FULL rationale as the 'REJECTED by verification' comment
             (one comment per bounce — the move comment is canonical)
          2. Posts a "bounced" comment on the linked GitHub issue (if any)

        Use bounce when ONE more loop can plausibly fix the ticket — the
        implementation has a bug, the test misses coverage, the diff is
        partial. If more loops cannot fix it (wrong premise, wrong dept,
        unavailable dependency, duplicates done work), use `ticket_reject`
        instead — that closes won't-do without burning more engineer turns.

        Rationale must be at least 80 characters and SHOULD be a numbered
        list of every issue found, with file:line references where applicable.

        skip_tdd_red=True signals to the next loop iteration that stage 3
        (tdd-red) should be SKIPPED for this ticket — go straight to stage 4
        (tdd-green). Use ONLY when the existing test is sound and the
        implementation is wrong (i.e., adding more tests wouldn't help). The
        default (False) re-runs RED so the tester can expand coverage.

        This is the canonical bounce path. Do NOT call ticket_move directly
        for the in-review → in-progress transition — this wrapper enforces
        the rationale length and writes the comment for you.
        """
        args = [dept, ticket_id, verifier, rationale]
        if skip_tdd_red:
            args.append("--skip-tdd-red")
        return run("ticket-bounce", *args, company_dir=company_dir).to_dict()

    @server.tool()
    def ticket_reject(
        dept: str,
        ticket_id: str,
        verifier: str,
        rationale: str,
        company_dir: str | None = None,
    ) -> dict:
        """Verification IC's REJECT path — close the ticket as won't-do
        without further loop iterations. Atomically:
          1. Writes the rationale as a comment on the ticket
          2. Moves the ticket from in-review to done with reason=rejected
             (auto-closes the linked PR)

        Use reject when MORE LOOPS CANNOT FIX THIS TICKET — the premise is
        wrong, it belongs to a different department, it duplicates already-
        done work, it depends on infrastructure that doesn't exist, the
        engineer's comments make clear it's structurally unworkable, etc.
        For "the implementation has a bug" or "the test misses coverage,"
        use `ticket_bounce` instead.

        Without this path, the verifier was forced to bounce repeatedly
        until the loop cap deferred the ticket to backlog, wasting an
        entire sprint of engineer turns before any VP could re-route the
        work. Reject when you know more iteration won't help.

        Rationale must be at least 80 characters and should explain WHY
        more loops cannot fix this (what's structural about the failure)
        AND name the alternate path if any (e.g., "refile in <other-dept>",
        "defer until <dependency> exists", "duplicate of <id>").
        """
        return run("ticket-reject", dept, ticket_id, verifier, rationale,
                   company_dir=company_dir).to_dict()

    @server.tool()
    def ticket_route_expedited(
        type: str,
        priority: str,
        title: str,
        description: str,
        created_by: str,
        target_department: str,
        estimate: str | None = None,
        company_dir: str | None = None,
    ) -> dict:
        """Create an expedited ticket directly on the target dept's backlog,
        bypassing the normal review queue. For HIGH/CRITICAL blockers only.

        The script enforces: priority must be high|critical and type must be
        bug|infrastructure. Returns the new ticket ID (or the duplicate's ID
        if a ticket with the same title already exists). The CEO gets a
        tracking notification.
        """
        args = [type, priority, title, description, created_by, target_department]
        if estimate:
            args.append(estimate)
        return run("ticket-route-expedited", *args, company_dir=company_dir).to_dict()
