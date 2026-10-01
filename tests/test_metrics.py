import copy
from datetime import date
import json
from pathlib import Path
import unittest

from observatory.metrics import report, working_days
from observatory.model import Identities, day, size_units, validate
from observatory.storage import disposition, draft, merge_snapshots

ROOT = Path(__file__).parents[1]


class MetricContracts(unittest.TestCase):
    def setUp(self):
        self.data = json.loads((ROOT / "examples/synthetic.json").read_text())
        self.config = json.loads((ROOT / "examples/config.json").read_text())

    def result(self, **kwargs):
        return report(self.data, self.config, "2026-09-01", "2026-09-30", **kwargs)

    def test_calendar_uses_all_working_days_including_zero_output(self):
        self.assertEqual(working_days(date(2025, 1, 1), date(2025, 12, 31)), 261)
        self.assertEqual(working_days(date(2026, 1, 1), date(2026, 9, 30)), 195)
        self.assertEqual(working_days(date(2025, 1, 1), date(2025, 1, 3), ["2025-01-01"]), 2)

    def test_neutral_date_fields_reject_compact_and_week_dates(self):
        self.config["people"][0]["history_to"] = "2026-09-30"
        self.config["holidays"] = ["2026-09-01"]
        fields = (
            ("data", ("prs", 0, "created")),
            ("data", ("prs", 0, "merged")),
            ("data", ("events", 0, "date")),
            ("data", ("usage", 0, "date")),
            ("data", ("ai_reviews", 0, "date")),
            ("data", ("coverage", 0, "from")),
            ("data", ("coverage", 0, "to")),
            ("config", ("baseline", "from")),
            ("config", ("baseline", "to")),
            ("config", ("people", 0, "history_from")),
            ("config", ("people", 0, "history_to")),
            ("config", ("holidays", 0)),
        )
        for source, path in fields:
            for representation in ("compact", "week"):
                with self.subTest(source=source, field=path, representation=representation):
                    data, config = copy.deepcopy(self.data), copy.deepcopy(self.config)
                    record = {"data": data, "config": config}[source]
                    for key in path[:-1]:
                        record = record[key]
                    original = date.fromisoformat(record[path[-1]])
                    year, week, weekday = original.isocalendar()
                    record[path[-1]] = (original.isoformat().replace("-", "") if representation == "compact"
                                        else f"{year:04d}-W{week:02d}-{weekday}")
                    with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                        validate(data, config)

    def test_report_period_rejects_noncanonical_dates(self):
        for start, end in (("20260901", "2026-09-30"),
                           ("2026-W36-2", "2026-09-30"),
                           ("2026-09-01", "20260930"),
                           ("2026-09-01", "2026-W40-3")):
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
                    report(self.data, self.config, start, end)

    def test_canonical_dates_still_require_valid_calendar_days(self):
        for value in ("1900-02-29", "2023-02-29", "2024-04-31", "2024-13-01"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    day(value)
        self.assertEqual(day("2000-02-29"), date(2000, 2, 29))
        self.assertEqual(day("2024-02-29"), date(2024, 2, 29))

    def test_leap_day_pr_is_counted_in_daily_and_monthly_output(self):
        self.data["prs"][0].update(created="2024-02-29", merged="2024-02-29")
        result = report(self.data, self.config, "2024-02-29", "2024-02-29")
        self.assertEqual(result["daily"][0]["date"], "2024-02-29")
        self.assertEqual(result["daily"][0]["created"], 1)
        self.assertEqual(result["daily"][0]["merged"], 1)
        self.assertEqual(result["monthly"][0]["month"], "2024-02")
        self.assertEqual(result["monthly"][0]["merged"], 1)

    def test_human_output_excludes_bots_and_deduplicates_collaboration(self):
        r = self.result()
        self.assertEqual(r["summary"]["merged"], 48)
        self.assertEqual(r["summary"]["collaboration"], 42)
        self.assertEqual(r["summary"]["interaction_events"], 126)
        self.assertEqual(len(r["daily"]), 30)
        self.assertEqual(sum(d["merged"] for d in r["daily"]), 48)
        self.assertEqual(r["automation"][0]["category"], "Code assistance and review")
        dependency = next(a for a in r["automation"] if a["category"] == "Dependency updates")
        self.assertEqual(dependency["created"], 8)

    def test_confirmed_aliases_join_platforms_and_billing(self):
        ids = Identities(self.config)
        self.assertEqual(ids.person("GITHUB:AVERY-DEMO"), ids.person("bitbucket:{synthetic-avery}"))
        r = self.result(person="avery")
        self.assertEqual(r["summary"]["merged"], 24)
        self.assertEqual(r["usage"]["github-copilot"]["credits"], 22 * 235)

    def test_duplicate_alias_ownership_is_rejected(self):
        self.config["people"][1]["aliases"].append("github:avery-demo")
        with self.assertRaises(ValueError):
            self.result()

    def test_existing_human_alias_wins_over_bot_name_heuristic(self):
        self.config["people"][0]["aliases"].append("github:copilot-helper")
        self.assertIsNone(Identities(self.config).category("github:copilot-helper"))

    def test_size_larger_band_wins_and_caps_extreme_changes(self):
        def pr(files, lines):
            return {"detail_status": "complete", "files": [{"path": f"src/{i}.py", "added": lines if i == 0 else 0, "removed": 0} for i in range(files)]}
        self.assertEqual(size_units(pr(4, 300)), 2)
        self.assertEqual(size_units(pr(20, 100)), 3)
        self.assertEqual(size_units(pr(1, 10)), .5)
        self.assertEqual(size_units(pr(1, 11)), 1)
        self.assertEqual(size_units(pr(5, 170)), 1)
        self.assertEqual(size_units(pr(5, 171)), 2)
        self.assertEqual(size_units(pr(100, 100000)), 3)

    def test_unavailable_and_churn_only_diffs_are_not_zero_work(self):
        self.assertIsNone(size_units({"detail_status": "partial", "files": []}))
        self.assertIsNone(size_units({"detail_status": "complete", "files": [{"path": "package-lock.json", "added": 8000, "removed": 0}]}))
        self.assertEqual(size_units({"detail_status": "complete", "files": [{"path": "config/site.yml", "added": 8, "removed": 0}]}), .5)

    def test_personal_reference_and_new_person_fallback(self):
        r = self.result()
        baseline = {p["id"]: p for p in r["baseline"]["people"]}
        self.assertAlmostEqual(baseline["avery"]["raw_daily_prs"], 120 / 261)
        self.assertEqual(baseline["avery"]["months"], 12)
        self.assertTrue(baseline["avery"]["personal_eligible"])
        self.assertFalse(baseline["casey"]["personal_eligible"])
        self.assertFalse(baseline["drew"]["personal_eligible"])
        self.assertTrue(baseline["drew"]["in_team_reference"])
        self.assertAlmostEqual(r["baseline"]["team_daily_units"], (126 + 60 + 3) / 261 / 3)
        casey = next(p for p in r["people"] if p["id"] == "casey")
        self.assertIn("Team reference", casey["reference"])

    def test_partial_collection_cannot_create_complete_baseline(self):
        self.data["coverage"][0]["status"] = "failed"
        r = self.result()
        self.assertIsNone(r["baseline"]["team_daily_units"])
        self.assertIsNone(r["summary"]["historical_equivalent_hours"])

    def test_diff_coverage_gate_and_baseline_version(self):
        before = self.result()
        for p in self.data["prs"]:
            if p["author"] == "bitbucket:{synthetic-avery}" and p["merged"].startswith("2025-01"):
                p["detail_status"] = "partial"
        after = self.result()
        avery = next(p for p in after["baseline"]["people"] if p["id"] == "avery")
        self.assertFalse(avery["personal_eligible"])
        self.assertNotEqual(before["baseline"]["version"], after["baseline"]["version"])

    def test_unmatched_repo_never_enters_historical_equivalence(self):
        r = self.result(person="avery")
        avery = r["people"][0]
        self.assertEqual(avery["merged"], 24)
        self.assertEqual(avery["comparable_prs"], 18)
        self.assertEqual(avery["unmatched_prs"], 6)
        r = self.result(person="avery", repositories=["demo-org/experimental"])
        self.assertIsNone(r["people"][0]["equivalent_days"])
        self.assertIsNone(r["summary"]["historical_equivalent_hours"])

    def test_constant_availability_formula_is_explicit(self):
        r = self.result(person="avery")
        person = r["people"][0]
        self.assertAlmostEqual(person["equivalent_days"], person["weighted_units"] / (126 / 261))
        self.assertAlmostEqual(person["equivalent_hours"], person["equivalent_days"] * 8)
        self.assertTrue(r["assumptions"]["capacity_is_estimate"])

    def test_sponsored_review_does_not_become_a_human_comment(self):
        r = self.result(person="avery")
        self.assertEqual(r["summary"]["ai_reviews"], 18)
        self.assertEqual(r["summary"]["interaction_events"], 36)
        self.assertTrue(any(c["comments"] == 0 for c in r["ai_review_work"]))
        self.assertEqual(self.result()["summary"]["attributed_ai_reviews"], 42)

    def test_manual_attribution_without_evidence_is_unresolved(self):
        self.data["ai_reviews"][0].pop("evidence")
        self.assertEqual(self.result()["summary"]["attributed_ai_reviews"], 41)

    def test_automatic_sponsor_must_be_author(self):
        self.data["ai_reviews"][0]["sponsor"] = "github:blair-demo"
        self.assertEqual(self.result()["summary"]["attributed_ai_reviews"], 41)

    def test_enterprise_org_usage_overlap_is_not_double_counted(self):
        usage = self.result()["usage"]["github-copilot"]
        self.assertEqual(usage["credits"], 22 * (235 + 58 + 58))
        self.assertIsNone(usage["tokens"])
        self.assertEqual(usage["prompts"], 0)
        self.assertEqual(usage["code_review_active"], 22)

    def test_conflicting_same_priority_usage_fails(self):
        row = copy.deepcopy(self.data["usage"][0])
        row["credits"] += 10
        self.data["usage"].append(row)
        with self.assertRaises(ValueError):
            self.result()

    def test_repository_filter_does_not_allocate_unattributed_spend(self):
        r = self.result(repositories=["demo-org/core"])
        self.assertEqual(r["usage"], {})
        self.assertGreater(r["quality"]["unallocated_usage_rows"], 0)

    def test_drafts_and_changed_revisions_are_excluded_from_approved_totals(self):
        before = self.result()
        self.assertEqual(before["summary"]["approved_prs"], 16)
        key = self.data["effort"][0]["pr"]
        next(p for p in self.data["prs"] if p["key"] == key)["revision"] = "new-revision"
        after = self.result()
        self.assertEqual(after["summary"]["approved_prs"], 15)
        self.assertEqual(after["summary"]["stale_effort"], 1)

    def test_rubric_change_invalidates_all_prior_approvals(self):
        self.config["effort_rubric_version"] = "new-rubric"
        self.assertEqual(self.result()["summary"]["approved_prs"], 0)

    def test_later_rejection_removes_approval_without_deleting_audit(self):
        effort = self.data["effort"][0]
        count = len(self.data["effort"])
        disposition(self.data, self.config, effort["pr"], "demo-admin", "rejected", 3, 8, effort["revision"])
        self.assertEqual(len(self.data["effort"]), count + 1)
        self.assertEqual(self.data["effort"][0]["status"], "approved")
        self.assertEqual(self.result()["summary"]["approved_prs"], 15)

    def test_unconfigured_reviewer_or_unseen_revision_is_rejected(self):
        e = self.data["effort"][0]
        with self.assertRaises(ValueError):
            disposition(self.data, self.config, e["pr"], "outsider", "approved", 3, 8, e["revision"])
        with self.assertRaises(ValueError):
            disposition(self.data, self.config, e["pr"], "demo-admin", "approved", 3, 8, "unseen")

    def test_draft_is_unapproved_and_does_not_rewrite_existing_audit(self):
        before = self.result()["summary"]["approved_prs"]
        self.assertGreater(draft(self.data, self.config), 0)
        self.assertEqual(self.result()["summary"]["approved_prs"], before)

    def test_duplicates_negative_values_and_nonfinite_values_rejected(self):
        for invalid in (-1, float("nan"), float("inf"), True):
            with self.subTest(invalid=invalid):
                data = copy.deepcopy(self.data)
                data["prs"][0]["files"][0]["added"] = invalid
                with self.assertRaises(ValueError):
                    validate(data, self.config)
        self.data["events"].append(self.data["events"][0])
        with self.assertRaises(ValueError):
            self.result()

    def test_merge_snapshots_is_idempotent_and_keeps_dispositions(self):
        result = merge_snapshots([self.data, self.data])
        self.assertEqual(len(result["prs"]), len(self.data["prs"]))
        self.assertEqual(len(result["events"]), len(self.data["events"]))
        self.assertEqual(len(result["effort"]), len(self.data["effort"]))
        validate(result, self.config)

    def test_unknown_person_and_reversed_dates_rejected(self):
        with self.assertRaises(ValueError):
            self.result(person="unknown")
        with self.assertRaises(ValueError):
            report(self.data, self.config, "2026-10-01", "2026-09-30")

    def test_baseline_requires_complete_month_boundaries(self):
        self.config["baseline"]["to"] = "2025-12-15"
        with self.assertRaises(ValueError):
            self.result()

    def test_export_payload_never_executes_titles_as_markup(self):
        import tempfile
        from observatory.cli import export_html
        self.data["prs"][-9]["title"] = '</script><script>alert("fixture")</script>'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "report.html"
            export_html(self.data, self.config, "2026-09-01", "2026-09-30", path)
            text = path.read_text()
            self.assertNotIn('</script><script>alert("fixture")', text)
            self.assertIn('\\u003c/script>', text)
