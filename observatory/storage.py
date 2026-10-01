"""Local private records and append-only effort dispositions."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

from .model import number, size_units, validate


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as handle:
        temp = Path(handle.name)
        try:
            os.chmod(temp, 0o600)
            json.dump(value, handle, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temp.unlink(missing_ok=True)
            raise
    os.replace(temp, path)


def disposition(data, config, key, reviewer, status, low=None, high=None, expected_revision=None):
    validate(data, config)
    if reviewer not in {a["id"] for a in config.get("admins", [])}:
        raise ValueError("Reviewer is not a configured admin")
    pr = next((p for p in data["prs"] if p["key"] == key), None)
    if not pr:
        raise ValueError("Unknown PR")
    if expected_revision is not None and pr["revision"] != expected_revision:
        raise ValueError("PR revision changed; reload before reviewing")
    if status not in ("approved", "rejected"):
        raise ValueError("Disposition must be approved or rejected")
    low, high = number(low, "low_hours"), number(high, "high_hours")
    if low > high:
        raise ValueError("Effort range is reversed")
    row = {"pr": key, "revision": pr["revision"], "rubric_version": config.get("effort_rubric_version", "effort-v1"),
           "status": status, "low_hours": low, "high_hours": high,
           "approved_by": reviewer, "approved_at": datetime.now(timezone.utc).isoformat(),
           "method": "admin reviewed whole-PR estimate"}
    data.setdefault("effort", []).append(row)
    return row


def draft(data, config):
    ids = validate(data, config)
    existing = {e["pr"] for e in data.get("effort", [])}
    ranges = config.get("effort_ranges", {"0.5": [1, 3], "1": [3, 8], "2": [8, 24], "3": [24, 60]})
    added = 0
    for pr in data["prs"]:
        if not pr.get("merged") or pr["key"] in existing or ids.category(pr["author"], pr.get("author_bot")):
            continue
        units = size_units(pr, config.get("size"))
        if units is None:
            continue
        low, high = ranges[str(units)]
        data.setdefault("effort", []).append({"pr": pr["key"], "revision": pr["revision"],
                      "rubric_version": config.get("effort_rubric_version", "effort-v1"), "status": "draft",
                      "low_hours": low, "high_hours": high,
                      "method": "size-based starter heuristic; human review required"})
        added += 1
    return added


def merge_snapshots(inputs):
    """Later records replace the same source key. Usage stays explicit for scope reconciliation."""
    result = {"schema_version": 1, "synthetic": all(x.get("synthetic", False) for x in inputs),
              "prs": [], "events": [], "ai_reviews": [], "usage": [], "effort": [], "coverage": [], "collection_errors": []}
    for field, identity in (("prs", "key"), ("events", "id"), ("ai_reviews", "id")):
        rows = {}
        for snapshot in inputs:
            for row in snapshot.get(field, []):
                rows[row[identity]] = row
        result[field] = list(rows.values())
    for field in ("usage", "effort", "coverage", "collection_errors"):
        seen = set()
        for snapshot in inputs:
            for row in snapshot.get(field, []):
                key = json.dumps(row, sort_keys=True)
                if key not in seen:
                    result[field].append(row)
                    seen.add(key)
    return result
