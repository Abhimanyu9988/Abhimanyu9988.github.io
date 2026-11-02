#!/usr/bin/env python3
"""The live board: one Slack message the team edits in place, so a pinned message always shows today's work.

Inputs: GitHub pull requests and workflow runs, plus the lists cto.py passes in. Slack is private, so open tasks can appear.
Outputs: that one message, and its id in analytics/slack-board.json. The id is the only thing saved to the public branch.
It must not call a model, post a new message on each refresh, or fill in for a source it could not read.
"""
import datetime, json
from radar import HOME_REPO, gh
import analytics, slack

STATE = "analytics/slack-board.json"
MAX_ROWS = 8
MENTION_DAYS = 14                              # an old mention is history, not a request
WORK = {"replicate": "Lab Engineer is running a lab", "radar": "Topic Scout is looking for topics",
        "problems": "Topic Scout is looking for failures to test", "publish": "Publisher is publishing",
        "analytics": "Analyst is reading the numbers", "tech-head": "Tech Head is reviewing a change"}
MUST_ACT = {"review_requested_from_him": "Review", "prs_assigned_to_him": "Assigned PR", "assigned_to_him": "Assigned",
            "changes_requested_on_his_prs": "Fix your PR"}
MIGHT_ACT = {"code_owner_team_reviews": "Team review", "pr_mentions": "Mentioned", "mentions": "Mentioned"}

def link(label, url):
    return f"<{url}|{slack.escape(label[:100])}>"

def iso(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")

def wanted_from_him(needed, now):
    """(items that need him, items worth a look). A failed search shows as failed, because an empty list would mean 'all clear'."""
    must, might, seen = [], [], set()
    recent = (now - datetime.timedelta(days=MENTION_DAYS)).strftime("%Y-%m-%d")
    for labels, rows, fresh in ((MUST_ACT, must, ""), (MIGHT_ACT, might, recent)):
        for key, word in labels.items():
            got = needed.get(key, "the search failed")
            if not isinstance(got, list):
                rows.append(f"Could not check {key.replace('_', ' ')}")
                continue
            for item in got:
                if item["url"] not in seen and item["updated"] >= fresh:
                    seen.add(item["url"])
                    rows.append(link(f"{word}: {item['title']}", item["url"]))
    return must, might

def gather(needed, tasks, ideas, now):
    """Every row comes from GitHub or the lists passed in. If GitHub fails, the old board stays, with its old time on it."""
    week, day = iso(now - datetime.timedelta(days=7)), iso(now - datetime.timedelta(days=1))
    pulls = gh("GET", f"/repos/{HOME_REPO}/pulls?state=all&sort=updated&direction=desc&per_page=50") or []
    running = []
    for state in ("in_progress", "queued"):
        running += (gh("GET", f"/repos/{HOME_REPO}/actions/runs?status={state}&per_page=30") or {}).get("workflow_runs", [])
    failed = (gh("GET", f"/repos/{HOME_REPO}/actions/runs?status=failure&per_page=30&created=%3E{day}") or {}).get("workflow_runs", [])
    must, might = wanted_from_him(needed, now)
    review = [link(("Approve or reject: " if p["head"]["ref"].startswith("draft/") else "Merge or close: ")
                   + p["title"].removeprefix("Draft: "), p["html_url"]) for p in pulls if p["state"] == "open"]
    done = []
    for p in pulls:
        draft, title = p["head"]["ref"].startswith("draft/"), p["title"].removeprefix("Draft: ")
        if (p.get("merged_at") or "") >= week:
            done.append((p["merged_at"], link(("Published: " if draft else "Shipped: ") + title, p["html_url"])))
        elif draft and p["state"] == "closed" and (p.get("closed_at") or "") >= week:
            done.append((p["closed_at"], link("Rejected: " + title, p["html_url"])))
    work = []
    for r in running:
        if r.get("name") in WORK:
            title = r.get("display_title") or ""
            work.append(link(WORK[r["name"]] + (f": {title}" if title and title != r["name"] else ""), r["html_url"]))
    broken = {}
    for r in failed:                           # newest first, so each workflow shows its latest failure
        if not str(r.get("event")).startswith("pull_request"):
            broken.setdefault(r.get("name", "?"), link(f"{r.get('name', '?')} failed", r["html_url"]))
    return {"needs": review + must, "maybe": might, "working": work,
            "todo": [f"{slack.escape(t.get('title', '')[:100])} ({slack.escape(t.get('owner', '?'))})" for t in tasks],
            "next": [f"Topic idea: {slack.escape(i[:100])}" for i in ideas],
            "done": [row for _, row in sorted(done, key=lambda x: x[0], reverse=True)], "failed": list(broken.values())}

def section(title, rows):
    if not rows:
        return f"*{title}*\n_Nothing right now._"
    more = f"\n_and {len(rows) - MAX_ROWS} more_" if len(rows) > MAX_ROWS else ""
    return f"*{title} ({len(rows)})*\n" + "\n".join(f"• {r}" for r in rows[:MAX_ROWS]) + more

def render(rows, spent, limit, now):
    """The <!date> code shows the time in whoever is reading it's own time zone."""
    stamp = f"<!date^{int(now.timestamp())}^{{date_short_pretty}} {{time}}|{iso(now)}>"
    parts = [f"*Team board*  ·  updated {stamp}  ·  ${spent:.2f} of ${limit:g} spent this month",
             section(":red_circle: Needs you", rows["needs"])]
    if rows["maybe"]:
        parts.append(section(":eyes: Worth a look", rows["maybe"]))
    parts += [section(":large_blue_circle: In progress", rows["working"])]
    if rows["next"]:
        parts.append(section(":calendar: Up next", rows["next"]))
    if rows["todo"]:
        parts.append(section(":clipboard: To do, no agent has started", rows["todo"]))
    if rows["failed"]:
        parts.append(section(":warning: Failed in the last day", rows["failed"]))
    parts.append(section(":white_check_mark: Done in the last 7 days", rows["done"]))
    return "\n\n".join(parts)

def refresh(needed, tasks, ideas, spent, limit, now=None):
    if not slack.enabled():
        return
    now = now or datetime.datetime.now(datetime.timezone.utc)
    saved, sha = analytics.read_file(STATE)
    ts = json.loads(saved).get("ts") if saved.strip() else None
    new = slack.keep_updated("manager", render(gather(needed, tasks, ideas, now), spent, limit, now), ts)
    if new != ts:
        analytics.write_file(STATE, json.dumps({"ts": new}), sha, "slack: board message")
        slack.say("manager", "That message above is your live board. I'll keep it up to date. Pin it once so it stays easy to "
                             "find: on your phone, press and hold it and choose Pin to channel.")
