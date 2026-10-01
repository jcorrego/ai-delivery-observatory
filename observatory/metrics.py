"""Pure calculations. A report carries its assumptions and missing-data counts."""

from calendar import monthrange
from datetime import date, timedelta
from statistics import mean, median

from .model import day, fingerprint, size_units, validate


def working_days(start, end, holidays=()):
    excluded = set(holidays)
    return sum((start + timedelta(days=i)).weekday() < 5 and
               (start + timedelta(days=i)).isoformat() not in excluded
               for i in range(max(0, (end - start).days + 1)))


def full_month_window(start, end):
    if start.day != 1:
        start = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    if end.day != monthrange(end.year, end.month)[1]:
        end = end.replace(day=1) - timedelta(days=1)
    return start, end


def month_count(start, end):
    return max(0, (end.year - start.year) * 12 + end.month - start.month + 1)


def covered(data, repositories, start, end):
    """Require explicit successful collection coverage, not the first observed PR."""
    return bool(repositories) and all(any(c.get("status") == "complete" and c["repository"] == repo and
                   day(c["from"]) <= start and day(c["to"]) >= end
                   for c in data.get("coverage", [])) for repo in repositories)


def baselines(data, config, ids, repos):
    policy = config["baseline"]
    holidays = config.get("holidays", [])
    rows = []
    for pid, person in ids.people.items():
        start = max(day(policy["from"]), day(person["history_from"]))
        end = min(day(policy["to"]), day(person.get("history_to", policy["to"])))
        start, end = full_month_window(start, end)
        months = month_count(start, end)
        days = working_days(start, end, holidays)
        history_ok = bool(repos) and months >= policy.get("min_months", 3) and days > 0 and covered(data, repos, start, end)
        prs = [p for p in data["prs"] if p["repository"] in repos and p.get("merged") and
               ids.person(p["author"]) == pid and not ids.category(p["author"], p.get("author_bot")) and
               start <= day(p["merged"]) <= end]
        units = [size_units(p, config.get("size")) for p in prs]
        known = [u for u in units if u is not None]
        coverage = len(known) / len(prs) if prs else 1
        weighted_ok = coverage >= policy.get("min_diff_coverage", .95)
        rate = sum(known) / days if history_ok and weighted_ok else None
        rows.append({"id": pid, "name": person["name"], "from": start.isoformat(),
                     "to": end.isoformat(), "months": months, "working_days": days,
                     "merged_prs": len(prs), "scored_prs": len(known), "diff_coverage": coverage,
                     "weighted_units": sum(known), "daily_units": rate,
                     "raw_daily_prs": len(prs) / days if history_ok else None,
                     "history_complete": history_ok,
                     "personal_eligible": rate is not None and rate > 0 and len(prs) >= policy.get("min_prs", 20),
                     "in_team_reference": person.get("include_in_baseline", True) and rate is not None})
    team_rows = [r for r in rows if r["in_team_reference"]]
    rates = [r["daily_units"] for r in team_rows]
    team_rate = mean(rates) if rates else None
    without_top = mean(sorted(rates)[:-1]) if len(rates) > 1 else None
    return {"version": fingerprint({"policy": policy, "size": config.get("size"), "rows": rows}),
            "label": policy.get("label", "Historical reference"), "repositories": sorted(repos),
            "from": policy["from"], "to": policy["to"], "people": rows,
            "team_daily_units": team_rate, "team_median_daily_units": median(rates) if rates else None,
            "without_largest_daily_units": without_top, "team_people": len(team_rows)}


def canonical_usage(data, ids, start, end, person, repositories):
    # Normalized rows are daily totals. Model breakdowns must never be added to totals.
    priorities = {"enterprise": 3, "organization": 2, "local": 1}
    chosen = {}
    unallocated = 0
    for row in data.get("usage", []):
        if not start <= day(row["date"]) <= end:
            continue
        pid = ids.person(row["actor"])
        if person and pid != person:
            continue
        if repositories:
            if not row.get("repository"):
                unallocated += 1
                continue
            if row["repository"] not in repositories:
                continue
        key = (pid or row["actor"], row["provider"], row["date"])
        previous = chosen.get(key)
        priority = priorities.get(row.get("scope"), 0)
        if previous is None or priority > priorities.get(previous.get("scope"), 0):
            chosen[key] = row
        elif priority == priorities.get(previous.get("scope"), 0) and row != previous:
            raise ValueError("Conflicting daily usage totals at the same scope; reconcile before reporting")
    totals = {}
    for row in chosen.values():
        provider = totals.setdefault(row["provider"], {"days": set(), "credits": [], "tokens": [], "prompts": [], "cost_usd": [], "code_review_active": [], "code_review_passive": []})
        provider["days"].add(row["date"])
        for field in ("credits", "tokens", "prompts", "cost_usd"):
            if row.get(field) is not None:
                provider[field].append(row[field])
        for field in ("code_review_active", "code_review_passive"):
            if row.get(field) is not None:
                provider[field].append(row[field])
    return {name: {"observed_days": len(r["days"]),
                   **{k: sum(r[k]) if r[k] else None for k in ("credits", "tokens", "prompts", "cost_usd", "code_review_active", "code_review_passive")}}
            for name, r in totals.items()}, unallocated


def report(data, config, from_date, to_date, person=None, repositories=None):
    ids = validate(data, config)
    start, end = day(from_date), day(to_date)
    if end < start:
        raise ValueError("Report end precedes start")
    if person and person not in ids.people:
        raise ValueError("Unknown person filter")
    requested = set(repositories or [])
    scoped = [p for p in data["prs"] if not requested or p["repository"] in requested]
    by_key = {p["key"]: p for p in scoped}
    all_events = [e for e in data.get("events", []) if e["pr"] in by_key]
    events = [e for e in all_events if start <= day(e["date"]) <= end]
    cycles = [c for c in data.get("ai_reviews", []) if c["pr"] in by_key and
              start <= day(c["date"]) <= end and c.get("completed", False)]
    # A sponsor is supported only by explicit request or automatic-policy evidence.
    def sponsor(c):
        if c.get("attribution") not in ("manual_request", "automatic_author_policy") or not c.get("evidence"):
            return None
        pid = ids.person(c.get("sponsor"))
        if c["attribution"] == "automatic_author_policy" and pid != ids.person(by_key[c["pr"]]["author"]):
            return None
        return pid

    selected_events = [e for e in events if not person or ids.person(e["actor"]) == person]
    selected_cycles = [c for c in cycles if not person or sponsor(c) == person]
    relevant_keys = {p["key"] for p in scoped if ids.person(p["author"]) == person}
    relevant_keys |= {e["pr"] for e in all_events if ids.person(e["actor"]) == person}
    relevant_keys |= {c["pr"] for c in cycles if sponsor(c) == person}
    selected = [p for p in scoped if not person or ids.person(p["author"]) == person]
    created = [p for p in selected if start <= day(p["created"]) <= end and not ids.category(p["author"], p.get("author_bot"))]
    merged = [p for p in selected if p.get("merged") and start <= day(p["merged"]) <= end and not ids.category(p["author"], p.get("author_bot"))]
    human_events = [e for e in selected_events if not ids.category(e["actor"], e.get("actor_bot"))]
    collaboration = [e for e in human_events if (ids.person(e["actor"]) or e["actor"].casefold()) !=
                     (ids.person(by_key[e["pr"]]["author"]) or by_key[e["pr"]]["author"].casefold())]
    months = {}
    cursor = start.replace(day=1)
    while cursor <= end:
        months[cursor.strftime("%Y-%m")] = {"month": cursor.strftime("%Y-%m"), "created": 0, "merged": 0, "collaboration": set(), "ai_reviews": 0}
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    for name, prs, field in (("created", created, "created"), ("merged", merged, "merged")):
        for pr in prs:
            months[pr[field][:7]][name] += 1
    for event in collaboration:
        months[event["date"][:7]]["collaboration"].add(event["pr"])
    for cycle in selected_cycles:
        months[cycle["date"][:7]]["ai_reviews"] += 1
    daily = {}
    for i in range((end - start).days + 1):
        value = (start + timedelta(days=i)).isoformat()
        daily[value] = {"date": value, "created": 0, "merged": 0, "collaboration": set(), "ai_reviews": 0}
    for name, prs in (("created", created), ("merged", merged)):
        for pr in prs:
            daily[pr[name]][name] += 1
    for event in collaboration:
        daily[event["date"]]["collaboration"].add(event["pr"])
    for cycle in selected_cycles:
        daily[cycle["date"]]["ai_reviews"] += 1
    automation = {}
    for pr in scoped:
        if person and pr["key"] not in relevant_keys:
            continue
        category = ids.category(pr["author"], pr.get("author_bot"))
        if category and start <= day(pr["created"]) <= end:
            automation.setdefault(category, {"created": set(), "interacted": set(), "platforms": set()})["created"].add(pr["key"])
            automation[category]["platforms"].add(pr["platform"])
    for event in events:
        if person and event["pr"] not in relevant_keys:
            continue
        category = ids.category(event["actor"], event.get("actor_bot"))
        if category:
            row = automation.setdefault(category, {"created": set(), "interacted": set(), "platforms": set()})
            row["interacted"].add(event["pr"])
            row["platforms"].add(by_key[event["pr"]]["platform"])
    baseline_repos = set(config["baseline"]["repositories"])
    if requested:
        baseline_repos &= requested
    baseline = baselines(data, config, ids, baseline_repos)
    baseline_by_person = {r["id"]: r for r in baseline["people"]}
    current_days = working_days(start, end, config.get("holidays", []))
    people = []
    for pid, member in ids.people.items():
        if person and pid != person:
            continue
        authored = [p for p in merged if ids.person(p["author"]) == pid]
        comparable = [p for p in authored if p["repository"] in baseline_repos]
        known = [size_units(p, config.get("size")) for p in comparable]
        scored = [u for u in known if u is not None]
        base = baseline_by_person[pid]
        use_personal = base["personal_eligible"]
        rate = base["daily_units"] if use_personal else baseline["team_daily_units"]
        coverage = len(scored) / len(comparable) if comparable else 1
        equivalent = sum(scored) / rate if rate and coverage >= config["baseline"].get("min_diff_coverage", .95) and comparable else None
        people.append({"id": pid, "name": member["name"],
                       "created": sum(ids.person(p["author"]) == pid for p in created),
                       "merged": len(authored), "collaboration": len({e["pr"] for e in collaboration if ids.person(e["actor"]) == pid}),
                       "ai_reviews": sum(sponsor(c) == pid for c in selected_cycles),
                       "comparable_prs": len(comparable), "scored_prs": len(scored),
                       "unmatched_prs": len(authored) - len(comparable), "weighted_units": sum(scored),
                       "diff_coverage": coverage, "daily_reference_units": rate,
                       "reference": "Personal historical reference" if use_personal else "Team reference used: insufficient personal history",
                       "equivalent_days": equivalent,
                       "equivalent_hours": equivalent * config.get("hours_per_day", 8) if equivalent is not None else None,
                       "capacity_ratio": equivalent / current_days if equivalent is not None and current_days else None})
    approvals = {}
    rubric = config.get("effort_rubric_version", "effort-v1")
    for effort in data.get("effort", []):
        approvals[effort["pr"]] = effort  # Append-only audit trail, latest disposition wins.
    eligible_keys = {p["key"] for p in merged}
    approved = []
    stale, pending = 0, 0
    for key in eligible_keys:
        effort = approvals.get(key)
        if not effort:
            continue
        if effort.get("status") != "approved":
            pending += 1
        elif effort.get("revision") != by_key[key]["revision"] or effort.get("rubric_version") != rubric:
            stale += 1
        else:
            approved.append(effort)
    usage, unallocated = canonical_usage(data, ids, start, end, person, requested)
    unresolved = sorted({p["author"] for p in scoped if not ids.person(p["author"]) and not ids.category(p["author"], p.get("author_bot"))} |
                        {e["actor"] for e in all_events if not ids.person(e["actor"]) and not ids.category(e["actor"], e.get("actor_bot"))})
    unknown_current = sum(not ids.person(p["author"]) for p in merged)
    equivalent_values = [r["equivalent_hours"] for r in people if r["equivalent_hours"] is not None]
    result = {"schema_version": 1, "organization": config.get("organization", "Organization"),
              "synthetic": data.get("synthetic", False), "period": {"from": from_date, "to": to_date, "working_days": current_days},
              "filter": {"person": person, "repositories": sorted(requested)},
              "summary": {"created": len(created), "merged": len(merged), "collaboration": len({e["pr"] for e in collaboration}),
                          "interaction_events": len(collaboration), "ai_reviews": len(selected_cycles),
                          "attributed_ai_reviews": sum(sponsor(c) is not None for c in selected_cycles),
                          "approved_prs": len(approved), "effort_low_hours": sum(e["low_hours"] for e in approved),
                          "effort_high_hours": sum(e["high_hours"] for e in approved),
                          "pending_effort": pending, "stale_effort": stale,
                          "historical_equivalent_hours": sum(equivalent_values) if equivalent_values else None,
                          "unlinked_merged_prs": unknown_current},
              "monthly": [{**m, "collaboration": len(m["collaboration"])} for m in months.values()],
              "daily": [{**d, "collaboration": len(d["collaboration"])} for d in daily.values()],
              "people": people, "baseline": baseline, "usage": usage,
              "ai_review_work": [{**c, "person": sponsor(c)} for c in selected_cycles],
              "automation": [{"category": key, "created": len(r["created"]), "interacted": len(r["interacted"]),
                              "platforms": sorted(r["platforms"])} for key, r in sorted(automation.items())],
              "quality": {"unlinked_accounts": unresolved, "unallocated_usage_rows": unallocated,
                          "source_coverage": data.get("coverage", []),
                          "current_collection_complete": covered(data, requested or ({p["repository"] for p in scoped} |
                               {c["repository"] for c in data.get("coverage", [])}), start, end)},
              "assumptions": {"hours_per_day": config.get("hours_per_day", 8),
                              "size_version": config.get("size", {}).get("version", "trial-size-v1"),
                              "effort_rubric_version": rubric,
                              "capacity_is_estimate": True, "availability_is_constant": True}}
    result["effort_queue"] = [{"pr": key, "title": by_key[key].get("title", key),
                               "revision": by_key[key]["revision"],
                               "low_hours": approvals.get(key, {}).get("low_hours"),
                               "high_hours": approvals.get(key, {}).get("high_hours"),
                               "status": ("stale" if approvals.get(key, {}).get("status") == "approved" and
                                          (approvals[key].get("revision") != by_key[key]["revision"] or
                                           approvals[key].get("rubric_version") != rubric) else
                                          approvals.get(key, {}).get("status", "not estimated"))}
                              for key in sorted(eligible_keys)]
    result["report_version"] = fingerprint(result)
    return result
