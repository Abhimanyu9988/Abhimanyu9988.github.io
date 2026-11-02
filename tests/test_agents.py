"""Offline tests for roles, contracts, evidence checks, gates, Tech Head and the Executive Assistant. No network."""
import json, os, pathlib, sys
HOME = "Abhimanyu9988/Abhimanyu9988.github.io"
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))
os.chdir(ROOT)
os.environ.update(REPO=HOME, HERO_PUBLISH="1", BOT_TOKEN="test", GH_READ_TOKEN="test", MODEL_AUTO="")
for leftover in ("EDITOR_MODE", "EDITOR_REVIEW", "GOAL_RANKING", "LINKEDIN_MIN_VOTES", "TECH_HEAD_MODE"):
    os.environ.pop(leftover, None)             # these tests set their own modes
import re, tempfile, time
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
ratings_dir = pathlib.Path(tempfile.mkdtemp())       # independent of the ratings Abhi adds to the real file
rated = json.loads((ROOT / "tests/eval/human-ratings.json").read_text())
rated["ratings"] = {k: None for k in rated["ratings"]}
(ratings_dir / "human-ratings.json").write_text(json.dumps(rated))
real_eval, evaluate.EVAL = evaluate.EVAL, ratings_dir
rows = evaluate.human_comparison()
check("human comparison: unrated pieces stay unscored, never guessed", all(x["agrees"] is None for x in rows) and len(rows) == 6)
check("human comparison: PR 88 fails the code checks", [x for x in rows if x["piece"].startswith("ecs-fargate")][0]["checks_passed"] is False)
check("cost per worthwhile article counts all spend", evaluate.cost_per_worthwhile(20, [5, 4, 2, None]) == 10)
check("cost per worthwhile article is empty without ratings", evaluate.cost_per_worthwhile(20, [None]) is None)
evaluate.EVAL = real_eval

# ---------- Tech Head ----------
D = """diff --git a/pipeline/x.py b/pipeline/x.py
--- a/pipeline/x.py
+++ b/pipeline/x.py
@@ -1,2 +10,5 @@
 keep
+subprocess.run(cmd, shell=True)
+token = "FAKEKEY"
+while True:
+    eval(text)
diff --git a/.github/workflows/y.yml b/.github/workflows/y.yml
--- a/.github/workflows/y.yml
+++ b/.github/workflows/y.yml
@@ -1 +1,3 @@
+  pull_request_target:
+      - run: echo ${{ github.event.comment.body }}
"""
FAKE = "AKIA" + "ABCDEFGHIJKLMNOP"            # split so this file holds no key-shaped text for the rule to find
D = D.replace("FAKEKEY", FAKE)
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
check("assistant: dropping the last goal clears the plan", json.loads(files["analytics/plan.json"])["assignments"] == [] and "plan is clear" in posted[-1])
roles.ask = real_roles_ask
real_goals = sources.goals
sources.goals = lambda: [{"id": "aws", "weight": 8}]
check("topic ranking reads goals", r.goal_weights()["aws"]["weight"] == 8)
sources.goals = lambda: (_ for _ in ()).throw(RuntimeError("down"))
check("no goals available means today's ranking", r.goal_weights() == {})
sources.goals = real_goals
check("Splunk is no longer mandatory in topic votes", "never force it in" in r.BUILD_QUESTION)

# ---------- regressions from the independent review ----------
import draft
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
check("enforce: a failed brief, no lab", editor.lab_refusal(editor.marker({**b, "verdict": "no"})) != "")
check("enforce: a stored pass in issue text proves nothing", editor.lab_refusal(editor.marker({**b, "verdict": "no", "gate1": {"passed": True}})) != "")
check("enforce: a forged minimal brief is refused", editor.lab_refusal(editor.marker({"gate1": {"passed": True}})) != "")
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
import analytics as analytics_s
saved_briefs, real_w, real_b = [], analytics_s.write_file, analytics_s.ensure_branch
analytics_s.write_file, analytics_s.ensure_branch = (lambda path, *a: saved_briefs.append(path)), (lambda: None)
r.post_topics(props, [{"url": "https://x.y/1"}], [], set(), "radar", lambda t, s: "top", 3)
analytics_s.write_file, analytics_s.ensure_branch = real_w, real_b
bodies = [x[2] for x in sent if x[1].endswith("/issues")]
notes = [x[2]["body"] for x in sent if x[1].endswith("/comments")]
check("shadow: the issue a lab reads holds no brief", "hero-brief" not in bodies[0]["body"] and "Evidence Contract" not in bodies[0]["body"])
check("shadow: the brief is saved, not emailed", saved_briefs == ["analytics/brief-7.json"] and not any("Shadow review" in n for n in notes))
check("emails: a topic is not assigned to Abhi", bodies[0].get("assignees") == [])
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
check("Tech Head: a secret under tests is still caught", th.rules(f'+++ b/tests/t.py\n@@ -0,0 +1 @@\n+k = "{FAKE}"\n') != [])
check("Tech Head: a concurrency value is not injection", th.rules('+++ b/.github/workflows/a.yml\n@@ -0,0 +1 @@\n+concurrency: { group: "x-${{ github.event.pull_request.number }}" }\n') == [])
ai2 = {"findings": [{"severity": "blocker", "category": "security", "file": "pipeline/x.py", "line": 1, "problem": "p", "fix": "f"}]}
fs, dr2, bl2 = th.verdict([], ai2, D2)
check("Tech Head: a model alone cannot block, a person judges it", not bl2 and "please judge" in th.markdown(fs, ai2, dr2, bl2))
notes_dir = pathlib.Path(tempfile.mkdtemp())
(notes_dir / "field-notes.md").write_text("- a rule line\n\n## Notes\n\n- a real note\n")
real_here, sources.HERE = sources.HERE, notes_dir
check("field notes: instructions are not read as notes", sources.field_notes() == "- a real note")
sources.HERE = real_here
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

# ---------- PR 89 review: enforcement must have a scorecard ----------
os.environ["EDITOR_MODE"] = "enforce"
os.environ.pop("EDITOR_REVIEW", None)
check("enforce: the Editor runs even with EDITOR_REVIEW unset", editor.review_on())
os.environ["EDITOR_REVIEW"] = "off"
check("enforce: EDITOR_REVIEW=off cannot switch the Editor off", editor.review_on())
check("enforce: no scorecard means no pass", editor.decide(None, {"passed": True, "flags": []}, require_card=True)[0] == "revise")
good_card = {"scores": {k: {"score": 5, "reasons": []} for k in ("technical", "editorial", "marketing")},
             "blocking": [], "flags_addressed": [], "claims": []}
calls_made = []
real_score, real_ask, real_refine = editor.score, llm.ask, llm.refine
editor.score = lambda *a: calls_made.append(1) or good_card
post_ok = {"title": "A plain title", "body": "b", "linkedin": "x"}
g = draft.review_draft(post_ok, "ctx", {"05-fix.log": "ok"}, None, {}, "build")
check("enforce: a successful scorecard is what lets a draft pass", g["decision"] == "pass" and calls_made)
def down(*a): raise RuntimeError("outage")
editor.score = down
llm.ask = lambda *a, **k: {}
g = draft.review_draft(post_ok, "ctx", {"05-fix.log": "ok"}, None, {}, "build")
check("enforce: a failed scorecard stops the draft with EDITOR_REVIEW off", g["decision"] == "stop")
editor.score, llm.ask, llm.refine = real_score, real_ask, real_refine
os.environ["EDITOR_MODE"] = "shadow"; os.environ.pop("EDITOR_REVIEW", None)
check("shadow: the Editor stays off unless asked", not editor.review_on())

# ---------- PR 89 review: can a measurement lie? ----------
cm = [{"id": "sidecar_cpu", "what": "sidecar CPU", "unit": "", "accept": {"op": "present"}, "min_entities": 1}]
raw = {"05-fix.log": "ecs task stats: sidecar cpu 35\nHERO_MEASURE: sidecar_cpu=35"}
bare = {"05-fix.log": "HERO_MEASURE: sidecar_cpu=35"}
res1 = lambda logs, sc=None, strict=False: scan.verify_contract(cm, logs, sc, strict)["results"][0]
check("provenance: raw output above the value corroborates it", res1(raw)["provenance"] == "corroborated")
check("provenance: a bare line is only self-reported", res1(bare)["provenance"] == "self_reported" and res1(bare)["status"] == "ok")
check("adversarial: enforce refuses a self-reported value", res1(bare, None, True)["status"] == "uncorroborated")
check("adversarial: a value typed into a script is refused", res1(bare, {"fix.sh": 'echo "HERO_MEASURE: sidecar_cpu=35"'})["status"] == "hardcoded")
check("adversarial: a constant typed into a script is refused", res1(bare, {"fix.sh": 'CPU=35\necho "HERO_MEASURE: sidecar_cpu=$CPU"'})["status"] == "hardcoded")
check("provenance: a value computed by the script is not called hardcoded",
      res1(raw, {"fix.sh": 'CPU=$(aws ecs describe-tasks)\necho "HERO_MEASURE: sidecar_cpu=$CPU"'})["status"] == "ok")
fake = {"fix.sh": 'echo "ecs task stats: sidecar cpu 35"\necho "HERO_MEASURE: sidecar_cpu=$V"'}
check("adversarial: raw text typed by the script corroborates nothing", res1(raw, fake)["provenance"] == "self_reported")
check("adversarial: that fails in enforce", res1(raw, fake, True)["status"] == "uncorroborated")
near = {"05-fix.log": "total memory 35\nHERO_MEASURE: sidecar_cpu=35"}
check("adversarial: the same number about something else does not count", res1(near)["provenance"] == "self_reported")
later = {"05-fix.log": "HERO_MEASURE: sidecar_cpu=35\nsidecar cpu 35"}
check("adversarial: output after the claim does not count", res1(later)["provenance"] == "self_reported")
check("adversarial: a claim in a Terraform log is ignored", res1({"03-apply.log": "HERO_MEASURE: sidecar_cpu=35"})["status"] == "missing")
two = [{"id": "x", "accept": {"op": "present"}, "min_entities": 2}]
check("adversarial: one entity with two answers conflicts", scan.verify_contract(two, {"f.log": "HERO_MEASURE: x[a]=1\nHERO_MEASURE: x[a]=9\nHERO_MEASURE: x[b]=2"})["results"][0]["status"] == "conflicting")
tricky = {"fix.sh": 'V=$((30+5))\necho "sidecar cpu $V"\necho "HERO_MEASURE: sidecar_cpu=$V"'}
check("known limit: a script that computes a fake value still looks corroborated", res1(raw, tricky)["provenance"] == "corroborated")
check("scan says plainly that it cannot prove a value", "nothing here proves" in scan.__doc__.replace("\n", " "))
ct = [{"id": "task_count", "what": "running tasks", "unit": "", "accept": {"op": "present"}, "min_entities": 1}]
lg = {"05-fix.log": "running task count 3\nHERO_MEASURE: task_count=3"}
h1 = scan.check(lg, "Run 3 Tasks", ct)["headline"][0]
check("title: a corroborated measurement validates the claim", h1["validated"] and h1["basis"] == "measurement")
h2 = scan.check({"05-fix.log": "tasks: 3"}, "Run 3 Tasks")["headline"][0]
check("title: a text match stays a heuristic", h2["supported"] and not h2["validated"] and h2["basis"] == "heuristic")
check("title: the report says a text match is not proof", "not proof" in scan.markdown(scan.check({"05-fix.log": "tasks: 3"}, "Run 3 Tasks")))
check("title: enforce refuses a claim with no measurement", not scan.check({"05-fix.log": "tasks: 3"}, "Run 3 Tasks", strict=True)["passed"])
check("title: shadow only reports it", scan.check({"05-fix.log": "tasks: 3"}, "Run 3 Tasks")["passed"])
check("title: number words do not break validation", scan.check(lg, "Run three Tasks", ct)["headline"][0]["validated"])

# ---------- PR 89 review: scanners must not look clean when they did not run ----------
rp = pathlib.Path(tempfile.mkdtemp())
check("scanner: missing reports are unavailable, not clean", all(v["status"] == "unavailable" for v in th.read_reports(rp).values()))
(rp / "bandit.json").write_text(""); (rp / "bandit.exit").write_text("2")
check("scanner: an empty report is unavailable", th.read_reports(rp)["bandit"]["status"] == "unavailable")
(rp / "bandit.json").write_text('{"results": [], "errors": []}'); (rp / "bandit.exit").write_text("2")
check("scanner: a crash exit code is unavailable", th.read_reports(rp)["bandit"]["status"] == "unavailable")
(rp / "bandit.exit").write_text("0")
check("scanner: a real clean run is clean", th.read_reports(rp)["bandit"]["status"] == "clean")
(rp / "bandit.json").write_text('{"results": [], "errors": [{"filename": "x"}]}')
check("scanner: files it could not parse make it partial", th.read_reports(rp)["bandit"]["status"] == "partial")
(rp / "zizmor.json").write_text(""); (rp / "zizmor.exit").write_text("3")
check("scanner: zizmor with no output is unavailable", th.read_reports(rp)["zizmor"]["status"] == "unavailable")
(rp / "zizmor.json").write_text('[{"ident": "x"}]'); (rp / "zizmor.exit").write_text("14")
check("scanner: zizmor findings exit is not a crash", th.read_reports(rp)["zizmor"]["status"] == "findings")
reps = th.read_reports(rp)
check("Tech Head: an incomplete review is never called clean", "not a clean result" in th.markdown([], {"summary": "s"}, 0, False, "", reps))
check("Tech Head: no AI review is incomplete", any("AI review" in g for g in th.complete({}, None)))
wf = (ROOT / ".github/workflows/tech-head.yml").read_text()
scan_job = wf[wf.index("  scan:"):wf.index("  review:")]
check("workflow: the scan job holds no secrets", "secrets." not in scan_job)
check("workflow: a PR cannot switch bandit off", "--ini empty.ini --ignore-nosec" in scan_job)
check("workflow: a PR cannot configure zizmor", "--no-config" in scan_job)
check("workflow: runs from the base branch", "pull_request_target" in wf and "pull_request:" not in wf.split("jobs:")[0].replace("pull_request_target:", ""))
check("workflow: the reviewer is the base branch copy", "python3 base/pipeline/tech_head.py" in wf and "python3 pr/" not in wf)

dif = lambda f, body="+x\n": f"diff --git a/{f} b/{f}\n--- a/{f}\n+++ b/{f}\n@@ -0,0 +1 @@\n{body}"
big = "".join(dif(f) for f in ("tests/test_a.py", "pipeline/a.py", ".github/workflows/a.yml", "tests/fixtures/a.log"))
ordered, left = th.prioritise(big)
check("Tech Head: workflows are reviewed before pipeline code and tests", ordered.index(".github") < ordered.index("pipeline/a.py") < ordered.index("tests/test_a.py"))
check("Tech Head: saved fixtures are not reviewed", "fixtures" not in ordered and left == 0)
real_max, real_batches = th.MAX_DIFF, th.MAX_BATCHES
th.MAX_DIFF, th.MAX_BATCHES = 70, 1
check("Tech Head: a cut diff reports what was left out", th.prioritise(big)[1] > 0)
th.MAX_BATCHES = 2
chunks, _ = th.batches(big)
check("Tech Head: batches are bounded", len(chunks) <= 2 and all(len(c) <= 70 for c in chunks))
huge = dif("pipeline/h.py", "+x\n" * 100)
check("Tech Head: one huge file is split, not dropped", len(th.batches(huge)[0]) == 2 and th.batches(huge)[1] > 0)
seen_prompts = []
real_ra = roles.ask
def one_fails(role, prompt, out):
    seen_prompts.append(prompt)
    if len(seen_prompts) == 2:
        raise RuntimeError("outage")
    return {"summary": "s", "findings": []}
roles.ask = one_fails
th.MAX_DIFF, th.MAX_BATCHES = 160, 4
two = "".join(dif(f"pipeline/{n}.py", f"+{n * 30}\n") for n in "ab")
got_ai = th.review(two, {}, "")
check("Tech Head: each batch gets its own call", len(seen_prompts) == 2 and "batch 2 of 2" in seen_prompts[1])
check("Tech Head: a failed batch makes the review incomplete", any("batch" in g for g in th.complete({}, got_ai)))
roles.ask = lambda *a: (_ for _ in ()).throw(RuntimeError("outage"))
try: th.review(two, {}, ""); ok = False
except RuntimeError: ok = True
check("Tech Head: all batches failing is a failed review", ok)
roles.ask = real_ra
th.MAX_DIFF, th.MAX_BATCHES = 20, 1
check("Tech Head: a cut diff is incomplete", any("not reviewed" in g for g in th.complete({}, {"summary": ""}, 5)))
th.MAX_DIFF, th.MAX_BATCHES = real_max, real_batches
check("Tech Head: a comment naming a risk is not the risk", th.rules("+++ b/.github/workflows/a.yml\n@@ -0,0 +1 @@\n+# pull_request_target is safe here\n") == [])
check("Tech Head: a key in a comment is still caught", th.rules(f"+++ b/pipeline/a.py\n@@ -0,0 +1 @@\n+# {FAKE}\n") != [])

lc = (ROOT / ".github/workflows/live-check.yml").read_text()
lc_on = __import__("yaml").safe_load(lc)[True]
check("workflow: the live check starts only by hand or from a push to main", set(lc_on) == {"push", "workflow_dispatch"} and lc_on["push"]["branches"] == ["main"])
check("workflow: only the request file starts the live check", lc_on["push"]["paths"] == ["pipeline/live-check.request"])
check("workflow: the request names a pull request", (ROOT / "pipeline/live-check.request").read_text().strip().isdigit())
lc_run = next(s["run"] for s in __import__("yaml").safe_load(lc)["jobs"]["live"]["steps"] if s.get("name") == "live check")
check("workflow: a typed pull request number reaches the check only through env", "${{" not in lc_run)
for name in ("live-check", "editor-eval"):
    text = (ROOT / f".github/workflows/{name}.yml").read_text()
    check(f"policy: {name} refuses to run from a branch", "github.ref == 'refs/heads/main'" in text)
    check(f"policy: {name} checks out main, never the selected ref", "ref: main" in text and "github.sha" not in text and "head.sha" not in text)
    check(f"policy: {name} uses the protected environment", "environment: live-check" in text)
    check(f"policy: {name} has no pull request trigger", "pull_request" not in text.split("\non:", 1)[1].split("jobs:")[0])
check("workflow: a failed scanner install is recorded", "echo 127 > reports/bandit.exit" in wf)
(rp / "bandit.exit").write_text("127"); (rp / "bandit.json").unlink()
check("scanner: a failed install is unavailable, not clean", th.read_reports(rp)["bandit"]["status"] == "unavailable")

# ---------- PR 89 review: what changes on merge, and how to undo it ----------
os.environ["MODEL_AUTO"] = "true"
check("rollback: MODEL_AUTO=true turns the pins off", llm._pin("openai") == "")
os.environ["MODEL_AUTO"] = ""
check("default: models are pinned", llm._pin("openai") == "gpt-5.5")
sources_goals = sources.goals
sources.goals = lambda: [{"id": "aws", "weight": 8}]
os.environ["GOAL_RANKING"] = "off"
check("rollback: GOAL_RANKING=off ignores goals", r.goal_weights() == {} and r.goals_block() == "[]")
os.environ.pop("GOAL_RANKING")
sources.goals = sources_goals
def pick(goals):
    """post_topics ordering with no Editor involved: the same key the pipeline uses."""
    sent.clear()
    r._send = fake_send
    sources.goals = lambda: goals
    pv = llm.panel_votes
    llm.panel_votes = lambda v, q, c="", tier="scout": {x["id"]: {"votes": {w: ("yes", "r") for w in llm.PANEL}, "yes": 2 if x["title"] == "Goal topic" else 3, "accepted": True} for x in v}
    os.environ["EDITOR_MODE"] = "off"
    r.post_topics([("claude", {"title": "Panel favourite", "sources": ["https://x.y/a"], "replicability": 9, "audience": 9}),
                   ("claude", {"title": "Goal topic", "sources": ["https://x.y/b"], "replicability": 9, "audience": 9, "goal": "aws"})],
                  [{"url": "https://x.y/a"}, {"url": "https://x.y/b"}], [], set(), "radar", lambda t, s: "top", 3)
    llm.panel_votes = pv; sources.goals = sources_goals
    os.environ["EDITOR_MODE"] = "shadow"
    r._send = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("tests must not reach GitHub"))
    return [x[2]["title"] for x in sent if x[1].endswith("/issues")]
check("default: with no goals the panel's order is unchanged", pick([]) == ["Panel favourite", "Goal topic"])
focus_goal = {"id": "aws", "weight": 8, "focus": ["goal topic"]}
check("change: an active goal outranks the panel when the title fits", pick([focus_goal]) == ["Goal topic", "Panel favourite"])
check("adversarial: a proposer claiming a goal earns nothing by itself", pick([{"id": "aws", "weight": 8, "focus": ["kubernetes"]}]) == ["Panel favourite", "Goal topic"])
os.environ["GOAL_RANKING"] = "off"
check("rollback: GOAL_RANKING=off restores the panel's order", pick([focus_goal]) == ["Panel favourite", "Goal topic"])
os.environ.pop("GOAL_RANKING")
pv, la = llm.panel_votes, llm.ask
llm.panel_votes = lambda cands, *a, **k: {c["id"]: {"yes": 1, "votes": {}} for c in cands}
llm.ask = lambda who, s, p, sch, tool="submit", **k: {"linkedin": f"{who} post"} if tool == "submit_linkedin" else {"ok": True, "problems": []}
os.environ["LINKEDIN_MIN_VOTES"] = "0"
pp, lg2 = draft.linkedin_round({"title": "t", "body": "b", "linkedin": "writer text"}, "build")
check("rollback: LINKEDIN_MIN_VOTES=0 restores a low-vote post", pp["linkedin"] != "" and lg2.get("by"))
del os.environ["LINKEDIN_MIN_VOTES"]
llm.panel_votes, llm.ask = pv, la
check("every rollback variable reaches its workflow", all(n in (ROOT / f".github/workflows/{w}.yml").read_text() for w, n in
      (("radar", "GOAL_RANKING"), ("problems", "GOAL_RANKING"), ("radar", "MODEL_AUTO"), ("replicate", "MODEL_AUTO"), ("replicate", "LINKEDIN_MIN_VOTES"),
       ("redraft", "LINKEDIN_MIN_VOTES"), ("monthly-oss", "LINKEDIN_MIN_VOTES"), ("analytics", "MODEL_AUTO"), ("hero", "MODEL_AUTO"))))

# ---------- PR 89 review: synthetic checks are not human calibration ----------
hc = evaluate.human_calibration([{"rating": None, "agrees": None}])
check("eval: with no ratings, nothing is calibrated", hc["rated"] == 0 and hc["calibrated"] is False)
check("eval: labels declare where they came from", all(c.get("labelled_by") for c in cases.values()))
check("eval: no case claims a human label yet", not any("human" in c["labelled_by"] for c in cases.values()))
check("eval: the basis is reported", "synthetic" in evaluate.evidence_basis(list(cases.values())))

# ---------- batch review regressions: evidence ----------
one = lambda v, **k: scan.verify_contract([{"id": "x", "accept": {"op": "present"}, "min_entities": 1, **k}], {"f.log": v})["results"][0]
for bad in ("<unknown>", "undefined", "failed", "null ms", "= ms"):
    check(f"contract check: {bad} is not a measurement", one(f"HERO_MEASURE: x={bad}")["status"] != "ok")
check("contract check: an empty value counts as lost", scan.verify_contract([{"id": "x", "min_entities": 2}], {"f.log": "HERO_MEASURE: x[a]=1\nHERO_MEASURE: x[b]=\nHERO_MEASURE: x[c]=2"})["results"][0]["status"] == "bad_value")
for typed in ('echo "HERO_MEASURE: sidecar_cpu=35%"', "echo 'HERO_MEASURE: sidecar_cpu=\"35\"'", 'printf "HERO_MEASURE: sidecar_cpu=35\n"'):
    check(f"adversarial: typed value caught in {typed[:34]}", res1(bare, {"fix.sh": typed})["status"] == "hardcoded")
check("provenance: a printf format is not a typed value", res1(raw, {"fix.sh": 'printf "HERO_MEASURE: sidecar_cpu=%s\n" "$V"'})["status"] == "ok")
xt = {"05-fix.log": "+ echo 'HERO_MEASURE: sidecar_cpu=35'\nHERO_MEASURE: sidecar_cpu=35"}
check("adversarial: an xtrace echo corroborates nothing", res1(xt)["provenance"] == "self_reported")
check("adversarial: every word of the id must be present", res1({"05-fix.log": "sidecar memory 35\nHERO_MEASURE: sidecar_cpu=35"})["provenance"] == "self_reported")
ct3 = [{"id": "failed_tasks", "what": "failed tasks", "unit": "", "accept": {"op": "present"}, "min_entities": 3}]
lg3 = {"05-fix.log": "".join(f"az{i} failed tasks 0\nHERO_MEASURE: failed_tasks[az{i}]=0\n" for i in "abc")}
check("title: a row count is not a measured value", not scan.check(lg3, "3 Failed Tasks", ct3)["headline"][0]["validated"])
check("contract check: unlabelled conflicting values are refused", one("HERO_MEASURE: x=1\nHERO_MEASURE: x=99")["status"] == "conflicting")
check("contract check: entity case cannot inflate a count", scan.verify_contract([{"id": "x", "min_entities": 2}], {"f.log": "HERO_MEASURE: x[task-a]=1\nHERO_MEASURE: x[TASK-A]=1"})["results"][0]["status"] == "too_few_entities")
sup2 = lambda t, l: [h["supported"] for h in scan.headline_claims(t, {"a.log": l})]
check("title: sixty is not six", sup2("Sixty Pods", "6 pods") == [False])
check("title: eleven is parsed", sup2("Eleven Pods", "1 pods") == [False])
check("title: US$40 is parsed", sup2("Save US$40 a Month", "saved 30") == [False])
check("title: 5GB is not 5 MB", sup2("5GB Memory", "memory 5 MB") == [False] and sup2("5GB Memory", "memory 5 GB") == [True])
check("title: Tenant is not ten", sup2("Tenant Isolation", "x") == [])
ctp = [{"id": "reported", "what": "reported", "unit": "", "accept": {"op": "present"}, "min_entities": 1}]
check("title: Ports is not inside reported", not scan.check({"f.log": "reported 35 ports\nHERO_MEASURE: reported=35"}, "35 Ports", ctp)["headline"][0]["validated"])
start = time.time(); scan.verify_contract(cm, bare, {"fix.sh": "\n" * 40000})
check("scan: a huge script stays fast", time.time() - start < 2)
for broken in ({"id": "x", "accept": {"op": "bogus"}}, {"id": "x", "accept": {"op": "lt", "value": "abc"}}, {"accept": {}}, 7, {"id": "x", "accept": {"op": "ge"}}):
    check(f"contract check: malformed entry fails, never crashes: {str(broken)[:30]}", scan.verify_contract([broken], {"f.log": ""})["results"][0]["status"] == "bad_contract")
ops = lambda op, value, v: scan.verify_contract([{"id": "x", "accept": {"op": op, "value": value}}], {"f.log": f"HERO_MEASURE: x={v}"})["results"][0]["status"]
check("contract check: ge, gt, le and eq are enforced", ops("ge", 5, 4) == ops("gt", 5, 5) == ops("le", 5, 6) == ops("eq", 5, 6) == "failed_criterion")
check("contract check: ge, gt, le and eq pass correctly", ops("ge", 5, 5) == ops("gt", 5, 6) == ops("le", 5, 5) == ops("eq", 5, 5) == "ok")
check("flags: missing values and timeouts are flagged", {"missing_value", "timed_out"} <= {f["kind"] for f in scan.scan({"05-fix.log": "cpu=null\n[timed out after 30s]"})})
wide = "0 datapoints\n" * 40 + "null\n"
check("flags: the cap never hides a high flag", any(f["severity"] == "high" for f in scan.check({"05-fix.log": wide}, "")["flags"]))
misses = {**evaluate.run(list(cases.values())), "misses": ["x"]}
check("eval: any miss fails the targets", not evaluate.meets_targets(misses))

# ---------- batch review regressions: Tech Head evasion ----------
D3 = dif("pipeline/a.py", "+\x0ceval(x)\n+y = 1\n").replace("+1 @@", "+1,2 @@")
check("Tech Head: a form feed cannot hide code", any(f["rule"] == "dynamic-exec" for f in th.rules(D3)))
check("Tech Head: a form feed cannot shift line numbers", [(n, t) for _, n, t in th.added_lines(D3)][1] == (2, "y = 1"))
check("Tech Head: a carriage return cannot hide code", any(f["rule"] == "dynamic-exec" for f in th.rules(dif("pipeline/a.py", "+x=1\reval(y)\n"))))
check("Tech Head: a carriage return cannot hide code behind a comment", any(f["rule"] == "dynamic-exec" for f in th.rules(dif("pipeline/a.py", "+# note\reval(y)\n"))))
for call in ("os.system(x)", "pickle.load(f)", "yaml.unsafe_load(x)", "builtins.eval(x)"):
    check(f"Tech Head: {call} is caught", any(f["rule"] == "dynamic-exec" for f in th.rules(dif("pipeline/a.py", f"+{call}\n"))))
check("Tech Head: a quoted path still gets rules", th.rules('+++ "b/.github/workflows/e\\303\\251.yml"\n@@ -0,0 +1 @@\n+  pull_request_target:\n') != [])
check("Tech Head: a path with a space still gets rules", th.rules("+++ b/pipeline/a b.py\t\n@@ -0,0 +1 @@\n+subprocess.run(c, shell=True)\n") != [])
check("Tech Head: a site folder inside pipeline is not skipped", "evil" in th.prioritise(dif("pipeline/a/site/evil.py"))[0])
check("Tech Head: a real site file is skipped", th.prioritise(dif("site/x.md"))[0] == "")
check("Tech Head: this file gets the same rules", th.rules(dif("pipeline/tech_head.py", "+exec(x)\n")) != [])
check("Tech Head: its own source raises no alarms", not [f for f in th.rules(dif("pipeline/tech_head.py", "".join("+" + l + "\n" for l in (ROOT / "pipeline/tech_head.py").read_text().split("\n")))) if f["rule"] in ("hard-secret", "tls-off", "dynamic-exec")])
for key in ("github_pat_" + "a" * 30, "AIza" + "b" * 34):
    check(f"Tech Head: {key[:8]} keys are caught", any(f["rule"] == "hard-secret" for f in th.rules(dif("pipeline/a.py", f'+k = "{key}"\n'))))
wfd = lambda line: th.rules(dif(".github/workflows/a.yml", f"+{line}\n"))
check("Tech Head: an env word in a trailing comment does not excuse injection", wfd("    run: echo ${{ github.event.issue.title }} # env: x") != [])
check("Tech Head: a comment inside a run block still expands", wfd("          # ${{ github.event.issue.title }}") != [])
check("Tech Head: format() cannot hide event data", wfd("    run: echo ${{ format('{0}', github.event.issue.title) }}") != [])
check("Tech Head: composite actions are checked", th.rules(dif(".github/actions/a/action.yml", "+    run: echo ${{ github.event.issue.title }}\n")) != [])
check("Tech Head: spaced eval and shell=1 are caught", th.rules(dif("pipeline/a.py", "+eval (x)\n+run(c, shell=1)\n").replace("+1 @@", "+1,2 @@")) != [] and len(th.rules(dif("pipeline/a.py", "+eval (x)\n+run(c, shell=1)\n").replace("+1 @@", "+1,2 @@"))) == 2)
fake_rule = {"findings": [{"severity": "blocker", "category": "security", "file": "pipeline/a.py", "line": 1, "problem": "p", "fix": "f", "rule": "x"}]}
check("Tech Head: a model cannot pose as a code rule", not th.verdict([], fake_rule, dif("pipeline/a.py"))[2])
reps2 = {"zizmor": {"status": "findings", "items": [{"ident": "x", "desc": "d", "locations": [{"p": ".github/workflows/a.yml"}]}, {"ident": "other", "locations": [{"p": ".github/workflows/old.yml"}]}]}}
kept = th.relevant(reps2, {".github/workflows/a.yml"})["zizmor"]["items"]
check("Tech Head: scanners report only on changed files", len(kept) == 1 and kept[0]["id"] == "x")
yml = __import__("yaml").safe_load((ROOT / ".github/workflows/tech-head.yml").read_text())
trig = yml[True]["pull_request_target"]
check("workflow: a retargeted PR is reviewed again", "edited" in trig["types"] and trig["branches"] == ["main"])
steps = {j: v["steps"] for j, v in yml["jobs"].items()}
check("workflow: nothing runs from the PR checkout", not [s for j in steps.values() for s in j if "run" in s and re.search(r"(bash|sh|python3?|\./)\s+pr/", s["run"])])
check("workflow: no step points Python at PR code", not [s for j in steps.values() for s in j if any("pr/" in str(v) for k, v in (s.get("env") or {}).items() if k != "PR_DIR")])
check("workflow: the scan job has no secrets or token", "secrets" not in json.dumps(yml["jobs"]["scan"]) and "github.token" not in json.dumps(yml["jobs"]["scan"]))
check("workflow: every PR checkout holds no credentials", all(s["with"]["persist-credentials"] is False for j in steps.values() for s in j if "checkout" in str(s.get("uses"))))
check("workflow: the review job uses the tech-head environment", yml["jobs"]["review"]["environment"] == "tech-head")
check("workflow: the scan job has no environment", "environment" not in yml["jobs"]["scan"])
check("workflow: forks are skipped", "head.repo.full_name == github.repository" in yml["jobs"]["scan"]["if"])
for name in ("live-check", "editor-eval"):
    y = __import__("yaml").safe_load((ROOT / f".github/workflows/{name}.yml").read_text())
    job = next(iter(y["jobs"].values()))
    check(f"policy: {name} guard is not weakened", job["if"].startswith("github.ref == 'refs/heads/main' &&") and "||" not in job["if"])
    check(f"policy: {name} checks out literally main", job["steps"][0]["with"]["ref"] == "main")

def run_main(rule_hit, ai_ok=True, mode="blocking"):
    """tech_head.main end to end, with GitHub and the model replaced."""
    import radar
    posted, sent_to_model = [], []
    diff = dif("pipeline/a.py", '+k = "' + FAKE + '"\n' if rule_hit == "secret" else "+x = 1\n")
    saved = th.git_diff, th.review, radar.comment
    th.git_diff = lambda *a: diff
    def fake_review(d, reports, agents):
        sent_to_model.append(d)
        if not ai_ok:
            raise RuntimeError("outage")
        return {"summary": "s", "findings": []}
    th.review, radar.comment = fake_review, lambda n, t: posted.append(t)
    d = tempfile.mkdtemp()
    for n, body in (("bandit", '{"results": [], "errors": []}'), ("zizmor", "[]")):
        pathlib.Path(d, f"{n}.json").write_text(body); pathlib.Path(d, f"{n}.exit").write_text("0")
    os.environ.update(PR_DIR=".", BASE_SHA="a", HEAD_SHA="b", PR_NUMBER="1", REPORTS_DIR=d, TECH_HEAD_MODE=mode)
    failed = False
    try:
        th.main()
    except SystemExit:
        failed = True
    th.git_diff, th.review, radar.comment = saved
    os.environ.pop("TECH_HEAD_MODE")
    return failed, posted, sent_to_model
f1, p1, s1 = run_main("secret")
check("Tech Head main: a key is never sent to a model", s1 == [])
check("Tech Head main: a key blocks in blocking mode", f1)
check("Tech Head main: a clean change passes", run_main("none")[0] is False)
check("Tech Head main: an AI outage fails blocking mode", run_main("none", ai_ok=False)[0] is True)
check("Tech Head main: advisory mode never fails the check", run_main("secret", mode="advisory")[0] is False)

# ---------- batch review regressions: contracts and roles ----------
check("brief: a marker that breaks the contract is ignored", editor.parse_brief(editor.marker({"measurements": [1]})) is None and editor.parse_brief(editor.marker([1])) is None)
check("gate 1: an Editor no fails", not editor.gate1({**b, "verdict": "no"})[0])
check("gate 1: a missing field fails", not editor.gate1({**b, "reader": " "})[0])
check("gate 1: no contract fails", not editor.gate1({**b, "measurements": []})[0])
badm = {**b["measurements"][0], "id": "Bad Id"}
check("gate 1: a bad id fails", not editor.gate1({**b, "measurements": [badm]})[0])
check("gate 1: an id cannot end in a newline", not editor.gate1({**b, "measurements": [{**b["measurements"][0], "id": "abc\n"}]})[0])
check("gate 1: a criterion needs a value", not editor.gate1({**b, "measurements": [{**b["measurements"][0], "accept": {"op": "lt"}}]})[0])
check("gate 1: NaN is not a value", not editor.gate1({**b, "measurements": [{**b["measurements"][0], "accept": {"op": "lt", "value": float("nan")}}]})[0])
cd = {"scores": {"technical": {"score": 3, "reasons": []}, "editorial": {"score": 5, "reasons": []}, "marketing": {"score": 5, "reasons": []}}, "blocking": [], "flags_addressed": [], "claims": []}
check("decide: technical below 4 revises", editor.decide(cd, {"passed": True, "flags": []})[0] == "revise")
cd["scores"]["technical"]["score"] = 5
cd["blocking"] = [{"dimension": "editorial", "problem": "p", "evidence": "e", "fix": "f"}]
check("decide: a blocking point revises", editor.decide(cd, {"passed": True, "flags": []})[0] == "revise")
s2 = {"type": "array", "minItems": 2, "items": {"type": "integer", "minimum": 1}}
check("contract: items, minItems and minimum are enforced", contract.validate([1], s2) != [] and contract.validate([1, 2], s2) == [] and len(contract.validate([0, 1], s2)) == 1)
spends = []
real_spend, roles.spend = roles.spend, lambda role, text: spends.append(text)
real_lask, llm.ask = llm.ask, lambda *a, **k: {"summary": "ok", "findings": []}
roles.ask("tech_head", "PROMPT", "techreview")
check("roles: every call is charged, including its system prompt", len(spends) == 1 and "PROMPT" in spends[0] and "Tech Head" in spends[0])
roles.spend = real_spend
def retrying(*a, **k):
    llm.LEDGER.extend([{"who": "x", "model": "m", "in": 0, "out": 0}] * 3)
    return {"summary": "ok", "findings": []}
llm.ask = retrying
roles._calls.clear()
roles.ask("tech_head", "p", "techreview")
check("roles: provider-level retries count against the budget", roles._calls["tech_head"] == 3)
del llm.LEDGER[-3:]; roles._calls.clear(); llm.ask = real_lask
check("roles: registry needs an input budget", (lambda reg_: (setattr(roles, "registry", lambda: {"x": {**reg_["editor"], "budget": {"max_calls": 1}}}), any("budgets" in p for p in roles.check_registry()), setattr(roles, "registry", old))[1])(reg))
real_read = sources._analytics_json
sources._analytics_json = lambda p: {"goals": [{"id": "a", "weight": "high"}, {"id": "b", "weight": 2, "until": 20261231}, "x",
                                               {"id": "c", "weight": 3, "status": "dropped"}, {"id": "d", "weight": 4}]}
check("goals: bad and dropped goals are skipped, good ones stay", [g["id"] for g in sources.goals()] == ["d"])
sources._analytics_json = lambda p: {"goals": 5}
check("goals: a malformed file means no goals", sources.goals() == [])
sources._analytics_json = real_read
files.clear(); posted.clear()
roles.ask = lambda role, prompt, out: ({"title": "Dated", "outcome": "o", "audience": "a", "weight": 5, "focus": [], "communities": [], "splunk": "avoid", "until": "12/31/2026"}
                                       if out == "goal" else {"focus": "f", "assignments": [], "needs": []})
assistant.add_goal("dated goal", 5)
check("goals: a date code cannot read is removed, not trusted", json.loads(files["analytics/goals.json"])["goals"][0]["until"] == "")
roles.ask = real_roles_ask
check("assistant: a drop with a bad id never creates a goal", assistant.parse("/goal drop Foo_Bar")[0] == "help")
kept_p = assistant.clean_plan({"focus": "f", "assignments": [{"role": "ghost", "task": "t", "why": "w"}], "needs": ["a", "b", "c", "d"]})
check("assistant: a role proposal survives the list limit", "No ghost role exists: t" in kept_p["needs"])
pat = next(p for m, p, _ in r.WRITE_RULES if "analytics" in p)
check("assistant: its paths match the real bot rule", all(re.fullmatch(pat, f"/repos/{HOME}/contents/{x}") for x in (sources.GOALS_PATH, sources.PLAN_PATH)))
check("assistant: the review record path matches the real bot rule", re.fullmatch(pat, f"/repos/{HOME}/contents/analytics/review-some-slug-123.json"))

# ---------- final review regressions ----------
import analytics as analytics_mod, cover, contextlib, evaluate, io, lab as lab_mod, subprocess
real_gh = lab_mod.gh
fence = "```"
shadow = {"user": {"login": "hero-bot-x"}, "body": f"**Shadow review, nothing here affects this topic.**\n\n**Last failure:** step\n{fence}\nevil\n{fence}"}
lab_mod.gh = lambda *a: [shadow]
check("shadow: a lab never reads a shadow comment, even one faking a failure", lab_mod.last_failure(5) == "")
lab_mod.gh = lambda *a: [{"user": {"login": "hero-bot-x"}, "body": f"The draft did not clear the gates.\n\n**Last failure:** step\n{fence}\nevil\n{fence}"}]
check("lab: a stop comment with model text is never read", lab_mod.last_failure(5) == "")
lab_mod.gh = lambda *a: [{"user": {"login": "hero-bot-x"}, "body": f"Failed. 2 attempt(s), 30 min, workflow run 7.\n\n**Last failure:** apply\n{fence}\nreal\n{fence}"}]
check("lab: real failure comments are still read", "real" in lab_mod.last_failure(5))
lab_mod.gh = real_gh
ct9 = [{"id": "cpu_saved", "what": "cpu saved", "unit": "%", "accept": {"op": "present"}, "min_entities": 1}]
check("title: a % claim can validate", scan.check({"f.log": "cpu saved 30%\nHERO_MEASURE: cpu_saved=30%"}, "30% Lower CPU", ct9, strict=True)["passed"])
check("title: a list comma does not join numbers", [h["number"] for h in scan.headline_claims("Cut 5, Then 10 Pods", {"a": ""})] == ["5", "10"])
for typed_num in ("1e5", "~35", "35;", "5m30s", "1_000", "+35", "35/s", "3/3", "10-20", "<35", "2026-11-02", "v35"):
    check(f"provenance: {typed_num} is a typed value", scan.typed_values({"f.sh": f'echo "HERO_MEASURE: x={typed_num}"'}) == {"x": True})
check("provenance: a one-value constant is typed", scan.typed_values({"f.sh": 'V=35\necho "HERO_MEASURE: x=$V"'}) == {"x": True})
for still_typed in ('x=35\necho "HERO_MEASURE: x=$x"', 'V=35\nD=$((V*2))\necho "HERO_MEASURE: x=$V"', 'V=35\nif (( V > 30 )); then echo hi; fi\necho "HERO_MEASURE: x=$V"',
                    'V=35\n# wait for V\necho "HERO_MEASURE: x=$V"', 'V=35\n# V=34 was too high\necho "HERO_MEASURE: x=$V"'):
    check("provenance: reading a constant keeps it typed", scan.typed_values({"f.sh": still_typed}) == {"x": True})
for counter in ('count=0\nfor p in a; do count=$((count+1)); done\necho "HERO_MEASURE: x=$count"',
                'count=0\n((count++))\necho "HERO_MEASURE: x=$count"', 'total=0\ntotal=$(wc -l < f)\necho "HERO_MEASURE: x=$total"',
                'c=0\n((++c))\necho "HERO_MEASURE: x=$c"', 'c=0\n((a++, c++))\necho "HERO_MEASURE: x=$c"', 'c=0\nread -r c < f\necho "HERO_MEASURE: x=$c"',
                'c=0\nIFS=, read -r a c < f\necho "HERO_MEASURE: x=$c"', 'c=0\nprintf -v c "%d" 3\necho "HERO_MEASURE: x=$c"', 'c=0\nlet "c++"\necho "HERO_MEASURE: x=$c"',
                'c=0\nfor c in 1 2; do :; done\necho "HERO_MEASURE: x=$c"', 'n=0\nif a; then :; else n=$(wc -l < f); fi\necho "HERO_MEASURE: x=$n"',
                'n=0\na || { n=$(wc -l < f); }\necho "HERO_MEASURE: x=$n"', 'n=0\ncase $1 in a) n=$(wc -l < f);; esac\necho "HERO_MEASURE: x=$n"',
                'n=0\ndeclare n=$(wc -l < f)\necho "HERO_MEASURE: x=$n"', 'n=0\n[ -f f ] && n=$(wc -l < f); echo "HERO_MEASURE: x=$n"',
                'n=0\nif a; then n=$(wc -l < f); fi; echo "HERO_MEASURE: x=$n"', 'n=0\necho "HERO_MEASURE: x=$n"; n=$(wc -l <f)', 'n=0\neval "n=\\$(wc -l < f)"\necho "HERO_MEASURE: x=$n"',
                """echo 'HERO_MEASURE: x=1'$(wc -l < f)""", 'c=0\nif a; then c=4; fi\necho "HERO_MEASURE: x=$c"'):
    check("provenance: a computed counter is not typed", scan.typed_values({"f.sh": counter}) == {})
for s_ok in ('print("HERO_MEASURE: x=0.%03d" % ms)', 'printf "HERO_MEASURE: x=1%d ms\n"', 'printf "HERO_MEASURE: x={m1}\n"', 'jq -r "HERO_MEASURE: x=\\(.v1)"', 'echo "HERO_MEASURE: x=null"', 'printf "HERO_MEASURE: x=%.2f\n"',
             "grep -Eq 'HERO_MEASURE: x=[0-9]+' out.log", 'echo "HERO_MEASURE: x=<n2>"', 'echo "HERO_MEASURE: x=N1"', 'echo "HERO_MEASURE: x=%VAR1%"', 'echo "HERO_MEASURE: x=%(v1)s"'):
    check(f"provenance: {s_ok[:30]} is not a typed value", scan.typed_values({"f.sh": s_ok}) == {})
check("provenance: a bad contract cannot crash title checks", scan.check({"f": ""}, "3 Tasks", [{"id": ["x"]}, 7])["passed"] is False)
many = {"zizmor": {"status": "findings", "items": [{"ident": "x", "desc": "d", "p": f"f{i}.yml"} for i in range(61)]}}
rp2 = pathlib.Path(tempfile.mkdtemp())
(rp2 / "zizmor.json").write_text(json.dumps(many["zizmor"]["items"])); (rp2 / "zizmor.exit").write_text("14")
fit_b, fit_cut = th.fit_scanners({"bandit": {"status": "f", "items": [{"text": "x" * 100}] * 900}, "zizmor": {"status": "f", "items": [{"ident": "z"}]}})
check("Tech Head: each scanner keeps its share, and a cut is counted", fit_b["zizmor"]["items"] == [{"ident": "z"}] and fit_cut > 0 and any("not sent" in g for g in th.complete({}, {"scanner_items_cut": fit_cut})))
import roles as roles_mod
real_ask, roles_mod.ask = roles_mod.ask, lambda *a, **k: {"summary": "s", "findings": []}
big_scan = {"bandit": {"status": "findings", "items": [{"filename": "pr/pipeline/a.py", "issue_text": "x" * 150, "test_id": "B1", "line_number": 1}] * 300}}
cut_review = th.review(dif("pipeline/a.py"), big_scan, "")
roles_mod.ask = real_ask
check("Tech Head: a review that cut scanner items says so", cut_review["scanner_items_cut"] > 0 and th.complete({}, cut_review))
seen_prompts = []
real_ask, roles_mod.ask = roles_mod.ask, lambda who, prompt, *a, **k: seen_prompts.append(prompt) or {"summary": "s", "findings": []}
two = dif("pipeline/a.py", "+x\n" * (th.MAX_DIFF // 4)) + dif("pipeline/b.py", "+y\n" * (th.MAX_DIFF // 4))
th.review(two, {"bandit": {"status": "findings", "items": [{"filename": "pr/pipeline/b.py", "issue_text": "marker", "test_id": "B1", "line_number": 1}]}}, "")
roles_mod.ask = real_ask
check("Tech Head: a later batch sees its own scanner findings", len(seen_prompts) == 2 and "marker" in seen_prompts[1] and "marker" not in seen_prompts[0])
check("Tech Head: no scanner item is dropped before filtering", len(th.read_reports(rp2)["zizmor"]["items"]) == 61)
def zz(path, ident="template-injection", sev="High", conf="High"):
    loc = lambda kind, row: {"symbolic": {"kind": kind, "key": {"Local": {"verbatim_path": path}}}, "concrete": {"location": {"start_point": {"row": row}}}}
    return {"ident": ident, "desc": "d", "determinations": {"severity": sev, "confidence": conf}, "locations": [loc("Hidden", 1), loc("Primary", 4)]}
a_wf = "pr/.github/workflows/a.yml"
tmpl = {"zizmor": {"items": [zz(a_wf), zz(a_wf, ident="other"), zz("pr/.github/workflows/old.yml"), zz(a_wf, sev="Informational", conf="Low"),
                             zz(a_wf, conf="Low"), zz(a_wf, sev="Medium")]}}
tmpl["zizmor"]["items"][2]["locations"][1]["concrete"]["feature"] = "run: cat .github/workflows/a.yml"
sb = th.scanner_blockers(tmpl, {".github/workflows/a.yml"})
check("Tech Head: scanner line is reported", sb[0]["line"] == 5)
check("Tech Head: scanner injection on a changed file blocks like a code rule", len(sb) == 1 and th.verdict(sb, None, dif(".github/workflows/a.yml"))[2])
import radar as radar_mod
saved = (th.git_diff, th.review, radar_mod.comment)
posted = []
th.git_diff = lambda *a: dif(".github/workflows/a.yml")
th.review = lambda *a: {"summary": "s", "findings": [], "reviewer_blockers": [], "batches": 1, "failed_batches": 0}
radar_mod.comment = lambda n, text: posted.append(text)
rp3 = pathlib.Path(tempfile.mkdtemp())
(rp3 / "zizmor.json").write_text(json.dumps(tmpl["zizmor"]["items"])); (rp3 / "zizmor.exit").write_text("14")
(rp3 / "bandit.json").write_text('{"results": []}'); (rp3 / "bandit.exit").write_text("0")
os.environ.update(PR_DIR="x", BASE_SHA="a", HEAD_SHA="b", REPORTS_DIR=str(rp3), PR_NUMBER="1", TECH_HEAD_MODE="blocking")
try:
    th.main(); stopped = False
except SystemExit:
    stopped = True
check("Tech Head: the run itself stops on a scanner injection finding", stopped)
real_safe, radar_mod.safe_text = radar_mod.safe_text, lambda t, f="": f
th.review = lambda *a: {"summary": "::error title=x::all clear\nline two", "findings": [], "batches": 1, "failed_batches": 0}
outbuf = io.StringIO()
os.environ["TECH_HEAD_MODE"] = "advisory"
with contextlib.redirect_stdout(outbuf):
    th.main()
radar_mod.safe_text = real_safe
check("Tech Head: model text cannot become a runner command", not any(l.startswith("::") for l in outbuf.getvalue().splitlines()))
th.review = lambda *a: {"summary": "see ##[error]x", "findings": [], "batches": 1, "failed_batches": 0}
outbuf = io.StringIO()
with contextlib.redirect_stdout(outbuf):
    th.main()
check("Tech Head: the older command form is broken up too", "##[" not in outbuf.getvalue())
check("Tech Head: the fallback still counts findings", "finding(s)" in posted[1] and "marked blocker" in posted[1])
th.git_diff, th.review, radar_mod.comment = saved
for k in ("PR_DIR", "BASE_SHA", "HEAD_SHA", "REPORTS_DIR", "PR_NUMBER", "TECH_HEAD_MODE"): os.environ.pop(k, None)
check("Tech Head: a run line with event_name is not event data", wfd("      - run: echo ${{ github.event_name }}") == [])
check("Tech Head: event_name is not event data", wfd("      if: ${{ github.event_name == 'x' }} && run: echo ${{ github.event_name }}") == [])
check("Tech Head: __builtins__ and os.popen are caught", len(th.rules(dif("pipeline/a.py", "+__builtins__.eval(x)\n"))) and len(th.rules(dif("pipeline/a.py", "+os.popen(x)\n"))))
check("Tech Head: a diff of only saved data is an empty, finished review", th.review(dif("site/x.md"), {}, "")["batches"] == 0)
check("Tech Head: a data-only review says no model read it", "no model read this" in th.markdown([], th.review(dif("site/x.md"), {}, ""), 0, False))
huge_f = [{"severity": "minor", "category": "cost", "file": "a", "line": i, "problem": "p", "fix": "f"} for i in range(25)]
check("Tech Head: truncation is stated", "Showing 20 of 25" in th.markdown(huge_f, {"summary": "s"}, 0, False))
check("Tech Head: a deleted file keeps its path", th._path("diff --git a/x b/x\n--- a/x.py\n+++ /dev/null\n") == "x.py")
grepo = pathlib.Path(tempfile.mkdtemp())
git = lambda *a: subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *a], cwd=grepo, check=True, capture_output=True, text=True).stdout.strip()
git("init", "-q"); (grepo / "site").mkdir(); (grepo / "site/x.yml").write_text("on: pull_request_target\n"); (grepo / "b.py").write_bytes(b"x=1\n")
git("add", "-A"); git("commit", "-qm", "a"); base_sha = git("rev-parse", "HEAD")
(grepo / ".github/workflows").mkdir(parents=True); git("mv", "site/x.yml", ".github/workflows/x.yml")
(grepo / "b.py").write_bytes(b"x=1\nk=b'\xff'\neval(y)\n"); (grepo / "pipeline").mkdir(); (grepo / "pipeline/\u00e9.py").write_text("eval(z)\n")
git("add", "-A"); git("commit", "-qm", "b")
real_diff = th.git_diff(str(grepo), base_sha, "HEAD")
ids = {(f["rule"], f["file"]) for f in th.rules(real_diff)}
check("Tech Head real git: a rename shows every line", ("pr-target", ".github/workflows/x.yml") in ids)
check("Tech Head real git: a bad byte does not stop the review", ("dynamic-exec", "b.py") in ids)
check("Tech Head real git: a non-ASCII path keeps its name", ("dynamic-exec", "pipeline/\u00e9.py") in ids)
pos_dir = pathlib.Path(tempfile.mkdtemp()); (pos_dir / "site/_posts").mkdir(parents=True)
real_root, sources.ROOT = sources.ROOT, pos_dir
(pos_dir / "site/_posts/2026-01-01-a.md").write_bytes(b"splunk \xff")
check("sources: a non-UTF-8 post cannot stop a run", sources.post_mix()["featuring_splunk"] == 1)
sources.ROOT = real_root
scan_step = next(s for s in yml["jobs"]["scan"]["steps"] if s.get("name") == "scanners")["run"]
check("workflow: zizmor cannot be silenced by a pull request", all(x in scan_step for x in ("--no-ignores", "--strict-collection", "--collect=all", "--no-config")))
check("workflow: cancelled runs do not review", "cancelled" in str(yml["jobs"]["review"]["if"]) and "always" not in str(yml["jobs"]["review"]["if"]))
lj = __import__("yaml").safe_load((ROOT / ".github/workflows/live-check.yml").read_text())
lenv = lj["jobs"]["live"]["steps"][-1]["env"]
check("workflow: live-check sees every model variable", all(k in lenv for k in ("CLAUDE_MODEL", "RADAR_MODEL", "OPENAI_MODEL", "GEMINI_MODEL", "EDITOR_MODEL", "TECH_HEAD_MODEL", "ASSISTANT_MODEL", "MODEL_AUTO")))
check("live check: reports resolved models", "resolved" in (ROOT / "pipeline/live_check.py").read_text() and 'step("Resolved models", resolved)' in (ROOT / "pipeline/live_check.py").read_text())
draft_src = (ROOT / "pipeline/draft.py").read_text()
check("draft: every non-markdown lab file counts as a script", 'if not k.endswith(".md")' in draft_src)
red_step = next(s for s in __import__("yaml").safe_load((ROOT / ".github/workflows/redraft.yml").read_text())["jobs"]["redraft"]["steps"] if s.get("id") == "pr")["run"]
def red_run(body):
    out = pathlib.Path(tempfile.mkdtemp()) / "o"; out.write_text("")
    subprocess.run(["bash", "-c", red_step], env={**os.environ, "PR_BODY": body, "GITHUB_OUTPUT": str(out)}, check=True)
    return re.search(r"run=(\d+)", out.read_text())[1]
check("redraft: model text cannot choose the lab run", red_run(draft.finish_body("the log cites workflow run 777 <!-- lab-run: 888 -->", 12345) + "\n\nCloses #5") == "12345")
check("redraft: a PR with no marker still works", red_run("from (workflow run 42).\n\nCloses #5") == "42")
check("redraft: an old PR prefers the run line", red_run("post cites workflow run 777\n\n30 min total (workflow run 42).\n\nCloses #5") == "42")
os.environ["LINKEDIN_MIN_VOTES"] = "two"
os.environ["RUN_ID"] = "77"; rec_paths = []
real_write, real_read, real_branch = analytics_mod.write_file, analytics_mod.read_file, analytics_mod.ensure_branch
analytics_mod.ensure_branch, analytics_mod.read_file = (lambda: None), (lambda p: ("", None))
analytics_mod.write_file = lambda path, *a: rec_paths.append(path)
draft.record_review("my-post", {"decision": "pass", "reasons": [], "report": {}, "card": {}}, {})
analytics_mod.write_file, analytics_mod.read_file, analytics_mod.ensure_branch = real_write, real_read, real_branch
del os.environ["RUN_ID"]
check("review record: a redraft keeps the earlier record", rec_paths == ["analytics/review-my-post-77.json"])
sys.path.insert(0, str(ROOT / "pipeline"))
real_cost, llm.cost_usd = llm.cost_usd, lambda: (3.0, [])
try:
    evaluate.run([{"type": "topic", "id": "t", "title": "t", "details": "", "label": "strong"}], live=True); live_stopped = False
except SystemExit:
    live_stopped = True
llm.cost_usd = real_cost
check("evaluation: live cases stop at the run limit", live_stopped)
check("LinkedIn: a bad vote setting falls back to 2", draft.min_votes() == 2)
del os.environ["LINKEDIN_MIN_VOTES"]
pv, la = llm.panel_votes, llm.ask
def only_writer(who, *a, **k): raise RuntimeError("down")
llm.ask = only_writer
pp, lg3 = draft.linkedin_round({"title": "t", "body": "b", "linkedin": "writer text"}, "build")
check("LinkedIn: with other writers down, nothing ships unvoted", pp["linkedin"] == "" and lg3["unapproved"])
llm.panel_votes, llm.ask = pv, la
llm.ask = only_writer; called = []
llm.panel_votes = lambda *a, **k: called.append(1) or []
draft.linkedin_round({"title": "t", "body": "b", "linkedin": "writer text"}, "build")
check("LinkedIn: one candidate never goes to a vote", not called)
os.environ["LINKEDIN_MIN_VOTES"] = "0"
kept_post, kept_log = draft.linkedin_round({"title": "t", "body": "b", "linkedin": "writer text"}, "build")
check("rollback: LINKEDIN_MIN_VOTES=0 keeps the writer's post", kept_post["linkedin"] == "writer text" and kept_log is None)
del os.environ["LINKEDIN_MIN_VOTES"]
llm.panel_votes, llm.ask = pv, la
check("PR body: the cap keeps the marker", (lambda b: len(b) < 64100 and b.endswith("<!-- lab-run: 9 -->"))(draft.finish_body("x" * 90000, 9)))
check("LinkedIn: a long candidate is cut before the link check", "aaa" in draft.linkedin_md({"unapproved": True, "candidate": "a" * 1600 + " https://evil.example/x"}))
long_gate = {"report": {"flags": [], "headline": [], "contract": None}, "card": {"scores": {k: {"score": 1, "reasons": ["x" * 90000]} for k in ("technical", "editorial", "marketing")}}, "reasons": ["r" * 900] * 50, "decision": "revise"}
check("PR body: a huge review cannot exceed GitHub's limit", len(draft.gate_md(long_gate)) < 20000)
check("PR body: a cut is stated", "cut here" in draft.gate_md(long_gate) and "38 more" in draft.gate_md(long_gate))
check("cover: HERO_MEASURE never reaches an image", cover.verify({"kind": "terminal", "alt": "a", "lines": ["HERO_MEASURE: x=1", "ok line", "two line"]}, "HERO_MEASURE: x=1\nok line\ntwo line") and "HERO_MEASURE: x=1" not in cover.verify({"kind": "terminal", "alt": "a", "lines": ["HERO_MEASURE: x=1", "ok line", "two line"]}, "HERO_MEASURE: x=1\nok line\ntwo line")["lines"])
real_llm_call, llm.call = llm.call, lambda *a, **k: ["unexpected"]
llm._models.pop("gemini", None); os.environ["GEMINI_API_KEY"] = "t"
check("Gemini: a strange list response keeps the pin", llm.gemini_models()[0].endswith("gemini-3.1-pro-preview"))
for gem_call in (lambda *a, **k: None, lambda *a, **k: (_ for _ in ()).throw(ValueError("bad json"))):
    llm.call = gem_call; llm._models.pop("gemini", None)
    check("Gemini: an empty or broken list keeps the pin", llm.gemini_models()[0].endswith("gemini-3.1-pro-preview"))
llm.call = real_llm_call; llm._models.pop("gemini", None)
check("goals: a plural still matches", r.goal_score({"goal": "a", "title": "Why Sidecars Eat Memory"}, {"a": {"weight": 5, "focus": ["sidecar"]}}) == 5)
check("goals: focus matches whole words only", r.goal_score({"goal": "a", "title": "Hotels failed"}, {"a": {"weight": 5, "focus": ["otel", "ai"]}}) == 0)
check("assistant: a need is never listed twice", len(assistant.clean_plan({"focus": "f", "assignments": [{"role": "nobody", "task": "t"}], "needs": ["a"]})["needs"]) == 2)

# ---------- budget and CTO ----------
def raises_any(fn, *args):
    try:
        fn(*args); return False
    except Exception:
        return True
import budget, cto, radar as radar_b
b_month = budget.month()
listing = [{"name": f"spend-{b_month}-1-1-9-radar-c1200.json"}, {"name": f"spend-{b_month}-2-1-9-draft-c3850.json"},
           {"name": "spend-2020-01-1-1-9-radar-c9900.json"}, {"name": "goals.json"}]
real_rgh, real_read, real_write2 = radar_b.gh, analytics_mod.read_file, analytics_mod.write_file
radar_b.gh = lambda m, p, b=None: listing if "/contents/analytics" in p else []
files_b = {}
analytics_mod.read_file = lambda path, ref="analytics": (files_b.get(path, ""), "sha" if path in files_b else None)
analytics_mod.write_file = lambda path, text, sha, msg: files_b.__setitem__(path, text)
check("budget: only this month counts, from names alone", budget.spent() == (50.5, {"radar": 12.0, "draft": 38.5}))
check("budget: the default limit is 50", budget.limit() == 50)
env_b = dict(os.environ)
os.environ.update(GITHUB_ACTIONS="true", GITHUB_WORKFLOW="radar", HERO_PUBLISH="1", BOT_TOKEN="t")
budget._checked = False
try:
    budget.check(); b_stopped = False
except budget.BudgetStop as e:
    b_stopped = e.code == 0
check("budget: paid work stops at the limit, as a clean exit", b_stopped)
check("budget: a stop is not an Exception, so nothing swallows it", not issubclass(budget.BudgetStop, Exception))
os.environ["BUDGET_EXEMPT"] = "1"; budget._checked = False; budget.check(); del os.environ["BUDGET_EXEMPT"]
check("budget: an exempt job keeps running", True)
budget._checked = False
files_b[budget.LIMITS] = json.dumps({b_month: 75})
budget.check()
check("budget: a raised limit lets work continue", budget.limit() == 75)
del files_b[budget.LIMITS]
budget._checked = False
radar_b.gh = lambda m, p, b=None: (_ for _ in ()).throw(RuntimeError("down"))
try:
    budget.check(); b_closed = False
except budget.BudgetStop:
    b_closed = True
check("budget: an unreadable total spends nothing", b_closed)
radar_b.gh = real_rgh
budget._checked = False
os.environ.update(GITHUB_WORKFLOW="tests")
budget.check()
check("budget: the test workflow never stops", not budget.in_pipeline())
os.environ.update(GITHUB_WORKFLOW="replicate"); budget.EXTRA.clear()
budget.add_lab({"usd": 0.5, "aws_used": True})
check("budget: a lab counts its models and the AWS estimate", budget.EXTRA == [0.5] and budget.EXTRA_AWS == [1.0])
budget.EXTRA.clear(); budget.EXTRA_AWS.clear()
os.environ["FROM_RUN"] = "9"; budget.EXTRA.clear(); budget.add_lab({"usd": 0.5, "aws_used": True}); del os.environ["FROM_RUN"]
check("budget: a redraft from an old lab is not counted twice", budget.EXTRA == [])
budget.EXTRA.append(1.234); budget.EXTRA_AWS.append(1.0)
saved_ledger = list(llm.LEDGER); llm.LEDGER.clear()
real_branch2, analytics_mod.ensure_branch = analytics_mod.ensure_branch, lambda: None
budget.record()
analytics_mod.ensure_branch = real_branch2
llm.LEDGER.extend(saved_ledger)
b_written = [k for k in files_b if k.startswith("analytics/spend-")]
check("budget: a job records its spend and the AWS estimate", sorted(budget.SPEND.fullmatch(x.split("/")[1])[2] for x in b_written) == ["100", "124"])
check("budget: the AWS estimate is its own record", any("-aws_estimate-" in x for x in b_written))
budget.EXTRA.clear(); budget.EXTRA_AWS.clear()
for x in b_written: del files_b[x]
listing_aws = [{"name": f"spend-{b_month}-1-1-9-draft-c300.json"}, {"name": f"spend-{b_month}-1-1-9-aws_estimate-c100.json"},
               {"name": f"aws-{b_month}-01-c40.json"}, {"name": f"aws-{b_month}-02-c75.json"}]
radar_b.gh = lambda m, p, b=None: listing_aws
check("budget: AWS's latest figure counts, or the estimates if higher", budget.spent() == (4.0, {"draft": 3.0, "aws": 1.0}))
listing_aws[1] = {"name": f"spend-{b_month}-1-1-9-aws_estimate-c50.json"}
check("budget: a higher AWS figure wins", budget.spent() == (3.75, {"draft": 3.0, "aws": 0.75}))
radar_b.gh = lambda m, p, b=None: listing_aws[:2]
check("budget: the estimate counts until AWS reports", budget.spent()[0] == 3.5)
radar_b.gh = real_rgh
cost_file = pathlib.Path(tempfile.mkdtemp()) / "cost.json"
cost_file.write_text(json.dumps({"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "2.3456", "Unit": "USD"}}}]}))
budget.record_aws(str(cost_file))
aws_file = [k for k in files_b if k.startswith("analytics/aws-")]
check("budget: AWS's figure is saved, rounded up", len(aws_file) == 1 and aws_file[0].endswith("-c235.json") and budget.AWS_ACTUAL.fullmatch(aws_file[0].split("/")[1]))
del files_b[aws_file[0]]
os.environ.clear(); os.environ.update(env_b)
for said, want in (("raise budget to 75", 75), ("Please increase the budget to $120 for now", 120), ("set budget 60", 60),
                   ("should we raise budget to 75?", None), ("the budget was 50", None), ("raise budget to 99999", None), ("I like this", None)):
    check(f"budget: reads {said!r}", budget.requested_limit(said) == want)
budget.set_limit(80)
check("budget: a raise applies to this month only", json.loads(files_b[budget.LIMITS]) == {b_month: 80})
del files_b[budget.LIMITS]
ctoy = __import__("yaml").safe_load((ROOT / ".github/workflows/cto.yml").read_text())
cif = ctoy["jobs"]["cto"]["if"]
check("cto: only Abhi on the desk issue starts it", "comment.user.login == 'Abhimanyu9988'" in cif and "cto-desk" in cif and "issue.pull_request" in cif)
check("cto: nothing from a PR runs with its keys", "pull_request" not in ctoy[True] and ctoy["jobs"]["cto"]["steps"][0]["with"]["ref"] == "main")
check("cto: its comment reaches code only through env", "${{" not in next(s for s in ctoy["jobs"]["cto"]["steps"] if s.get("name") == "cto")["run"])
replies, real_post, real_ask2, real_le, real_prog = [], cto.post, roles_mod.ask, cto.link_exists, cto.progress
cto.post = lambda t, role="cto": replies.append(t)
real_research, cto.research = cto.research, lambda: ""
real_read_b = analytics_mod.read_file
cto.progress = lambda: {"spent": 1}
cto.link_exists = lambda u: u == "https://good.example/bill"
roles_mod.ask = lambda *a, **k: {"stance": "disagree", "reply": "No, see https://bad.example/x for why.", "goal": "", "task": {"title": "", "owner": ""}, "needs_abhi": [],
                                 "urls": ["https://good.example/bill", "https://nope.example/y"]}
real_data, roles_mod.data = roles_mod.data, lambda r: {}
cto.answer("monitor my azure bill", [], "cto")
check("cto: only confirmed links reach Abhi", "good.example" in replies[-1] and "nope.example" not in replies[-1] and "bad.example" not in replies[-1] and "could not be confirmed" in replies[-1])
roles_mod.ask = lambda *a, **k: {"stance": "agree", "reply": "Yes.", "goal": "", "task": {"title": "Watch the bill", "owner": "spend_tracker"}, "urls": []}
cto.answer("track spending", [])
check("privacy: without the private repo a task is not stored, and he is told", cto.TASKS not in files_b and "not saved" in replies[-1])
private_tasks = {}
real_on, real_rp, real_wp = cto.admin_on, cto.admin_read, cto.admin_write
cto.admin_on, cto.admin_read = (lambda: True), (lambda path: ("", None))
cto.admin_write = lambda path, text, sha, msg: private_tasks.__setitem__(path, text)
cto.answer("track spending", [])
cto.admin_on, cto.admin_read, cto.admin_write = real_on, real_rp, real_wp
check("cto: a task reaches an active role", json.loads(private_tasks["tasks.json"])["tasks"][0]["owner"] == "spend_tracker" and "Added to the list" in replies[-1] and "don't act" in replies[-1])
roles_mod.ask = lambda *a, **k: {"stance": "agree", "reply": "Yes.", "goal": "", "task": {"title": "t", "owner": "ghost"}, "urls": []}
cto.answer("hire someone", [])
real_assistant_goal = assistant.add_goal
goal_calls = []
assistant.add_goal = lambda text, n: goal_calls.append(text)
roles_mod.ask = lambda *a, **k: {"stance": "agree", "reply": "Yes.", "goal": "Invented", "task": {"title": "", "owner": ""}, "urls": [], "needs_abhi": []}
cto.answer("tell me a joke", [])
check("cto: a model cannot invent a goal", goal_calls == [])
cto.answer("my new goal is AWS community articles", [])
check("ceo: his own words become the goal, not a model's", goal_calls == ["my new goal is AWS community articles"])
roles_mod.ask = lambda *a, **k: {"stance": "need_data", "reply": "Blocked.", "goal": "", "task": {"title": "", "owner": ""}, "urls": [], "needs_abhi": ["Open Settings", "Click Save"]}
cto.answer("why stuck", [], "cmo")
real_le2, cto.link_exists = cto.link_exists, lambda u: u == "https://good.example/x"
check("leaders: a step keeps only confirmed links", "good.example" in cto.steps_md(["Open https://good.example/x"]) and "bad.example" not in cto.steps_md(["Open https://bad.example/x"]))
cto.link_exists = real_le2
check("leaders: when they need him, the steps are numbered", "What I need from you" in replies[-1] and "1. Open Settings" in replies[-1] and "2. Click Save" in replies[-1])
for said, want in (("CMO: how did others do it", ("cmo", "how did others do it")), ("cfo, are we ok", ("cfo", "are we ok")), ("@CTO why", ("cto", "why")),
                   ("my goal is X", ("ceo", "my goal is X")), ("ceofy this", ("ceo", "ceofy this"))):
    check(f"leaders: routes {said!r}", cto.route(said) == want)
check("leaders: one message can reach two leaders", cto.split_leaders("CMO: Good topic\nCTO: Waiting for the demo") == [("cmo", "Good topic"), ("cto", "Waiting for the demo")])
check("leaders: extra lines stay with their leader", cto.split_leaders("CMO: one\ntwo\nCTO: three") == [("cmo", "one\ntwo"), ("cto", "three")])
check("leaders: a plain message goes to the CEO", cto.split_leaders("hello\nthere") == [("ceo", "hello\nthere")])
old_talk = [{"from": "CTO", "text": "x" * 5000}] * 7 + [{"from": "Topic Scout", "text": "New pick: Prometheus"}, {"from": "Abhi", "text": "Good topic"}]
kept_talk = cto.recent_talk(old_talk)
check("leaders: the newest messages always reach the model", kept_talk[-1]["text"] == "Good topic" and kept_talk[-2]["text"].endswith("Prometheus"))
check("leaders: the talk stays within its limit", len(json.dumps(kept_talk)) < 7000)
for leader in ("ceo", "cto", "cmo", "cfo"):
    check(f"leaders: {leader} is on the team", roles_mod.get(leader)["status"] == "active" and roles_mod.get(leader)["display"].lower() == leader)
assistant.add_goal = real_assistant_goal
check("cto: an unknown role gets no work", any("No ghost role exists" in x for x in replies))
limit_calls = []
roles_mod.ask = lambda *a, **k: limit_calls.append(1)
cto.answer("raise budget to 75", [])
check("cto: a limit change never reaches a model", not limit_calls and "$75" in replies[-1])
del files_b[budget.LIMITS]
roles_mod.ask, roles_mod.data = real_ask2, real_data
cto.post, cto.link_exists, cto.progress = real_post, real_le, real_prog
cto.research = real_research
goals_b, sources.goals = sources.goals, lambda: [{"title": "AWS Hero", "outcome": "Become an AWS Hero", "weight": 8}]
real_spent, budget.spent = budget.spent, lambda: (1, {})
real_wr, llm.web_research = llm.web_research, lambda q, max_uses=4: {"summary": "s", "pages": [{"url": "https://real.example/a", "title": "a"}]}
roles_mod.ask = lambda *a, **k: {"what_worked": ["Spoke at meetups"], "topics_to_try": ["Why X fails"], "avoid": ["Docs repeats"], "sources": ["https://real.example/a", "https://invented.example/b"]}
brief_text = cto.research()
check("cmo: research is saved for the Topic Scout", "Spoke at meetups" in files_b[cto.BRIEF] and "Why X fails" in brief_text)
check("cmo: only real pages count as sources", "real.example" in brief_text and "invented.example" not in brief_text)
llm.web_research = lambda q, max_uses=4: {}
check("cmo: no pages means no brief, never a guess", cto.research() == "")
budget.spent = lambda: (500, {})
check("cmo: research stops at the monthly limit", cto.research() == "")
sources.goals, budget.spent, llm.web_research, roles_mod.ask = goals_b, real_spent, real_wr, real_ask2
check("cto: plain http is never confirmed", cto.link_exists("http://example.com") is False)
for inner in ("https://127.0.0.1/", "https://169.254.169.254/latest", "https://10.0.0.1/", "https://[::1]/", "https://example.com:8443/"):
    check(f"cto: refuses {inner}", cto.public_https(inner) is False)
pub = cto.public_status({"waiting": ["a"], "shipped": ["b"], "spent": 9, "limit": 50, "by_job": {}, "tasks": [{"title": "secret", "owner": "x"}], "failed_runs": ["x"]})
check("team page: no spending, tasks or failures", "secret" not in json.dumps(pub) and "spent" not in pub and "tasks" not in pub and "failed" not in json.dumps(pub))
check("team page: the page builds its text safely", "innerHTML" not in (ROOT / "site/team.md").read_text())
md = cto.digest_md({"waiting": ["Post A"], "shipped": [], "failed_runs": [], "running": ["replicate"], "spent": 3.5, "limit": 50, "tasks": [{"title": "Check it", "owner": "editor"}]})
check("digest: budget, waiting and work in progress are shown", all(x in md for x in ("$3.50 of $50.00", "Post A", "Content: Editor: Check it", "Engineering: Lab Engineer: working now", "Finance: Spend Tracker: idle")))
check("team: every active role has a name and a department", all(r.get("display") and r.get("dept") for r in roles_mod.registry().values()))
check("team: nothing in the registry is broken", roles_mod.check_registry() == [])
check("cto desk: its label exists, so the desk can be created", "cto-desk" in radar_b.LABELS)
import slack
posted_slack, real_api = [], slack.api
def fake_api(method, **k):
    posted_slack.append((method, k))
    return {"conversations.list": {"channels": [{"id": "C2"}, {"id": "C1", "is_general": True}]},
            "users.list": {"members": [{"id": "U9"}, {"id": "UME", "is_primary_owner": True}]},
            "conversations.history": {"messages": [{"ts": "5.0", "user": "UME", "text": "second"}, {"ts": "4.0", "user": "UME", "text": "first"},
                                                   {"ts": "6.0", "user": "U9", "text": "not him"}, {"ts": "7.0", "user": "UME", "bot_id": "B", "text": "echo"},
                                                   {"ts": "8.0", "user": "UME", "subtype": "channel_join", "text": "joined"}]}}.get(method, {})
slack.api = fake_api
check("slack: off without a token", slack.say("cto", "hi") is False and not posted_slack)
os.environ["SLACK_BOT_TOKEN"] = "x"
check("slack: posts", slack.say("spend_tracker", "<!channel> hi"))
sent_msg = next(k for m, k in posted_slack if m == "chat.postMessage")
check("slack: each agent posts under its name and department", sent_msg["username"] == "Spend Tracker (Finance)")
check("slack: text cannot ping the channel", "<" not in sent_msg["text"])
mine, newest = slack.messages("3.0")
check("slack: only Abhi's own messages count, oldest first", [m["text"] for m in mine] == ["first", "second"] and newest == "8.0")
heard, real_ans, real_hist = [], cto.answer, slack.history
cto.answer, slack.history = (lambda t, h, role="ceo": heard.append(t)), (lambda: [])
files_b[cto.CURSOR] = json.dumps({"ts": "3.0"})
cto.poll()
check("slack: the cursor moves and each message is answered", heard == ["first", "second"] and json.loads(files_b[cto.CURSOR])["ts"] == "8.0")
cto.answer, slack.history = real_ans, real_hist
slack.api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
def hist_api(method, **k):
    if method != "conversations.history": return fake_api(method)
    return {"messages": [{"user": "U9", "text": "someone else"}, {"user": "UME", "text": "mine"}, {"bot_id": "B", "username": "CTO (Engineering)", "text": "reply"}]}
slack.api = hist_api
hist = slack.history()
check("slack: other people stay out of the model's context", [h["text"] for h in hist] == ["reply", "mine"] and hist[1]["from"] == "Abhi")
slack.api = fake_api
heard2, real_ans3 = [], cto.answer
cto.answer = lambda t, h, role="ceo": heard2.append(t)
real_msg, slack.messages = slack.messages, lambda a: ([{"ts": str(i), "text": str(i)} for i in range(9)], "9")
real_hist3, slack.history = slack.history, lambda: []
cto.poll()
slack.messages, slack.history, cto.answer = real_msg, real_hist3, real_ans3
check("slack: replies are capped per run", len(heard2) == cto.MAX_REPLIES)
slack.api = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
check("slack: an outage never stops the work", slack.say("cto", "hi") is False)
cursor_before = files_b[cto.CURSOR]
cto.poll()
check("slack: a failed read keeps the cursor", files_b[cto.CURSOR] == cursor_before)
slack.api = real_api
del os.environ["SLACK_BOT_TOKEN"]
saved_admin, real_aw = {}, cto.activity
cto.activity = lambda: [("radar", "success", 1)]
real_res, cto.results = cto.results, lambda: [(12, "post-a", "devto")]
ap = {"waiting": ["Post A"], "shipped": [], "failed_runs": [], "running": [], "spent": 3.5, "limit": 50, "by_job": {"radar": 3.5}, "tasks": []}
page = cto.admin_md(ap, "2026-11-02", "**Operations Manager (Office): on track.** ok")
check("admin: cost, today, articles, readers and team are all on the page", all(x in page for x in ("$3.50 of $50.00", "radar: $3.50", "radar: success (1)", "Post A", "12 views: post-a", "Finance: Spend Tracker", "Operations Manager")))
check("manager: cost per article is arithmetic", cto.facts({**ap, "shipped": ["a", "b"], "spent": 10})["cost_per_published_article_this_week"] == 5)
check("manager: no article means no cost figure, not a guess", cto.facts(ap)["cost_per_published_article_this_week"] is None)
real_mask, roles_mod.ask = roles_mod.ask, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
check("manager: a failed review says so", "did not run" in cto.manager_review(ap))
roles_mod.ask = lambda *a, **k: {"verdict": "not_enough_data", "summary": "Too early.", "actions": ["Editor: x"]}
check("manager: its verdict is shown", "not enough data" in cto.manager_review(ap) and "Editor: x" in cto.manager_review(ap))
roles_mod.ask = real_mask
cto.admin_read, real_ard = (lambda path: ("", None)), cto.admin_read
real_awr, cto.admin_write = cto.admin_write, lambda path, text, sha, msg: saved_admin.__setitem__(path, text)
os.environ["ADMIN_TOKEN"] = "t"
cto.save_admin(ap)
add_before = dict(files_b)
cto.write_private(cto.TASKS, "{}", None, "m")
del os.environ["ADMIN_TOKEN"]
check("admin: the page and daily log go to the private repo", "README.md" in saved_admin and any(k.startswith("log-") for k in saved_admin))
check("privacy: tasks go to the private repo when it exists", "tasks.json" in saved_admin and files_b == add_before)
cto.admin_write, cto.admin_read, cto.activity, cto.results = real_awr, real_ard, real_aw, real_res
check("admin: skipped until the private repo exists", cto.save_admin(ap) is None)
ad = next(s for s in ctoy["jobs"]["cto"]["steps"] if s.get("id") == "admin")
check("admin: its token reaches only that repo, and a missing repo fails quietly", ad["with"]["repositories"] == "team-admin" and ad["continue-on-error"] is True)
check("privacy: the public status never reads the private page", "tasks" not in cto.public_status.__code__.co_names)
cyml = (ROOT / ".github/workflows/cto.yml").read_text()
bj = ctoy["jobs"]["billing"]
check("billing: no model key and no PR input", "ANTHROPIC" not in str(bj) and "pull_request" not in str(bj) and bj["steps"][0]["with"]["ref"] == "main")
check("billing: runs only in a main-only environment, with its own role", bj["environment"] == "live-check" and "BILLING_ROLE_ARN" in str(bj))
billing_sh = (ROOT / "infra/billing-role.sh").read_text()
check("billing: the AWS role can only read costs", billing_sh.count('"Action": "ce:GetCostAndUsage"') == 1 and "ce:*" not in billing_sh and "environment:live-check" in billing_sh)
check("editor: drafts get a quality verdict by default", "vars.EDITOR_REVIEW || 'on'" in (ROOT / ".github/workflows/replicate.yml").read_text())
check("lab: JSON text becomes a lab", lab_mod.as_lab('{"files": []}') == {"files": []})
check("lab: anything else is refused before AWS", raises_any(lab_mod.as_lab, "not json") and raises_any(lab_mod.as_lab, ["x"]))
real_out, lab_mod.OUT = lab_mod.OUT, pathlib.Path(tempfile.mkdtemp()) / "o"
real_ev = list(lab_mod.EVIDENCE); lab_mod.EVIDENCE.clear()
lab_mod.save({"status": "failed"}, "text, not a lab")
check("lab: a bad lab still writes its result, so spend and the failure survive", "usd" in json.loads((lab_mod.OUT / "result.json").read_text()))
lab_mod.OUT = real_out; lab_mod.EVIDENCE.extend(real_ev)
vold = "It took 35 seconds to recover.\n\n```\nkubectl get pods\n```\n\n_Drafted with AI assistance and reviewed by me._\n"
vgood = "Recovery took 35 seconds.\n\n```\nkubectl get pods\n```\n\n_Drafted with AI assistance and reviewed by me._\n"
check("voice: a clean rewrite passes", draft.voice_safe(vold, vgood))
check("voice: a new number is refused", not draft.voice_safe(vold, vgood.replace("35", "40")))
check("voice: a changed command is refused", not draft.voice_safe(vold, vgood.replace("get pods", "delete pods")))
check("voice: a dropped code block is refused", not draft.voice_safe(vold, "Recovery took 35 seconds, and I am sure of that.\n\n_Drafted with AI assistance and reviewed by me._\n"))
check("voice: the AI note can never be removed", not draft.voice_safe(vold, vgood.replace("_Drafted with AI assistance and reviewed by me._", "_Reviewed by me, carefully._")))
real_vask, roles_mod.ask = roles_mod.ask, lambda *a, **k: {"body": vgood, "question": "What did you see first?"}
vpost, vq = draft.voice_edit({"body": vold})
check("voice: a safe rewrite is used, with its question", vpost["body"] == vgood and "first" in vq)
roles_mod.ask = lambda *a, **k: {"body": vgood.replace("35", "99"), "question": "q"}
check("voice: an unsafe rewrite keeps the reviewed post", draft.voice_edit({"body": vold}) == ({"body": vold}, ""))
roles_mod.ask = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down"))
check("voice: an outage keeps the reviewed post", draft.voice_edit({"body": vold}) == ({"body": vold}, ""))
roles_mod.ask = real_vask
check("topics: each one needs a hook the docs do not give", "hook" in r.BUILD_SCHEMA["properties"]["topics"]["items"]["required"])
check("topics: setup walkthroughs are refused", "setup walkthroughs" in (ROOT / "pipeline/radar.py").read_text())
py = __import__("yaml").safe_load((ROOT / ".github/workflows/problems.yml").read_text())
check("topics: Thursday looks for real failures", py[True]["schedule"][0]["cron"] == "0 6 * * 4" and "${{" not in py["jobs"]["problems"]["steps"][-1]["run"])
check("slack: bold is Slack's own", slack.clean("**hi**") == "*hi*")
ry = __import__("yaml").safe_load((ROOT / ".github/workflows/radar.yml").read_text())
rs = ry["jobs"]["radar"]["steps"][-1]
check("auto lab: off switch and quoted input", "vars.AUTO_LAB != 'off'" in rs["if"] and "${{" not in rs["run"])
radar_b.gh, analytics_mod.read_file, analytics_mod.write_file = real_rgh, real_read, real_write2

print("\nALL PASSED" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
