#!/usr/bin/env python3
"""Radar: find fresh AWS container news (AWS What's New, releases, Hacker News), let the three-AI
panel vote, and open the winners as topic issues in this repo. The best one gets `replicate`.

Safety rules, shared by every script in pipeline/:
  * GitHub writes need HERO_PUBLISH=1 and BOT_TOKEN, and gh() allows only the writes in
    WRITE_RULES: all of them in HOME_REPO, none on main, none under .github/.
  * Nothing the bot writes to an issue, comment, PR or commit message may contain a GitHub
    link, reference or @mention, so other projects never get a "mentioned this" backlink or a
    notification. (File contents in a draft PR may link out: files never create backlinks.)
"""
import base64, calendar, hashlib, html, json, os, re, time, urllib.error, urllib.parse, urllib.request
import xml.etree.ElementTree as ET

HOME_REPO = "Abhimanyu9988/Abhimanyu9988.github.io"
PROFILE_REPO = "Abhimanyu9988/Abhimanyu9988"     # his own profile repo: LinkedIn drafts only
REPO = os.environ["REPO"]
if REPO != HOME_REPO:
    raise SystemExit(f"refusing to run: REPO is {REPO!r}, not {HOME_REPO}")
PUBLISH = os.environ.get("HERO_PUBLISH") == "1"
DRY = os.environ.get("DRY_RUN", "").lower() == "true"
MAX_ISSUES, LABEL, UA = 3, "radar", "hero-radar/1.0"
LABELS = {"radar": ("0e8a16", "Topic idea from the radar"),
          "problem": ("d93f0b", "Problem idea from the problem finder"),
          "replicate": ("5319e7", "Replicate this on the AWS lab account (needs your deployment approval)"),
          "drafted": ("1d76db", "Replicated; a draft post PR is open"),
          "not-reproduced": ("cccccc", "The lab could not reproduce this"),
          "replication-disputed": ("fbca04", "The lab ran, but the panel did not confirm the replication"),
          "analytics": ("0052cc", "Impact log: weekly analytics for the published posts")}
# AWS container projects: releases feed the radar, issues feed the problem finder.
WATCH = ["aws/containers-roadmap", "aws/karpenter-provider-aws", "aws/amazon-vpc-cni-k8s",
         "kubernetes-sigs/aws-load-balancer-controller", "kubernetes-sigs/aws-ebs-csi-driver",
         "aws/amazon-ecs-agent", "aws/eks-pod-identity-agent", "eksctl-io/eksctl",
         "aws/aws-node-termination-handler", "awslabs/amazon-eks-ami", "bottlerocket-os/bottlerocket"]
HN_QUERIES = ["EKS", "ECS", "Fargate", "Karpenter", "ECR", "Bottlerocket", "AWS Kubernetes"]
WHATS_NEW = "https://aws.amazon.com/about-aws/whats-new/recent/feed/"
CONTAINER_WORDS = re.compile(r"\b(EKS|ECS|Fargate|ECR|App Runner|Karpenter|Bottlerocket|containers?|"
                             r"Kubernetes|Copilot|Cloud Map|service connect)\b", re.I)
# What a lab can do. Shared with the panel (so it rejects impossible topics) and with lab.py.
LAB_LIMITS = """The lab runs in a dedicated AWS account in eu-central-1, created and destroyed by Terraform in one
GitHub Actions job (about 60 minutes of wall time, under 1 USD per run).
- Allowed EC2 instance types: t3.medium, t3.large, t4g.medium, t4g.large, m7g.large, c7g.large. At most 3 instances.
- Not allowed: EKS managed node groups, Auto Scaling groups, spot requests, EC2 fleets, NAT gateways.
  EKS worker nodes are plain aws_instance resources joined with nodeadm; subnets are public.
- Services the lab can use: EKS, EC2, ECS (incl. Fargate in public subnets), ECR, ELB, CloudWatch, Logs, SQS,
  EventBridge, Application Auto Scaling, SSM parameters (read). Not available: RDS, S3 buckets, Lambda, Route 53, KMS.
- IAM: only roles named hero-lab-* that carry the permissions boundary. EKS Pod Identity and IRSA both work.
- Tools in the lab sandbox: terraform, aws CLI v2, kubectl, helm, jq, curl, git, openssl (no docker)."""
# Sources of the deleted issues 9 and 10. Never use them again.
SKIP_SOURCES = {
    "https://github.com/aws/karpenter-provider-aws/issues/9616",
    "https://github.com/aws/karpenter-provider-aws/issues/9617",
    "https://github.com/aws/karpenter-provider-aws/issues/9644",
}
NO_REFS = ("Never put URLs, issue or PR numbers, '#' references, owner/repo references or @-handles "
           "in any text field; links belong only in the sources key.")

class Blocked(RuntimeError): """A GitHub write the safety rules don't allow."""
class Unsafe(RuntimeError): """Text that could create a link, backlink or mention."""

# ---------- text safety ----------
GH_URL = re.compile(r"(?:https?://)?(?:[\w-]+\.)*github\.com\b[^\s<>()\[\]\"'`,;]*", re.I)
XREF = re.compile(r"\b([\w.-]+/[\w.-]+)#(\d+)")
SHAREF = re.compile(r"\b([\w.-]+(?:/[\w.-]+)?)@([0-9a-f]{7,40})\b", re.I)
HASHREF = re.compile(r"(?<![\w&/])#(\d+)\b")
GHREF = re.compile(r"\bGH-(\d+)\b", re.I)
REL_LINK = re.compile(r"(?<![\w.:/-])/[\w.-]+/[\w.-]+/(?:issues|pull|pulls|discussions|commit)/\w", re.I)
MENTION = re.compile(r"@(?!\u200b)[A-Za-z0-9]")
ZW = re.compile(r"[\u200b-\u200d\u2060\ufeff]")
CHECKS = ((XREF, "an owner/repo#N reference"), (SHAREF, "an owner@sha reference"),
          (HASHREF, "a #N reference"), (GHREF, "a GH-N reference"),
          (REL_LINK, "a relative GitHub path"), (MENTION, "an @mention"))
KINDS = {"issues": "issue", "pull": "PR", "pulls": "PR", "discussions": "discussion", "commit": "commit"}

def _describe(m):
    path = re.sub(r"^(?:https?://)?[^/]*", "", m.group(0))
    p = [x for x in (re.split(r"[#?]", seg)[0] for seg in path.split("/")) if x]
    if len(p) < 2:
        return "GitHub"
    repo = f"{p[0]}/{p[1]}"
    if len(p) >= 4 and p[2] in KINDS:
        return f"{repo} {KINDS[p[2]]} {p[3][:40]}"
    if len(p) >= 5 and p[2] == "releases" and p[3] == "tag":
        return f"{repo} release {p[4][:40]}"
    return f"{repo} on GitHub"

REL_PATH = re.compile(r"(?<![\w.:/-])/([\w.-]+)/([\w.-]+)/(issues|pull|pulls|discussions|commit)/(\w+)", re.I)

def neutralize(s):
    """Turn GitHub links and references into plain text, and break @mentions. Works on the decoded
    text (the same forms assert_safe checks), so encoded tricks can't survive it."""
    s = _views(str(s or ""))[-1]
    s = re.sub(r"github\s*\.\s*com", "github.com", ZW.sub("", s), flags=re.I)
    s = s.replace("<", "\u2039").replace(">", "\u203a")
    s = GH_URL.sub(_describe, s)
    s = REL_PATH.sub(lambda m: f"{m[1]}/{m[2]} {KINDS.get(m[3].lower(), m[3])} {m[4][:40]}", s)
    s = XREF.sub(r"\1 issue \2", s)
    s = SHAREF.sub(r"\1 commit \2", s)
    s = HASHREF.sub(r"no. \1", s)
    s = GHREF.sub(r"no. \1", s)
    return s.replace("@", "@\u200b")

def _views(text):
    """The text as written, plus its %-decoded and HTML-entity-decoded forms."""
    out = [text]
    for _ in range(3):
        t = urllib.parse.unquote(html.unescape(out[-1]))
        if t == out[-1]:
            break
        out.append(t)
    return out

def safe_text(text, fallback="[left out: failed the link-safety check]"):
    """neutralize(), and if anything still fails assert_safe, a fixed placeholder instead."""
    t = neutralize(text)
    try:
        assert_safe(t)
        return t
    except Unsafe:
        return fallback

def assert_safe(text):
    """Final check on the complete text, URLs included. Raises Unsafe."""
    for v in _views(str(text or "")):
        if re.search(r"github\s*\.\s*com", ZW.sub("", v), re.I):
            raise Unsafe("contains a github.com link")
        for rx, what in CHECKS:
            if rx.search(v):
                raise Unsafe(f"contains {what}")

NEW_MARK = re.compile(r"<!-- hero-sources:v2 ([A-Za-z0-9+/=]+) -->")
OLD_MARK = re.compile(r"<!-- (?:radar|problem)-sources: (\[.*?\]) -->", re.S)

def src_marker(srcs):
    return f"<!-- hero-sources:v2 {base64.b64encode(json.dumps(srcs).encode()).decode()} -->"

def used_sources(body):
    out = set()
    for rx, dec in ((NEW_MARK, lambda g: base64.b64decode(g).decode()), (OLD_MARK, lambda g: g)):
        for g in rx.findall(body):
            try:
                out.update(u for u in json.loads(dec(g)) if isinstance(u, str))
            except (ValueError, UnicodeDecodeError):
                pass
    out.update(re.findall(r"^- (https?://\S+)\s*$", body, re.M))
    return out

def sources_block(srcs):
    lines = [f"- {neutralize(u)} _(GitHub link left out on purpose)_" if GH_URL.search(u) else f"- {u}"
             for u in srcs]
    return "**Sources:**\n" + "\n".join(lines)

# ---------- HTTP ----------
def _send(method, url, body=None, headers=None, timeout=30):
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    data = None
    if body is not None:
        data = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        msg = e.read()[:500].decode("utf-8", "replace")
        raise RuntimeError(f"{method} {url.split('?')[0]} -> HTTP {e.code}: {msg}") from None

def call(method, url, body=None, headers=None, timeout=30):
    """Non-GitHub requests. GitHub writes must use gh()."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    if method.upper() != "GET" and (host == "github.com" or host.endswith(".github.com")):
        raise Blocked(f"{method} {host}: GitHub writes must go through gh()")
    return _send(method, url, body, headers, timeout)

_H = re.escape(f"/repos/{HOME_REPO}")
CLOSES = re.compile(r"\n\nCloses #\d+\Z")       # the one reference code (never AI) appends to a draft PR

def _issue(b):
    for k in ("title", "body"):
        assert_safe(b.get(k, ""))

def _labels(b):
    bad = [l for l in (b.get("labels") or []) if l not in LABELS]
    if bad or not b.get("labels"):
        raise Blocked(f"labels not allowed: {bad or 'none given'}")

def _ref(b):
    if not re.fullmatch(r"refs/heads/(draft/[a-z0-9][a-z0-9-]{0,80}|analytics)", str(b.get("ref", ""))):
        raise Blocked(f"branches must be draft/<slug> or analytics, got {b.get('ref')!r}")

def _contents(b):
    if not re.fullmatch(r"draft/[a-z0-9][a-z0-9-]{0,80}", str(b.get("branch", ""))):
        raise Blocked("file writes must target a draft/<slug> branch")
    assert_safe(b.get("message", ""))

def _analytics(b):
    if b.get("branch") != "analytics":
        raise Blocked("analytics files may only be written to the analytics branch")
    assert_safe(b.get("message", ""))

def _linkedin_draft(b):
    if b.get("labels") != ["linkedin-draft"] or b.get("assignees"):
        raise Blocked("profile repo: only issues labelled exactly linkedin-draft")
    _issue(b)

def _pull(b):
    if b.get("base") != "main" or not re.fullmatch(r"draft/[a-z0-9][a-z0-9-]{0,80}", str(b.get("head", ""))):
        raise Blocked("PRs must go from draft/<slug> into main")
    assert_safe(b.get("title", "")); assert_safe(CLOSES.sub("", b.get("body", "")))

# Every GitHub write the bot may make, all inside HOME_REPO.
WRITE_RULES = [
    ("POST", rf"{_H}/issues", _issue),
    ("POST", rf"{_H}/labels", lambda b: None),
    ("POST", rf"{_H}/issues/\d+/comments", lambda b: assert_safe(b.get("body", ""))),
    ("POST", rf"{_H}/issues/\d+/labels", _labels),
    ("POST", rf"{_H}/git/refs", _ref),
    ("PUT", rf"{_H}/contents/(?:site/_posts|labs)/[\w.-]+(?:/[\w.-]+){{0,3}}", _contents),
    ("PUT", rf"{_H}/contents/analytics/[\w.-]+", _analytics),
    ("POST", rf"{_H}/pulls", _pull),
    ("POST", rf"{re.escape(f'/repos/{PROFILE_REPO}')}/issues", _linkedin_draft),
]

def gh(method, path, body=None):
    """Reads use GH_READ_TOKEN. Writes need HERO_PUBLISH=1, a WRITE_RULES match and BOT_TOKEN."""
    method = method.upper()
    if not path.startswith("/") or ".." in path or "%" in path.split("?")[0]:
        raise Blocked(f"bad path {path!r}")
    if method == "GET":
        token = os.environ["GH_READ_TOKEN"]
    else:
        if not PUBLISH:
            raise Blocked(f"{method} {path}: this job may not write to GitHub")
        rule = next((r for r in WRITE_RULES if r[0] == method and re.fullmatch(r[1], path)), None)
        if not rule:
            raise Blocked(f"{method} {path}: not on the write allowlist")
        rule[2](body or {})
        token = os.environ["BOT_TOKEN"]
    return _send(method, "https://api.github.com" + path, body, {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})

# ---------- shared helpers ----------
def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()

def existing_issues(labels=("radar", "problem")):
    """Titles of bot issues (newest first), plus every source URL they used."""
    titles, used = [], set()
    for label in labels:
        for page in range(1, 11):
            got = gh("GET", f"/repos/{REPO}/issues?labels={label}&state=all&sort=created"
                            f"&direction=desc&per_page=100&page={page}")
            for i in got:
                if "pull_request" in i:
                    continue
                titles.append(i["title"])
                used |= used_sources(i.get("body") or "")
            if len(got) < 100:
                break
    return titles, used

def ensure_label(label):
    color, desc = LABELS[label]
    try:
        gh("GET", f"/repos/{REPO}/labels/{label}")
    except RuntimeError as e:
        if "HTTP 404" not in str(e):
            raise
        gh("POST", f"/repos/{REPO}/labels", {"name": label, "color": color, "description": desc})

def post_issue(title, body, label):
    assert_safe(title); assert_safe(body)
    iss = gh("POST", f"/repos/{REPO}/issues", {"title": title, "body": body, "labels": [label]})
    print(f"opened #{iss['number']}: {title}")
    return iss

def add_label(number, label):
    ensure_label(label)
    gh("POST", f"/repos/{REPO}/issues/{int(number)}/labels", {"labels": [label]})

def comment(number, body):
    assert_safe(body)
    gh("POST", f"/repos/{REPO}/issues/{int(number)}/comments", {"body": body})

def panel_line(c, d):
    """How the three AIs proposed and voted on a topic."""
    import llm
    mark = lambda v: "\u2705" if v == "yes" else "\u274c"
    who = ", ".join(llm.NAMES[w] for w in c["by"])
    lines = [f"**Proposed by:** {who} \u00b7 **Panel vote:** {d['yes']} of 3"]
    lines += [f"- {llm.NAMES[w]} {mark(v)} _{safe_text(r)}_" for w, (v, r) in d["votes"].items()]
    return "\n".join(lines)

PANEL_QUESTION = ("Should this become a post on Abhimanyu's blog? The niche: take a real problem AWS container users "
                  "hit (EKS, ECS, Fargate, ECR, Karpenter), REPLICATE it in a disposable AWS lab, then show the fix. "
                  "Readers: students learning AWS containers and working engineers. The goal is content strong "
                  "enough to support an AWS Container Hero nomination. Vote no if it cannot be reproduced within the "
                  "LAB LIMITS, has no clear lesson, is vendor drama or opinion, or repeats an existing topic.")

def load_insights():
    """What the analytics panel learned so far (analytics branch), or "" before there is any."""
    try:
        f = gh("GET", f"/repos/{HOME_REPO}/contents/analytics/insights.md?ref=analytics")
        return base64.b64decode(f.get("content", "")).decode("utf-8", "replace")[:6000]
    except Exception:
        return ""

def propose_all(system, prompt, schema):
    """Each of the three proposes topics on its own. Returns [(who, topic)]."""
    import llm
    out = []
    for who in llm.PANEL:
        try:
            got = llm.ask(who, system, prompt, schema, tool="submit_topics", tier="scout")
            ts = [t for t in (got.get("topics") or []) if isinstance(t, dict)]
            print(f"{who} proposed {len(ts)} topic(s)")
            out += [(who, t) for t in ts]
        except Exception as e:
            llm.warn(f"{who} could not propose: {str(e)[:400]}")
    return out

def post_topics(proposals, items, existing, used, label, render, limit):
    """proposals: [(who, topic)] from propose_all. Pool them (same title or shared source = one topic), let all
    three vote, open the topics with 2+ yes as issues, and mark the best one `replicate`.
    render(topic, sources) returns the top text of the issue, or None to skip the topic."""
    import llm
    allowed = {i["url"] for i in items}
    have = {norm(t) for t in existing}
    cands = []
    for who, t in proposals:
        title = neutralize(t.get("title")).strip()[:100]
        srcs = [u for u in (t.get("sources") or []) if isinstance(u, str) and u in allowed]
        if not title or norm(title) in have or not srcs or any(u in used for u in srcs):
            print(f"skip ({who}) duplicate, empty, unsourced or already covered: {title}"); continue
        same = next((c for c in cands if norm(c["title"]) == norm(title) or set(srcs) & set(c["srcs"])), None)
        if same:
            if who not in same["by"]:
                same["by"].append(who)
            continue
        top = render(t, srcs)
        if top is None:
            continue
        cands.append({"id": f"t{len(cands) + 1}", "title": title, "top": top, "srcs": srcs, "by": [who],
                      "score": int(t.get("replicability") or 0)})
    insights = load_insights()
    view = [{"id": c["id"], "title": c["title"], "details": c["top"], "replicability_1_to_10": c["score"],
             "proposed_by": [llm.NAMES[w] for w in c["by"]]} for c in cands]
    votes = llm.panel_votes(view, PANEL_QUESTION, f"LAB LIMITS:\n{LAB_LIMITS}\n\nEXISTING TOPICS:\n"
                            f"{json.dumps(existing[:150])}\n\nWHAT HAS WORKED SO FAR (analytics panel):\n"
                            f"{insights or 'no posts measured yet'}")
    for c in cands:
        d = votes[c["id"]]
        print(f"{'ACCEPT' if d['accepted'] else 'reject'} {d['yes']}/3 by={c['by']}: {c['title']}")
    ranked = sorted((c for c in cands if votes[c["id"]]["accepted"]),
                    key=lambda c: (votes[c["id"]]["yes"], len(c["by"]), c["score"]), reverse=True)[:limit]
    best = next((c for c in ranked if c["score"] >= 7), None)
    if ranked and not best:
        print("no accepted topic scored 7+ for replicability; nothing marked replicate")
    if ranked and not DRY:
        ensure_label(label)
    made = 0
    for c in ranked:
        body = (f"{c['top']}\n\n{panel_line(c, votes[c['id']])}\n\n{sources_block(c['srcs'])}\n\n"
                f"_Found by the {label} bot. Add the `replicate` label to run it on the AWS lab; close it if "
                f"you're not interested._\n\n{src_marker(c['srcs'])}")
        try:
            assert_safe(c["title"]); assert_safe(body)
        except Unsafe as e:
            print(f"skip, {e}: {c['title']}"); continue
        used.update(c["srcs"])
        if DRY:
            print(f"[dry run] would open{' + replicate' if c is best else ''}: {c['title']}\n{body}\n"); continue
        iss = post_issue(c["title"], body, label)
        made += 1
        if c is best:
            add_label(iss["number"], "replicate")
            print(f"marked #{iss['number']} replicate")
    print(f"done: {made} issue(s) opened. {llm.usage_summary()}")

# ---------- radar ----------
def collect():
    since = int(time.time()) - 8 * 86400
    hn, rels, seen = [], [], set()
    for q in HN_QUERIES:
        url = ("https://hn.algolia.com/api/v1/search_by_date?tags=story&hitsPerPage=20&query="
               + urllib.parse.quote(q) + f"&numericFilters=created_at_i>{since},points>30")
        try:
            hits = call("GET", url)["hits"]
        except Exception as e:
            print(f"warn: HN {q}: {e}"); continue
        for h in hits:
            link = h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}"
            if link not in seen:
                seen.add(link)
                hn.append({"src": "hn", "title": h.get("title", ""), "url": link, "points": h.get("points") or 0})
    for r in WATCH:
        try:
            got = gh("GET", f"/repos/{r}/releases?per_page=5")
        except Exception as e:
            print(f"warn: {r}: {e}"); continue
        for rel in got:
            ts = rel.get("published_at")
            if rel.get("draft") or not ts or calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")) < since:
                continue
            rels.append({"src": "release", "title": f"{r} {rel.get('name') or rel['tag_name']}",
                         "url": rel["html_url"], "notes": (rel.get("body") or "")[:600]})
    news = []
    try:
        req = urllib.request.Request(WHATS_NEW, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            root = ET.fromstring(r.read())
        for it in root.iter("item"):
            title, link = (it.findtext("title") or "").strip(), (it.findtext("link") or "").strip()
            when = it.findtext("pubDate") or ""
            try:
                ts = calendar.timegm(time.strptime(when[5:25], "%d %b %Y %H:%M:%S"))
            except ValueError:
                ts = since
            desc = re.sub(r"<[^>]+>", " ", html.unescape(it.findtext("description") or ""))
            if link and ts >= since and CONTAINER_WORDS.search(title + " " + desc):
                news.append({"src": "aws-whats-new", "title": title, "url": link, "notes": " ".join(desc.split())[:600]})
    except Exception as e:
        print(f"warn: AWS What's New: {e}")
    hn.sort(key=lambda i: -i["points"])
    return news[:30] + rels + hn[:25]

TOPIC_SCHEMA = {"type": "object", "required": ["topics"], "properties": {"topics": {"type": "array", "items": {
    "type": "object", "required": ["title", "why", "lab", "replicability", "sources"], "properties": {
        "title": {"type": "string", "description": "max 80 chars"},
        "why": {"type": "string", "description": "2 sentences: why this matters to AWS container users now"},
        "lab": {"type": "string", "description": "1-2 sentences: what the lab builds and shows"},
        "replicability": {"type": "integer", "description": "1-10: how well this can be reproduced within LAB LIMITS"},
        "sources": {"type": "array", "items": {"type": "string"}, "description": "URLs copied exactly from ITEMS"}}}}}}

def ask_panel(items, existing):
    return propose_all(
        "You pick topics for Abhimanyu's hands-on blog about AWS containers. Every post replicates something "
        "real in a disposable AWS lab and shows the result.",
        f"From the ITEMS below (AWS What's New entries, releases of AWS container projects, and Hacker News "
        f"stories), choose up to {MAX_ISSUES + 2} topics that would make a strong hands-on post right now: a new "
        "feature or behaviour change that people will trip over, demonstrated in a lab. Prefer EKS, ECS, Fargate, "
        f"ECR and Karpenter. Do not repeat anything in EXISTING. {NO_REFS}\n\nLAB LIMITS:\n{LAB_LIMITS}\n\n"
        f"WHAT HAS WORKED SO FAR:\n{load_insights() or 'no posts measured yet'}\n\n"
        f"EXISTING:\n{json.dumps(existing[:200])}\n\nITEMS:\n{json.dumps(items)}",
        TOPIC_SCHEMA)

def main():
    existing, used = existing_issues()
    items = [i for i in collect() if i["url"] not in used and i["url"] not in SKIP_SOURCES]
    print(f"collected {len(items)} new items ({len(used)} source URLs already used)")
    if not items:
        print("done: nothing new"); return
    topics = ask_panel(items, existing)
    post_topics(topics, items, existing, used, LABEL,
                lambda t, srcs: f"**Why now:** {neutralize(t.get('why'))}\n\n**Lab idea:** {neutralize(t.get('lab'))}",
                MAX_ISSUES)

if __name__ == "__main__":
    main()
