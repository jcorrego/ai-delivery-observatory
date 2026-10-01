import unittest
from unittest.mock import patch
from urllib.request import Request

from observatory.providers import Client, SafeRedirect, bitbucket, github, normalize_date


class FakeGitHub:
    def pages(self, path, bitbucket=False):
        if "/pulls?" in path:
            return [{"number": 42, "updated_at": "2026-09-03T10:00:00Z"}]
        if "/files?" in path:
            return [{"filename": "src/example.py", "additions": 3, "deletions": 2}]
        if "/reviews?" in path:
            return [{"id": 123, "submitted_at": "2026-09-02T10:00:00Z", "state": "COMMENTED", "user": {"login": "copilot[bot]", "type": "Bot"}}]
        if "/timeline?" in path:
            return [{"id": 456, "event": "review_requested", "requested_reviewer": {"login": "copilot[bot]"}, "actor": {"login": "requester", "type": "User"}, "created_at": "2026-09-02T09:00:00Z"}]
        if "/comments?" in path:
            return []
        raise AssertionError(path)

    def get(self, path):
        return {"number": 42, "user": {"login": "author", "type": "User"}, "title": "Synthetic PR", "html_url": "https://example.invalid/42",
                "created_at": "2026-09-01T10:00:00Z", "merged_at": "2026-09-03T10:00:00Z", "merged_by": {"login": "merger", "type": "User"},
                "head": {"sha": "synthetic-commit"}, "changed_files": 1}, {}


class ProviderContracts(unittest.TestCase):
    def test_timezone_normalization_preserves_local_day(self):
        self.assertEqual(normalize_date("2026-09-30T23:30:00Z", "Europe/Madrid"), "2026-10-01")

    def test_github_requester_is_separate_from_author_and_zero_findings_count(self):
        prs, events, cycles, errors = github(FakeGitHub(), "demo/service", "2025-01-01", "2026-09-30", "UTC")
        self.assertEqual(prs[0]["detail_status"], "complete")
        self.assertEqual(cycles[0]["sponsor"], "github:requester")
        self.assertEqual(cycles[0]["comments"], 0)
        self.assertTrue(cycles[0]["completed"])
        self.assertEqual(cycles[0]["attribution"], "manual_request")
        self.assertTrue(any(e["kind"] == "merge" and e["actor"] == "github:merger" for e in events))
        self.assertEqual(errors, [])

    def test_missing_review_request_stays_unresolved(self):
        fake = FakeGitHub()
        original = fake.pages
        fake.pages = lambda path, bitbucket=False: [] if "/timeline?" in path else original(path, bitbucket)
        cycles = github(fake, "demo/service", "2025-01-01", "2026-09-30", "UTC")[2]
        self.assertEqual(cycles[0]["attribution"], "unresolved")
        self.assertNotIn("sponsor", cycles[0])

    def test_file_cap_does_not_claim_complete_diff(self):
        fake = FakeGitHub()
        original = fake.get
        def incomplete(path):
            data, headers = original(path)
            data["changed_files"] = 3001
            return data, headers
        fake.get = incomplete
        prs, _, _, errors = github(fake, "demo/service", "2025-01-01", "2026-09-30", "UTC")
        self.assertEqual(prs[0]["detail_status"], "partial")
        self.assertFalse(errors[0]["blocking"])

    def test_changed_origin_is_blocked_before_request(self):
        client = Client("https://api.github.com/", "synthetic-not-a-real-token")
        with self.assertRaises(ValueError):
            client.get("https://foreign.example.invalid/stolen")
        with self.assertRaises(ValueError):
            client.get("https://api.github.com@foreign.example.invalid/path")

    def test_changed_origin_redirect_never_forwards_credentials(self):
        request = Request("https://api.github.com/start", headers={"Authorization": "Bearer synthetic"})
        with self.assertRaises(ValueError):
            SafeRedirect().redirect_request(request, None, 302, "Found", {}, "https://foreign.example.invalid/")

    def test_repeated_or_truncated_pagination_fails(self):
        client = Client("https://api.github.com/", "synthetic", max_pages=1)
        client.get = lambda url: ([{"id": 1}], {"Link": '<https://api.github.com/next>; rel="next"'})
        with self.assertRaisesRegex(ValueError, "limit"):
            client.pages("start")
        client.max_pages = 3
        with self.assertRaisesRegex(ValueError, "loop"):
            client.pages("start")

    def test_bitbucket_merge_time_comes_from_activity_and_comment_bodies_are_omitted(self):
        class Fake:
            def pages(self, path, bitbucket=False):
                if "/diffstat?" in path:
                    return [{"new": {"path": "src/example.py"}, "lines_added": 8, "lines_removed": 1}]
                if "/activity?" in path:
                    return [{"update": {"state": "MERGED", "date": "2026-09-03T10:00:00Z", "author": {"uuid": "{merger}"}}},
                            {"update": {"state": "MERGED", "date": "2026-09-05T10:00:00Z", "author": {"uuid": "{merger}"}}}]
                if "/comments?" in path:
                    return [{"id": 99, "user": {"uuid": "{reviewer}"}, "created_on": "2026-09-02T10:00:00Z", "content": {"raw": "PRIVATE BODY"}}]
                return [{"id": 42, "state": "MERGED", "author": {"uuid": "{author}"}, "title": "Synthetic",
                         "created_on": "2026-09-01T10:00:00Z", "updated_on": "2026-09-20T10:00:00Z",
                         "source": {"commit": {"hash": "synthetic"}}, "links": {"html": {"href": "https://example.invalid/42"}}}]
        prs, events, _, errors = bitbucket(Fake(), "demo/service", "2025-01-01", "2026-09-30", "UTC")
        self.assertEqual(prs[0]["merged"], "2026-09-03")
        self.assertEqual(sum(e["kind"] == "merge" for e in events), 1)
        self.assertNotIn("PRIVATE BODY", str(events))
        self.assertEqual(errors, [])
