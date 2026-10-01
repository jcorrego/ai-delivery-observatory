"""Generate deterministic fictional organization records. No network or private input."""

from datetime import date, timedelta
import json
from pathlib import Path

ROOT = Path(__file__).parent
PEOPLE = [("avery", "Avery Chen", "2024-01-01"), ("blair", "Blair Morgan", "2024-01-01"),
          ("casey", "Casey Reed", "2026-06-01"), ("drew", "Drew Patel", "2024-01-01")]
config = {"schema_version": 1, "organization": "Example Engineering Organization",
          "people": [{"id": pid, "name": name, "history_from": start,
                      "aliases": [f"github:{pid}-demo", f"bitbucket:{{synthetic-{pid}}}", f"email:{pid}@example.invalid"]}
                     for pid, name, start in PEOPLE],
          "admins": [{"id": "demo-admin", "token_env": "OBSERVATORY_ADMIN_TOKEN"}],
          "bots": {"bitbucket:{synthetic-pipeline}": "CI and deployment"},
          "baseline": {"from": "2025-01-01", "to": "2025-12-31", "label": "2025 historical reference",
                       "repositories": ["demo-org/core"], "min_prs": 20, "min_months": 3, "min_diff_coverage": .95},
          "size": {"version": "trial-size-v1", "bands": [[1, 10, .5], [5, 170, 1], [18, 750, 2]], "cap": 3},
          "hours_per_day": 8, "holidays": [], "effort_rubric_version": "starter-effort-v1"}
data = {"schema_version": 1, "synthetic": True, "prs": [], "events": [], "ai_reviews": [], "usage": [], "effort": [],
        "coverage": [{"repository": "demo-org/core", "from": "2025-01-01", "to": "2026-09-30", "status": "complete", "details_status": "complete"},
                     {"repository": "demo-org/experimental", "from": "2026-04-01", "to": "2026-09-30", "status": "complete", "details_status": "complete"}]}


def add_pr(pid, value, units, platform="bitbucket", repo="demo-org/core", bot=False):
    index = len(data["prs"]) + 1
    key = f"{platform}:{repo}#{index}"
    files = {0.5: 1, 1: 3, 2: 8, 3: 22}[units]
    added = {0.5: 4, 1: 16, 2: 30, 3: 50}[units]
    author = pid if bot else f"{platform}:" + (f"{pid}-demo" if platform == "github" else "{synthetic-" + pid + "}")
    pr = {"key": key, "platform": platform, "repository": repo, "author": author, "author_bot": bot,
          "title": f"Synthetic change {index}: improve example workflow", "created": (value - timedelta(days=2)).isoformat(),
          "merged": value.isoformat(), "revision": f"synthetic-revision-{index}", "detail_status": "complete",
          "files": [{"path": f"src/example_{n}.py", "added": added, "removed": 2} for n in range(files)]}
    data["prs"].append(pr)
    return pr


for year in (2025, 2026):
    for month in range(1, 13 if year == 2025 else 9):
        for pid, count in (("avery", 10 if year == 2025 else 13), ("blair", 5 if year == 2025 else 7), ("drew", 1 if month % 2 == 0 else 0)):
            for n in range(count):
                add_pr(pid, date(year, month, 3 + n * 2), (.5, 1, 1, 2)[n % 4])

for pid, count in (("avery", 18), ("blair", 12), ("casey", 8), ("drew", 4)):
    for n in range(count):
        pr = add_pr(pid, date(2026, 9, 3 + n), (1, 1, 2, 3)[n % 4], "github")
        reviewer = "blair" if pid != "blair" else "avery"
        for kind in ("approval", "comment", "merge"):
            data["events"].append({"id": pr["key"] + ":" + kind, "pr": pr["key"], "actor": f"github:{reviewer}-demo", "kind": kind, "date": pr["merged"]})
        bot = "github:Copilot" if n % 2 else "github:copilot-pull-request-reviewer[bot]"
        for i in range(3):
            data["events"].append({"id": f"{pr['key']}:bot-comment:{i}", "pr": pr["key"], "actor": bot, "actor_bot": True, "kind": "comment", "date": pr["merged"]})
        data["ai_reviews"].append({"id": pr["key"] + ":ai-review", "pr": pr["key"], "bot": bot, "sponsor": f"github:{pid}-demo",
                    "attribution": "manual_request" if n % 2 else "automatic_author_policy", "evidence": {"source": "synthetic request ledger", "id": pr["key"]},
                    "completed": True, "date": pr["merged"], "comments": 3 if n % 2 else 0, "suggestions": 2 if n % 2 else 0, "applied_suggestions": None})
        if n < 4:
            data["effort"].append({"pr": pr["key"], "revision": pr["revision"], "rubric_version": "starter-effort-v1",
                    "low_hours": 3, "high_hours": 8, "status": "approved", "approved_by": "demo-admin", "approved_at": "2026-09-30T12:00:00Z"})
        elif n < 7:
            data["effort"].append({"pr": pr["key"], "revision": pr["revision"], "rubric_version": "starter-effort-v1", "low_hours": 3, "high_hours": 8, "status": "draft"})
for n in range(6):
    add_pr("avery", date(2026, 9, 22 + n), 2, "github", "demo-org/experimental")
for n in range(8):
    add_pr("github:dependabot" if n % 2 else "github:dependabot[bot]", date(2026, 9, 3 + n), .5, "github", bot=True)
pr = data["prs"][-1]
data["ai_reviews"].append({"id": "synthetic-unresolved-review", "pr": pr["key"], "bot": "github:copilot[bot]", "attribution": "unresolved", "completed": True,
                         "date": "2026-09-25", "comments": 4, "suggestions": 2, "applied_suggestions": None})
for pid in ("avery", "blair", "casey", "drew"):
    for n in range(30):
        d = date(2026, 9, 1) + timedelta(days=n)
        if d.weekday() >= 5:
            continue
        usage = {"date": d.isoformat(), "actor": f"github:{pid}-demo", "provider": "github-copilot", "scope": "enterprise",
                 "credits": 235 if pid == "avery" else 58 if pid != "drew" else 0, "tokens": None, "prompts": 0,
                 "cost_usd": None, "code_review_active": pid == "avery", "code_review_passive": pid != "drew"}
        data["usage"].extend([usage, {**usage, "scope": "organization"}])
for name, value in (("config.json", config), ("synthetic.json", data)):
    (ROOT / name).write_text(json.dumps(value, indent=2) + "\n")
print(f"Generated {len(data['prs'])} fictional PRs")
