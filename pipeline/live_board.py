#!/usr/bin/env python3
"""The live board: a web page of cards for Abhi's outside work, and one Slack message that links to it with today's counts.

It covers open source and public articles only. Pipeline PRs, failed runs and agent tasks belong to the CTO and the admin page.
Inputs: GitHub pull requests and workflow runs, plus the lists cto.py passes in.
Outputs: analytics/board.json for site/board.html, the Slack message, and its id in analytics/slack-board.json.
The analytics branch is public, so only public data goes in board.json. Card moves stay in Abhi's browser.
It must not call a model, post a new message on each refresh, or fill in for a source it could not read.
"""
import datetime, json, re
from radar import HOME_REPO, gh
import analytics, slack

STATE, DATA = "analytics/slack-board.json", "analytics/board.json"
PAGE = "https://abhimanyu9988.github.io/board/"
MENTION_DAYS = 14                              # an old mention is history, not a request
WORK = {"replicate": "Lab Engineer is testing an article", "radar": "Topic Scout is looking for topics",
        "problems": "Topic Scout is looking for failures to test", "publish": "Publisher is publishing"}
MUST_ACT = {"review_requested_from_him": "Review", "prs_assigned_to_him": "Assigned PR", "assigned_to_him": "Assigned",
            "changes_requested_on_his_prs": "Fix your PR"}
MIGHT_ACT = {"code_owner_team_reviews": "Team review", "pr_mentions": "Mentioned", "mentions": "Mentioned"}

def card(kind, title, url=""):
    """Board cards are shown as plain text by the page, so a title can't inject anything there."""
    repo = re.search(r"github\.com/[^/]+/([^/]+)", url)
    return {"id": url or f"{kind}:{title}", "kind": kind, "title": title[:160], "url": url, "where": repo.group(1) if repo else ""}

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
                rows.append(card("Check failed", f"Could not check {key.replace('_', ' ')}"))
                continue
            for item in got:
                if item["url"] not in seen and item["updated"] >= fresh:
                    seen.add(item["url"])
                    rows.append(card(word, item["title"], item["url"]))
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
    review = [card("Approve or reject", p["title"].removeprefix("Draft: "), p["html_url"]) for p in articles if p["state"] == "open"]
    done = [(p["merged_at"], card("Published", p["title"].removeprefix("Draft: "), p["html_url"]))
            for p in articles if (p.get("merged_at") or "") >= week]
    work = []
    for r in running:
        if r.get("name") in WORK:
            title = r.get("display_title") or ""
            work.append(card(WORK[r["name"]], title if title != r["name"] else "Running now", r["html_url"]))
    for key, word, rows in (("open", "Your PR", work), ("merged", "Merged", None)):
        got = mine.get(key, "the search failed")
        if not isinstance(got, list):
            work.append(card("Check failed", f"Could not check your {key} PRs"))
        elif rows is not None:
            rows += [card(word, i["title"], i["url"]) for i in got]
        else:
            done += [(i["closed"], card(word, i["title"], i["url"])) for i in got]
    return {"needs": review + must, "maybe": might, "working": work,
            "next": [card("Up next", i) for i in dict.fromkeys(ideas)],
            "done": [row for _, row in sorted(done, key=lambda x: x[0], reverse=True)]}

def summary(rows, spent, limit, now):
    """The <!date> code shows the time in whoever is reading it's own time zone."""
    stamp = f"<!date^{int(now.timestamp())}^{{date_short_pretty}} {{time}}|{iso(now)}>"
    counts = [f"{len(rows['needs'])} need you", f"{len(rows['maybe'])} worth a look", f"{len(rows['working'])} in progress", f"{len(rows['done'])} done this week"]
    return f"*<{PAGE}|Open your board>*\n{'  ·  '.join(counts)}\nChecked {stamp}  ·  ${spent:.2f} of ${limit:g} spent this month"

def save_cards(rows, now):
    """Saved only when a card changed, so the public branch doesn't gain a commit every ten minutes."""
    old, sha = analytics.read_file(DATA)
    if old.strip() and json.loads(old).get("columns") == rows:
        return
    analytics.write_file(DATA, json.dumps({"updated": iso(now), "columns": rows}, indent=1), sha, "board: cards changed")

def refresh(needed, mine, ideas, spent, limit, now=None):
    if not slack.enabled():
        return
    now = now or datetime.datetime.now(datetime.timezone.utc)
    saved, sha = analytics.read_file(STATE)
    ts = json.loads(saved).get("ts") if saved.strip() else None
    rows = gather(needed, mine, ideas, now)
    save_cards(rows, now)
    new = slack.keep_updated("manager", summary(rows, spent, limit, now), ts)
    if new != ts:
        analytics.write_file(STATE, json.dumps({"ts": new}), sha, "slack: board message")
        slack.say("manager", "That message above opens your board, and I'll keep its counts up to date. Pin it once so it stays "
                             "easy to find: on your phone, press and hold it and choose Pin to channel.")
