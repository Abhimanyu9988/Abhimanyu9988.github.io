#!/usr/bin/env python3
"""Publish ONE proposal you approved: the exact title and body saved by a radar/problems run."""
import json, os
from radar import (HOME_REPO, LABELS, gh, norm, used_sources, existing_issues, ensure_label,
                   post_issue, proposal_id)

run_id, want = os.environ["RUN_ID"].strip(), os.environ["PROPOSAL_ID"].strip()
if not run_id.isdigit():
    raise SystemExit("refusing: run_id must be a number")
run = gh("GET", f"/repos/{HOME_REPO}/actions/runs/{run_id}")
if (run.get("path") not in (".github/workflows/radar.yml", ".github/workflows/problems.yml")
        or run.get("head_branch") != "main" or run.get("conclusion") != "success"):
    raise SystemExit("refusing: that is not a successful radar/problems run on main")
match = [p for p in json.load(open("proposals/proposals.json")) if proposal_id(p) == want]
if len(match) != 1:
    raise SystemExit(f"refusing: proposal {want} not found in run {run_id}")
p = match[0]
if p["label"] not in LABELS:
    raise SystemExit(f"refusing: unknown label {p['label']!r}")
titles, used = existing_issues()
if norm(p["title"]) in {norm(t) for t in titles} or used_sources(p["body"]) & used:
    raise SystemExit("refusing: already posted, or its sources are already used")
print(f"publishing exactly:\nTITLE: {p['title']}\n{p['body']}")
ensure_label(p["label"])
post_issue(p["title"], p["body"], p["label"])
