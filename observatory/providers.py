"""Read-only provider adapters. Tokens never enter exported records."""

from datetime import date, datetime, timedelta, timezone
import json
import re
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, quote
from urllib.request import Request, HTTPRedirectHandler, build_opener
from zoneinfo import ZoneInfo


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urljoin(req.full_url, newurl)
        old, new = urlsplit(req.full_url), urlsplit(target)
        if (old.scheme, old.netloc) != (new.scheme, new.netloc):
            raise ValueError("Provider redirect changed origin; credentials were not forwarded")
        return super().redirect_request(req, fp, code, msg, headers, target)


class Client:
    def __init__(self, base, token, max_pages=1000):
        origin = urlsplit(base)
        if origin.scheme != "https" or origin.username or origin.password or not origin.hostname:
            raise ValueError("Provider API requires an HTTPS URL without embedded credentials")
        self.base, self.token, self.max_pages = base.rstrip("/") + "/", token, max_pages
        self.origin = (origin.scheme, origin.netloc)
        self.opener = build_opener(SafeRedirect())

    def get(self, url):
        url = urljoin(self.base, url)
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc) != self.origin or parsed.username or parsed.password:
            raise ValueError("Pagination URL changed origin")
        headers = {"Accept": "application/json", "User-Agent": "ai-delivery-observatory/0.1"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if parsed.hostname == "api.github.com":
            headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        for attempt in range(3):
            try:
                with self.opener.open(Request(url, headers=headers, method="GET"), timeout=30) as response:
                    return json.load(response), response.headers
            except HTTPError as error:
                if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(attempt + 1)
                    continue
                raise RuntimeError(f"Provider GET failed with HTTP {error.code}; inspect access or rate limits") from None
            except (URLError, TimeoutError):
                raise RuntimeError("Provider connection failed; collection is incomplete") from None
        raise RuntimeError("Provider request failed")

    def iter_pages(self, path, bitbucket=False):
        seen, url = set(), path
        for _ in range(self.max_pages):
            if url in seen:
                raise ValueError("Provider pagination loop")
            seen.add(url)
            data, headers = self.get(url)
            yield data.get("values", []) if bitbucket else data
            match = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link", ""))
            url = data.get("next") if bitbucket else match.group(1) if match else None
            if not url:
                return
        raise ValueError("Pagination limit reached; collection is incomplete")

    def pages(self, path, bitbucket=False):
        return [item for page in self.iter_pages(path, bitbucket) for item in page]


def normalize_date(value, tz):
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(ZoneInfo(tz)).date().isoformat()


def actor(user, platform):
    value = (user or {}).get("login") if platform == "github" else (user or {}).get("uuid") or (user or {}).get("account_id")
    return platform + ":" + str(value or "unknown")


def github_updates(client, path, since, tz):
    # The request sorts by updated time descending. Examine the whole boundary
    # page before stopping, and do not treat an empty page as a dated cutoff.
    for page in client.iter_pages(path):
        relevant = [item for item in page if normalize_date(item["updated_at"], tz) >= since]
        if page and not relevant:
            return
        yield from relevant


def github(client, repository, since, until, tz):
    owner, repo = repository.split("/", 1)
    base = f"repos/{quote(owner, safe='')}/{quote(repo, safe='')}"
    raw = github_updates(client, base + "/pulls?state=all&sort=updated&direction=desc&per_page=100", since, tz)
    prs, events, cycles, errors = [], [], [], []
    for item in raw:
        number = item["number"]
        key = f"github:{repository}#{number}"
        detail, _ = client.get(base + f"/pulls/{number}")
        pr = {"key": key, "platform": "github", "repository": repository,
              "author": actor(detail["user"], "github"), "author_bot": detail["user"].get("type") == "Bot",
              "title": detail["title"], "url": detail["html_url"],
              "created": normalize_date(detail["created_at"], tz), "merged": normalize_date(detail.get("merged_at"), tz),
              "revision": detail["head"]["sha"], "detail_status": "partial", "files": []}
        prs.append(pr)
        try:
            files = client.pages(base + f"/pulls/{number}/files?per_page=100")
            pr["files"] = [{"path": f["filename"], "added": f["additions"], "removed": f["deletions"]} for f in files]
            pr["detail_status"] = "complete" if len(files) == detail["changed_files"] else "partial"
            if pr["detail_status"] == "partial":
                errors.append({"pr": key, "detail": "File listing did not match changed_files", "blocking": False})
        except (RuntimeError, ValueError) as error:
            errors.append({"pr": key, "detail": str(error), "blocking": False})
        reviews = client.pages(base + f"/pulls/{number}/reviews?per_page=100")
        comments = client.pages(base + f"/pulls/{number}/comments?per_page=100")
        comments += client.pages(base + f"/issues/{number}/comments?per_page=100")
        requests = client.pages(base + f"/issues/{number}/timeline?per_page=100")
        for review in reviews:
            if not review.get("submitted_at") or review["state"] == "PENDING":
                continue
            login = actor(review["user"], "github")
            events.append({"id": f"{key}:review:{review['id']}", "pr": key, "actor": login,
                           "actor_bot": review["user"].get("type") == "Bot",
                           "kind": "approval" if review["state"] == "APPROVED" else "review",
                           "date": normalize_date(review["submitted_at"], tz)})
            if "copilot" in login.casefold() or "coderabbit" in login.casefold():
                matching = [r for r in requests if r.get("event") == "review_requested" and
                            actor(r.get("requested_reviewer"), "github").casefold() == login.casefold() and
                            r.get("created_at", "") <= review["submitted_at"] and r.get("actor", {}).get("type") != "Bot"]
                request = max(matching, key=lambda r: r["created_at"]) if matching else None
                inline = [c for c in comments if c.get("pull_request_review_id") == review["id"]]
                cycle = {"id": f"{key}:ai-review:{review['id']}", "pr": key, "bot": login,
                         "date": normalize_date(review["submitted_at"], tz), "completed": True,
                         "comments": len(inline), "suggestions": sum("```suggestion" in c.get("body", "") for c in inline),
                         "applied_suggestions": None, "attribution": "unresolved"}
                if request:
                    cycle.update({"sponsor": actor(request["actor"], "github"), "attribution": "manual_request",
                                  "evidence": {"source": "github_review_requested_event", "event_id": str(request["id"]),
                                               "requested_at": request["created_at"]}})
                cycles.append(cycle)
        for comment in comments:
            namespace = "inline" if "pull_request_review_id" in comment else "issue"
            events.append({"id": f"{key}:{namespace}-comment:{comment['id']}", "pr": key,
                           "actor": actor(comment.get("user"), "github"), "actor_bot": comment.get("user", {}).get("type") == "Bot",
                           "kind": "comment", "date": normalize_date(comment["created_at"], tz)})
        if pr["merged"] and detail.get("merged_by"):
            events.append({"id": f"{key}:merge", "pr": key, "actor": actor(detail["merged_by"], "github"),
                           "actor_bot": detail["merged_by"].get("type") == "Bot", "kind": "merge", "date": pr["merged"]})
    return prs, events, cycles, errors


def bitbucket(client, repository, since, until, tz):
    workspace, repo = repository.split("/", 1)
    base = f"repositories/{quote(workspace, safe='')}/{quote(repo, safe='')}/pullrequests"
    lookback = (date.fromisoformat(since) - timedelta(days=1)).isoformat()
    query = urlencode({"pagelen": 50, "q": f'updated_on >= "{lookback}"', "sort": "-updated_on",
                       "state": ["OPEN", "MERGED", "DECLINED", "SUPERSEDED"]}, doseq=True)
    raw = client.pages(base + "?" + query, True)
    prs, events, errors = [], [], []
    for item in raw:
        key = f"bitbucket:{repository}#{item['id']}"
        path = base + "/" + str(item["id"])
        pr = {"key": key, "platform": "bitbucket", "repository": repository,
              "author": actor(item["author"], "bitbucket"), "title": item["title"],
              "url": item["links"]["html"]["href"], "created": normalize_date(item["created_on"], tz),
              "merged": None, "revision": item["source"]["commit"]["hash"], "files": [], "detail_status": "partial"}
        prs.append(pr)
        try:
            files = client.pages(path + "/diffstat?pagelen=100", True)
            pr["files"] = [{"path": (f.get("new") or f.get("old"))["path"], "added": f["lines_added"],
                            "removed": f["lines_removed"]} for f in files]
            pr["detail_status"] = "complete"
        except (RuntimeError, ValueError, TypeError, KeyError) as error:
            errors.append({"pr": key, "detail": type(error).__name__ + ": file comparison unavailable", "blocking": False})
        activity = client.pages(path + "/activity?pagelen=100", True)
        merge_updates = []
        for entry in activity:
            if entry.get("approval"):
                approval = entry["approval"]
                events.append({"id": f"{key}:approval:{approval['date']}:{actor(approval['user'], 'bitbucket')}",
                               "pr": key, "actor": actor(approval["user"], "bitbucket"), "kind": "approval",
                               "date": normalize_date(approval["date"], tz)})
            update = entry.get("update", {})
            if update.get("state") == "MERGED":
                merge_updates.append(update)
        if merge_updates:
            update = min(merge_updates, key=lambda u: u["date"])
            pr["merged"] = normalize_date(update["date"], tz)
            events.append({"id": f"{key}:merge", "pr": key, "actor": actor(update.get("author") or item.get("closed_by"), "bitbucket"),
                           "kind": "merge", "date": pr["merged"]})
        comments = client.pages(path + "/comments?pagelen=100", True)
        for comment in comments:
            if not comment.get("deleted"):
                events.append({"id": f"{key}:comment:{comment['id']}", "pr": key, "actor": actor(comment["user"], "bitbucket"),
                               "kind": "comment", "date": normalize_date(comment["created_on"], tz)})
        if item["state"] == "MERGED" and not pr["merged"]:
            errors.append({"pr": key, "detail": "Merge event unavailable; updated_on is not substituted", "blocking": True})
            pr["merge_date_unknown"] = True
    return prs, events, [], errors


def collect(platform, repositories, token, since, until, tz="UTC", max_pages=1000):
    base = "https://api.github.com/" if platform == "github" else "https://api.bitbucket.org/2.0/"
    client = Client(base, token, max_pages)
    output = {"schema_version": 1, "synthetic": False, "prs": [], "events": [], "ai_reviews": [],
              "usage": [], "effort": [], "coverage": [], "collection_errors": [],
              "collected_at": datetime.now(timezone.utc).isoformat(), "timezone": tz}
    adapter = github if platform == "github" else bitbucket
    for repository in repositories:
        try:
            prs, events, cycles, errors = adapter(client, repository, since, until, tz)
            output["prs"].extend(prs)
            output["events"].extend(events)
            output["ai_reviews"].extend(cycles)
            output["collection_errors"].extend(errors)
            output["coverage"].append({"repository": repository, "from": since, "to": until,
                                       "status": "partial" if any(e.get("blocking", True) for e in errors) else "complete",
                                       "details_status": "partial" if errors else "complete"})
        except (RuntimeError, ValueError, KeyError) as error:
            output["collection_errors"].append({"repository": repository, "detail": type(error).__name__})
            output["coverage"].append({"repository": repository, "from": since, "to": until, "status": "failed"})
    return output
