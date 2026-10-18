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
check("validate: aliased aws provider allowed",
      lab.validate({"files": good["files"] + [{"path": "x.tf", "content": 'provider "aws" {\n  alias = "use1"\n}\n'}]}) == [])
check("validate: missing files reported", "missing required file fix.sh" in " ".join(lab.validate({"files": [{"path": "main.tf", "content": ""}]})))
check("hero_result: last line wins", lab.hero_result("HERO_RESULT: not_reproduced\n...\nHERO_RESULT: reproduced\n") == "reproduced")
check("hero_result: absent", lab.hero_result("done") is None)
red = lab.redact("arn:aws:iam::300054528837:role/x key ASIAABCDEFGHIJKLMNOP aws_session_token = abc/def")
check("redact: account id, key id, session token", "300054528837" not in red and "ASIA" not in red and "abc/def" not in red)
check("slugify", lab.slugify("Debugging ECS: Tasks stuck in PENDING (Fargate)!") == "debugging-ecs-tasks-stuck-in-pending-far")

def plan(*rcs):
    return {"resource_changes": [{"type": t, "address": f"{t}.x", "change": {"actions": ["create"], "after": a}}
                                 for t, a in rcs]}
check("plan: allowed instance OK", check_plan.problems(plan(("aws_instance", {"instance_type": "t3.medium"}))) == [])
check("plan: big instance refused", check_plan.problems(plan(("aws_instance", {"instance_type": "m5.24xlarge"}))) != [])
check("plan: managed node group refused", check_plan.problems(plan(("aws_eks_node_group", {}))) != [])
check("plan: 4 instances refused", check_plan.problems(plan(*[("aws_instance", {"instance_type": "t3.medium"})] * 4)) != [])
check("plan: 200 GB volume refused", check_plan.problems(plan(("aws_ebs_volume", {"size": 200}))) != [])

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
check("draft: comment + drafted label", any(p.endswith("/issues/4/comments") for m, p, b in sent)
      and any(p.endswith("/issues/4/labels") and b == {"labels": ["drafted"]} for m, p, b in sent))

sent.clear()
res.update(status="not_reproduced", last_failure={"step": "reproduce (problem did not appear)",
                                                  "output": "see https://github.com/a/b/issues/1 and #3"})
(draft.OUT / "result.json").write_text(json.dumps(res))
draft.main()
check("draft: not reproduced -> comment + label, no PR",
      not any(p.endswith("/pulls") for m, p, b in sent) and any(b == {"labels": ["not-reproduced"]} for m, p, b in sent))
sent.clear(); (draft.OUT / "result.json").unlink()
draft.main()
check("draft: no result -> explanatory comment", len(sent) == 1 and sent[0][1].endswith("/issues/4/comments"))
shutil.rmtree(tmp)
print("\nALL PASSED" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
