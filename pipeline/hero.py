#!/usr/bin/env python3
"""Helpers for the AWS Container Hero goal, beyond writing posts.

  MODE=watch    (weekly) Watch the AWS Community Builders page. When applications open, or in early January
                when they usually do, open an issue with an application kit: every post with its numbers.
  MODE=monthly  (1st of the month) From last month's best lab: a 10-minute talk kit for an AWS User Group or
                Community Day, and a 60-minute session pack for the students. ChatGPT drafts both, Claude
                checks them against the article, Gemini settles disputes.
  MODE=reply    (`/reply <question url>` comment) Draft an answer for a re:Post, Stack Overflow or GitHub
                question, pointing to your matching lab when there is one. You post it yourself.
"""
import datetime, html, json, os, pathlib, re, urllib.request
from radar import HOME_REPO, gh, safe_text, comment, post_issue, ensure_label
import analytics, llm

BLOG = "https://abhimanyu9988.github.io"
CB_PAGE = "https://builder.aws.com/community/community-builders"
CB_WAITLIST = "https://pulse.aws/application/BM2AKLSX?p=0"
WATCH_FILE = "analytics/watch.json"
NOW = datetime.datetime.now(datetime.timezone.utc)

# ---------- posts and their numbers ----------
def posts():
    """[{slug, title, description, date, url, body}] from site/_posts, newest first."""
    out = []
    for p in sorted(pathlib.Path("site/_posts").glob("*.md"), reverse=True):
        text = p.read_text()
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
        if not m:
            continue
        fm, body = m.groups()
        field = lambda k: (re.search(rf'^{k}:\s*"?(.*?)"?\s*$', fm, re.M) or [None, ""])[1]
        slug = p.stem.split("-", 3)[3]
        out.append({"slug": slug, "title": json.loads(f'"{field("title")}"') if field("title") else slug,
                    "description": field("description"), "date": p.stem[:10], "url": f"{BLOG}/posts/{slug}/",
                    "body": body})
    return out

def numbers():
    rows, _ = analytics.load_ledger()
    seen = analytics.latest(rows)
    per = {}
    for (post, plat, metric), v in seen.items():
        per.setdefault(post, {})[f"{plat} {metric}"] = v["value"]
    return per

def reach(n):
    return sum(v for k, v in n.items() if k.endswith((" views", " impressions", " reads")))

# ---------- watch ----------
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hero-pipeline watcher)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")

def cb_status():
    try:
        page = fetch(CB_PAGE)
    except Exception as e:
        llm.warn(f"Community Builders page unreachable: {e}")
        return "unknown"
    if re.search(r"join the waitlist", page, re.I):
        return "waitlist"
    if re.search(r"apply now|applications? (are|is) (now )?open|submit (your|an) application", page, re.I):
        return "open"
    return "unknown"

def kit_md():
    ps, num = posts(), numbers()
    if not ps:
        return "_No posts published yet._"
    lines = ["| Post | Published | Reach so far |", "|---|---|---|"]
    for p in ps:
        n = num.get(p["slug"], {})
        lines.append(f"| [{safe_text(p['title'])}]({p['url']}) | {p['date']} | {reach(n):,} |")
    return "\n".join(lines)

def watch():
    text, sha = analytics.read_file(WATCH_FILE)
    state = json.loads(text) if text.strip() else {}
    status = cb_status()
    print(f"Community Builders: {status} (was {state.get('cb_status')})")
    year = NOW.year
    tips = ("**What reviewers look for:** consistent contribution over time, linked evidence (show, don't tell), "
            "and why you share. Lead with the replicated labs, the source-thread answers, talks and the student "
            "sessions; the impact log on the analytics branch has the dated numbers.")
    if status == "open" and state.get("cb_status") != "open":
        ensure_label("hero-goal")
        post_issue("AWS Community Builders applications look open", f"The Community Builders page no longer shows "
                   f"the waitlist and looks open for applications: {CB_PAGE}\n\nPick the **Containers** category.\n\n"
                   f"{tips}\n\n**Your posts so far:**\n\n{kit_md()}", "hero-goal")
    elif NOW.month == 1 and NOW.day <= 14 and state.get("jan_reminder") != year:
        ensure_label("hero-goal")
        post_issue(f"Community Builders: applications usually open in early January ({year})",
                   f"Check {CB_PAGE} this week (status from the page right now: {status}). If you haven't yet, join "
                   f"the waitlist: {CB_WAITLIST}\n\nPick the **Containers** category.\n\n{tips}\n\n**Your posts so "
                   f"far:**\n\n{kit_md()}", "hero-goal")
        state["jan_reminder"] = year
    state.update(cb_status=status, checked=NOW.strftime("%Y-%m-%d"))
    analytics.ensure_branch()
    analytics.write_file(WATCH_FILE, json.dumps(state, indent=1), sha, f"watch: community builders {status}")

# ---------- monthly talk kit and student session ----------
TALK_SCHEMA = {"type": "object", "required": ["title", "abstract", "audience", "outline", "demo", "takeaways", "cfp_short"],
    "properties": {
        "title": {"type": "string"}, "abstract": {"type": "string", "description": "at most 120 words, first person"},
        "audience": {"type": "string"},
        "outline": {"type": "array", "items": {"type": "object", "required": ["minutes", "part", "what"], "properties": {
            "minutes": {"type": "integer"}, "part": {"type": "string"}, "what": {"type": "string"}}}},
        "demo": {"type": "array", "items": {"type": "string"}, "description": "live demo steps, commands from the article"},
        "takeaways": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
        "cfp_short": {"type": "string", "description": "at most 50 words, for short CFP forms"}}}
SESSION_SCHEMA = {"type": "object", "required": ["title", "goals", "prerequisites", "cost_note", "agenda", "hands_on",
                                                 "quiz", "discussion", "feedback_questions"],
    "properties": {
        "title": {"type": "string"}, "goals": {"type": "array", "items": {"type": "string"}},
        "prerequisites": {"type": "array", "items": {"type": "string"}},
        "cost_note": {"type": "string", "description": "what it costs a student and how to avoid surprises"},
        "agenda": {"type": "array", "items": {"type": "object", "required": ["minutes", "part", "what"], "properties": {
            "minutes": {"type": "integer"}, "part": {"type": "string"}, "what": {"type": "string"}}}},
        "hands_on": {"type": "array", "items": {"type": "string"}},
        "quiz": {"type": "array", "items": {"type": "object", "required": ["q", "options", "answer"], "properties": {
            "q": {"type": "string"}, "options": {"type": "array", "items": {"type": "string"}}, "answer": {"type": "string"}}}},
        "discussion": {"type": "array", "items": {"type": "string"}},
        "feedback_questions": {"type": "array", "items": {"type": "string"}}}}

KIT_RUBRIC = """You are the technical checker. Blocking: anything technically wrong or not supported by the ARTICLE; demo
or hands-on commands that differ from the article; timings that don't add up to the stated length; a cost note
that could let a student run up a bill (labs must be destroyed; EKS and NAT cost money even when idle). Minor:
clarity, tone, a weak title."""

def pick_post():
    ps, num = posts(), numbers()
    since = (NOW - datetime.timedelta(days=35)).strftime("%Y-%m-%d")
    recent = [p for p in ps if p["date"] >= since] or ps[:1]
    if not recent:
        return None
    return max(recent, key=lambda p: (reach(num.get(p["slug"], {})), p["date"]))

def table(rows, cols):
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] +
                     ["| " + " | ".join(safe_text(str(r.get(c.lower(), ""))) for c in cols) + " |" for r in rows])

def monthly():
    p = pick_post()
    if not p:
        print("no published posts yet; nothing to build"); return
    ctx = f"ARTICLE ({p['url']}):\n# {p['title']}\n{p['body'][:30000]}"
    talk = llm.ask("chatgpt", "You help Abhimanyu turn his replicated AWS labs into short meetup talks.",
                   "Draft a 10-minute talk for an AWS User Group or AWS Community Day from this article: the "
                   "replication is the live demo. Human, specific, no hype.\n\n" + ctx, TALK_SCHEMA)
    talk, tlog = llm.refine("10-minute talk kit", talk, KIT_RUBRIC, ctx, TALK_SCHEMA,
                            writer="chatgpt", reviewer="claude", judge="gemini")
    ses = llm.ask("chatgpt", "You help Abhimanyu teach AWS containers to students at a coaching institute.",
                  "Draft a 60-minute hands-on session from this article for students new to AWS containers. They use "
                  "their own AWS accounts; keep it safe and cheap.\n\n" + ctx, SESSION_SCHEMA)
    ses, slog = llm.refine("student session pack", ses, KIT_RUBRIC, ctx, SESSION_SCHEMA,
                           writer="chatgpt", reviewer="claude", judge="gemini")
    month = NOW.strftime("%B %Y")
    for label in ("talk-kit", "teaching"):
        ensure_label(label)
    li = lambda xs: "\n".join(f"- {safe_text(x)}" for x in xs)
    post_issue(safe_text(f"Talk kit ({month}): {talk['title']}")[:200],
               f"From your post [{safe_text(p['title'])}]({p['url']}), last month's strongest lab.\n\n"
               f"**Abstract**\n\n{safe_text(talk['abstract'])}\n\n**Short version for CFP forms**\n\n"
               f"{safe_text(talk['cfp_short'])}\n\n**Audience:** {safe_text(talk['audience'])}\n\n**10 minutes**\n\n"
               f"{table(talk['outline'], ['Minutes', 'Part', 'What'])}\n\n**Live demo**\n\n{li(talk['demo'])}\n\n"
               f"**Takeaways**\n\n{li(talk['takeaways'])}\n\n**Where to submit:** your nearest AWS User Group "
               "(they're always looking for 10 to 20 minute talks), AWS Community Day calls for papers, and Kubernetes "
               "Community Days. After you give it, comment `/stats " + p["slug"] + " talk attendees=40` here so it "
               f"lands in the impact log.\n\n<!-- post: {p['slug']} -->\n\n_ChatGPT drafted, Claude checked "
               f"({len(tlog['points'])} point(s)), Gemini on call for disputes. {llm.usage_summary()}_", "talk-kit")
    quiz = "\n".join(f"{i}. {safe_text(q['q'])}\n" + "\n".join(f"   - {safe_text(o)}" for o in q.get("options", []))
                     + f"\n   - _Answer: {safe_text(q.get('answer'))}_" for i, q in enumerate(ses["quiz"], 1))
    post_issue(safe_text(f"Student session ({month}): {ses['title']}")[:200],
               f"A 60-minute hands-on session built from [{safe_text(p['title'])}]({p['url']}).\n\n**Goals**\n\n"
               f"{li(ses['goals'])}\n\n**Before the session**\n\n{li(ses['prerequisites'])}\n\n**Cost and safety**\n\n"
               f"{safe_text(ses['cost_note'])}\n\n**Agenda**\n\n{table(ses['agenda'], ['Minutes', 'Part', 'What'])}\n\n"
               f"**Hands-on**\n\n{li(ses['hands_on'])}\n\n**Quiz**\n\n{quiz}\n\n**Discussion**\n\n{li(ses['discussion'])}"
               f"\n\n**Feedback form questions**\n\n{li(ses['feedback_questions'])}\n\nAfterwards, comment for example "
               f"`/stats {p['slug']} teaching students=45 positive_feedback=40` so it lands in the impact log (it "
               f"counts as teaching evidence).\n\n<!-- post: {p['slug']} -->\n\n_ChatGPT drafted, Claude checked "
               f"({len(slog['points'])} point(s)), Gemini on call for disputes._", "teaching")

# ---------- /reply ----------
REPLY_SCHEMA = {"type": "object", "required": ["reply", "uses_post"], "properties": {
    "reply": {"type": "string", "description": "60 to 160 words, first person"},
    "uses_post": {"type": "string", "description": "the URL of the matching post, or empty"}}}
REPLY_RUBRIC = """You are the technical checker. Blocking: anything technically wrong, anything the matching post or the
question doesn't support, a link to a post that doesn't actually answer the question, a reply that is mostly
self-promotion. Minor: tone, length."""

def question_text(url):
    import lab
    got = lab.fetch_sources([url])[0]
    if len(got) > 1:
        return json.dumps(got)[:12000]
    page = fetch(url)
    page = re.sub(r"(?s)<(script|style|noscript)[^>]*>.*?</\1>", " ", page)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())[:12000]

def reply():
    issue = int(os.environ["ISSUE"])
    words = os.environ.get("COMMENT_BODY", "").strip().split(None, 2)
    if len(words) < 2 or not re.match(r"https?://\S+$", words[1]):
        comment(issue, "Usage: `/reply <link to the question>`, optionally followed by what you want to say."); return
    url, note = words[1], (words[2] if len(words) > 2 else "")
    try:
        q = question_text(url)
    except Exception as e:
        comment(issue, f"Couldn't read that page ({safe_text(str(e))[:200]}). Paste the question text after the link "
                       "and I'll work from that."); return
    catalog = [{"title": p["title"], "url": p["url"], "description": p["description"]} for p in posts()]
    ctx = f"QUESTION ({url}):\n{q}\n\nABHI'S NOTE: {note or 'none'}\n\nABHI'S POSTS:\n{json.dumps(catalog)}"
    sysmsg = "You draft answers Abhimanyu posts himself on AWS re:Post, Stack Overflow or GitHub."
    d = llm.ask("chatgpt", sysmsg, "Draft a helpful answer in Abhi's voice. Answer the question first; link one of his "
                "posts only if it genuinely covers this, as 'I reproduced this here: <url>'. No greetings, no fluff.\n\n"
                + ctx, REPLY_SCHEMA)
    d, log = llm.refine("community answer", d, REPLY_RUBRIC, ctx, REPLY_SCHEMA, writer="chatgpt", reviewer="claude",
                        judge="gemini")
    where = ("Stack Overflow doesn't allow AI-written answers: rewrite this in your own words before posting."
             if "stackoverflow.com" in url else "Read it through and post it yourself; the bot never posts outside "
             "your repos.")
    text = safe_text(d["reply"], "[left out: failed the link-safety check]").replace("```", "'''")
    comment(issue, f"**Draft answer** for {safe_text(url, 'that question')}\n\n_{where}_\n\n```text\n{text}\n```\n\n"
                   f"_ChatGPT drafted, Claude checked ({len(log['points'])} point(s))._ Once you've posted it, comment "
                   "`/stats community-answers repost answers=1` (or `stackoverflow`) so it counts in the impact log.")
    # Drafts aren't impact. Once you've posted it, record it: /stats community-answers repost answers=1

if __name__ == "__main__":
    {"watch": watch, "monthly": monthly, "reply": reply}[os.environ.get("MODE", "watch")]()
