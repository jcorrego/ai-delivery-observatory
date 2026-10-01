# Contributing

Use a dedicated worktree and a `feature/` or `bugfix/` branch. Include a concrete
before/after behavior, meaningful verification, and any evidence limitations in a PR.
Use sentence case in public documentation.

```sh
python3 -m unittest discover -s tests -v
python3 examples/generate.py
python3 -m observatory html --data examples/synthetic.json --config examples/config.json \
  --from 2026-01-01 --to 2026-09-30 --output docs/demo.html
```

Keep examples deterministic and fictional. Never open a PR with organization data,
tokens, real employee aliases, private PR titles, comment text, or generated exports
from real inputs. Test calculations independently of the dashboard and mock provider
responses. Explain source scope and unknown-data behavior when changing a metric.

The contribution should preserve observed events, reviewed estimates, and modeled
historical equivalence as separate measures. Add external dependencies only when
they solve a specific limitation and justify the operating cost.
