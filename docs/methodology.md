# Metrics and assumptions

## Evidence classes

Observed events, reviewed estimates, and historical capacity are different measures.
Show their periods, samples, source coverage, and versions together. Do not combine
them into a single productivity score or rank engineers from PR counts.

| Metric | Definition | Boundary |
| --- | --- | --- |
| Human PRs created | Distinct PRs whose creation date is in the period and author is human | Authorship belongs to the recorded account, including confirmed aliases |
| Human PRs merged | Distinct human-authored PRs with an evidenced merge date in the period | Merge date does not establish when the work began |
| Collaboration PRs | Distinct PRs by another author with a human review, approval, comment, or merge event in the period | Multiple events on one PR count once for this total |
| Interaction events | Those human collaboration events, counted individually | Comments are not additional whole PRs |
| Sponsored AI reviews | Completed AI review cycles with supported human sponsorship | Separate from human-written reviews and comments |
| Automation activity | Bot-created PRs and bot interaction PRs, grouped by category | A PR can occur in more than one category; category totals are not an overall unique total |
| Approved manual effort | Latest approved whole-PR low/high range with matching revision and rubric | Includes implementation, tests, review, and integration; do not add review effort twice |
| Recorded credits | Canonical provider daily billing units | Credits are not tokens, an invoice, or exact PR spend |
| Recorded tokens | Explicit imported token totals | Missing token data is unavailable, even when credits exist |
| Review active/passive flags | Days on which a user is recorded as actively/passively using code review | Boolean usage flags are not numbers of review requests, comments, or suggestions |
| Historical equivalent | Comparable weighted output divided by historical daily capacity | A model under constant availability; not measured hours saved or a causal AI effect |

Dates in neutral records use the declared organization timezone. Collectors normalize
provider timestamps to that timezone. Weekends stay in event counts; only the capacity
denominator uses Monday through Friday, excluding configured holidays. Zero-output
working days are retained. Leave and allocation to coding are not measured.

## Historical reference

Start with complete months in a fixed historical period, such as 2025. Calling the
period historical is more defensible than calling it pre-AI without usage evidence.
The first positive AI-usage date is a candidate cutoff, not proof of first AI use.
Missing earlier billing data is not evidence of no AI use.

Each person's `history_from` and optional `history_to` declare an observed history
window. Round that window inward to complete months. Successful source coverage
must span the same repositories and period. A person's first observed PR alone does
not establish complete observation or employment dates.

```text
raw daily capacity = historical human merged PRs / historical working days
monthly average = historical human merged PRs / number of complete months
weighted daily capacity = historical scored size units / historical working days
```

Use actual working-day counts rather than 30-day months. A 261-working-day year has
an average 21.75 working days per month. Calculate daily rates from the complete
period; averaging monthly daily rates without weighting by their working days
would give short months extra influence.

Starter personal eligibility is at least 3 complete months, 20 merged human PRs,
and 95% scored diff coverage. These are operational defaults, not a confidence
guarantee. Preserve the sample size, baseline version, and sensitivity before use.

If the personal reference is insufficient or zero, use the mean historical daily
rate of the configured baseline cohort. The cohort includes people with complete
history and sufficient diff coverage even when their PR sample is below 20. This
avoids removing low-output people solely because they miss the personal threshold.
Use `include_in_baseline` to record explicit cohort choices. New people can use the
team reference; they must not silently lower a historical denominator for a period
before their observation began.

## Trial size index

The supplied bands are editable starter assumptions. They are not universal effort
standards or measured labor hours. Calibrate them against your own eligible history
and reviewed examples, then freeze the policy while comparing later periods.

| Band | Eligible files | Added + removed eligible lines | Units |
| --- | ---: | ---: | ---: |
| Small | At most 1 | At most 10 | 0.5 |
| Typical | At most 5 | At most 170 | 1 |
| Large | At most 18 | At most 750 | 2 |
| Above large | Either threshold exceeded | Either threshold exceeded | 3, capped |

Both thresholds must fit a band. Equivalently, choose the larger file/line band.
Four files and 300 lines score 2; twenty files and 100 lines score 3. Known lockfiles,
dependency trees, build outputs, source maps, and minified files are excluded by
default. Documentation and application configuration remain eligible. Customize
the patterns when your repository produces generated migrations, snapshots, or
other churn. File counts and diffs are proxies for size, not difficulty or value.

Incomplete, unavailable, empty, and churn-only diffs are unscored. They are not
zero-size work. Coverage is scored PRs / merged PRs. Historical equivalence is
suppressed below the configured coverage threshold; above it, the reported known
units are still a subtotal. Compare file-only, line-only, and capped policies during
calibration. Do not select a policy solely because it gives a favorable AI result.

## Equivalent days and hours

```text
equivalent historical days = recent comparable scored units / reference units per day
capacity ratio = equivalent historical days / current period working days
modeled hours = equivalent historical days * declared constant hours per day
```

Each person uses their own eligible reference or the team fallback. A team report
sums the known person-level equivalents. It never applies one person's rate to
everyone. New repositories without matching history remain outside capacity
estimates and have visible unmatched counts. Collaboration stays alongside authored
output; there is no validated interaction-to-effort weight in this version.

An example statement is: "During these 3 working days, the recorded comparable output
was equivalent to 40 working days at this historical reference, under constant
availability." Add the sample, scope, weight policy, and missing-data coverage.
Do not shorten it to "AI saved 37 days." Work can span the window boundaries, and
prioritization, tooling, team roles, and release batching can change the result.

Inspect the mean, median, and mean without the largest reference contributor. An
outlier is a reason to examine work mix and data, not automatically remove a person.
Keep the historical reference fixed while inspecting monthly later results.

## Reviewed effort and private cost

Heuristic whole-PR draft ranges start at 1–3, 3–8, 8–24, and 24–60 hours by size
band. These are configurable proposals requiring individual admin review. Reviewers
consider discovery, implementation, test coverage, review, and integration. They
can override both bounds. Approvals record reviewer, timestamp, PR revision, and
rubric version. The latest rejection or draft removes a prior approval from totals.

`approved manual hours * scenario hourly rate` is a modeled manual cost. The default
USD 50 scenario is a round illustrative assumption, not a market-rate finding or
an employer compensation estimate. Imported actual USD spend appears separately.
Do not infer net savings or financial ROI without complete costs, an appropriate
counterfactual, and actual human effort. Hours are the primary public narrative.

GitHub's [code review billing documentation](https://docs.github.com/en/copilot/concepts/agents/code-review)
distinguishes manual requester attribution and automatic PR-author attribution.
Its [usage metrics reference](https://docs.github.com/en/copilot/reference/copilot-usage-metrics/copilot-usage-metrics)
distinguishes prompts, review flags, and generated activity. Preserve those boundaries
when importing current exports. Provider definitions can change; review them before
each integration update.
