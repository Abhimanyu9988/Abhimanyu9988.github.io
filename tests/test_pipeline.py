"""Offline tests for the three-AI panel, the lab runner's checks and the drafter. No network, no AWS."""
import io, json, os, pathlib, shutil, sys, tempfile
HOME = "Abhimanyu9988/Abhimanyu9988.github.io"
PIPE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline")
os.environ.update(REPO=HOME, HERO_PUBLISH="1", BOT_TOKEN="test", GH_READ_TOKEN="test", ISSUE="4", RUN_ID="123456789")
sys.path.insert(0, PIPE)
import radar as r, llm, lab, draft, check_plan

fails = 0
def raises_unsafe(text):
    try: r.assert_safe(text); return False
    except r.Unsafe: return True

def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name); fails += not ok

# ---------- claude_json without forced tool choice ----------
llm._models["claude"] = {"write": "claude-test", "scout": "claude-test"}
sent_bodies, replies = [], []
def fake_stream(body):
    sent_bodies.append(body); return replies.pop(0)
real_stream, real_cj = llm._claude_stream, llm.claude_json
llm._claude_stream = fake_stream
replies[:] = [({"submit": {"a": 1}}, "", "tool_use", {})]
check("claude_json: tool call returned", llm.claude_json("s", "p", {}) == {"a": 1})
check("claude_json: tool_choice is auto (works on every model)", sent_bodies[-1]["tool_choice"] == {"type": "auto"})
replies[:] = [({}, 'Here you go: {"a": 2}', "end_turn", {})]
check("claude_json: JSON in text accepted", llm.claude_json("s", "p", {}) == {"a": 2})
replies[:] = [({}, "I think the lab should...", "end_turn", {}), ({"submit": {"a": 3}}, "", "tool_use", {})]
check("claude_json: asks again when it gets prose", llm.claude_json("s", "p", {}) == {"a": 3} and len(sent_bodies[-1]["messages"]) == 3)
replies[:] = [({}, "", "max_tokens", {})]
try: llm.claude_json("s", "p", {}); ok = False
except RuntimeError as e: ok = "cut off" in str(e)
check("claude_json: truncation is reported", ok)
llm._claude_stream = real_stream

# ---------- Gemini model choice ----------
llm._models.pop("gemini", None)
real_call = llm.call
llm.call = lambda m, u, *a, **k: {"models": [{"name": n, "supportedGenerationMethods": ["generateContent"]} for n in
    ("models/gemini-2.5-pro", "models/gemini-3.1-pro-preview", "models/gemini-3.8-flash", "models/gemini-2.5-flash",
     "models/gemini-3.1-pro-preview-tts", "models/gemini-embedding-001")]}
os.environ["GEMINI_API_KEY"] = "x"
check("gemini: newest pro (preview ok) first, then fallbacks", llm.gemini_models() ==
      ["models/gemini-3.1-pro-preview", "models/gemini-2.5-pro", "models/gemini-3.8-flash", "models/gemini-2.5-flash"])
llm.call = real_call; llm._models.pop("gemini", None)

# ---------- panel votes and verdict (all three vote, 2 of 3 decides) ----------
VOTES = {"claude": {"t1": "yes", "t2": "yes", "t3": "no"},
         "chatgpt": {"t1": "yes", "t2": "no", "t3": "no"},
         "gemini": {"t1": "no", "t2": "yes"}}                      # gemini skips t3; t4 gets no votes at all
calls = []
def fake_ask(who, system, prompt, schema, tool="submit", tier="write"):
    calls.append(who)
    if tool == "submit_votes":
        return {"votes": [{"id": k, "vote": v, "reason": f"{who} says {v}"} for k, v in VOTES[who].items()]}
    if tool == "submit_verdict":
        return {"vote": "no" if who == "gemini" else "yes", "reason": "evidence matches"}
    raise AssertionError(tool)
real_ask = llm.ask; llm.ask = fake_ask
d = llm.panel_votes([{"id": "t1"}, {"id": "t2"}, {"id": "t3"}, {"id": "t4"}], "q")
check("panel: every model votes once", sorted(calls) == ["chatgpt", "claude", "gemini"])
check("panel: 2 of 3 yes -> accepted", d["t1"]["accepted"] and d["t2"]["accepted"] and d["t1"]["yes"] == 2)
check("panel: 0 of 3 -> rejected; a missing vote counts as no", not d["t3"]["accepted"] and not d["t4"]["accepted"]
      and d["t4"]["votes"]["claude"] == ("no", "no vote"))
v = llm.verdict("q", "ctx")
check("verdict: 2 of 3 yes passes, dissent kept", v["passed"] and v["yes"] == 2 and v["votes"]["gemini"][0] == "no")
def broken(who, *a, **k):
    if who != "claude": raise RuntimeError("HTTP 500")
    return {"vote": "yes", "reason": "fine"}
llm.ask = broken
check("verdict: one yes and two failed models does not pass", not llm.verdict("q", "ctx")["passed"])
llm.ask = real_ask

# ---------- refine, with configurable roles ----------
DRAFT = {"x": 1}
calls = []
def role_ask(who, system, prompt, schema, tool="submit", tier="write"):
    calls.append((who, tool))
    if tool == "submit_review":
        return {"approve": False, "points": [
            {"id": "p1", "severity": "blocking", "issue": "a", "suggestion": "a"},
            {"id": "p2", "severity": "blocking", "issue": "b", "suggestion": "b"},
            {"id": "p3", "severity": "blocking", "issue": "c", "suggestion": "c"},
            {"id": "p4", "severity": "minor", "issue": "d", "suggestion": "d"}]}
    if tool == "respond":
        return {"responses": [{"id": "p1", "decision": "accept", "reason": "ok"},
                              {"id": "p2", "decision": "reject", "reason": "wrong"},
                              {"id": "p3", "decision": "reject", "reason": "wrong"},
                              {"id": "p4", "decision": "reject", "reason": "style"}], "revised": {"x": 2}}
    if tool == "submit_rulings":
        return {"rulings": [{"id": "p2", "side": "reviewer", "reason": "it is a bug"},
                            {"id": "p3", "side": "writer", "reason": "fine"}]}
    return {"x": 3}
llm.ask = role_ask
final, log = llm.refine("article", DRAFT, "rubric", "", {"type": "object"}, writer="chatgpt", reviewer="claude", judge="gemini")
out = {p["issue"]: p["outcome"] for p in log["points"]}
check("refine: roles respected (ChatGPT writes, Claude reviews, Gemini judges)",
      calls == [("claude", "submit_review"), ("chatgpt", "respond"), ("gemini", "submit_rulings"), ("chatgpt", "revise")])
check("refine: accepted point applied", out["a"] == "applied (ChatGPT agreed)")
check("refine: judge sides with reviewer -> writer must apply", out["b"] == "applied (Gemini sided with Claude)")
check("refine: judge sides with writer -> not applied", out["c"] == "not applied (Gemini sided with ChatGPT)")
check("refine: rejected minor point needs no tiebreak", out["d"].startswith("not applied (minor"))
check("refine: forced revision is the final draft", final == {"x": 3})
llm.ask = lambda *a, **k: {"approve": True, "points": []}
final, log = llm.refine("lab", DRAFT, "r", "", {})
check("refine: clean approval returns draft untouched", final == DRAFT and log["approved"] and log["reviewer"] == "ChatGPT")
llm.ask = real_ask

# ---------- topic discussion: pooled proposals, all three vote ----------
items = [{"url": "https://stackoverflow.com/questions/1/a"}, {"url": "https://stackoverflow.com/questions/2/b"},
         {"url": "https://stackoverflow.com/questions/3/c"}]
props = [("claude", {"title": "ECS stuck at zero", "sources": [items[0]["url"]], "replicability": 8}),
         ("gemini", {"title": "Fargate scale from zero", "sources": [items[0]["url"]], "replicability": 9}),
         ("chatgpt", {"title": "EKS pods pending", "sources": [items[1]["url"]], "replicability": 6}),
         ("chatgpt", {"title": "Opinion piece", "sources": ["https://not-in-items.example"], "replicability": 9})]
seen_view = {}
def topic_votes(view, question, context="", tier="scout"):
    seen_view["v"] = view
    return {c["id"]: {"votes": {w: ("yes", "ok") for w in llm.PANEL}, "yes": 3 if c["id"] == "t1" else 2,
                      "accepted": True} for c in view}
real_pv = llm.panel_votes; llm.panel_votes = topic_votes
r.load_insights = lambda: "dev.to readers liked ECS posts"
r.DRY = True
import contextlib
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    r.post_topics(props, items, [], set(), "problem", lambda t, srcs: f"About {t['title']}", 3)
outp = buf.getvalue()
check("topics: same source from two models becomes one topic proposed by both",
      len(seen_view["v"]) == 2 and seen_view["v"][0]["proposed_by"] == ["Claude", "Gemini"])
check("topics: a source not in the collected items is dropped", "Opinion piece" not in json.dumps(seen_view["v"]))
check("topics: best replicable topic is marked replicate", "would open + replicate: ECS stuck at zero" in outp)
check("topics: issue shows who proposed and every vote", "Proposed by:** Claude, Gemini" in outp and "ChatGPT \u2705" in outp)
llm.panel_votes = real_pv; r.DRY = False

# ---------- publish links and /stats ----------
import analytics
post_meta = {"title": "Why ECS stays at zero", "linkedin": "Queue full, zero tasks. Seen it? @AWS\nFull write-up: {POST_URL}"}
sys.modules.setdefault("frontmatter", type(sys)("frontmatter")); sys.modules.setdefault("yaml", type(sys)("yaml"))
sys.modules["yaml"].safe_load = lambda f: {"url": "https://abhimanyu9988.github.io", "permalink": "/posts/:slug/"}
import publish
block = "\n".join(publish.share_block(post_meta, "https://abhimanyu9988.github.io/posts/why-ecs/",
                                       {"devto": {"url": "https://dev.to/abhi/why-ecs"}}, "why-ecs"))
check("publish: LinkedIn link opens the post box with the text and the real post URL",
      "linkedin.com/feed/?shareActive=true&text=" in block and "abhimanyu9988.github.io%2Fposts%2Fwhy-ecs" in block)
check("publish: Medium import and dev.to link listed", "medium.com/p/import" in block and "dev.to/abhi/why-ecs" in block)
check("publish: report text has no live @mention", "@AWS" not in block.split("](https://www.linkedin.com")[0]
      and "@\u200bAWS" in block)
check("publish: report carries the post marker for /stats", "<!-- post: why-ecs -->" in block)
post_meta["replies"] = [{"url": "https://stackoverflow.com/questions/80006730/x", "text": "I reproduced this. {POST_URL}"}]
block = "\n".join(publish.share_block(post_meta, "https://abhimanyu9988.github.io/posts/why-ecs/",
                                       {"medium": {"url": "https://medium.com/@abhi/draft-1"}}, "why-ecs"))
check("publish: source reply ready to post, with the SO rule and the real link",
      "I reproduced this. https://abhimanyu9988.github.io/posts/why-ecs/" in block and "own words" in block)
check("publish: Medium draft link instead of import when a draft was made",
      "medium.com/@abhi/draft-1" in block and "medium.com/p/import" not in block)
calls_m = []
def fake_medium_call(method, url, payload, headers):
    calls_m.append((method, url, payload))
    return {"data": {"id": "u1"}} if url.endswith("/me") else {"data": {"id": "p1", "url": "https://medium.com/p/p1"}}
publish.call = fake_medium_call; os.environ["MEDIUM_TOKEN"] = "x"
pid, url = publish.medium({"title": "T", "description": "D", "tags": ["aws", "ecs", "sqs", "devops"]},
                          "Text\n\n```bash\nterraform apply\n```", "https://abhimanyu9988.github.io/posts/t/", None)
sent_m = calls_m[-1][2]
check("medium: created as a DRAFT in markdown with canonical, code fences intact",
      sent_m["publishStatus"] == "draft" and sent_m["contentFormat"] == "markdown"
      and sent_m["canonicalUrl"].endswith("/posts/t/") and "```bash\nterraform apply\n```" in sent_m["content"])
check("medium: never re-created", publish.medium({}, "", "", "p1") == ("p1", None))
import share
li_body = share.body_for({"slug": "why-ecs", "title": "Why ECS", "canonical": "https://abhimanyu9988.github.io/posts/why-ecs/",
                          "linkedin": "Queue full. Thanks @AWS team.\n#aws\nhttps://abhimanyu9988.github.io/posts/why-ecs/"})
check("share: draft issue in the profile panel's format (/post works there)",
      "<!-- POST:START -->" in li_body and "<!-- POST:END -->" in li_body and "social:meta" in li_body)
check("share: no @mentions, passes the link-safety check",
      "@AWS" not in li_body and not raises_unsafe(li_body))
check("stats: platform + numbers, post taken from the report marker",
      analytics.parse_stats("/stats linkedin impressions=1,200 reactions=40", "x <!-- post: why-ecs -->")
      == ("why-ecs", "linkedin", {"impressions": 1200, "reactions": 40}))
check("stats: explicit post slug", analytics.parse_stats("/stats why-ecs medium views=300", "")[0] == "why-ecs")
for bad in ("/stats", "/stats linkedin", "/stats linkedin impressions=lots", "/stats mastodon views=1"):
    try: analytics.parse_stats(bad, "<!-- post: why-ecs -->"); ok = False
    except ValueError: ok = True
    check(f"stats: rejects {bad!r}", ok)
rows = [{"date_utc": "2026-10-20", "post": "a", "platform": "devto", "metric": "views", "value": "10"},
        {"date_utc": "2026-10-27", "post": "a", "platform": "devto", "metric": "views", "value": "35"}]
check("analytics: latest value and weekly change", analytics.latest(rows)[("a", "devto", "views")] == {"value": 35, "date": "2026-10-27", "change": 25})

# ---------- lab checks ----------
good = {"files": [{"path": p, "content": "x"} for p in lab.REQUIRED] + [{"path": "k8s/app.yaml", "content": "x"}]}
check("validate: good lab passes", lab.validate(good) == [])
bad = {"files": good["files"] + [
    {"path": "../evil.sh", "content": "x"}, {"path": "_runner.tf", "content": "x"},
    {"path": "net.tf", "content": 'terraform {\n  backend "s3" {}\n}\nprovider "aws" {\n  region = "us-east-1"\n}\n'
                                  'variable "lab_name" {}\n'}]}
errs = " | ".join(lab.validate(bad))
check("validate: path escape refused", "../evil.sh" in errs)
check("validate: runner file refused", "_runner.tf" in errs)
check("validate: backend / default provider / reserved variable refused",
      "backend" in errs and "default aws provider" in errs and "variable" in errs)
check("validate: one-line default provider refused",
      "default aws provider" in " ".join(lab.validate({"files": good["files"] + [{"path": "p.tf", "content": 'provider "aws" {}'}]})))
check("validate: 'alias' only in a comment does not count",
      "default aws provider" in " ".join(lab.validate({"files": good["files"] + [{"path": "p.tf",
          "content": 'provider "aws" {\n  # alias = "x"\n  region = "us-east-1"\n}'}]})))
check("validate: override files refused", "override" in " ".join(lab.validate({"files": good["files"] + [{"path": "x_override.tf", "content": ""}]})))
check("validate: external data source refused",
      "external" in " ".join(lab.validate({"files": good["files"] + [{"path": "e.tf", "content": 'data "external" "x" {}'}]})))
check("validate: scripts may not run terraform apply",
      "terraform output" in " ".join(lab.validate({"files": [f if f["path"] != "fix.sh" else {"path": "fix.sh",
          "content": "terraform apply -auto-approve"} for f in good["files"]]})))
check("validate: scripts may read terraform output",
      lab.validate({"files": [f if f["path"] != "reproduce.sh" else {"path": "reproduce.sh",
          "content": 'C=$(terraform output -raw cluster_name)'} for f in good["files"]]}) == [])
check("validate: aliased aws provider allowed",
      lab.validate({"files": good["files"] + [{"path": "x.tf", "content": 'provider "aws" {\n  alias = "use1"\n}\n'}]}) == [])
check("validate: missing files reported", "missing required file fix.sh" in " ".join(lab.validate({"files": [{"path": "main.tf", "content": ""}]})))
check("hero_result: last line wins", lab.hero_result("HERO_RESULT: not_reproduced\n...\nHERO_RESULT: reproduced\n") == "reproduced")
check("hero_result: absent", lab.hero_result("done") is None)
FAKE_KEY_ID = "AS" + "IA" + "X" * 16     # built at runtime so secret scanners do not flag a fake key
red = lab.redact(f"arn:aws:iam::300054528837:role/x key {FAKE_KEY_ID} aws_session_token = abc/def")
check("redact: account id, key id, session token", "300054528837" not in red and FAKE_KEY_ID not in red and "abc/def" not in red)
os.environ["OPENAI_API_KEY"] = "zz-not-a-pattern-but-the-real-value-123"
red = lab.redact("k1 " + "sk-ant-" + "api03-" + "A" * 24 + " k2 " + "AI" + "za" + "Sy" + "A" * 33 + " "
                 "k3 " + "gh" + "s_" + "A" * 36 + " k4 zz-not-a-pattern-but-the-real-value-123 bucket=hero-pipeline-tfstate-300054528837")
check("redact: model keys, GitHub tokens, exact secret values, account in bucket name",
      not any(x in red for x in ("sk-ant-api03", "AIzaSy", "ghs_", "real-value", "300054528837")))

# sandbox: what reaches AI-written code
captured = {}
class FakeP:
    pid, returncode = 0, 0
    def __init__(self, args, **kw): captured.update(args=args, env=kw["env"])
    def communicate(self, timeout=None): return "ok", None
real_popen = lab.subprocess.Popen; lab.subprocess.Popen = FakeP
os.environ.update(AWS_ACCESS_KEY_ID="test-access-key-id", AWS_SECRET_ACCESS_KEY="secret-value-xyz", ANTHROPIC_API_KEY="sk-ant-real",
                  ACTIONS_ID_TOKEN_REQUEST_TOKEN="oidc-request-token")
tmpw = pathlib.Path(tempfile.mkdtemp())
lab.TFCACHE = tmpw / ".tfcache"
lab.run("t", ["bash", "fix.sh"], 60, cwd=tmpw, env={"LAB_NAME": "x"}, sandbox=True)
argv, env = " ".join(captured["args"]), captured["env"]
check("sandbox: runs in docker as the runner's uid", captured["args"][:3] == ["docker", "run", "--rm"] and "--user" in captured["args"])
check("sandbox: AWS credentials passed by name only, never on argv",
      " -e AWS_SECRET_ACCESS_KEY " in f" {argv} " and "secret-value-xyz" not in argv)
check("sandbox: model keys and OIDC request token never reach the container",
      "ANTHROPIC" not in argv and "ACTIONS_ID_TOKEN" not in argv and "ANTHROPIC_API_KEY" not in env
      and "ACTIONS_ID_TOKEN_REQUEST_TOKEN" not in env)
check("sandbox: only the lab folder is mounted", argv.count(" -v ") == 2 and f"{tmpw.resolve()}:/lab" in argv)
lab.subprocess.Popen = real_popen
shutil.rmtree(tmpw)
check("slugify", lab.slugify("Debugging ECS: Tasks stuck in PENDING (Fargate)!") == "debugging-ecs-tasks-stuck-in-pending-far")

def plan(*rcs):
    return {"resource_changes": [{"type": t, "address": f"{t}.x", "change": {"actions": ["create"], "after": a}}
                                 for t, a in rcs]}
check("plan: allowed instance OK", check_plan.problems(plan(("aws_instance", {"instance_type": "t3.medium"}))) == [])
check("plan: big instance refused", check_plan.problems(plan(("aws_instance", {"instance_type": "m5.24xlarge"}))) != [])
check("plan: managed node group refused", check_plan.problems(plan(("aws_eks_node_group", {}))) != [])
check("plan: 4 instances refused", check_plan.problems(plan(*[("aws_instance", {"instance_type": "t3.medium"})] * 4)) != [])
check("plan: 200 GB volume refused", check_plan.problems(plan(("aws_ebs_volume", {"size": 200}))) != [])
check("plan: io2 volume refused", check_plan.problems(plan(("aws_ebs_volume", {"size": 10, "type": "io2"}))) != [])
check("plan: dedicated tenancy refused", check_plan.problems(plan(("aws_instance", {"instance_type": "t3.medium", "tenancy": "dedicated"}))) != [])
check("plan: unlisted resource type refused (dedicated host)", check_plan.problems(plan(("aws_ec2_host", {}))) != [])
check("plan: typical EKS + k8s lab allowed", check_plan.problems(plan(("aws_vpc", {}), ("aws_subnet", {}), ("aws_eks_cluster", {}),
      ("aws_iam_role", {}), ("aws_eks_access_entry", {}), ("helm_release", {}), ("kubernetes_namespace_v1", {}),
      ("aws_vpc_security_group_ingress_rule", {}), ("tls_private_key", {}))) == [])

# ---------- drafter, against the real write allowlist ----------
tmp = tempfile.mkdtemp(); os.chdir(tmp)
sent, TAKEN, PUBLISHED = [], set(), set()
def fake_send(m, u, b=None, h=None, timeout=30):
    path = u.replace("https://api.github.com", "")
    if m == "GET" and "/git/ref/heads/draft/" in path:
        if path.endswith(tuple(TAKEN)): return {"object": {"sha": "x"}}
        raise RuntimeError("HTTP 404")
    if m == "GET" and "/contents/labs/" in path:
        if path.split("/contents/")[1].split("?")[0] in PUBLISHED: return {}
        raise RuntimeError("HTTP 404")
    if m == "GET" and path.endswith("/git/ref/heads/main"): return {"object": {"sha": "abc"}}
    if m == "GET" and "/labels/" in path: return {}
    sent.append((m, path, b)); return {"number": 9}
r._send = fake_send
draft.OUT = pathlib.Path("out")
(draft.OUT / "lab" / "k8s").mkdir(parents=True); (draft.OUT / "evidence").mkdir()
for f in lab.REQUIRED: (draft.OUT / "lab" / f).write_text("echo hi")
(draft.OUT / "lab" / "k8s" / "app.yaml").write_text("kind: Pod")
(draft.OUT / "evidence" / "05-reproduce.log").write_text("Error: pods are stuck\nHERO_RESULT: reproduced")
res = {"status": "reproduced_and_fixed", "slug": "debugging-ecs-pending", "run_id": "123456789", "attempts": 1,
       "minutes": 31.5, "summary": "ECS on Fargate in public subnets", "destroyed": True,
       "review": {"chatgpt_approved": False, "points": [{"severity": "blocking", "issue": "see aws/x#5 @bob",
                                                          "outcome": "applied (Claude agreed)"}]}}
(draft.OUT / "result.json").write_text(json.dumps(res))
(draft.OUT / "topic.json").write_text(json.dumps({"sources": [{"url": "https://stackoverflow.com/questions/1/a"}]}))
writer_calls = []
def draft_ask(who, system, prompt, schema, tool="submit", tier="write"):
    if tool == "submit_verdict":
        return {"vote": VERDICT.get(who, "yes"), "reason": "the evidence shows it, see @bob and aws/x#5"}
    writer_calls.append(who)
    return {"title": "Why ECS tasks stay PENDING", "description": "d", "tags": ["AWS", "ECS!", "fargate", "devops", "extra"],
            "body": "Someone hit this at 2am.\n\nBody links https://github.com/aws/x/issues/5 freely.",
            "linkedin": "Your queue is full and Fargate sits at zero. Thoughts? @AWS #aws\n\nFull write-up: {POST_URL}",
            "source_replies": [{"url": "https://stackoverflow.com/questions/1/a", "text": "I hit this too. {POST_URL}"},
                               {"url": "https://made-up.example/x", "text": "spam"}]}
VERDICT = {}
llm.ask = draft_ask
real_refine = llm.refine
roles = []
llm.refine = lambda kind, d, *a, **k: (roles.append((k.get("writer"), k.get("reviewer"), k.get("judge"))) or
                                       (d, {"approved": True, "reviewer": "Claude", "points": []}))
try:
    draft.main(); ok = True
except Exception as e:
    print("   draft.main raised:", type(e).__name__, e); ok = False
check("draft: success path runs entirely inside the allowlist", ok)
puts = [p for m, p, b in sent if m == "PUT"]
check("draft: post + lab + providers.tf + evidence written",
      any(p.endswith("-debugging-ecs-pending.md") for p in puts) and any(p.endswith("/labs/debugging-ecs-pending/providers.tf") for p in puts)
      and any("/evidence/05-reproduce.log" in p for p in puts) and any(p.endswith("/k8s/app.yaml") for p in puts))
post = next(b for m, p, b in sent if m == "PUT" and p.endswith(".md") and "_posts" in p)
import base64
text = base64.b64decode(post["content"]).decode()
check("draft: front matter has channels and cleaned tags", 'tags: ["aws", "ecs", "fargate", "devops"]' in text and "channels:" in text)
check("draft: ChatGPT writes, Claude reviews, Gemini judges", writer_calls == ["chatgpt"] and roles == [("chatgpt", "claude", "gemini")])
check("draft: LinkedIn text stored in front matter", "linkedin: " in text and "{POST_URL}" in text)
check("draft: source replies kept only for real sources, stored in front matter", "replies: " in text
      and "stackoverflow.com/questions/1/a" in text and "made-up.example" not in text)
check("draft: post file may link to GitHub (files never backlink)", "github.com/aws/x/issues/5" in text)
pr = next(b for m, p, b in sent if p.endswith("/pulls"))
check("draft: PR closes the topic and neutralizes AI text", pr["body"].endswith("Closes #4") and "aws/x#5" not in pr["body"]
      and "@bob" not in pr["body"] and "@AWS" not in pr["body"])
check("draft: PR shows the post itself, the LinkedIn draft and the replication check",
      "Someone hit this at 2am." in pr["body"] and "LinkedIn draft" in pr["body"] and "Replication check:** 3 of 3" in pr["body"])
order = [m for m, p, b in sent]
check("draft: branch created only after the PR text passed the checks", order.index("POST") >= 0 and sent[0][1].endswith("/git/refs"))
check("draft: comment + drafted label", any(p.endswith("/issues/4/comments") for m, p, b in sent)
      and any(p.endswith("/issues/4/labels") and b == {"labels": ["drafted"]} for m, p, b in sent))

sent.clear(); TAKEN.add("draft/debugging-ecs-pending")
draft.main()
refs = [b["ref"] for m, p, b in sent if p.endswith("/git/refs")]
puts = [p for m, p, b in sent if m == "PUT"]
check("redraft: new branch -v2, but the post and lab keep their clean names",
      refs == ["refs/heads/draft/debugging-ecs-pending-v2"] and any(p.endswith("-debugging-ecs-pending.md") for p in puts)
      and any("/labs/debugging-ecs-pending/main.tf" in p for p in puts))
sent.clear(); TAKEN.clear(); PUBLISHED.add("labs/debugging-ecs-pending")
draft.main()
puts = [p for m, p, b in sent if m == "PUT"]
check("draft: a name already published on main gets the run suffix", any("/labs/debugging-ecs-pending-456789/" in p for p in puts))
PUBLISHED.clear()
sent.clear(); writer_calls.clear()
VERDICT.update(chatgpt="no", gemini="no")
draft.main()
check("draft: panel rejects the replication -> no branch, no PR, disputed label",
      not any(p.endswith("/pulls") or p.endswith("/git/refs") for m, p, b in sent) and writer_calls == []
      and any(b == {"labels": ["replication-disputed"]} for m, p, b in sent))
VERDICT.clear()
sent.clear()
res.update(status="not_reproduced", last_failure={"step": "reproduce (problem did not appear)",
                                                  "output": "see https://github.com/a/b/issues/1 and #3"})
(draft.OUT / "result.json").write_text(json.dumps(res))
draft.main()
check("draft: not reproduced -> comment + label, no PR",
      not any(p.endswith("/pulls") for m, p, b in sent) and any(b == {"labels": ["not-reproduced"]} for m, p, b in sent))
sent.clear()
res.update(status="failed", last_failure=None, error="RuntimeError: Claude's reply was cut off (max_tokens)")
(draft.OUT / "result.json").write_text(json.dumps(res))
draft.main()
check("draft: early error -> comment shows the error", len(sent) == 1 and "cut off" in sent[0][2]["body"])
sent.clear(); (draft.OUT / "result.json").unlink()
draft.main()
check("draft: no result -> explanatory comment", len(sent) == 1 and sent[0][1].endswith("/issues/4/comments"))
shutil.rmtree(tmp)

# ---------- lab runner end to end, simulated (no AWS, no docker) ----------
def simulate(plans, repros, fixes, attempts=3):
    """plans/repros/fixes: per-attempt outcomes. Returns (result, destroy calls)."""
    tmp = tempfile.mkdtemp(); os.chdir(tmp)
    lab.OUT, lab.WORK, lab.GOOD, lab.TFCACHE = pathlib.Path("out"), pathlib.Path("work/lab"), pathlib.Path("work/good"), pathlib.Path("work/.tfcache")
    lab.EVIDENCE.clear(); lab.MAX_ATTEMPTS = attempts; lab.DRY = False; lab.BUCKET = "b"
    n = {"plan": 0, "repro": 0, "fix": 0, "lab": 0}; destroys = []
    def mk():
        n["lab"] += 1
        return {"summary": f"v{n['lab']}", "expected": "", "files": [{"path": f, "content": f"v{n['lab']}"} for f in lab.REQUIRED]}
    llm.claude_json = lambda *a, **k: mk()
    llm.refine = lambda kind, d, *a, **k: (d, {"approved": True, "reviewer": "ChatGPT", "points": []})
    def fake_run(step, cmd, timeout, cwd=None, env=None, record=True, sandbox=False):
        c = " ".join(cmd)
        if "terraform plan" in c:
            ok = plans[n["plan"]]; n["plan"] += 1; return (0, "planned") if ok else (1, "Error: invalid HCL")
        if "terraform show" in c: return 0, '{"resource_changes": []}'
        if "reproduce.sh" in c:
            r_ = repros[n["repro"]]; n["repro"] += 1; return 0, f"HERO_RESULT: {r_}"
        if "fix.sh" in c:
            f_ = fixes[n["fix"]]; n["fix"] += 1; return 0, f"HERO_RESULT: {f_}"
        if "terraform destroy" in c:
            destroys.append((pathlib.Path(cwd) / "main.tf").read_text()); return 0, ""
        if "sts" in c: return 0, "300054528837"
        if "list-objects-v2" in c: return 0, "[]"
        if cmd[0] == "aws" and "--output" in cmd: return 0, "{}"
        return 0, ""
    lab.run, lab.s3 = fake_run, (lambda *a: (0, ""))
    def gh_issue(m, u, b=None, h=None, timeout=30):
        return {"title": "Why ECS tasks stay PENDING", "body": "x", "labels": [{"name": "problem"}]}
    r._send = gh_issue
    lab.main()
    res = json.loads((lab.OUT / "result.json").read_text())
    os.chdir("/"); shutil.rmtree(tmp)
    return res, destroys

res, d = simulate([True], ["reproduced"], ["fixed"])
check("lab sim: happy path -> reproduced_and_fixed, destroyed once", res["status"] == "reproduced_and_fixed" and res["destroyed"] and len(d) == 1)
res, d = simulate([True, False], ["not_reproduced"], [], attempts=2)
check("lab sim: broken revision -> destroy uses the last config that planned (v1, not v2)", d == ["v1"] and res["destroyed"])
check("lab sim: last failure recorded", res["last_failure"]["step"] == "plan" and res["status"] == "failed")
res, d = simulate([True, False, True], ["not_reproduced", "reproduced"], ["fixed"])
check("lab sim: recovers on attempt 3 and destroys v3", res["status"] == "reproduced_and_fixed" and res["attempts"] == 3 and d == ["v3"])
res, d = simulate([False, False, False], [], [])
check("lab sim: nothing ever planned -> nothing to destroy", res["destroyed"] is True and d == [])
print("\nALL PASSED" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
