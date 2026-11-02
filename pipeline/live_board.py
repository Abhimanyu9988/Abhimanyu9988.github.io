#!/usr/bin/env python3
"""The live board: one Slack message the team edits in place, so a pinned message always shows Abhi's outside work.

It covers open source and public articles only. Pipeline PRs, failed runs and agent tasks belong to the CTO and the admin page.
Inputs: GitHub pull requests and workflow runs, plus the lists cto.py passes in.
Outputs: that one message, and its id in analytics/slack-board.json. The id is the only thing saved to the public branch.
It must not call a model, post a new message on each refresh, or fill in for a source it could not read.
"""
import datetime, json
from radar import HOME_REPO, gh
import analytics, slack

STATE = "analytics/slack-board.json"
MAX_ROWS = 8
MENTION_DAYS = 14                              # an old mention is history, not a request
WORK = {"replicate": "Lab Engineer is testing an article", "radar": "Topic Scout is looking for topics",
        "problems": "Topic Scout is looking for failures to test", "publish": "Publisher is publishing"}
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

def gather(needed, mine, ideas, now):
    """Every row comes from GitHub or the lists passed in. If GitHub fails, the old board stays, with its old time on it."""
    week = iso(now - datetime.timedelta(days=7))
    pulls = gh("GET", f"/repos/{HOME_REPO}/pulls?state=all&sort=updated&direction=desc&per_page=50") or []
    running = []
    for state in ("in_progress", "queued"):
        running += (gh("GET", f"/repos/{HOME_REPO}/actions/runs?status={state}&per_page=30") or {}).get("workflow_runs", [])
    must, might = wanted_from_him(needed, now)
    articles = [p for p in pulls if p["head"]["ref"].startswith("draft/")]
    review = [link("Approve or reject: " + p["title"].removeprefix("Draft: "), p["html_url"]) for p in articles if p["state"] == "open"]
    done = [(p["merged_at"], link("Published: " + p["title"].removeprefix("Draft: "), p["html_url"]))
            for p in articles if (p.get("merged_at") or "") >= week]
    work = []
    for r in running:
        if r.get("name") in WORK:
            title = r.get("display_title") or ""
            work.append(link(WORK[r["name"]] + (f": {title}" if title and title != r["name"] else ""), r["html_url"]))
    for key, word, rows in (("open", "Your PR: ", work), ("merged", "Merged: ", None)):
        got = mine.get(key, "the search failed")
        if not isinstance(got, list):
            work.append(f"Could not check your {key} PRs")
        elif rows is not None:
            rows += [link(word + i["title"], i["url"]) for i in got]
        else:
            done += [(i["closed"], link(word + i["title"], i["url"])) for i in got]
    return {"needs": review + must, "maybe": might, "working": work,
            "next": [f"Topic idea: {slack.escape(i[:100])}" for i in dict.fromkeys(ideas)],
            "done": [row for _, row in sorted(done, key=lambda x: x[0], reverse=True)]}

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
    parts.append(section(":large_blue_circle: In progress", rows["working"]))
    if rows["next"]:
        parts.append(section(":calendar: Up next", rows["next"]))
    parts.append(section(":white_check_mark: Done in the last 7 days", rows["done"]))
    return "\n\n".join(parts)

def refresh(needed, mine, ideas, spent, limit, now=None):
    if not slack.enabled():
        return
    now = now or datetime.datetime.now(datetime.timezone.utc)
    saved, sha = analytics.read_file(STATE)
    ts = json.loads(saved).get("ts") if saved.strip() else None
    new = slack.keep_updated("manager", render(gather(needed, mine, ideas, now), spent, limit, now), ts)
    if new != ts:
        analytics.write_file(STATE, json.dumps({"ts": new}), sha, "slack: board message")
        slack.say("manager", "That message above is your live board. I'll keep it up to date. Pin it once so it stays easy to "
                             "find: on your phone, press and hold it and choose Pin to channel.")
