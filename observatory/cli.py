"""Run with python -m observatory. Network access happens only in collect."""

import argparse
import csv
import json
import os
from pathlib import Path
import sys
import tempfile
from datetime import datetime
from zoneinfo import ZoneInfo

from .metrics import report
from .model import day, validate
from .providers import collect
from .server import make_server
from .storage import disposition, draft, merge_snapshots, read, write


def export_html(data, config, start, end, path):
    reports = {"team": report(data, config, start, end)}
    for person in config["people"]:
        reports[person["id"]] = report(data, config, start, end, person["id"])
    bundle = {"reports": reports, "people": [{"id": p["id"], "name": p["name"]} for p in config["people"]]}
    payload = json.dumps(bundle, allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    page = Path(__file__).with_name("dashboard.html").read_text().replace("/*BOOTSTRAP*/null", payload)
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        # NamedTemporaryFile creates mode 0600 before any report data is written.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=destination.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(page)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def parser():
    root = argparse.ArgumentParser(description="Engineering delivery and AI usage, with explicit evidence boundaries")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("validate", "report", "html", "draft-effort", "review", "serve"):
        cmd = commands.add_parser(name)
        cmd.add_argument("--data", required=True)
        cmd.add_argument("--config", required=True)
        if name in ("report", "html"):
            cmd.add_argument("--from", dest="start", required=True)
            cmd.add_argument("--to", dest="end", required=True)
            cmd.add_argument("--output", required=True)
        if name == "report":
            cmd.add_argument("--person")
            cmd.add_argument("--repository", action="append")
        if name == "serve":
            cmd.add_argument("--port", type=int, default=8787)
        if name == "review":
            cmd.add_argument("--pr", required=True)
            cmd.add_argument("--reviewer", required=True)
            cmd.add_argument("--revision", required=True)
            cmd.add_argument("--status", choices=("approved", "rejected"), required=True)
            cmd.add_argument("--low", type=float, required=True)
            cmd.add_argument("--high", type=float, required=True)
    cmd = commands.add_parser("collect")
    cmd.add_argument("--platform", choices=("github", "bitbucket"), required=True)
    cmd.add_argument("--repository", action="append", required=True, help="Explicit owner/repo or workspace/repo allowlist")
    cmd.add_argument("--token-env", required=True, help="Environment variable name; never pass the token itself")
    cmd.add_argument("--from", dest="start", required=True)
    cmd.add_argument("--to", dest="end", required=True)
    cmd.add_argument("--timezone", default="UTC")
    cmd.add_argument("--max-pages", type=int, default=1000)
    cmd.add_argument("--output", required=True)
    cmd = commands.add_parser("merge")
    cmd.add_argument("inputs", nargs="+")
    cmd.add_argument("--output", required=True)
    cmd = commands.add_parser("import-usage")
    cmd.add_argument("--csv", required=True, help="Normalized daily totals; see docs/data-contract.md")
    cmd.add_argument("--output", required=True)
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == "collect":
            start, end = day(args.start), day(args.end)
            if end < start or end > datetime.now(ZoneInfo(args.timezone)).date():
                raise ValueError("Collection period is reversed or ends in the future")
            if args.max_pages < 1:
                raise ValueError("max-pages must be positive")
            token = os.environ.get(args.token_env)
            if not token:
                raise ValueError("Provider token environment variable is not set")
            for repository in args.repository:
                parts = repository.split("/")
                if len(parts) != 2 or not all(p and p not in (".", "..") for p in parts):
                    raise ValueError("Repository must be owner/repo or workspace/repo")
            output = collect(args.platform, args.repository, token, args.start, args.end, args.timezone, args.max_pages)
            write(args.output, output)
            print(f"Collected {len(output['prs'])} PRs; {len(output['collection_errors'])} coverage issues")
            return 2 if any(c["status"] != "complete" for c in output["coverage"]) else 0
        if args.command == "merge":
            write(args.output, merge_snapshots([read(p) for p in args.inputs]))
            return 0
        if args.command == "import-usage":
            with open(args.csv, newline="") as handle:
                rows = list(csv.DictReader(handle))
            for row in rows:
                for field in ("credits", "tokens", "prompts", "cost_usd"):
                    row[field] = float(row[field]) if row.get(field) else None
                for field in ("code_review_active", "code_review_passive"):
                    if row.get(field, "").lower() not in ("", "true", "false"):
                        raise ValueError("Code review flags must be true, false, or blank")
                    row[field] = None if not row.get(field) else row[field].lower() == "true"
                row.setdefault("scope", "organization")
            output = {"schema_version": 1, "prs": [], "events": [], "usage": rows}
            validate(output, {"schema_version": 1, "people": [], "baseline": {"from": "2000-01-01", "to": "2000-01-31", "repositories": []}})
            write(args.output, output)
            return 0
        data, config = read(args.data), read(args.config)
        validate(data, config)
        if args.command == "validate":
            print(f"Valid records: {len(data.get('prs', []))} PRs")
        elif args.command == "report":
            write(args.output, report(data, config, args.start, args.end, args.person, args.repository))
        elif args.command == "html":
            export_html(data, config, args.start, args.end, args.output)
        elif args.command == "draft-effort":
            count = draft(data, config)
            write(args.data, data)
            print(f"Created {count} unapproved effort drafts")
        elif args.command == "review":
            disposition(data, config, args.pr, args.reviewer, args.status, args.low, args.high, args.revision)
            write(args.data, data)
        elif args.command == "serve":
            server = make_server(args.data, args.config, args.port)
            print(f"Admin server at http://127.0.0.1:{server.server_port}. Keep tokens in your credential store.", flush=True)
            try:
                server.serve_forever()
            finally:
                server.server_close()
        return 0
    except (ValueError, KeyError, OSError) as error:
        # Never print response bodies, environment variables, or input rows.
        print(f"Cannot complete {args.command}: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
