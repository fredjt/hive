#!/usr/bin/env bash
set -euo pipefail

# init-marketing-scaffold.sh - Create the project's <project_root>/marketing/
# directory tree for marketing deliverables.
#
# This is intentionally OUTSIDE .company/ because these are real shippable
# artifacts (landing pages, blog posts, pricing strategy, sales scripts) -
# not internal coordination state.
#
# Usage: init-marketing-scaffold.sh <project-root>
#
# Idempotent: existing files are not overwritten.

if [ $# -lt 1 ]; then
  echo "Usage: init-marketing-scaffold.sh <project-root>" >&2
  exit 1
fi

PROJECT_ROOT="$(cd "$1" && pwd)"
MK_DIR="$PROJECT_ROOT/marketing"

mkdir -p "$MK_DIR/output"

write_if_missing() {
  local path="$1" content="$2"
  if [ -f "$path" ]; then
    return
  fi
  printf '%s\n' "$content" > "$path"
  echo "  + $path" >&2
}

write_if_missing "$MK_DIR/strategy.md" "# Marketing Strategy

> WHO is this for, WHAT problem does it solve, WHY us over competitors.
> VP-Marketing maintains this. Update every cycle as understanding sharpens.

## Target users (primary)
- TBD — to be filled in by marketing-researcher's persona work

## The problem we solve
- TBD

## Why us (positioning)
- TBD — refer to marketing/competitors.md for the differentiation gap analysis

## North-star message
> One sentence. Should be testable on a real human.
"

write_if_missing "$MK_DIR/pricing.md" "# Pricing

> What people pay, what they get for free, how trials work.
> VP-Marketing owns this. Frontend builds the page; backend builds the billing.

## Tiers (draft)

| Tier | Price | Limits | Audience |
|------|-------|--------|----------|
| Free | \$0 | TBD | Solo users, evaluators |
| Pro  | \$TBD/mo | TBD | Small teams |
| Team | \$TBD/user/mo | TBD | Growing teams |
| Enterprise | Contact | unlimited + SSO + SLA | Large orgs |

## Trial mechanics
- TBD: 14-day free trial? Reverse trial? Free tier with limits?

## Tickets needed for monetization
- [ ] frontend: pricing page at /pricing
- [ ] backend: Stripe checkout integration
- [ ] backend: subscription state in user model
- [ ] frontend: account/billing settings page
"

write_if_missing "$MK_DIR/gtm-plan.md" "# Go-to-Market Plan

## Channels (priority order)
1. **Show HN** — when first version is live
2. **Product Hunt** — at v1.0 launch
3. **Content / SEO** — long-tail blog posts targeting our personas
4. **dev.to / engineering blogs** — long-form posts about how we built it
5. **Reddit** — relevant subs (TBD per persona)
6. **X (Twitter)** — for engineering audience
7. **Newsletter syndication** — Pragmatic Engineer, Lenny's, etc.

## Launch timeline
- TBD — fill in once core features are shippable

## Asks for engineering
- A demo URL that doesn't fall over under HN-front-page traffic
- Working signup flow before any launch
"

write_if_missing "$MK_DIR/competitors.md" "# Competitive Landscape

> marketing-researcher fills this in. VP-Marketing reviews each cycle.

| Competitor | Pricing | Strengths | Weaknesses | Our differentiation |
|------------|---------|-----------|------------|---------------------|
| TBD        | TBD     | TBD       | TBD        | TBD                 |
"

write_if_missing "$MK_DIR/customer-personas.md" "# Customer Personas

> marketing-researcher writes detailed personas based on real research.

## Persona 1: TBD
- Role:
- Problem:
- Currently uses:
- Why they'd switch:
- What they'd pay:
- Where to reach them:
"

write_if_missing "$MK_DIR/customer-feedback.md" "# Customer Feedback Log

> marketing-liaison aggregates user feedback themes. Patterns mentioned 3+ times
> become a feature ticket on the relevant department.

## Recurring themes
(none yet)
"

write_if_missing "$MK_DIR/testimonials.md" "# Testimonials

> Captured from real customer interactions, support tickets, and reviews.
> NEVER fabricate testimonials.

(none yet)
"

write_if_missing "$MK_DIR/metrics.json" '{
  "as_of": null,
  "targets": {
    "month_1_signups": 100,
    "month_3_paying_customers": 10,
    "month_6_mrr_usd": 1000
  },
  "actuals": {
    "landing_pages_published": 0,
    "blog_posts_published": 0,
    "monetization_features_shipped": 0,
    "current_mrr_estimate_usd": 0
  },
  "next_milestones": [
    "Ship a landing page",
    "Ship a pricing page",
    "Ship Stripe integration",
    "First Show HN"
  ]
}'

write_if_missing "$MK_DIR/output/INDEX.md" "# Marketing Output Index

> Manifest of marketing collateral. marketing-reporter keeps this current.

## Landing pages
(none yet)

## Blog posts
(none yet)

## Social
(none yet)

## Comparison pages
(none yet)
"

# Add marketing/ to .gitignore? NO - these are real artifacts the user
# probably wants to commit. Don't add to gitignore.

echo "Marketing scaffold ready at $MK_DIR" >&2
