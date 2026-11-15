#!/usr/bin/env python3
"""Replies to comments on Abhi's LinkedIn posts, checked by the Fact Checker and the Editor, sent to him in Slack.

  Input   LinkedIn's "commented on your post" emails, read from his Gmail by the small Google Apps Script in
          infra/linkedin-inbox.gs. That script runs as him, returns only LinkedIn comment notifications, and answers only
          a POST that carries LINKEDIN_INBOX_KEY. LINKEDIN_INBOX_URL and LINKEDIN_INBOX_KEY are repository secrets.
          The article each post links to, from the ShowMerit repo, matched by the post text quoted in the email.
  Steps   The Community Manager drafts a reply from the article. Code checks it: length, no links, hashtags, mentions
          or commands, and every number in it must appear in the article or the comment. The Fact Checker and the Editor
          can each block it. A blocked reply is redrafted with their points, up to MAX_DRAFTS drafts in all.
  Output  One Slack message per comment from the Secretary, tagging Abhi, with the reply ready to paste. What was handled
          is kept in the private team-admin repository (linkedin-replies.json), never in this public repo.

It must not: post to LinkedIn, print or store comment text in this public repo or its logs, let a model's approval pass
a reply that code refused, or run past the monthly budget. Only code passes a reply (AGENTS.md rule 2).
"""
import base64, hashlib, json, os, re, time, urllib.parse, urllib.request

STATE = "linkedin-replies.json"               # private repo: message ids seen and comments answered
SITE_REPO, ARTICLES = "Abhimanyu9988/showmerit", "content/abhimanyu/articles"
INBOX_PREFIX = "https://script.google.com/macros/s/"
MAX_DRAFTS = 3                                  # first draft plus two rewrites
MAX_COMMENTS = 2                                # each poll; the cto job has a 10-minute timeout
MAX_TRIES = 3                                   # a notification that keeps failing is dropped after this many polls
TIME_BUDGET_S = 330                             # leave the rest of the cto job's time for its other steps
MAX_CHARS, MAX_WORDS = 1250, 120                # LinkedIn's comment limit, and the team's own limit
FOOTER = re.compile(r"(?im)^.*\b(unsubscribe|this email was intended for|you are receiving|learn why we included this|"
                    r"linkedin corporation|help:|© ?20\d\d)\b.*$")
URL = re.compile(r"https?://\S+|www\.\S+")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
COMMAND = re.compile(r"(?im)(^\s*\$\s)|\b(kubectl|helm|curl|docker|aws|gcloud|az|pip|npm|java -jar)\s+[a-z-]{2,}")
WORD = re.compile(r"[a-z0-9][a-z0-9.\-]*[a-z0-9]|[a-z0-9]")

# ---------- reading the inbox ----------
def configured():
    return os.environ.get("LINKEDIN_INBOX_URL", "").startswith(INBOX_PREFIX) and bool(os.environ.get("LINKEDIN_INBOX_KEY"))

def fetch():
    """The script's recent LinkedIn comment notifications. The key travels in the POST body, never in a URL or a log.
    Apps Script answers a POST with a redirect to script.googleusercontent.com, which urllib follows as a GET."""
    req = urllib.request.Request(os.environ["LINKEDIN_INBOX_URL"], method="POST",
                                 data=json.dumps({"key": os.environ["LINKEDIN_INBOX_KEY"]}).encode(),
                                 headers={"Content-Type": "application/json", "User-Agent": "team"})
    with urllib.request.urlopen(req, timeout=30) as r:
        host = urllib.parse.urlparse(r.geturl()).hostname or ""
        if host not in ("script.google.com", "script.googleusercontent.com"):
            raise RuntimeError("inbox answered from an unexpected host")
        got = json.loads(r.read(2_000_000).decode("utf-8"))
    if not got.get("ok"):
        raise RuntimeError("inbox refused the key")
    return [m for m in got.get("messages") or [] if isinstance(m, dict) and m.get("id") and m.get("body")]

def clean(body):
    """Email text for the model: no links, no email addresses, nothing after LinkedIn's footer starts."""
    cut = FOOTER.search(body)
    text = body[:cut.start()] if cut else body
    text = EMAIL.sub("", URL.sub("", text))
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", text)).strip()[:4000]

# ---------- the article behind the post ----------
def words(text):
    return WORD.findall(str(text).lower())

def shingles(text, n=3):
    w = words(text)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}

def best_article(email_text, articles):
    """The article whose text shares the most three-word runs with the email. The email quotes the start of his post,
    and his post text comes from the article's linkedin field, so a real match shares several. None below 3."""
    mail = shingles(email_text)
    scored = sorted(((len(mail & shingles(a["text"])), a["name"]) for a in articles), reverse=True)
    if not scored or scored[0][0] < 3:
        return None
    return next(a for a in articles if a["name"] == scored[0][1])

def load_articles():
    from radar import gh
    out = []
    for f in gh("GET", f"/repos/{SITE_REPO}/contents/{ARTICLES}") or []:
        if not str(f.get("name", "")).endswith(".md"):
            continue
        got = gh("GET", f"/repos/{SITE_REPO}/contents/{ARTICLES}/{f['name']}") or {}
        text = base64.b64decode(got.get("content", "")).decode("utf-8", "replace")
        title = re.search(r"(?m)^title:\s*['\"]?(.+?)['\"]?\s*$", text)
        out.append({"name": f["name"][:-3], "title": title.group(1).replace("''", "'") if title else f["name"][:-3],
                    "text": text})
    return out

# ---------- code checks ----------
def numbers(text):
    return {n.replace(",", "") for n in NUMBER.findall(str(text))}

def code_problems(reply, source):
    """What code refuses, whatever the reviewers say. `source` is the article text plus the comment."""
    out, r = [], reply.strip()
    if not r:
        return ["The reply is empty."]
    if len(r) > MAX_CHARS or len(r.split()) > MAX_WORDS:
        out.append(f"Cut it to under {MAX_WORDS} words.")
    if URL.search(r) or EMAIL.search(r):
        out.append("Remove the link.")
    if re.search(r"(?<!\w)#\w", r):
        out.append("Remove the hashtags.")
    if re.search(r"(?<![\w.])@\w", r):
        out.append("Remove the @mention.")
    if "```" in r or COMMAND.search(r):
        out.append("Remove the command; describe the change in words.")
    unknown = sorted(numbers(r) - numbers(source), key=len)
    if unknown:
        out.append(f"These numbers are not in the article or the comment: {', '.join(unknown[:5])}. Use only numbers from them.")
    return out

# ---------- the team ----------
def blocks(text, label):
    return f"\n\n{label}:\n<<<\n{text}\n>>>"

def draft(email_text, article, fix=None, item=None):
    import roles
    prompt = blocks(email_text, "EMAIL")
    prompt += blocks(article["text"][:24000], f"ARTICLE ({article['title']})") if article else "\n\nARTICLE: none matched."
    if item and fix:
        prompt += (f"\n\nRewrite only the reply to {item['name']}'s comment. Return that one item."
                   f"{blocks(item['comment'], 'COMMENT')}{blocks(item['reply'], 'LAST REPLY')}"
                   f"\n\nFIX:\n" + "\n".join(f"- {p}" for p in fix))
    return roles.ask("community", prompt, "commentreply").get("items") or []

def review(role, item, article):
    import roles
    prompt = (blocks(item["comment"], "COMMENT") + blocks(item["reply"], "REPLY") +
              (blocks(article["text"][:24000], f"ARTICLE ({article['title']})") if article else "\n\nARTICLE: none."))
    got = roles.ask(role, prompt, "replycheck")
    return [] if got.get("ok") and not got.get("blocking") else (got.get("blocking") or [got.get("notes") or "Not ready."])

def finish(item, email_text, article):
    """(reply, None) once code and both reviewers pass it, or (None, last points) after MAX_DRAFTS drafts."""
    source = f"{article['text'] if article else ''}\n{item['comment']}"
    for n in range(MAX_DRAFTS):
        points = code_problems(item["reply"], source)
        if not points:                                         # reviewers only see replies code has already passed
            points = review("fact_check", item, article) + review("editor", item, article)
        if not points:
            return item["reply"].strip(), None
        if n + 1 < MAX_DRAFTS:
            again = draft(email_text, article, points, item)
            if not again:
                return None, points
            item = {**item, "reply": again[0]["reply"]}
    return None, points

# ---------- state and delivery ----------
def key(item):
    return hashlib.sha256(f"{item['name'].lower().strip()}|{' '.join(item['comment'].lower().split())[:300]}".encode()).hexdigest()[:16]

def message(item, reply, article):
    on = f" on “{article['title']}”" if article else ""
    said = " ".join(item["comment"].split())
    said = said if len(said) <= 220 else said[:217].rsplit(" ", 1)[0] + "…"
    return (f"Reply for {item['name']}{on}, checked by the Fact Checker and the Editor.\n"
            f"They said: “{said}”\n\nReady to paste:\n```\n{reply}\n```")

def run(read_private, write_private, say, budget_left=lambda: True, articles=None, now=time.monotonic):
    """One pass over new notifications. Returns how many replies were sent. Callers pass the private-repo and Slack
    helpers, so this module never holds a token of its own."""
    if not configured():
        return 0
    text, sha = read_private(STATE)
    state = json.loads(text) if text.strip() else {}
    seen, done, tries = set(state.get("seen", [])), set(state.get("done", [])), dict(state.get("tries", {}))
    new = [m for m in fetch() if m["id"] not in seen]
    if not new:
        return 0
    if not budget_left():
        print("::notice title=community::monthly budget reached, comments wait")
        return 0
    box = {"sha": sha}

    def save():
        state.update(seen=sorted(seen)[-500:], done=sorted(done)[-1000:], tries={k: v for k, v in tries.items() if k not in seen})
        write_private(STATE, json.dumps(state, indent=1), box["sha"], "community: LinkedIn replies")
        box["sha"] = read_private(STATE)[1]

    sent, start, handled = 0, now(), 0
    articles = load_articles() if articles is None else articles
    for m in sorted(new, key=lambda m: m.get("date", "")):
        if handled >= MAX_COMMENTS or now() - start > TIME_BUDGET_S:
            break
        handled += 1
        tries[m["id"]] = tries.get(m["id"], 0) + 1
        try:
            email_text = clean(f"{m.get('subject', '')}\n{m['body']}")
            article = best_article(email_text, articles)
            items = [i for i in draft(email_text, article) if i.get("comment", "").strip() and i.get("name", "").strip()]
            if not items and re.search(r"(?i)\bcommented\b", m.get("subject", "")):
                say("secretary", f"{m['subject'][:150]}. LinkedIn's email didn't include the comment, so there's no reply "
                                 "for this one. Please open it on LinkedIn.")
            for item in items:
                if key(item) in done:
                    continue
                reply, points = finish(item, email_text, article)
                if reply:
                    if not say("secretary", message(item, reply, article)):
                        raise RuntimeError("Slack did not take the reply")   # the email is read again next poll
                    sent += 1
                    done.add(key(item))
                else:
                    print(f"::notice title=community::a reply did not pass after {MAX_DRAFTS} drafts")
                    say("secretary", f"{item['name']} commented{' on “' + article['title'] + '”' if article else ''}. "
                                     "No reply passed the fact and tone checks, so this one is yours.")
                    done.add(key(item))                       # never retried, so a hard comment can't burn the budget
            seen.add(m["id"])
        except Exception as e:
            print(f"::warning title=community::notification skipped: {type(e).__name__}")
            if tries[m["id"]] >= MAX_TRIES:
                seen.add(m["id"])
        save()
    return sent
