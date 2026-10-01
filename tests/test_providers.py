import unittest
from unittest.mock import patch
from urllib.request import Request

from observatory.providers import Client, SafeRedirect, bitbucket, collect, github, normalize_date


class FakeGitHub:
    def iter_pages(self, path, bitbucket=False):
        yield self.pages(path, bitbucket)

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
    def github_client(self, pages, max_pages=10, has_more=False):
        client = Client("https://api.github.com/", "synthetic", max_pages=max_pages)
        first = "repos/demo/service/pulls?state=all&sort=updated&direction=desc&per_page=100"
        urls = [first] + [client.base + first + f"&page={number}" for number in range(2, len(pages) + 2)]
        responses = {}
        for index, page in enumerate(pages):
            headers = {"Link": f'<{urls[index + 1]}>; rel="next"'} if index + 1 < len(pages) or has_more else {}
            responses[urls[index]] = (page, headers)
        requested = []
        fake = FakeGitHub()
        def get(path):
            requested.append(path)
            if "/pulls?" in path:
                return responses[path]
            if "?" in path:
                return fake.pages(path), {}
            return fake.get(path)
        client.get = get
        return client, requested, urls

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

    def test_github_mixed_boundary_page_is_processed_before_next_page(self):
        client, requested, urls = self.github_client([
            [{"number": 3, "updated_at": "2026-09-03T10:00:00Z"},
             {"number": 2, "updated_at": "2026-08-31T23:30:00Z"},
             {"number": 1, "updated_at": "2026-08-31T10:00:00Z"}],
            [{"number": 0, "updated_at": "2026-08-30T10:00:00Z"}],
        ], max_pages=2, has_more=True)
        prs, _, _, errors = github(client, "demo/service", "2026-09-01", "2026-09-02", "Europe/Madrid")
        self.assertEqual([p["key"] for p in prs], ["github:demo/service#3", "github:demo/service#2"])
        self.assertLess(requested.index("repos/demo/service/pulls/2"), requested.index(urls[1]))
        self.assertNotIn("repos/demo/service/pulls/1", requested)
        self.assertNotIn("repos/demo/service/pulls/0", requested)
        self.assertNotIn(urls[2], requested)
        self.assertEqual(errors, [])

    def test_github_wholly_old_page_satisfies_cutoff_at_page_limit(self):
        client, requested, urls = self.github_client([
            [{"number": 1, "updated_at": "2026-08-31T10:00:00Z"}],
        ], max_pages=1, has_more=True)
        with patch("observatory.providers.Client", return_value=client):
            result = collect("github", ["demo/service"], "synthetic", "2026-09-01", "2026-09-30")
        self.assertEqual(result["prs"], [])
        self.assertEqual(result["collection_errors"], [])
        self.assertEqual(result["coverage"][0]["status"], "complete")
        self.assertEqual(requested, [urls[0]])

    def test_github_page_limit_fails_when_requested_period_is_not_covered(self):
        for page in ([], [{"number": 2, "updated_at": "2026-09-01T10:00:00Z"},
                          {"number": 1, "updated_at": "2026-08-31T10:00:00Z"}]):
            with self.subTest(page=page):
                client, _, _ = self.github_client([page], max_pages=1, has_more=True)
                with patch("observatory.providers.Client", return_value=client):
                    result = collect("github", ["demo/service"], "synthetic", "2026-09-01", "2026-09-30")
                self.assertEqual(result["coverage"][0]["status"], "failed")
                self.assertEqual(result["collection_errors"][0]["detail"], "ValueError")

    def test_github_cutoff_uses_updated_order_not_creation_order(self):
        client, requested, urls = self.github_client([
            [{"number": 2, "created_at": "2025-01-01T10:00:00Z", "updated_at": "2026-09-02T10:00:00Z"}],
            [{"number": 1, "created_at": "2026-08-30T10:00:00Z", "updated_at": "2026-08-31T10:00:00Z"}],
        ], has_more=True)
        prs, _, _, errors = github(client, "demo/service", "2026-09-01", "2026-09-30", "UTC")
        self.assertEqual([p["key"] for p in prs], ["github:demo/service#2"])
        self.assertIn("state=all&sort=updated&direction=desc", requested[0])
        self.assertNotIn(urls[2], requested)
        self.assertEqual(errors, [])

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
