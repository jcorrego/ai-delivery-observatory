"""Neutral records, explicit identity linking, and a versioned size index."""

from datetime import date
import fnmatch
import hashlib
import json
import math

EXCLUDED = ("**/node_modules/**", "node_modules/**", "**/vendor/**", "vendor/**",
            "**/dist/**", "dist/**", "**/build/**", "build/**", "**/coverage/**",
            "coverage/**", "**/.next/**", ".next/**", "*.map", "*.min.js", "*.min.css",
            "**/package-lock.json", "package-lock.json", "**/yarn.lock", "yarn.lock",
            "**/pnpm-lock.yaml", "pnpm-lock.yaml", "**/composer.lock", "composer.lock")
BOTS = {
    "Code assistance and review": ("coderabbit", "copilot", "robodev"),
    "Dependency updates": ("dependabot", "renovate"),
    "CI and deployment": ("github-actions", "tugboat", "vercel"),
    "Security checks": ("github-advanced-security", "snyk", "sonar"),
}


def day(value):
    """Dates in neutral records are already normalized to the organization's timezone."""
    if not isinstance(value, str):
        raise ValueError("Expected an ISO date string")
    return date.fromisoformat(value)


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{label} must be finite and non-negative")
    return value


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:16]


class Identities:
    def __init__(self, config):
        self.people = {p["id"]: p for p in config["people"]}
        if len(self.people) != len(config["people"]):
            raise ValueError("Person IDs must be unique")
        self.aliases = {}
        for person in config["people"]:
            for alias in person["aliases"]:
                key = alias.casefold()
                if key in self.aliases:
                    raise ValueError(f"Alias has multiple owners: {alias}")
                self.aliases[key] = person["id"]
        self.bots = {key.casefold(): value for key, value in config.get("bots", {}).items()}

    def person(self, actor):
        return self.aliases.get(actor.casefold()) if actor else None

    def category(self, actor, source_bot=False):
        if not actor:
            return None
        key = actor.casefold()
        if key in self.bots:
            return self.bots[key]
        # Confirmed human identity wins over a name-based heuristic.
        if key in self.aliases:
            return None
        login = key.split(":", 1)[-1].replace("[bot]", "").replace("_", "-")
        for category, prefixes in BOTS.items():
            if any(login == p or login.startswith(p + "-") for p in prefixes):
                return category
        return "Other automations" if source_bot or key.endswith("[bot]") else None


def size_units(pr, policy=None):
    """Missing or churn-only diffs are unscored. The largest of both bands wins."""
    policy = policy or {}
    if pr.get("detail_status") != "complete" or not pr.get("files"):
        return None
    excludes = tuple(policy.get("exclude", EXCLUDED))
    files = [f for f in pr["files"] if not any(fnmatch.fnmatch(f["path"], p) for p in excludes)]
    if not files:
        return None
    lines = sum(f["added"] + f["removed"] for f in files)
    limits = policy.get("bands", [[1, 10, .5], [5, 170, 1], [18, 750, 2]])
    for file_limit, line_limit, units in limits:
        if len(files) <= file_limit and lines <= line_limit:
            return units
    return policy.get("cap", 3)


def validate(data, config):
    if data.get("schema_version") != 1 or config.get("schema_version") != 1:
        raise ValueError("Supported schema_version is 1")
    identities = Identities(config)
    baseline = config["baseline"]
    start, end = day(baseline["from"]), day(baseline["to"])
    if start.day != 1:
        raise ValueError("Baseline must start at the first day of a month")
    from calendar import monthrange
    if end.day != monthrange(end.year, end.month)[1] or end < start:
        raise ValueError("Baseline must end at a month end and follow its start")
    if number(config.get("hours_per_day", 8), "hours_per_day") == 0:
        raise ValueError("hours_per_day must be positive")
    for field, default in (("min_months", 3), ("min_prs", 20)):
        value = baseline.get(field, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{field} must be a positive integer")
    if not 0 < number(baseline.get("min_diff_coverage", .95), "min_diff_coverage") <= 1:
        raise ValueError("min_diff_coverage must be in (0, 1]")
    previous = (0, 0, 0)
    for band in config.get("size", {}).get("bands", [[1, 10, .5], [5, 170, 1], [18, 750, 2]]):
        if len(band) != 3 or any(number(v, "size band") <= p for v, p in zip(band, previous)):
            raise ValueError("Size band limits and units must be positive and increase")
        previous = band
    if number(config.get("size", {}).get("cap", 3), "size cap") < previous[2]:
        raise ValueError("Size cap cannot be below the largest band")
    for person in config["people"]:
        day(person["history_from"])
        if person.get("history_to"):
            day(person["history_to"])
    keys = set()
    for pr in data.get("prs", []):
        for key in ("key", "platform", "repository", "author", "created", "revision"):
            if not isinstance(pr.get(key), str) or not pr[key]:
                raise ValueError(f"PR requires {key}")
        if pr["key"] in keys:
            raise ValueError(f"Duplicate PR key: {pr['key']}")
        keys.add(pr["key"])
        day(pr["created"])
        if pr.get("merged"):
            if day(pr["merged"]) < day(pr["created"]):
                raise ValueError("Merge cannot precede creation")
        for file in pr.get("files", []):
            if not isinstance(file.get("path"), str):
                raise ValueError("File requires a path")
            number(file.get("added"), "added lines")
            number(file.get("removed"), "removed lines")
    event_keys = set()
    for event in data.get("events", []):
        if event.get("pr") not in keys or not event.get("id") or not event.get("actor"):
            raise ValueError("Event requires an ID, actor, and existing PR")
        if event["id"] in event_keys:
            raise ValueError(f"Duplicate event ID: {event['id']}")
        event_keys.add(event["id"])
        day(event["date"])
        if event["kind"] not in ("review", "approval", "comment", "merge"):
            raise ValueError("Unsupported event kind")
    for usage in data.get("usage", []):
        day(usage["date"])
        if not usage.get("actor") or not usage.get("provider"):
            raise ValueError("Usage requires actor and provider")
        for field in ("credits", "tokens", "prompts", "cost_usd"):
            if usage.get(field) is not None:
                number(usage[field], field)
        for field in ("code_review_active", "code_review_passive"):
            if usage.get(field) is not None and not isinstance(usage[field], bool):
                raise ValueError("Code review flags must be boolean or null")
    cycle_keys = set()
    for cycle in data.get("ai_reviews", []):
        if cycle.get("pr") not in keys or not cycle.get("id"):
            raise ValueError("AI review requires an ID and existing PR")
        if cycle["id"] in cycle_keys:
            raise ValueError("Duplicate AI review cycle")
        cycle_keys.add(cycle["id"])
        day(cycle["date"])
        for field in ("comments", "suggestions", "applied_suggestions"):
            if cycle.get(field) is not None:
                number(cycle[field], field)
    for effort in data.get("effort", []):
        if effort.get("pr") not in keys:
            raise ValueError("Effort requires an existing PR")
        low = number(effort["low_hours"], "low_hours")
        high = number(effort["high_hours"], "high_hours")
        if low > high:
            raise ValueError("Effort low_hours cannot exceed high_hours")
        if effort.get("status") == "approved" and not all(effort.get(k) for k in ("approved_by", "approved_at", "revision", "rubric_version")):
            raise ValueError("Approved effort requires reviewer, date, revision, and rubric version")
    return identities
