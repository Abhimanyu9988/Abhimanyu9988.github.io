"""Offline tests for roles, contracts, evidence checks, gates, Tech Head and the Executive Assistant. No network."""
import json, os, pathlib, sys
HOME = "Abhimanyu9988/Abhimanyu9988.github.io"
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
os.chdir(ROOT)
os.environ.update(REPO=HOME, HERO_PUBLISH="1", BOT_TOKEN="test", GH_READ_TOKEN="test", MODEL_AUTO="")
import contract, editor, evaluate, llm, radar as r, roles, scan, sources, tech_head as th
import analytics, assistant

fails = 0
def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name); fails += not ok
r._send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tests must not reach GitHub"))

# ---------- contracts ----------
s = {"type": "object", "required": ["a"], "additionalProperties": False, "properties": {
    "a": {"type": "integer", "minimum": 1, "maximum": 5}, "b": {"enum": ["x"]}, "c": {"type": "array", "maxItems": 1}}}
check("contract: valid object", contract.validate({"a": 3, "b": "x"}, s) == [])
check("contract: required field", any("required" in p for p in contract.validate({}, s)))
check("contract: bounds", contract.validate({"a": 9}, s) != [])
check("contract: enum", contract.validate({"a": 1, "b": "y"}, s) != [])
check("contract: extra field refused", contract.validate({"a": 1, "z": 1}, s) != [])
check("contract: a boolean is not an integer", contract.validate({"a": True}, s) != [])
check("contract: list length", contract.validate({"a": 1, "c": [1, 2]}, s) != [])
for name in ("brief", "scorecard", "techreview", "goal", "plan", "registry"):
    check(f"schema loads: {name}", contract.schema(name).get("type") == "object")

# ---------- the team ----------
check("every role can be hired", roles.check_registry() == [])
reg = roles.registry()
check("Tech Head comes from a different vendor than the lab author", reg["tech_head"]["vendor"] != reg["lab_engineer"]["vendor"])
check("Editor comes from a different vendor than the writer", reg["editor"]["vendor"] != reg["writer"]["vendor"])
old = roles.registry
roles.registry = lambda: {"x": {**reg["editor"], "data_sources": ["made_up"]}}
check("an unknown data source stops hiring", any("unknown data source" in p for p in roles.check_registry()))
roles.registry = lambda: {"x": {**reg["editor"], "model": ""}}
check("an AI role with no pinned model is refused", any("pinned model" in p for p in roles.check_registry()))
roles.registry = old
roles._calls.clear()
for _ in range(reg["tech_head"]["budget"]["max_calls"]):
    roles.spend("tech_head", "x")
try: roles.spend("tech_head", "x"); ok = False
except roles.BudgetExceeded: ok = True
check("a role cannot exceed its call budget", ok)
roles._calls.clear()
try: roles.spend("tech_head", "x" * 200000); ok = False
except roles.BudgetExceeded: ok = True
check("a role cannot exceed its input budget", ok and roles._calls == {})
real_ask = llm.ask
answers = [{"summary": 1, "findings": []}, {"summary": "ok", "findings": []}]
llm.ask = lambda *a, **k: answers.pop(0)
check("a broken answer gets one retry", roles.ask("tech_head", "p", "techreview")["summary"] == "ok")
roles._calls.clear()
answers[:] = [{"summary": 1}, {"summary": 2}]
roles.registry = lambda: {**reg, "tech_head": {**reg["tech_head"], "budget": {"max_calls": 5, "max_input_tokens": 9999}}}
try: roles.ask("tech_head", "p", "techreview"); ok = False
except roles.BadOutput: ok = True
check("a second broken answer is refused, not trusted", ok)
roles.registry = old; llm.ask = real_ask; roles._calls.clear()
kept = dict(os.environ)
os.environ["EDITOR_MODEL"] = "m-x"
check("a repo variable replaces a lead's model", roles.model("editor") == "m-x")
del os.environ["EDITOR_MODEL"]

# ---------- costs and pins ----------
check("cost: list price arithmetic", llm.cost_usd([{"who": "c", "model": "claude-sonnet-5-5", "in": 1_000_000, "out": 100_000}])[0] == 3.0)
check("cost: an unpriced model is reported, not hidden", llm.cost_usd([{"who": "c", "model": "new", "in": 1, "out": 1}])[1] == ["new"])
check("cost: searches count", llm.cost_usd([{"who": "web_search", "model": "web_search", "in": 0, "out": 0, "searches": 5}])[0] == 0.05)
llm._models.clear()
check("pin: Claude default needs no model list", llm.claude_model() == "claude-sonnet-5-5")
check("pin: OpenAI default", llm.openai_model() == "gpt-5.5")
llm._models.clear()

# ---------- web research ----------
calls = []
def fake_call(method, url, body=None, headers=None, timeout=30):
    calls.append(body)
    if len(calls) == 1:
        return {"stop_reason": "pause_turn", "usage": {}, "content": [{"type": "server_tool_use"}]}
    return {"stop_reason": "end_turn", "usage": {"input_tokens": 10, "output_tokens": 5}, "content": [
        {"type": "web_search_tool_result", "content": [{"url": "https://x.y/a", "title": "A"}]}, {"type": "text", "text": "sum"}]}
real_call, llm.call = llm.call, fake_call
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
got = llm.web_research("q")
check("web research: resumes a paused turn and keeps real pages", got["pages"][0]["url"] == "https://x.y/a" and len(calls) == 2)
def broken(*a, **k): raise RuntimeError("HTTP 400: nope")
llm.call = broken
check("web research: a failure weakens the brief, never breaks the run", llm.web_research("q") == {})
llm.call = real_call

# ---------- evidence checks ----------
pr88 = scan.read_logs("tests/fixtures/pr88")
rep = scan.check(pr88, "ECS Fargate: 3 Tasks, 3 Splunk Metric Series, One OTel Sidecar")
kinds = {f["kind"] for f in rep["flags"]}
check("scan: PR 88 null x200 is flagged", "null_output" in kinds)
check("scan: PR 88 1-of-3 gap is flagged", "coverage_gap" in kinds)
check("scan: PR 88's unsupported title claim is found", [h["claim"] for h in rep["headline"] if not h["supported"]] == ["3 Splunk Metric Series"])
check("scan: PR 88 does not pass", rep["passed"] is False)
check("scan: an invented percentage is caught", not scan.headline_claims("Cut latency by 90%", L_ := {"a.log": "p95 1200ms"})[0]["supported"])
check("scan: a measured percentage passes", scan.headline_claims("Send 10% to v2", {"a.log": "traffic 10 % to v2"})[0]["supported"])
check("scan: a reason names file and line", "05-fix.log:27" in scan.markdown(rep))
for lab, title in (("eks-canary-releases-alb", "Roll back an EKS release in 16 seconds without restarting a pod"),
                   ("scale-to-zero-sqs-workers-on-ecs-fargate", "Fargate SQS worker stuck at 0 tasks?")):
    check(f"scan: no false alarm on {lab}", scan.check(scan.read_logs(f"labs/{lab}/evidence"), title)["passed"])
m = {"id": "cpu", "what": "", "kind": "overhead", "unit": "", "accept": {"op": "present"}, "min_entities": 3}
L = lambda *x: {"a.log": "\n".join(x)}
check("contract check: missing measurement", scan.verify_contract([m], L("x"))["results"][0]["status"] == "missing")
check("contract check: null is not a value", scan.verify_contract([m], L("HERO_MEASURE: cpu[a]=null"))["results"][0]["status"] == "no_value")
check("contract check: too few entities", scan.verify_contract([m], L("HERO_MEASURE: cpu[a]=1"))["results"][0]["status"] == "too_few_entities")
check("contract check: complete", scan.verify_contract([m], L(*[f"HERO_MEASURE: cpu[{i}]=1" for i in "abc"]))["passed"])
m2 = {**m, "min_entities": 1, "accept": {"op": "lt", "value": 5}}
check("contract check: fixed criterion enforced", scan.verify_contract([m2], L("HERO_MEASURE: cpu=9"))["results"][0]["status"] == "failed_criterion")
check("contract check: text can't satisfy a number", scan.verify_contract([m2], L("HERO_MEASURE: cpu=fast"))["results"][0]["status"] == "not_numeric")

# ---------- Topic Investment Gate ----------
cases = {c["id"]: c for c in json.loads((ROOT / "tests/eval/cases.json").read_text())}
check("gate 1: a strong brief passes", editor.gate1(cases["strong-sidecar-cost"]["brief"])[0])
check("gate 1: count-only contract fails", not editor.gate1(cases["trivial-exists"]["brief"])[0])
check("gate 1: 'does it work' fails", not editor.gate1(cases["trivial-does-it-work"]["brief"])[0])
check("gate 1: forced Splunk fails", not editor.gate1(cases["trivial-forced-splunk"]["brief"])[0])
b = cases["strong-alb-503"]["brief"]
check("brief marker round trip", editor.parse_brief("x " + editor.marker(b)) == b)
check("brief marker passes GitHub text safety", r.assert_safe(editor.marker(b)) is None)
check("a bad marker is ignored", editor.parse_brief("<!-- hero-brief:v1 @@@ -->") is None)
os.environ["EDITOR_MODE"] = "nonsense"
check("mode: unknown falls back to shadow", editor.mode() == "shadow")
os.environ["EDITOR_MODE"] = "shadow"
body = "x " + editor.marker(b)
check("shadow: no rule reaches the lab", editor.lab_rules(body) == "")
check("shadow: nothing reaches the writer", editor.writer_context(b, {"flags": []}) == "")
check("editor review is off by default", not editor.review_on())
os.environ["EDITOR_MODE"] = "warn"
check("warn: the contract reaches the lab", "HERO_MEASURE" in editor.lab_rules(body))
check("warn: no brief means no contract rules", editor.lab_rules("plain") == "")
os.environ["EDITOR_MODE"] = "shadow"
found = {"pages": [{"title": "T", "url": "https://real/1"}], "summary": "s"}
reals = llm.web_research, roles.ask
llm.web_research = lambda q: found
roles.ask = lambda role, p, o: {**b, "existing_pages": [{"title": "T", "url": "https://real/1", "covers": ""},
                                                        {"title": "Fake", "url": "https://invented/2", "covers": ""}]}
check("brief: pages the search never found are removed", [p["url"] for p in editor.brief("t", "d")["existing_pages"]] == ["https://real/1"])
llm.web_research, roles.ask = reals

# ---------- decisions are made by code ----------
card = {"scores": {"technical": {"score": 5, "reasons": []}, "editorial": {"score": 2, "reasons": []},
                   "marketing": {"score": 5, "reasons": []}}, "blocking": [], "flags_addressed": [], "claims": []}
check("a 5 on technical cannot offset a 2 on editorial", editor.decide(card, {"passed": True, "flags": []})[0] == "revise")
card["scores"]["editorial"]["score"] = 4
card["scores"]["marketing"]["score"] = 1
check("marketing never blocks", editor.decide(card, {"passed": True, "flags": []})[0] == "pass")
check("evidence failure blocks whatever the Editor thinks", editor.decide(card, {"passed": False, "flags": []})[0] == "revise")
card["claims"] = [{"text": "x", "class": "sourced", "source_ref": "https://made/up"}]
check("an outside claim needs a known source", editor.decide(card, {"passed": True, "flags": []})[0] == "revise")
check("the same claim passes with a known source", editor.decide(card, {"passed": True, "flags": []}, ["https://made/up"])[0] == "pass")
card["claims"] = []
flag = {"id": "f1", "severity": "high", "why": "gap"}
check("an unanswered high flag blocks", editor.decide(card, {"passed": True, "flags": [flag]})[0] == "revise")
card["flags_addressed"] = [{"id": "f1", "addressed": True, "how": "stated"}]
check("an answered flag clears", editor.decide(card, {"passed": True, "flags": [flag]})[0] == "pass")

# ---------- evaluation set ----------
res = evaluate.run(list(cases.values()))
check("eval: every saved unsupported headline is detected", res["headline_detection"] == 1.0)
check("eval: trivial topics rejected", res["trivial_rejection"] >= 0.8)
check("eval: no strong topic wrongly rejected", res["strong_false_rejection"] == 0)
check("eval: no evidence failure bypasses the gate", res["evidence_bypass"] == 0)
check("eval: sample sizes are reported", res["headline_cases"] >= 3 and res["trivial_cases"] >= 5)
rows = evaluate.human_comparison()
check("human comparison: unrated pieces stay unscored, never guessed", all(x["agrees"] is None for x in rows) and len(rows) == 6)
check("human comparison: PR 88 fails the code checks", [x for x in rows if x["piece"].startswith("ecs-fargate")][0]["checks_passed"] is False)
check("cost per worthwhile article counts all spend", evaluate.cost_per_worthwhile(20, [5, 4, 2, None]) == 10)
check("cost per worthwhile article is empty without ratings", evaluate.cost_per_worthwhile(20, [None]) is None)

# ---------- Tech Head ----------
D = """diff --git a/pipeline/x.py b/pipeline/x.py
--- a/pipeline/x.py
+++ b/pipeline/x.py
@@ -1,2 +10,5 @@
 keep
+subprocess.run(cmd, shell=True)
+token = "AKIAABCDEFGHIJKLMNOP"
+while True:
+    eval(text)
diff --git a/.github/workflows/y.yml b/.github/workflows/y.yml
--- a/.github/workflows/y.yml
+++ b/.github/workflows/y.yml
@@ -1 +1,3 @@
+  pull_request_target:
+      - run: echo ${{ github.event.comment.body }}
"""
got = {f["rule"]: f for f in th.rules(D)}
check("Tech Head: finds a key, shell, eval and a loop", {"hard-secret", "shell-true", "dynamic-exec", "unbounded-loop"} <= set(got))
check("Tech Head: finds risky workflow triggers", {"pr-target", "workflow-injection"} <= set(got))
check("Tech Head: cites the new-file line", got["shell-true"]["line"] == 11 and got["shell-true"]["file"] == "pipeline/x.py")
check("Tech Head: a clean change has no findings", th.rules("+++ b/pipeline/z.py\n@@ -0,0 +1 @@\n+x = 1\n") == [])
check("Tech Head: test fixtures are not findings", th.rules("+++ b/tests/t.py\n@@ -0,0 +1 @@\n+eval(x)\n") == [])
ai = {"summary": "s", "findings": [{"severity": "major", "category": "cost", "file": "pipeline/x.py", "line": 1, "problem": "p", "fix": "f"},
                                   {"severity": "blocker", "category": "security", "file": "invented.py", "line": 1, "problem": "p", "fix": "f"}]}
f, dropped, blocked = th.verdict([], ai, D)
check("Tech Head: a finding outside the diff is dropped", dropped == 1 and len(f) == 1 and not blocked)
check("Tech Head: code rules block without any model", th.verdict(th.rules(D), None, D)[2])
k, dr, bl = th.verdict(th.rules(D), None, D)
check("Tech Head: output is safe to post", r.assert_safe(r.safe_text(th.markdown(k, None, dr, bl))) is None)

# ---------- Executive Assistant ----------
check("assistant: parse add", assistant.parse("/goal grow in the AWS community") == ("add", "grow in the AWS community"))
check("assistant: parse drop", assistant.parse("/goal drop aws-community") == ("drop", "aws-community"))
check("assistant: parse list", assistant.parse("/goals") == ("list", ""))
check("assistant: anything else is help", assistant.parse("hello")[0] == "help")
check("assistant: slugs stay unique", assistant.slug("AWS Community!", {"aws-community"}) == "aws-community-2")
p = assistant.clean_plan({"focus": "f", "assignments": [{"role": "editor", "task": "t", "why": "w"},
                                                        {"role": "seo_wizard", "task": "keywords", "why": "w"}], "needs": []})
check("assistant: real roles get work", [a["role"] for a in p["assignments"]] == ["editor"])
check("assistant: it can propose a role but never create one", "No seo_wizard role exists: keywords" in p["needs"])
saved = {}
files = {}
analytics.ensure_branch = lambda: None
analytics.read_file = lambda path, ref=None: (files.get(path, ""), "sha" if path in files else None)
def wf(path, text, sha, msg): files[path] = text; saved[path] = msg
analytics.write_file = wf
posted = []
assistant.comment = lambda n, t: posted.append(t)
real_roles_ask = roles.ask
def fake_ea(role, prompt, out):
    if out == "goal":
        return {"title": "AWS community articles", "outcome": "o", "audience": "a", "weight": 8, "focus": [], "communities": [], "splunk": "wherever_applicable"}
    return {"focus": "f", "assignments": [{"role": "editor", "task": "t", "why": "w"}], "needs": []}
roles.ask = fake_ea
assistant.sources.plan = lambda: {}
assistant.add_goal("AWS community articles", 5)
g = json.loads(files["analytics/goals.json"])["goals"][0]
check("assistant: a goal is saved with your words", g["stated"] == "AWS community articles" and g["id"] == "aws-community-articles")
check("assistant: the plan is saved and shown", "analytics/plan.json" in files and "editor" in posted[-1])
check("assistant: writes pass the bot's allowlist paths", all(p.startswith("analytics/") for p in files))
assistant.drop_goal("aws-community-articles", 5)
check("assistant: a dropped goal stops being active", json.loads(files["analytics/goals.json"])["goals"][0]["status"] == "dropped")
roles.ask = real_roles_ask
real_goals = sources.goals
sources.goals = lambda: [{"id": "aws", "weight": 8}]
check("topic ranking reads goal weights", r.goal_weights() == {"aws": 8})
sources.goals = lambda: (_ for _ in ()).throw(RuntimeError("down"))
check("no goals available means today's ranking", r.goal_weights() == {})
sources.goals = real_goals
check("Splunk is no longer mandatory in topic votes", "never force it in" in r.BUILD_QUESTION)

# ---------- regressions from the independent review ----------
import draft, tempfile, time
L1 = {"a.log": "found 1,000 series\nstep 3: list series\ncold start took 30 seconds"}
sup = lambda t, logs=L1: [h["supported"] for h in scan.headline_claims(t, logs)]
check("title: a hyphenated claim is parsed", sup("A 30-second Cold Start") == [True] and sup("A 45-second Cold Start") == [False])
check("title: a money claim is parsed", sup("Save $40 a Month") == [False])
check("title: number words are parsed", sup("Two Tasks, Five Series") == [False, False])
check("title: comma numbers are compared whole", sup("12,000 Metric Series") == [False] and sup("1,000 series") == [True])
check("title: a step number is not a count", sup("3 Splunk Metric Series") == [False])
check("title: one and zero stay ordinary words", sup("Trace a service with one annotation, zero code changes") == [])
c1 = [{"id": "x", "unit": "h", "accept": {"op": "lt", "value": 1}, "min_entities": 1}]
st = lambda v, c=c1: scan.verify_contract(c, {"a": f"HERO_MEASURE: x={v}"})["results"][0]["status"]
for bad in ('"null"', "<no value>", "error: not found", "5 (timeout)"):
    check(f"contract check: {bad} is not a value", st(bad) == "no_value")
check("contract check: a different unit is refused", st("0.5 m") == "wrong_unit")
check("contract check: a matching unit is accepted", st("0.5h") == "ok")
check("contract check: extra text is refused", st("5 maybe") == "wrong_unit" and st("about 5") == "not_numeric")
c3 = [{"id": "x", "min_entities": 3}]
check("contract check: entities must be labelled", scan.verify_contract(c3, {"a": "HERO_MEASURE: x=1\nHERO_MEASURE: x=2\nHERO_MEASURE: x=3"})["results"][0]["status"] == "too_few_entities")
check("contract check: one null among real values fails", scan.verify_contract([{"id": "x", "min_entities": 1}], {"a": "HERO_MEASURE: x=1\nHERO_MEASURE: x=null"})["results"][0]["status"] == "bad_value")
with tempfile.TemporaryDirectory() as d:
    (pathlib.Path(d) / "05-fix.log").write_text("HERO_MEASURE: x=1\n")
    (pathlib.Path(d) / "11-fix.log").write_text("HERO_MEASURE: x=null\n")
    check("evidence: an earlier attempt's numbers are ignored", list(scan.read_logs(d, first=6)) == ["11-fix.log"])
    check("evidence: all logs by default", len(scan.read_logs(d)) == 2)
ok_json = scan.scan({"04-fix.log": '  "finishedAt": null'})
check("flags: a null field in raw JSON is not an anomaly", ok_json == [])
check("flags: high flags do not fail checks that logs cannot fix", scan.check(pr88, "Fargate tasks")["passed"])
check("flags: they stay in the report", scan.check(pr88, "Fargate tasks")["flags"] != [])
check("flags: the report is capped", len(scan.check({"05-fix.log": "null\n" * 500}, "")["flags"]) == scan.MAX_FLAGS)

os.environ["EDITOR_MODE"] = "enforce"
good = {**b, "gate1": {"passed": True}}
check("enforce: no brief, no lab", editor.lab_refusal("plain") != "")
check("enforce: a failed brief, no lab", editor.lab_refusal(editor.marker({**b, "gate1": {"passed": False}})) != "")
check("enforce: a passing brief may run", editor.lab_refusal(editor.marker(good)) == "")
os.environ["EDITOR_MODE"] = "shadow"
check("shadow: labs are never refused", editor.lab_refusal("plain") == "")

draft.OUT = pathlib.Path(tempfile.mkdtemp())
os.environ["EDITOR_MODE"] = "enforce"; os.environ["EDITOR_REVIEW"] = "on"
logs_ok = {"05-fix.log": "ok"}
real_score, real_ask, real_refine = editor.score, llm.ask, llm.refine
def bad_score(*a): raise RuntimeError("outage")
editor.score = bad_score
refined = []
llm.ask = lambda *a, **k: {"body": "new body", "title": "t", "linkedin": "unchecked text"}
def fake_refine(kind, post, *a, **k):
    refined.append(post); return post, {}
llm.refine = fake_refine
g = draft.review_draft({"title": "t", "body": "b", "linkedin": "checked text"}, "ctx", logs_ok, None, {}, "build")
check("enforce: an Editor outage never reads as approval", g["decision"] == "stop")
check("enforce: a revision goes back through the fact check", len(refined) == 1)
check("enforce: revision keeps the checked LinkedIn text", g["post"]["linkedin"] == "checked text")
editor.score = real_score
os.environ["EDITOR_REVIEW"] = "off"
llm.ask = lambda *a, **k: {"body": "new body", "title": "Scale to 500 pods"}
g = draft.review_draft({"title": "Scale to 500 pods", "body": "b", "linkedin": "x"}, "ctx", logs_ok, None, {}, "build")
check("enforce: an unsupported title stops after one revision", g["decision"] == "stop")
os.environ["EDITOR_MODE"] = "shadow"
g = draft.review_draft({"title": "Scale to 500 pods", "body": "b"}, "ctx", logs_ok, None, {}, "build")
check("shadow: a failed check only reports", g["decision"] == "revise" and "Shadow review" in draft.gate_md(g))
llm.ask, llm.refine = real_ask, real_refine
real_pv, real_lask = llm.panel_votes, llm.ask
llm.panel_votes = lambda cands, *a, **k: {c["id"]: {"yes": 1, "votes": {}} for c in cands}
llm.ask = lambda who, s, p, sch, tool="submit", **k: {"linkedin": f"{who} post"} if tool == "submit_linkedin" else {"ok": True, "problems": []}
pp, lg = draft.linkedin_round({"title": "t", "body": "b", "linkedin": "writer text"}, "build")
check("LinkedIn: an unchosen post is blank, so it cannot reach the email", pp["linkedin"] == "" and lg["unapproved"])
check("LinkedIn: the PR keeps the text as a starting point", "writer text" in draft.linkedin_md(lg))
llm.panel_votes, llm.ask = real_pv, real_lask

# shadow runs after the issues exist, and never touches what a lab reads
sent = []
def fake_send(m, u, b=None, h=None, timeout=30):
    if m == "GET": raise RuntimeError("HTTP 404")
    sent.append((m, u, b)); return {"number": 7}
r._send = fake_send
real_brief = editor.brief
editor.brief = lambda t, d: dict(b)
real_pv = llm.panel_votes
llm.panel_votes = lambda v, q, c="", tier="scout": {x["id"]: {"votes": {w: ("yes", "r") for w in llm.PANEL}, "yes": 3, "accepted": True} for x in v}
props = [("claude", {"title": "Topic one", "sources": ["https://x.y/1"], "replicability": 9, "audience": 9})]
os.environ["EDITOR_MODE"] = "shadow"
r.post_topics(props, [{"url": "https://x.y/1"}], [], set(), "radar", lambda t, s: "top", 3)
bodies = [x[2] for x in sent if x[1].endswith("/issues")]
notes = [x[2]["body"] for x in sent if x[1].endswith("/comments")]
check("shadow: the issue a lab reads holds no brief", "hero-brief" not in bodies[0]["body"] and "Evidence Contract" not in bodies[0]["body"])
check("shadow: the brief arrives as a comment, after the issue", any("Shadow review" in n for n in notes))
sent.clear()
def hung(t, d): raise OSError("timed out")
editor.brief = hung
r.post_topics([("claude", {"title": "Topic two", "sources": ["https://x.y/2"], "replicability": 9, "audience": 9})],
              [{"url": "https://x.y/2"}], [], set(), "radar", lambda t, s: "top", 3)
check("shadow: a failed brief still opens the topic", any(x[1].endswith("/issues") for x in sent))
sent.clear()
real_gate = r.GATE_SECONDS
r.GATE_SECONDS = -1
got = r.investment_gate([{"id": "t1", "title": "x", "top": "y"}])
check("gate 1: the time limit stops further briefs", got == {})
r.GATE_SECONDS = real_gate
editor.brief = real_brief; llm.panel_votes = real_pv
r._send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tests must not reach GitHub"))

D2 = """diff --git a/pipeline/x.py b/pipeline/x.py
--- a/pipeline/x.py
+++ b/pipeline/x.py
@@ -0,0 +1,3 @@
+++ decoy
+subprocess.run(cmd, shell=True)
+z = 1
diff --git a/.github/workflows/y.yml b/.github/workflows/y.yml
--- a/.github/workflows/y.yml
+++ b/.github/workflows/y.yml
@@ -0,0 +1,6 @@
+on: pull_request_target
+      - run: |
+          echo ${{ github.event.comment.body }}
+      - uses: x
+        with:
+          ref: ${{ github.event.pull_request.head.sha }}
"""
ids = {f["rule"]: f for f in th.rules(D2)}
check("Tech Head: a line starting with ++ cannot hide later lines", "shell-true" in ids and ids["shell-true"]["line"] == 2)
check("Tech Head: the one-line trigger form is caught", "pr-target" in ids)
check("Tech Head: injection inside a multi-line run is caught", ids.get("workflow-injection", {}).get("line") == 3)
check("Tech Head: a plain with: value is not injection", len([f for f in th.rules(D2) if f["rule"] == "workflow-injection"]) == 1)
check("Tech Head: a secret under tests is still caught", th.rules('+++ b/tests/t.py\n@@ -0,0 +1 @@\n+k = "AKIAABCDEFGHIJKLMNOP"\n') != [])
ai2 = {"findings": [{"severity": "blocker", "category": "security", "file": "pipeline/x.py", "line": 1, "problem": "p", "fix": "f"}]}
fs, dr2, bl2 = th.verdict([], ai2, D2)
check("Tech Head: a model alone cannot block, a person judges it", not bl2 and "please judge" in th.markdown(fs, ai2, dr2, bl2))
check("field notes: instructions are not read as notes", sources.field_notes() == "")
check("roles: registry data sources reach the role", "field_notes" in roles.data("editor") and "agents_md" in roles.data("tech_head"))
real_read = sources._analytics_json
sources._analytics_json = lambda p: {"goals": [{"id": "a", "weight": 1, "until": "2020-01-01"}, {"id": "b", "weight": 1, "until": ""}]}
check("goals: an expired goal stops counting", [g["id"] for g in sources.goals()] == ["b"])
sources._analytics_json = real_read
files.clear(); posted.clear()
roles.ask = fake_ea
real_mp = assistant.make_plan
assistant.make_plan = lambda active: (_ for _ in ()).throw(RuntimeError("down"))
assistant.add_goal("same goal", 5)
check("assistant: a failed plan still replies", "could not update the team plan" in posted[-1])
assistant.make_plan = real_mp
assistant.add_goal("same goal", 5)
check("assistant: a retry does not duplicate the goal", len(json.loads(files["analytics/goals.json"])["goals"]) == 1)
roles.ask = real_roles_ask

print("\nALL PASSED" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
