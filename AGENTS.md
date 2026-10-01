# Working on AI Delivery Observatory

Use a dedicated worktree and a feature or bugfix branch. Run
`python3 -m unittest discover -s tests -v` and regenerate the synthetic demo before
opening a PR. Keep the calculation engine independent of the UI and providers.

Only synthetic fixtures belong in this repository. Do not commit real usage,
employee identities, private repository metadata, credentials, or employer code.
Do not label historical capacity estimates as measured hours saved. Preserve
unknown data as unknown, count distinct PRs separately from events, and require
review evidence before an effort draft enters approved totals.
