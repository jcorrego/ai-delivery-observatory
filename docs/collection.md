# Collection, backfills, and attribution

Collectors are read-only and use an explicit repository list. They do not enumerate
every repository your token can access. Use least-privilege read tokens and environment
variable references. GitHub pull-request, issue comment, and timeline reads may need
separate repository permissions. Bitbucket Cloud requires pull-request and repository
read access appropriate to your credential type.

## What is collected

| Source | Included | Coverage boundary |
| --- | --- | --- |
| GitHub | PR author, creation, merge, revision, file statistics, reviews, approvals, issue/inline comments, merger, review-request events | Files are complete only when their count matches `changed_files`; API caps and access failures stay partial |
| Bitbucket Cloud | PR author, creation, source revision, file statistics, approval activity, comments, merge activity and actor | Missing upstream file comparisons stay unscored; absent merge activity stays unknown |
| AI usage | Normalized daily CSV imports | Raw vendor schemas are not guessed; billing reconciliation is an administrator task |
| AI review cycles | GitHub Copilot/CodeRabbit submitted reviews; additional cycles through neutral imports | A supported requester event can establish manual sponsorship; automatic-policy sponsorship needs imported policy evidence |

The adapters do not export PR descriptions, diff patches, comment text, or prompts.
They count suggestion markers from fetched inline bodies without retaining the text.
Applying a suggestion is not observable from resolving its thread. Applied-suggestion
counts remain unavailable unless an authoritative import supplies them.

The GitHub adapter matches the most recent evidenced request for the exact review
account preceding submission. Accounts with different aliases need confirmation and
imported attribution rather than a name guess. A submitted bot review without request
evidence is still a completed unresolved cycle. Repository rules that automatically
review a PR do not justify assigning an unknown manual request to its author.

## Backfill procedure

1. Copy the example configuration to your ignored `data/` directory and confirm aliases.
2. Collect the complete historical period, beginning at the first day of a month.
   Repeat for both platforms and every included repository.
3. Inspect exit status, `coverage`, `collection_errors`, and unscored PRs. A failed
   page is not a successful historical scan. Fix access or rate limits and rerun.
4. Merge snapshots by stable provider keys and event IDs. Newer records replace
   earlier records with the same key; effort dispositions remain ordered.
5. Import reconciled daily usage totals. Keep enterprise and organization overlap
   explicit so canonicalization can avoid duplication.
6. Validate, review the cohort and size policy, and inspect a later period without
   changing the historical reference to favor the result.

GitHub lists all PR states and reads updates relevant to the start date. Bitbucket
queries all PR states with a one-day lookback before the requested start to preserve
timezone boundary events. Later updates may be fetched even when the requested end
is historical; report filters select events by their actual dates. The reference
collectors favor completeness over speed and do not implement resumable queues or
webhooks. For large deployments, add durable cursors, bounded jobs, and explicit
failed/unavailable detail states without changing the neutral contract.

Requests have bounded retries for transient failures and a pagination limit. Looping
pagination, changed-origin pagination links, and changed-origin redirects fail.
Provider tokens are never forwarded to another origin or written into export records.
An adapter failure can leave the repository absent from the snapshot; its failed
coverage record must remain visible when you merge the run.

Primary API references, checked October 1, 2026:

- [GitHub PR endpoints](https://docs.github.com/en/rest/pulls/pulls)
- [GitHub review endpoints](https://docs.github.com/en/rest/pulls/reviews)
- [GitHub timeline endpoints](https://docs.github.com/en/rest/issues/timeline)
- [Bitbucket Cloud pull-request endpoints](https://developer.atlassian.com/cloud/bitbucket/rest/api-group-pullrequests/)
- [GitHub Copilot code review and billing](https://docs.github.com/en/copilot/concepts/agents/code-review)
- [GitHub Copilot usage metrics](https://docs.github.com/en/copilot/reference/copilot-usage-metrics/copilot-usage-metrics)

These documents support the adapter design. Mocked contract tests do not establish
your private repository access, provider completeness, or organization deployment.
