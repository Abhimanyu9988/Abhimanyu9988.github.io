"""Daily answers email. The chat Answers Scout drafts answers to real questions and saves them in the private
team-admin repo (answers/inbox/<YYYY-MM-DD>.json). Here ChatGPT approves, fixes or rejects each one, then Claude Opus
double-checks ChatGPT's final text (approve or reject, no edits, so what he gets is exactly what both passed). Abhi's
rule (25 Nov 2026): nothing is posted until ChatGPT's best model and Opus have both approved it. The answers both passed
go to Abhi as one issue in team-admin at 20:00 Brussels, which GitHub emails him. He posts them himself.

Everything in the inbox came from the web: ChatGPT judges it as data, and none of it is ever run or followed.
Writes go only to the private team-admin repo (its own token), never to this public repo: the answers link to other
people's GitHub issues, which bot text here must never do."""
import base64
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo

import budget
import llm

ADMIN_REPO = "Abhimanyu9988/team-admin"
OWNER = "Abhimanyu9988"
BRUSSELS = ZoneInfo("Europe/Brussels")

REVIEW = """You are the final reviewer for answers that Abhimanyu Bajaj will post himself on public Q&A sites.
He is an OpenTelemetry code owner and works at Splunk, part of Cisco. His lab-tested articles are on showmerit.com.
Judge ONE draft answer against the question. Approve only if all are true:
- It actually answers this question, correctly and specifically. No made-up versions, flags, config keys or results.
- It reads like a helpful engineer, not an ad. A showmerit.com link appears at most once, and only where it helps.
- It follows normal community norms for the site (no link-only answers, no self-promotion spam).
- If it mentions Splunk or Cisco, it ends with "I work at Splunk, part of Cisco." It never disparages any vendor.
- No @mentions and no personal data.
If small edits fix it, make them and approve the fixed text. If it can't be fixed, reject.
Reply as JSON: {"verdict": "approve" or "reject", "answer": "<final answer text, plain markdown>", "reason": "<one line>"}"""


DOUBLE = REVIEW.split("If small edits fix it")[0] + """You are the second, independent check, after ChatGPT. Judge the answer exactly as written: do not edit it.
Reply with {"verdict": "approve" or "reject", "reason": "<one line>"}"""
DOUBLE_SCHEMA = {"type": "object", "required": ["verdict", "reason"], "properties": {
    "verdict": {"enum": ["approve", "reject"]}, "reason": {"type": "string"}}}


def opus_check(item, answer):
    """Claude Opus judges ChatGPT's final answer. A failed call is a reject, never a pass."""
    prompt = json.dumps({"site": item.get("site"), "question_title": item.get("question_title"),
                         "question_url": item.get("question_url"), "answer": answer}, ensure_ascii=False)
    try:
        got = llm.claude_json(DOUBLE, prompt, DOUBLE_SCHEMA, tool="submit_check", tier="best", max_tokens=16000)
        return {"verdict": "approve" if got.get("verdict") == "approve" else "reject", "reason": str(got.get("reason", ""))[:300]}
    except Exception as err:
        return {"verdict": "reject", "reason": f"Opus check failed: {' '.join(str(err).split())[:200]}"}


def admin(method, path, body=None):
    req = urllib.request.Request(f"https://api.github.com/repos/{ADMIN_REPO}/{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": f"Bearer {os.environ['ADMIN_TOKEN']}", "User-Agent": "team",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def read(path):
    try:
        f = admin("GET", f"contents/{path}")
        return base64.b64decode(f.get("content", "")).decode("utf-8"), f.get("sha")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return "", None
        raise


def write(path, text, msg):
    _, sha = read(path)
    body = {"message": msg, "content": base64.b64encode(text.encode()).decode()}
    admin("PUT", f"contents/{path}", {**body, "sha": sha} if sha else body)


def quiet(text):
    """No @mentions outside code blocks: a stray @name would notify a stranger."""
    return re.sub(r"@(?=[A-Za-z0-9-])", "@​", " ".join(str(text or "").split()))


def main():
    if not os.environ.get("ADMIN_TOKEN"):
        sys.exit("no team-admin token")
    now = datetime.datetime.now(BRUSSELS)
    eight = now.replace(hour=20, minute=0, second=0, microsecond=0)
    if (eight - now).total_seconds() > 3600:        # the run for the other half of the year (summer/winter time)
        print("more than an hour before 20:00 Brussels; the later run sends it")
        return
    day = now.date().isoformat()
    title = f"Answers to post: {day}"
    if any(i["title"] == title for i in admin("GET", "issues?state=all&labels=answers&per_page=30")):
        print("already sent", title)
        return
    raw, _ = read(f"answers/inbox/{day}.json")
    if not raw.strip():
        print("no drafts today")
        return
    items = [i for i in json.loads(raw).get("items", []) if str(i.get("question_url", "")).startswith("https://")][:8]
    budget.check()                                  # before the first paid call, as every job does
    model, approved, results = llm.openai_model(), [], []
    for item in items:
        prompt = json.dumps({k: item.get(k) for k in ("site", "question_title", "question_url", "answer")},
                            ensure_ascii=False)
        try:
            verdict = llm.gpt_json(REVIEW, prompt, max_tokens=8000)
        except Exception as err:                    # one bad reply drops that answer, not the email
            verdict = {"verdict": "reject", "reason": f"ChatGPT check failed: {' '.join(str(err).split())[:200]}"}
        final = str(verdict.get("answer", "")).strip()
        second = opus_check(item, final) if verdict.get("verdict") == "approve" and final else None
        results.append({**item, "chatgpt": verdict, "model": model, "opus": second, "opus_model": llm.claude_model("best")})
        if second and second["verdict"] == "approve":
            approved.append((item, final))
    write(f"answers/checked/{day}.json", json.dumps(results, indent=2, ensure_ascii=False) + "\n",
          f"answers: ChatGPT and Opus verdicts for {day}")
    if not approved:
        print("nothing approved today; no email")
        return
    parts = [f"{len(approved)} questions you can answer today. Open the link, paste the text, post. "
             f"Every answer was approved by ChatGPT ({model}) and double-checked by Claude ({llm.claude_model('best')}).", ""]
    for n, (item, answer) in enumerate(approved, 1):
        fence = "````" if "```" in answer else "```"
        parts += [f"### {n}. {quiet(item.get('question_title'))}",
                  f"**Where:** {quiet(item.get('site'))} · **Open:** {item['question_url']}",
                  f"**Why you:** {quiet(item.get('why'))}", "", f"{fence}markdown", answer, fence, ""]
    wait = (eight - datetime.datetime.now(BRUSSELS)).total_seconds()
    if wait > 0:
        time.sleep(wait)
    issue = admin("POST", "issues", {"title": title, "body": "\n".join(parts), "assignees": [OWNER],
                                     "labels": ["answers"]})
    print("sent", issue["html_url"])


if __name__ == "__main__":
    main()
