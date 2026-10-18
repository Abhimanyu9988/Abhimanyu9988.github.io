"""Offline tests for the three-AI panel, the lab runner's checks and the drafter. No network, no AWS."""
import json, os, pathlib, shutil, sys, tempfile
HOME = "Abhimanyu9988/Abhimanyu9988.github.io"
PIPE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline")
os.environ.update(REPO=HOME, HERO_PUBLISH="1", BOT_TOKEN="test", GH_READ_TOKEN="test", ISSUE="4", RUN_ID="123456789")
sys.path.insert(0, PIPE)
import radar as r, llm, lab, draft, check_plan

fails = 0
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

# ---------- vote ----------
calls = []
def fake_gpt(system, prompt, **k):
    calls.append("gpt")
    return {"votes": [{"id": "t1", "vote": "yes", "reason": "solid"}, {"id": "t2", "vote": "no", "reason": "vague"},
                      {"id": "t3", "vote": "no", "reason": "dup"}]}
def fake_gem(system, prompt, **k):
    calls.append("gemini")
    return {"votes": [{"id": "t2", "vote": "yes", "reason": "reproducible"}, {"id": "t3", "vote": "no", "reason": "agree"}]}
llm.gpt_json, llm.gemini_json = fake_gpt, fake_gem
d = {x["id"]: x for x in llm.vote([{"id": "t1"}, {"id": "t2"}, {"id": "t3"}, {"id": "t4"}], "q")}
check("vote: Claude+ChatGPT yes -> accepted, no tiebreak", d["t1"]["accepted"] and d["t1"]["gemini"] is None)
check("vote: split, Gemini yes -> accepted 2:1", d["t2"]["accepted"] and d["t2"]["gemini"][0] == "yes")
check("vote: split, Gemini no -> rejected", not d["t3"]["accepted"])
check("vote: missing ChatGPT vote goes to Gemini, missing there too -> rejected", not d["t4"]["accepted"])
check("vote: Gemini called once, only for splits", calls == ["gpt", "gemini"])

# ---------- refine ----------
DRAFT = {"x": 1}
def gpt_review(system, prompt, **k):
    return {"approve": False, "points": [
        {"id": "p1", "severity": "blocking", "issue": "a", "suggestion": "a"},
        {"id": "p2", "severity": "blocking", "issue": "b", "suggestion": "b"},
        {"id": "p3", "severity": "blocking", "issue": "c", "suggestion": "c"},
        {"id": "p4", "severity": "minor", "issue": "d", "suggestion": "d"}]}
claude_calls = []
def fake_claude(system, prompt, schema, tool="submit", **k):
    claude_calls.append(tool)
    if tool == "respond":
        return {"responses": [{"id": "p1", "decision": "accept", "reason": "ok"},
                              {"id": "p2", "decision": "reject", "reason": "wrong"},
                              {"id": "p3", "decision": "reject", "reason": "wrong"},
                              {"id": "p4", "decision": "reject", "reason": "style"}], "revised": {"x": 2}}
    return {"x": 3}
def gem_rule(system, prompt, **k):
    return {"rulings": [{"id": "p2", "side": "chatgpt", "reason": "it is a bug"},
                        {"id": "p3", "side": "claude", "reason": "fine"}]}
llm.gpt_json, llm.gemini_json, llm.claude_json = gpt_review, gem_rule, fake_claude
final, log = llm.refine("lab", DRAFT, "rubric", "", {"type": "object"})
out = {p["issue"]: p["outcome"] for p in log["points"]}
check("refine: accepted point applied", out["a"].startswith("applied (Claude"))
check("refine: Gemini sides with ChatGPT -> Claude must apply", out["b"] == "applied (Gemini sided with ChatGPT)")
check("refine: Gemini sides with Claude -> not applied", out["c"] == "not applied (Gemini sided with Claude)")
check("refine: rejected minor point needs no tiebreak", out["d"].startswith("not applied (minor"))
check("refine: forced revision is the final draft", final == {"x": 3} and claude_calls == ["respond", "revise"])
llm.gpt_json = lambda *a, **k: {"approve": True, "points": []}
final, log = llm.refine("lab", DRAFT, "r", "", {})
check("refine: clean approval returns draft untouched", final == DRAFT and log["chatgpt_approved"])

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
sent = []
def fake_send(m, u, b=None, h=None, timeout=30):
    path = u.replace("https://api.github.com", "")
    if m == "GET" and "/git/ref/heads/draft/" in path: raise RuntimeError("HTTP 404")
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
(draft.OUT / "result.json").write_text(json.dumps(res)); (draft.OUT / "topic.json").write_text("{}")
llm.claude_json = lambda *a, **k: {"title": "Why ECS tasks stay PENDING", "description": "d",
                                   "tags": ["AWS", "ECS!", "fargate", "devops", "extra"],
                                   "body": "Body links https://github.com/aws/x/issues/5 freely."}
llm.refine = lambda kind, d, *a, **k: (d, {"chatgpt_approved": True, "points": []})
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
check("draft: post file may link to GitHub (files never backlink)", "github.com/aws/x/issues/5" in text)
pr = next(b for m, p, b in sent if p.endswith("/pulls"))
check("draft: PR closes the topic and neutralizes AI text", pr["body"].endswith("Closes #4") and "aws/x#5" not in pr["body"])
order = [m for m, p, b in sent]
check("draft: branch created only after the PR text passed the checks", order.index("POST") >= 0 and sent[0][1].endswith("/git/refs"))
check("draft: comment + drafted label", any(p.endswith("/issues/4/comments") for m, p, b in sent)
      and any(p.endswith("/issues/4/labels") and b == {"labels": ["drafted"]} for m, p, b in sent))

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
    llm.refine = lambda kind, d, *a, **k: (d, {"chatgpt_approved": True, "points": []})
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
