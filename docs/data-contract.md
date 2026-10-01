# Neutral data contract

The Python validator is authoritative for schema version 1. Records contain counts
and metadata, not patches, comment bodies, prompts, or secrets. All dates are
`YYYY-MM-DD` in one declared organization timezone. Monetary amounts use explicit USD
fields. Null means unavailable; zero means an observed zero.

## Configuration

`examples/config.json` is a full fictional example. Required keys are
`schema_version`, `people`, and `baseline`. Each person has a stable `id`, display
`name`, confirmed provider-prefixed `aliases`, and `history_from`. Optional
`history_to` bounds an observed historical window; `include_in_baseline` controls the
reference cohort. Never join people by display name alone.

Aliases have a namespace, such as `github:example-login`, `bitbucket:{uuid}`, or
`email:user@example.invalid`. Collector Bitbucket aliases use UUID, falling back to
account ID. Map provider usernames and billing aliases through explicit confirmation.
An alias cannot have multiple owners. `bots` maps known service accounts to a category.
Confirmed human aliases take precedence over name heuristics; explicit bot mappings
take precedence over a conflicting human mapping, which administrators should correct.

`baseline` declares complete-month `from`/`to` dates, `repositories`, eligibility
thresholds, and a display label. `size` holds a version, increasing file/line/unit
bands, optional exclusion glob patterns, and a cap. `hours_per_day` and `holidays`
declare the availability assumption. `admins` contains IDs and environment variable
names for per-admin credentials, never credential values.

## PR and event records

```json
{
  "schema_version": 1,
  "synthetic": false,
  "prs": [{
    "key": "github:example-org/service#42",
    "platform": "github",
    "repository": "example-org/service",
    "author": "github:example-login",
    "author_bot": false,
    "title": "Example change",
    "created": "2026-09-01",
    "merged": "2026-09-03",
    "revision": "commit-or-source-revision",
    "detail_status": "complete",
    "files": [{"path": "src/example.py", "added": 25, "removed": 5}]
  }],
  "events": [{
    "id": "github:example-org/service#42:review:123",
    "pr": "github:example-org/service#42",
    "actor": "github:another-login",
    "actor_bot": false,
    "kind": "approval",
    "date": "2026-09-02"
  }],
  "coverage": [{
    "repository": "example-org/service",
    "from": "2025-01-01",
    "to": "2026-09-30",
    "status": "complete",
    "details_status": "partial"
  }]
}
```

Event kinds are `review`, `approval`, `comment`, and `merge`. Approval is one event,
not an approval plus a duplicate review. Stable event IDs deduplicate repeated imports.
Null `merged` means the merge date is unavailable or the PR is not merged. A provider
can also mark `merge_date_unknown`; never substitute its last-updated timestamp.

Coverage `status` describes metadata and activity enumeration. File comparison
coverage is separate in `details_status` and each PR's `detail_status`. Failed or
partial metadata coverage cannot support a complete historical reference. Complete
metadata with some unavailable files can qualify when the explicit scored-PR coverage
meets the threshold.

## Daily usage

Use `import-usage` for a CSV of normalized daily totals, not a raw vendor file with
both totals and model breakdowns. Reconcile source totals before import. Required
columns are `date`, `actor`, and `provider`. Optional columns are `scope`, `repository`,
`credits`, `tokens`, `prompts`, `cost_usd`, `code_review_active`, and `code_review_passive`.
Blank numeric and review-flag values are unavailable. Flags use `true` or `false`.

```csv
date,actor,provider,scope,credits,tokens,prompts,cost_usd,code_review_active,code_review_passive
2026-09-03,github:example-login,github-copilot,enterprise,420,,0,,true,true
2026-09-03,github:example-login,other-assistant,local,,30000,12,1.25,,
```

Canonical keys are person, provider, and day. Enterprise totals win over duplicate
organization totals, then local totals. Distinct conflicting totals at the same
priority fail rather than silently add. This contract assumes one consolidated daily
total at each scope. Normalize multiple non-overlapping organizations into one total
before import. Model subtotals must not be imported as additional daily totals.

Repository filtering excludes usage rows without repository attribution and shows
that missing allocation. Person linking applies equally to activity and usage. Do
not assume that all a person's daily AI usage supported the displayed PRs.

## AI review work

`ai_reviews` records stable `id`, `pr`, `date`, `bot`, `completed`, optional comment,
suggestion, and applied-suggestion counts. Sponsorship requires a confirmed `sponsor`
alias, an `attribution` of `manual_request` or `automatic_author_policy`, and nonempty
`evidence`. Automatic-author sponsorship must match the PR's author.

```json
{
  "id": "provider-review-cycle-123",
  "pr": "github:example-org/service#42",
  "date": "2026-09-02",
  "bot": "github:copilot-pull-request-reviewer[bot]",
  "sponsor": "github:example-login",
  "attribution": "manual_request",
  "evidence": {"source": "review_request_event", "event_id": "request-456"},
  "completed": true,
  "comments": 0,
  "suggestions": 0,
  "applied_suggestions": null
}
```

Missing sponsor evidence stays `unresolved`. A usage active/passive flag cannot
create a review cycle or identify a particular PR. A resolved thread is not proof
that its code suggestion was applied.

## Effort audit trail

`effort` is an ordered list of dispositions. Latest disposition per PR wins. Approved
entries require `approved_by`, `approved_at`, `revision`, `rubric_version`, `low_hours`,
and `high_hours`. The admin UI gets the reviewer from its authenticated session.
CLI review requires a configured reviewer and an expected revision; it is a trusted
local administrative operation, not an independent cryptographic signature.

To update a collector snapshot, merge new provider records, retain effort history,
validate the result, and rerun reporting. Do not rewrite old approval evidence to
make it match a new revision.
