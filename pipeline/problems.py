#!/usr/bin/env python3
"""Problem finder: find problems many AWS container users hit, let the three-AI panel vote, and open
the winners as issues. The best one gets `replicate`. GitHub access and text safety live in radar.py."""
import gzip, html, json, os, re, time, urllib.parse, urllib.request
from radar import (WATCH, SKIP_SOURCES, NO_REFS, LAB_LIMITS, gh, neutralize, existing_issues, post_topics,
                   propose_all, load_insights)

MAX_ISSUES, LABEL, UA, DAYS = 3, "problem", "hero-problems/1.0", 30
SO_TAGS = ["amazon-eks", "amazon-ecs", "aws-fargate", "eksctl", "karpenter", "aws-load-balancer-controller",
           "amazon-ecr"]
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}
WEAK = ("question", "needs-information", "invalid", "duplicate")
FIX = re.compile(r"\bfix(es|ed|ing)?\b", re.I)
API = "https://api.github.com"
COMMENT_PAGES, COMMENT_BUDGET = 5, 6000

def so_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw)

def so(tag, since):
    return so_get("https://api.stackexchange.com/2.3/questions?site=stackoverflow&sort=votes&order=desc"
                  f"&pagesize=8&fromdate={since}&tagged={urllib.parse.quote(tag)}").get("items", [])

def so_answers(qids):
    out = {}
    for i in range(0, len(qids), 30):
        ids = ";".join(str(q) for q in qids[i:i + 30])
        for page in range(1, 4):
            d = so_get(f"https://api.stackexchange.com/2.3/questions/{ids}/answers?site=stackoverflow"
                       f"&sort=votes&order=desc&pagesize=100&page={page}&filter=withbody")
            for a in d.get("items", []):
                out.setdefault(a["question_id"], []).append(a)
            if not d.get("has_more"):
                break
    return out

def discussion(comments_url, prefix):
    """All human comments in order, tagged maintainer/other. complete=False if anything was cut."""
    if not comments_url.startswith(API + "/repos/"):
        raise RuntimeError(f"unexpected comments URL {comments_url}")
    path, raw, complete = comments_url[len(API):], [], True
    for page in range(1, COMMENT_PAGES + 1):
        got = gh("GET", f"{path}?per_page=100&page={page}")
        raw += got
        if len(got) < 100:
            break
    else:
        complete = False                       # more comments than we read
    out = []
    for n, c in enumerate(raw, 1):
        if (c.get("user") or {}).get("type") == "Bot":
            continue
        maint = c.get("author_association") in MAINTAINERS
        text, limit = " ".join((c.get("body") or "").split()), (1500 if maint else 600)
        complete &= len(text) <= limit
        out.append({"id": f"{prefix}.c{n}", "by": "maintainer" if maint else "other", "text": text[:limit]})
    if sum(len(c["text"]) for c in out) > COMMENT_BUDGET:
        complete = False
        out = [c for c in out if c["by"] == "maintainer"]
        while out and sum(len(c["text"]) for c in out) > COMMENT_BUDGET:
            out.pop(0)
    return out, complete

def collect():
    since = int(time.time()) - DAYS * 86400
    day = time.strftime("%Y-%m-%d", time.gmtime(since))
    items, seen = [], set()
    def add(i):
        if i["url"] not in seen and i["url"] not in SKIP_SOURCES:
            seen.add(i["url"]); items.append(i)
    got, chunk = [], []
    for r in WATCH + [None]:                   # search queries are capped at 256 characters
        base = f"is:issue is:open created:>{day} "
        if r and len(base + " ".join(f"repo:{x}" for x in chunk + [r])) <= 250:
            chunk.append(r); continue
        if chunk:
            q = base + " ".join(f"repo:{x}" for x in chunk)
            try:
                got += gh("GET", "/search/issues?sort=reactions&order=desc&per_page=12&q=" + urllib.parse.quote(q))["items"]
            except Exception as e:
                print(f"warn: github search: {e}")
        chunk = [r] if r else []
    got.sort(key=lambda i: -((i.get("reactions") or {}).get("total_count", 0) + i.get("comments", 0)))
    got = got[:20]
    for n, i in enumerate(got, 1):
        if i["html_url"] in SKIP_SOURCES:
            continue
        comments, complete = [], True
        if i.get("comments"):
            try:
                comments, complete = discussion(i["comments_url"], f"g{n}")
            except Exception as e:
                print(f"warn: skipped an item, could not read its comments: {e}"); continue
        add({"src": "github", "id": f"g{n}", "title": i["title"], "url": i["html_url"],
             "reactions": (i.get("reactions") or {}).get("total_count", 0),
             "labels": [l.get("name") for l in (i.get("labels") or [])],
             "reporter_says": (i.get("body") or "")[:1500], "complete": complete, "comments": comments})
    for t in SO_TAGS:
        try:
            for x in so(t, since):
                if x.get("score", 0) >= 1:
                    add({"src": "stackoverflow", "title": html.unescape(x["title"]), "url": x["link"],
                         "score": x["score"], "qid": x["question_id"], "answer_count": x.get("answer_count", 0)})
        except Exception as e:
            print(f"warn: SO {t}: {e}")
    sos = [i for i in items if i["src"] == "stackoverflow"]
    try:
        ans = so_answers([i["qid"] for i in sos])
    except Exception as e:
        print(f"warn: SO answers: {e}"); ans = None
    for n, i in enumerate(sos, 1):
        got = sorted((ans or {}).get(i.pop("qid"), []), key=lambda a: (not a.get("is_accepted"), -a.get("score", 0)))
        texts = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", a.get("body") or "")).split()) for a in got[:3]]
        i["id"] = f"s{n}"
        i["answers"] = [{"id": f"s{n}.a{k}", "accepted": bool(a.get("is_accepted")), "score": a.get("score", 0),
                         "text": tx[:800]} for k, (a, tx) in enumerate(zip(got, texts), 1)]
        i["complete"] = (ans is not None and len(got) == i.pop("answer_count") <= 3
                         and all(len(tx) <= 800 for tx in texts))
    return items

def evidence_ok(item, eid):
    """A cited id counts only if it belongs to this source and is a maintainer comment or a strong answer."""
    if item["src"] == "github":
        return any(c["id"] == eid and c["by"] == "maintainer" for c in item.get("comments", []))
    return any(a["id"] == eid and (a["accepted"] or a["score"] >= 3) for a in item.get("answers", []))

def judge(t, srcs, by_url):
    """Return (skip | confirmed | unconfirmed, evidence ids). Any disputing source means skip."""
    per = {v.get("url"): v for v in (t.get("per_source") or []) if isinstance(v, dict)}
    if any(v.get("verdict") == "disputes" for v in per.values()):
        return "skip", []
    ev = []
    for u in srcs:
        v, item = per.get(u, {}), by_url[u]
        ids = [e for e in (v.get("evidence_ids") or []) if isinstance(e, str)]
        weak = any(w in str(l).lower() for l in item.get("labels", []) for w in WEAK)
        if (v.get("verdict") == "supports" and item.get("complete") and not weak and ids
                and all(evidence_ok(item, e) for e in ids)):
            ev += ids
    return ("confirmed" if ev else "unconfirmed"), ev

TOPIC_SCHEMA = {"type": "object", "required": ["topics"], "properties": {"topics": {"type": "array", "items": {
    "type": "object", "required": ["title", "problem", "cause", "lab", "replicability", "sources", "per_source"],
    "properties": {
        "title": {"type": "string", "description": "max 80 chars"},
        "problem": {"type": "string", "description": "2 sentences"},
        "cause": {"type": "string", "description": "1 sentence: the cause you claim"},
        "lab": {"type": "string", "description": "1-2 sentences: how the lab reproduces it, then investigates or fixes it"},
        "replicability": {"type": "integer", "description": "1-10: how well this can be reproduced within LAB LIMITS"},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs copied exactly from ITEMS"},
        "per_source": {"type": "array", "items": {"type": "object", "required": ["url", "verdict", "evidence_ids"],
            "properties": {"url": {"type": "string"}, "verdict": {"enum": ["supports", "disputes", "silent"]},
                           "evidence_ids": {"type": "array", "items": {"type": "string"}}}}}}}}}}

def ask_panel(items, existing):
    return propose_all(
        "You pick topics for Abhimanyu's hands-on blog about AWS containers. Every post REPLICATES a real "
        "problem in a disposable AWS lab, then investigates or fixes it.",
        "From the ITEMS below (GitHub issues from AWS container projects and Stack Overflow questions), choose up "
        f"to {MAX_ISSUES + 2} problems that many people hit and that make a strong replicate-then-fix post. Prefer "
        "problems students and newcomers to EKS/ECS hit. Do not repeat anything in EXISTING.\n"
        "How to judge: for GitHub items, 'reporter_says' is the reporter's own theory and may be wrong; "
        "'comments' lists the human comments in order, each with an id and 'by' (maintainer or other). "
        "For Stack Overflow items, 'answers' lists answers with id, accepted and score. For each topic, "
        "state the cause you claim in one sentence. Then, for EVERY source URL you list, add an entry to "
        "per_source with verdict 'supports' (a specific comment or answer states this cause), 'disputes' "
        "(a maintainer or answer contradicts it, or corrects the reporter's diagnosis) or 'silent', and "
        "evidence_ids: the ids of the specific comments or answers that state this cause. Only cite text "
        "that actually supports the cause; a question being 'answered' is not evidence. Never present an "
        "unsupported cause as fact, and never use 'fix' in the title unless a source supports the cause; "
        "use wording like 'Debugging X' or 'Why X happens'.\n"
        f"{NO_REFS}\n\nLAB LIMITS:\n{LAB_LIMITS}\n\n"
        f"WHAT HAS WORKED SO FAR:\n{load_insights() or 'no posts measured yet'}\n\n"
        f"EXISTING:\n{json.dumps(existing[:200])}\n\nITEMS:\n{json.dumps(items)}",
        TOPIC_SCHEMA)

def main():
    existing, used = existing_issues()
    items = [i for i in collect() if i["url"] not in used]
    print(f"collected {len(items)} new items ({len(used)} source URLs already used)")
    if not items:
        print("done: nothing new"); return
    topics = ask_panel(items, existing)
    by_url = {i["url"]: i for i in items}
    texts = {x["id"]: x["text"] for i in items for x in i.get("comments", []) + i.get("answers", [])}

    def render(t, srcs):
        name = neutralize(t.get("title"))[:100]
        status, ev = judge(t, srcs, by_url)
        if status == "skip":
            print(f"skip, a source disputes the cause: {name}"); return None
        if status == "unconfirmed" and FIX.search(name):
            print(f"skip, unconfirmed but titled as a fix: {name}"); return None
        if status == "confirmed":
            head = "**Status:** confirmed by the quoted text below. Read it before approving."
            head += "".join(f"\n> {neutralize(texts[e])[:240]}" for e in ev[:3])
        else:
            head = ("**Status:** \u26a0\ufe0f unconfirmed. No maintainer comment or strong answer "
                    "was found that states this cause.")
        return (f"{head}\n\n**Claimed cause:** {neutralize(t.get('cause'))}\n\n"
                f"**The problem:** {neutralize(t.get('problem'))}\n\n"
                f"**Lab idea:** {neutralize(t.get('lab'))}")

    post_topics(topics, items, existing, used, LABEL, render, MAX_ISSUES)

if __name__ == "__main__":
    main()
