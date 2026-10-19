#!/usr/bin/env python3
"""Turn a lab run into a draft post PR, or a comment on the topic issue explaining why not.

  1. Verdict: Claude, ChatGPT and Gemini each read the topic, the lab and its real output and say whether it
     genuinely replicated the problem and showed the fix. 2 of 3 yes is needed to draft.
  2. ChatGPT writes the article (human, story-first, every command) and a LinkedIn post. Claude checks the
     technical accuracy against the evidence; Gemini rules on anything they disagree about.
  3. The bot opens a PR from draft/<slug> showing the post itself at the top, with the lab and raw output.
     Abhimanyu edits and merges; publish.yml then publishes and hands him Medium and LinkedIn links.
Redrafts reuse an earlier lab run's attachment (no new AWS run): `/redraft <notes>` on a draft PR (redraft.yml)
passes your notes to the writer and closes the old PR once the new one is open.
"""
import base64, datetime, json, os, pathlib, re
from radar import HOME_REPO, gh, safe_text, assert_safe, comment, add_label, Unsafe
import llm, cover

OUT = pathlib.Path("out")
SITE_URL = "https://abhimanyu9988.github.io"
REPO_WEB = "https://github.com/" + HOME_REPO          # used only inside post files, never in issue/PR text

VERDICT_Q = """Did this lab GENUINELY replicate the problem described in TOPIC, and does EVIDENCE show the fix working?
Say no if the lab reproduced a different or contrived problem, if the evidence does not show what the scripts claim,
if the fix only "worked" because of a shortcut, or if the result would mislead a reader. Judge only from TOPIC, LAB and
EVIDENCE."""

ARTICLE_RULES = """You are writing as Abhimanyu (Abhi), in the first person. AUTHOR lists true facts about him; he
reproduced this problem himself in a disposable AWS lab. Readers: working engineers who just hit this, and students
learning AWS containers. They found the post because of a SYMPTOM, so every part of it serves one question: "what is
going on, and how do I fix it?" Write it the way he'd explain it to a teammate: warm, plain, specific, and short.

LENGTH IS A HARD RULE: 600 to 900 words of prose, about 1,200 words in total with code. At most 6 code blocks, and no
output block longer than 10 lines (cut with "..." and keep the lines that prove the point). If something isn't
needed to understand or fix the problem, leave it out; the lab folder has the rest.

Structure (markdown, no front matter, no H1):
- Open with the chosen HOOK (keep its substance and first line): the moment it hurts, in two or three short
  paragraphs, no heading. One personal line at most, using ONLY facts in AUTHOR. Never invent anecdotes, numbers,
  customers or employers.
- **TL;DR**: problem, cause, fix, in three short lines.
- "## What you'll see": the symptom, with ONE trimmed output block from EVIDENCE.
- "## Why it happens": the cause in plain words. Say what the sources and the lab confirm and what is only likely.
- "## The fix": the one to three commands or config lines that ARE the fix, then the trimmed output that shows it
  working. Mention any caveat in one sentence.
- "## Try it yourself": ONE code block that runs the whole lab, using the lab's real file names, e.g.
     git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
     cd Abhimanyu9988.github.io/labs/LAB_SLUG
     terraform init && terraform apply
     ./reproduce.sh      # shows the problem
     ./fix.sh            # applies the fix and checks it
     ./cleanup.sh && terraform destroy
  (match the lab: if the fix is fix.tf, say how it's applied; leave out scripts that don't exist). Then one line:
  the run's real minutes and that it costs a few cents (only if LAB or EVIDENCE supports it), and one line that every
  command is in those short scripts and the raw output of my run is in the lab's evidence folder.
- "## What I didn't test": two or three bullets.
- "## Sources": each source as a markdown link with a readable title, never a bare URL.
Then a line with only "---", then this exact line, on ONE line:
_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._

Voice: short paragraphs, plain words, explain jargon once, "you" and "I". STYLE SIGNALS show what works on dev.to
right now: borrow the shape (a concrete symptom up front, a clear promise, scannable headings), never the wording.
Banned: "In today's...", "delve", "landscape", "robust", "seamless", "game-changer", "leverage", "Let's dive in",
"unlock", "journey", emojis in headings, exclamation marks. Never mention the pipeline (S3 backends, plan files,
-no-color, HERO_RESULT, "the script loops over").
Hard rules: every command, output, error message, version or number must appear in LAB or EVIDENCE (trim with
"..."). Never invent output. Keep <account-id> placeholders.
Tags: at most 4, lowercase letters and digits only (for example aws, ecs, kubernetes, devops).

LinkedIn post (field "linkedin"), plain text, no markdown, 70 to 140 words, written as "a problem I solved":
- First line: the symptom as the reader would say it (no clickbait, no "I'm excited to share").
- Two or three short lines: what was really going on, and the fix, in plain words.
- One line: I reproduced it in a throwaway AWS lab and wrote up the fix with every command.
- Then a line containing only {LINKS} (the dev.to and Medium links are filled in there).
- Then 3 or 4 hashtags. At most one emoji. No @mentions (Abhi tags people himself). No other URLs.

Source replies (field "source_replies"): for EACH source URL, a short reply Abhi could post on that thread himself,
50 to 100 words, first person, helpful on its own (what I reproduced, what fixed it, one caveat), ending with
"I wrote up the full replication here: {POST_URL}". No greeting fluff.
Stack Overflow does not allow AI-written answers, so these are drafts Abhi rewrites in his own words."""

ARTICLE_RUBRIC = """You are the technical checker. Blocking: any personal claim about Abhi that is not in AUTHOR; any
command, output, error text, version or number that is not in EVIDENCE or LAB; a command or file name that differs
from the lab files; a claim about the cause stronger than the sources and evidence support; a missing reproduction
or fix verification; anything in the LinkedIn post that the article does not support; anything that would mislead a
beginner; over 900 words of prose or an output block over 10 lines. Minor: tone that reads robotic or like an AI,
clarity, a title that doesn't name the reader's symptom, anything a busy reader could skip."""

POST_SCHEMA = {"type": "object", "required": ["title", "description", "tags", "body", "linkedin", "source_replies"], "properties": {
    "title": {"type": "string", "description": "max 80 characters; names the symptom the reader hit, no clickbait"},
    "description": {"type": "string", "description": "one or two sentences for previews"},
    "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
    "body": {"type": "string", "description": "the article in markdown"},
    "linkedin": {"type": "string", "description": "the LinkedIn post, plain text, with a {LINKS} line"},
    "source_replies": {"type": "array", "items": {"type": "object", "required": ["url", "text"], "properties": {
        "url": {"type": "string"}, "text": {"type": "string"}}}}}}

def review_md(name, log):
    if not log:
        return ""
    pts = log.get("points") or []
    rev = log.get("reviewer", "ChatGPT")
    head = f"**{name}:** {rev} {'approved it as written' if log.get('approved', log.get('chatgpt_approved')) else 'raised points'}"
    if not pts:
        return head + "."
    lines = [f"- _{safe_text(p.get('severity'), 'note')[:12]}_ {safe_text(p.get('issue'))[:240]} \u2192 "
             f"{safe_text(p.get('outcome'))}" for p in pts[:12]]
    return head + f" ({len(pts)}):\n" + "\n".join(lines)

def fix_output_tabs(md):
    """Writers sometimes double-escape tabs in pasted output; turn a literal \\t back into a tab, but only inside
    output blocks (```text or bare), never in shell commands where \\t can be intended."""
    def fix(m):
        if m.group(1).strip().lower() not in ("", "text", "txt", "console", "output"):
            return m.group(0)
        return f"```{m.group(1)}\n{m.group(2).replace(chr(92) + 't', chr(9))}```"
    return re.sub(r"```([^\n`]*)\n(.*?)```", fix, md, flags=re.S)

def headline_md(h):
    if not h:
        return ""
    others = "".join(f"\n- {safe_text(o)}" for o in h.get("others", []))
    return (f"**Headline round:** the panel picked {h['by']}'s title and opening ({h['yes']} of 3 votes), shaped by "
            f"what's getting the most reactions on dev.to right now.{others}\n\n")

def verdict_md(v):
    mark = lambda x: "\u2705" if x == "yes" else "\u274c"
    lines = [f"- {llm.NAMES[w]} {mark(a)} _{safe_text(r)}_" for w, (a, r) in v["votes"].items()]
    return f"**Replication check:** {v['yes']} of 3 say the lab genuinely replicated it\n" + "\n".join(lines)

def put(branch, path, data, msg):
    raw = data if isinstance(data, bytes) else data.encode()
    gh("PUT", f"/repos/{HOME_REPO}/contents/{path}",
       {"message": msg, "branch": branch, "content": base64.b64encode(raw).decode()})

def make_cover(post, tags, out):
    """Claude picks the evidence to show; cover.verify drops anything not in the logs; then it's drawn."""
    ev = {p.name: p.read_text() for p in sorted((OUT / "evidence").glob("*.log"))}
    fig = None
    try:
        pick = llm.ask("claude", "You choose the cover image for a hands-on AWS post.",
                       f"{cover.PICK}\n\nTITLE: {post['title']}\n\nPOST:\n{post['body'][:15000]}\n\nEVIDENCE:\n"
                       f"{json.dumps({k: v[-12000:] for k, v in ev.items()})[:60000]}", cover.FIGURE_SCHEMA,
                       tool="submit_figure")
        fig = cover.verify(pick, "\n".join(ev.values()))
        if not fig:
            llm.warn("cover: the picked evidence didn't match the logs; using the title-only card")
    except Exception as e:
        llm.warn(f"cover: {str(e)[:200]}")
    cover.render(post["title"], fig, tags, out)
    return fig

def branch_free(name):
    try:
        gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/{name}")
        return False
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            return True
        raise

def gather(res, topic, slug):
    lab_dir = OUT / "lab"
    lab = {str(p.relative_to(lab_dir)): p.read_text() for p in sorted(lab_dir.rglob("*")) if p.is_file()}
    ev, budget = {}, 70_000
    for p in sorted((OUT / "evidence").glob("*.log")):
        t = p.read_text()[-8000:]
        if budget - len(t) < 0:
            break
        ev[p.name] = t; budget -= len(t)
    context = (f"TOPIC:\n{json.dumps(topic)[:20000]}\n\nRUN: status={res['status']}, attempts={res.get('attempts')}, "
               f"minutes={res.get('minutes')}, summary={res.get('summary')}\n\nLAB_SLUG: {slug}\nLAB_URL: "
               f"{REPO_WEB}/tree/main/labs/{slug}\n\nLAB:\n{json.dumps(lab)[:60000]}\n\nEVIDENCE:\n{json.dumps(ev)}")
    return lab, context

AUTHOR_FILE = pathlib.Path(__file__).with_name("author.md")
STYLE_TAGS = ("aws", "kubernetes", "devops", "docker")

def style_signals():
    """Titles, openings and headings of the most-reacted recent dev.to posts in our tags, plus what has worked on
    Abhi's own posts. Signals only: the writer borrows what works, never the wording."""
    from radar import call, load_insights
    seen, arts = set(), []
    for tag in STYLE_TAGS:
        try:
            for a in call("GET", f"https://dev.to/api/articles?tag={tag}&top=30&per_page=30") or []:
                if a.get("id") not in seen:
                    seen.add(a.get("id")); arts.append(a)
        except Exception as e:
            llm.warn(f"style signals: dev.to tag {tag}: {str(e)[:200]}")
    arts.sort(key=lambda a: -(a.get("public_reactions_count") or a.get("positive_reactions_count") or 0))
    lines = [f"- {a.get('title')} ({a.get('public_reactions_count') or a.get('positive_reactions_count') or 0} "
             f"reactions, {a.get('comments_count', 0)} comments)" for a in arts[:15]]
    shapes = []
    for a in arts[:5]:
        try:
            full = call("GET", f"https://dev.to/api/articles/{a['id']}")
            body = full.get("body_markdown") or ""
            first = next((p.strip() for p in body.split("\n\n") if p.strip() and not p.lstrip().startswith(("#", "!", "{%", "---"))), "")
            heads = re.findall(r"^#{2,3} (.+)$", body, re.M)[:8]
            shapes.append(f"- \"{a.get('title')}\": opens with: {first[:300]!r}; headings: {heads}")
        except Exception as e:
            llm.warn(f"style signals: dev.to article: {str(e)[:200]}")
    own = load_insights()
    return ("Most-reacted recent dev.to posts (aws, kubernetes, devops, docker):\n" + "\n".join(lines) +
            "\n\nHow the top ones open and section themselves:\n" + "\n".join(shapes) +
            f"\n\nWhat has worked on Abhi's own posts:\n{own or 'nothing measured yet'}")

TITLE_RULES = """A good title names the SYMPTOM the reader saw, in their words, with the service name early (ECS, EKS,
Fargate...), so it stops the right person scrolling and matches what they'd search. A metric or API name belongs in
it only if that's what they'd type. A small surprise or contrast is good; clickbait and promises the lab doesn't back
are not. Under 75 characters. Shapes that work: "ECS shows 1 running task while 3 are still draining", "Fargate worker stuck at 0 tasks? The scaling guard never runs", "EKS pods stuck in ContainerCreating after
an upgrade". The opening puts the reader in that moment in the first sentence."""

HEADLINE_SCHEMA = {"type": "object", "required": ["title", "hook"], "properties": {
    "title": {"type": "string", "description": "max 75 characters: the reader's symptom, true to the evidence"},
    "hook": {"type": "string", "description": "the first two or three sentences of the post"}}}

def headline_round(context, signals, author):
    """Each of the three proposes a title and opening; all three vote; 2 of 3 or the most votes wins."""
    props = []
    for who in llm.PANEL:
        try:
            h = llm.ask(who, "You write headlines and openings for Abhimanyu's hands-on AWS container posts.",
                        f"Propose ONE title and ONE opening (two or three sentences) for this post.\n{TITLE_RULES}\n"
                        "Use STYLE SIGNALS for what works now and AUTHOR for any personal angle.\n\nSTYLE SIGNALS:\n"
                        f"{signals}\n\nAUTHOR:\n{author}\n\n"
                        f"{context[:40000]}", HEADLINE_SCHEMA, tool="submit_headline")
            if h.get("title") and h.get("hook"):
                props.append({"id": f"h{len(props) + 1}", "by": llm.NAMES[who], "title": str(h["title"])[:120],
                              "hook": str(h["hook"])[:800]})
        except Exception as e:
            llm.warn(f"{who} proposed no headline: {str(e)[:200]}")
    if not props:
        return None
    votes = llm.panel_votes(props, "You are a busy ECS/EKS engineer scrolling dev.to or LinkedIn. Which of these would "
                            "you click, because it names a problem you've had or fear, AND would the post deliver it? "
                            f"Vote yes only for ones you would publish.\n{TITLE_RULES}",
                            f"STYLE SIGNALS:\n{signals}", tier="write")
    best = max(props, key=lambda p: votes[p["id"]]["yes"])
    best["yes"] = votes[best["id"]]["yes"]
    best["others"] = [f"{p['by']}: {p['title']} ({votes[p['id']]['yes']}/3)" for p in props if p is not best]
    return best

CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.S)
LIMITS = {"prose": 950, "blocks": 7, "longest": 12}       # the rules say 900 / 6 / 10; a little slack before cutting

def measure(md):
    blocks = CODE_BLOCK.findall(md)
    prose = re.sub(r"!\[[^\]]*\]\([^)]*\)|\]\([^)]*\)", " ", CODE_BLOCK.sub(" ", md))
    return {"prose": len(re.findall(r"[A-Za-z0-9][\w'\u2019.-]*", prose)), "blocks": len(blocks),
            "longest": max((len(b.strip("\n").splitlines()) for b in blocks), default=0)}

def over(m):
    return [k for k, v in LIMITS.items() if m[k] > v]

def _code_lines(md):
    return {re.sub(r"\s+", " ", l).strip() for b in CODE_BLOCK.findall(md) for l in b.splitlines()}

def only_cuts(old, new):
    """Every code line in the shorter version was already in the reviewed one (apart from '...' markers and the
    try-it block's comments), so cutting can't sneak in a command or output nobody checked."""
    had = _code_lines(old)
    for l in _code_lines(new) - had:
        if l and l.strip(". \u2026") and not re.sub(r"\s+#.*$", "", l) in {re.sub(r"\s+#.*$", "", h) for h in had}:
            return False
    return True

def tighten(post):
    """The reviewed post, cut to length if it runs long. Returns (post, note for the PR)."""
    m0 = m = measure(post.get("body", ""))
    for _ in range(2):
        if not over(m):
            break
        try:
            cut = llm.ask("chatgpt", "You edit hands-on posts down to length without changing what they say.",
                          f"This post is too long: {m['prose']} words of prose (limit 900), {m['blocks']} code blocks "
                          f"(limit 6), longest block {m['longest']} lines (limit 10). Cut it to fit. REMOVE, don't "
                          "rewrite: keep the title, the opening, the section structure, every fact and the voice. "
                          "Trim output blocks with '...' and keep the lines that prove the point. Drop repetition, "
                          "background a reader can skip, and detail that lives in the lab scripts. Never add a command, "
                          f"output or claim. Return every field.\n\nRULES:\n{ARTICLE_RULES}\n\nPOST:\n{json.dumps(post)}",
                          POST_SCHEMA, tool="revise")
        except Exception as e:
            llm.warn(f"tighten: {str(e)[:200]}"); break
        if not cut.get("body") or not only_cuts(post["body"], cut["body"]):
            llm.warn("tighten: the shorter version changed code or output; kept the reviewed one"); break
        post = {**post, **{k: v for k, v in cut.items() if v}}
        m = measure(post["body"])
    note = (f"cut from {m0['prose']} to {m['prose']} words of prose" if m is not m0 else "")
    return post, note

def write_post(context):
    notes = os.environ.get("REDRAFT_NOTES", "").strip()[:2000]
    if notes:                                      # Abhi's `/redraft` comment on the previous draft
        context = (f"ABHI'S NOTES ON THE PREVIOUS DRAFT (follow them; they override style choices, never the hard "
                   f"rules):\n{notes}\n\n{context}")
    author = AUTHOR_FILE.read_text() if AUTHOR_FILE.exists() else ""
    signals = style_signals()
    head = headline_round(context, signals, author)
    chosen = (f"TITLE: {head['title']}\nHOOK: {head['hook']}" if head else "TITLE/HOOK: write your own")
    context = f"{chosen}\n\nAUTHOR:\n{author}\n\nSTYLE SIGNALS:\n{signals}\n\n{context}"
    post = llm.ask("chatgpt", "You write hands-on AWS container articles for Abhimanyu's blog, strictly from real lab "
                              "output, in a warm human voice.", f"Write the article and the LinkedIn post.\n\n"
                   f"RULES:\n{ARTICLE_RULES}\n\n{context}", POST_SCHEMA)
    post, log = llm.refine("blog article and LinkedIn post", post, ARTICLE_RUBRIC, f"RULES:\n{ARTICLE_RULES}\n\n{context}",
                           POST_SCHEMA, writer="chatgpt", reviewer="claude", judge="gemini")
    log["headline"] = head
    post, log["tightened"] = tighten(post)
    return post, log

def on_main(path):
    try:
        gh("GET", f"/repos/{HOME_REPO}/contents/{path}?ref=main")
        return True
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            return False
        raise

def open_pr(issue_no, res, topic):
    slug = res["slug"][:60].strip("-")
    if on_main(f"labs/{slug}"):                    # already published: the new post needs its own name
        slug = f"{slug[:50].strip('-')}-{res['run_id'][-6:]}"
    branch, n = f"draft/{slug}", 2
    while not branch_free(branch):                 # a redraft: new branch, same post and lab names
        branch, n = f"draft/{slug[:70]}-v{n}", n + 1
    lab, context = gather(res, topic, slug)
    v = llm.verdict(VERDICT_Q, context)
    if not v["passed"]:
        add_label(issue_no, "replication-disputed")
        comment(issue_no, "\u26a0\ufe0f The lab reported reproduced-and-fixed, but the panel did not confirm the "
                          f"replication, so nothing was drafted.\n\n{verdict_md(v)}\n\nThe lab and raw output are in the "
                          f"`lab` attachment of workflow run {res['run_id']}.")
        return
    post, log = write_post(context)
    tags = [t for t in (re.sub(r"[^a-z0-9]", "", str(t).lower()) for t in post.get("tags", [])) if t][:4]
    today = datetime.date.today().isoformat()
    j = lambda v: json.dumps(v, ensure_ascii=False)
    linkedin = re.sub(r"@(?=\w)", "", str(post.get("linkedin", "")).strip())
    srcs = {x.get("url") for x in topic.get("sources") or [] if isinstance(x, dict)}
    replies = [{"url": str(r["url"]), "text": re.sub(r"@(?=\w)", "", str(r.get("text", "")).strip())}
               for r in post.get("source_replies") or []
               if isinstance(r, dict) and r.get("text") and (not srcs or r.get("url") in srcs)]
    png = OUT / "cover.png"
    fig = make_cover(post, tags, png)
    img_path = f"/assets/posts/{slug}/cover.png"
    alt = re.sub(r"[\[\]\n]", " ", (fig or {}).get("alt") or f"Cover: {post['title']}")[:300]
    body_md = f"![{alt}]({SITE_URL}{img_path})\n\n" + fix_output_tabs(post["body"]).strip() + "\n"
    front = "\n".join(["---", f"title: {j(post['title'][:90])}", f"description: {j(post['description'][:300])}",
                       f"image: {img_path}", f"image_alt: {j(alt)}",
                       f"tags: {json.dumps(tags)}", "channels: [\"devto\", \"hashnode\"]", f"lab: labs/{slug}",
                       f"linkedin: {j(linkedin)}", f"replies: {j(replies)}", "---", ""])
    files = {f"site/_posts/{today}-{slug}.md": front + body_md, f"site{img_path}": png.read_bytes()}
    for name, text in lab.items():
        files[f"labs/{slug}/{name}"] = text
    files[f"labs/{slug}/providers.tf"] = (
        "# Use this instead of the runner's backend file: terraform init && terraform apply\n"
        'variable "region" {\n  type    = string\n  default = "eu-central-1"\n}\n'
        f'variable "lab_name" {{\n  type    = string\n  default = "{slug[:24].strip("-")}"\n}}\n'
        'variable "permissions_boundary" {\n  type    = string\n  default = null\n}\n'
        'provider "aws" {\n  region = var.region\n}\n')
    for p in sorted((OUT / "evidence").glob("*.log")):
        files[f"labs/{slug}/evidence/{p.name}"] = p.read_text()

    warn = "" if res.get("destroyed") else ("\n\n\u26a0\ufe0f **The lab may not have been fully destroyed.** The next lab run "
                                             "retries automatically; check the AWS console if you're worried.")
    preview = safe_text(post["body"], "[preview left out: failed the link-safety check; read the post file]")
    li_preview = safe_text(linkedin.replace("{LINKS}", "(dev.to and Medium links go here)")
                           .replace("{POST_URL}", "(link to the post)"), "[left out]")
    m = measure(post["body"])
    words = len(re.findall(r"\S+", post["body"]))
    raw_cover = f"https://raw.githubusercontent.com/{HOME_REPO}/refs/heads/{branch}/site{img_path}"
    shown = ("a chart of the lab's real numbers" if (fig or {}).get("kind") == "chart" else
             "real lines from the lab's output" if fig else "the title (no evidence picture passed the check)")
    cut = f" The panel {log['tightened']}." if log.get("tightened") else ""
    body = (f"## {safe_text(post['title'])}\n\n_{safe_text(post['description'])}_\n\n"
            f"![cover]({raw_cover})\n\n"
            f"**Length:** about {words:,} words, {max(1, round(words / 230))} min read ({m['prose']:,} words of prose)."
            f"{cut} **Cover:** {shown}; used on your site, dev.to, Medium and LinkedIn.\n\n"
            f"<details open><summary><b>Read the post</b></summary>\n\n{preview}\n\n</details>\n\n"
            f"<details><summary><b>LinkedIn post</b></summary>\n\n{li_preview}\n\n</details>\n\n"
            + "".join(f"<details><summary><b>Reply for the source thread</b> ({safe_text(r['url'], 'a source')})"
                      "</summary>\n\n" + safe_text(r["text"].replace("{POST_URL}", "(link to the post)"), "[left out]")
                      + "\n\n</details>\n\n" for r in replies)
            + "<details><summary><b>How the panel checked it</b></summary>\n\n"
            f"**Topic:** issue no. {issue_no}. **Lab:** {safe_text(res.get('summary'))}\n\n"
            f"**Run:** reproduced and fixed in attempt {res.get('attempts')}, {res.get('minutes')} min total "
            f"(workflow run {res['run_id']}).\n\n{verdict_md(v)}\n\n"
            f"{headline_md(log.get('headline'))}"
            f"{review_md('Lab review', res.get('review'))}\n\n{review_md('Article review', log)}\n\n"
            f"**Model usage:** lab job: {res.get('usage')}; draft job: {llm.usage_summary()}\n\n</details>{warn}\n\n"
            "**Merge** to publish on your site and dev.to; you then get the Medium link and your LinkedIn draft. "
            f"To change anything, edit `site/_posts/{today}-{slug}.md` in this PR, or comment `/redraft <what to change>`.\n")
    title = safe_text("Draft: " + post["title"], "Draft post")[:120]
    if len(body) > 60000:
        body = body.replace(preview, "[preview too long for the PR description; read the post file]")
    assert_safe(title); assert_safe(body)          # check before anything is created, so a failure leaves no mess
    base = gh("GET", f"/repos/{HOME_REPO}/git/ref/heads/main")["object"]["sha"]
    gh("POST", f"/repos/{HOME_REPO}/git/refs", {"ref": f"refs/heads/{branch}", "sha": base})
    for path, text in files.items():
        put(branch, path, text, f"draft: add {path}")
    pr = gh("POST", f"/repos/{HOME_REPO}/pulls", {"title": title, "head": branch, "base": "main",
                                                  "body": f"{body}\n\nCloses #{issue_no}"})
    print(f"opened PR {pr['number']} from {branch}")
    old = os.environ.get("OLD_PR", "")
    if old.isdigit():                              # a `/redraft`: retire the draft it replaces
        comment(int(old), f"Replaced by the redraft in pull request no. {pr['number']}.")
        gh("PATCH", f"/repos/{HOME_REPO}/pulls/{old}", {"state": "closed"})
    comment(issue_no, f"\u2705 Replicated and fixed, and the panel confirmed it ({v['yes']} of 3). The draft post is "
                      f"ready for your review in pull request no. {pr['number']}.{warn}")
    add_label(issue_no, "drafted")

def main():
    issue_no = int(os.environ["ISSUE"])
    rf = OUT / "result.json"
    if not rf.exists():
        comment(issue_no, f"The lab job (workflow run {os.environ.get('RUN_ID', '?')}) ended without a result: it "
                          "was not approved, was cancelled, or crashed before writing output. Nothing was drafted.")
        return
    res = json.loads(rf.read_text())
    topic = json.loads((OUT / "topic.json").read_text()) if (OUT / "topic.json").exists() else {}
    if res["status"] == "reproduced_and_fixed":
        try:
            open_pr(issue_no, res, topic)
        except Unsafe:
            comment(issue_no, "Replicated and fixed, but the draft's PR text failed the link-safety check, so no PR "
                              f"was opened. The lab and its output are in the `lab` attachment of workflow run {res['run_id']}.")
        return
    warn = "" if res.get("destroyed") in (True, None) else "\n\n⚠️ **The lab may not have been fully destroyed.** The next run retries."
    if res["status"] == "dry_run":
        msg = (f"Dry run: the panel wrote and reviewed a lab; AWS was not touched.\n\n**Lab:** {safe_text(res.get('summary'))}"
               f"\n\n{review_md('Lab review', res.get('review'))}\n\n**Model usage:** {res.get('usage')}")
    else:
        lf = res.get("last_failure") or {}
        out = safe_text(lf.get("output") or res.get("error") or "", "[output left out: failed the link-safety check]")[-1500:]
        out = out.replace("```", "'''")
        nice = {"not_reproduced": "\U0001f50e The lab ran but could not reproduce the problem.",
                "reproduced_fix_failed": "\U0001f9ea The lab reproduced the problem, but the fix did not work.",
                "failed": "❌ The lab did not get to a result."}.get(res["status"], res["status"])
        msg = (f"{nice} {res.get('attempts')} attempt(s), {res.get('minutes')} min, workflow run {res['run_id']}."
               f"{warn}\n\n**Last failure:** {safe_text(lf.get('step') or 'before the lab ran')}\n```\n{out}\n```\n\n"
               f"{_cleanup_md(res)}**Lab:** {safe_text(res.get('summary') or 'not written')}\n\nThe lab files and raw output are in the run's `lab` "
               f"attachment. Remove and re-add the `replicate` label to retry.\n\n**Model usage:** {res.get('usage')}")
        if res["status"] == "not_reproduced":
            add_label(issue_no, "not-reproduced")
    try:
        comment(issue_no, msg)
    except Unsafe:
        comment(issue_no, f"The lab run ended with status {res['status']}. Details failed the link-safety check; see the "
                          f"`lab` attachment of workflow run {res['run_id']}.")

def _cleanup_md(res):
    acts = (res.get("janitor_before") or []) + (res.get("janitor_after") or [])
    swept = [s for s in res.get("swept") or [] if s.get("destroyed")]
    if not acts and not swept:
        return ""
    lines = [f"- destroyed leftover lab {safe_text(s['lab'])}" for s in swept] + [f"- {safe_text(a)}" for a in acts[:15]]
    return "**Cleanup of leftovers:**\n" + "\n".join(lines) + "\n\n"

if __name__ == "__main__":
    main()
