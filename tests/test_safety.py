import os, subprocess, sys
HOME = "Abhimanyu9988/Abhimanyu9988.github.io"
PIPE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pipeline")
os.environ.update(REPO=HOME, HERO_PUBLISH="1", BOT_TOKEN="test", GH_READ_TOKEN="test")
sys.path.insert(0, PIPE)
import radar as r, problems as p

fails = 0
def check(name, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + name); fails += not ok
def raises(exc, fn, *a):
    try: fn(*a)
    except exc: return True
    except Exception as e: print("   wrong exception:", type(e).__name__, e); return False
    return False

# 1. text safety
s = r.neutralize("See https://github.com/aws/karpenter-provider-aws/issues/9617#issuecomment-1, "
                 "aws/karpenter-provider-aws#9616, github.com/cilium/cilium/pull/42, #12, GH-7, "
                 "moby/moby@abcdef1234 and @jmdeal")
print("   ->", s)
check("neutralize: no github.com", "github.com" not in s.lower())
check("neutralize: no # references", "#" not in s)
check("neutralize: mention broken", "@jmdeal" not in s)
check("neutralize: readable", "aws/karpenter-provider-aws issue 9617" in s)
check("neutralize output passes the final check", not raises(r.Unsafe, r.assert_safe, s))
check("release link", r.neutralize("https://github.com/helm/helm/releases/tag/v3.19.0") == "helm/helm release v3.19.0")
BAD = ["https://github.com/a/b/issues/1", "www.GitHub.com/a/b", "a/b#1", "a/b@abcdef1", "hi @someone",
       "See #123", "see GH-123", "[x](https://github.com/a/b/issues/1)", "[x](#5)",
       "[x](/aws/karpenter-provider-aws/issues/1)", "https://github%2Ecom/a/b", "github&#46;com/a/b",
       "&#64;someone", "&#35;42", "https://stackoverflow.com/q/1?r=a%2Fb%231"]
for bad in BAD:
    check(f"final check refuses {bad!r}", raises(r.Unsafe, r.assert_safe, bad))
check("final check allows a Stack Overflow link",
      not raises(r.Unsafe, r.assert_safe, "- https://stackoverflow.com/questions/79000001/ecs-scale"))
srcs = ["https://github.com/aws/karpenter-provider-aws/issues/9617", "https://stackoverflow.com/questions/1/x"]
blk = r.sources_block(srcs) + "\n" + r.src_marker(srcs)
print(blk)
check("sources block passes the final check", not raises(r.Unsafe, r.assert_safe, blk))
check("hidden marker round trip", r.used_sources(blk) >= set(srcs))
check("old markers still read", "https://x.y/z" in r.used_sources('<!-- radar-sources: ["https://x.y/z"] -->'))

# 2. every write path
sent = []
def fake(m, u, b=None, h=None, timeout=30):
    if m == "GET": raise RuntimeError("HTTP 404")
    sent.append((m, u)); return {"number": 0}
r._send = fake
ok = {"title": "t", "body": "b"}
B64 = {"message": "draft: add x", "branch": "draft/x", "content": "eA=="}
for name, m, path, b in [
    ("issue in other repo", "POST", "/repos/aws/karpenter-provider-aws/issues", ok),
    ("comment in other repo", "POST", "/repos/aws/karpenter-provider-aws/issues/1/comments", {"body": "x"}),
    ("edit", "PATCH", f"/repos/{HOME}/issues/1", {"body": "x"}),
    ("delete", "DELETE", f"/repos/{HOME}/issues/1", None),
    ("path trick", "POST", f"/repos/{HOME}/issues/../../aws/x/issues", ok),
    ("encoded path trick", "POST", f"/repos/{HOME}/issues/1%2F..%2F..%2Fx/comments", {"body": "x"}),
    ("workflow file", "PUT", f"/repos/{HOME}/contents/.github/workflows/x.yml", B64),
    ("pipeline file", "PUT", f"/repos/{HOME}/contents/pipeline/radar.py", B64),
    ("file on main", "PUT", f"/repos/{HOME}/contents/site/_posts/x.md", {**B64, "branch": "main"}),
    ("file with unsafe commit message", "PUT", f"/repos/{HOME}/contents/labs/x/main.tf", {**B64, "message": "see a/b#1"}),
    ("branch not draft/", "POST", f"/repos/{HOME}/git/refs", {"ref": "refs/heads/main", "sha": "0"}),
    ("tag ref", "POST", f"/repos/{HOME}/git/refs", {"ref": "refs/tags/draft/x", "sha": "0"}),
    ("PR into other base", "POST", f"/repos/{HOME}/pulls", {"title": "t", "body": "b", "head": "draft/x", "base": "dev"}),
    ("PR from non-draft head", "POST", f"/repos/{HOME}/pulls", {"title": "t", "body": "b", "head": "main", "base": "main"}),
    ("PR body with cross-repo ref", "POST", f"/repos/{HOME}/pulls", {"title": "t", "body": "Closes aws/x#5", "head": "draft/x", "base": "main"}),
    ("unknown label", "POST", f"/repos/{HOME}/issues/1/labels", {"labels": ["good first issue"]}),
    ("comment with mention", "POST", f"/repos/{HOME}/issues/1/comments", {"body": "thanks @jmdeal"}),
    ("label edit", "PATCH", f"/repos/{HOME}/labels/radar", {"color": "000000"}),
    ("analytics file on main", "PUT", f"/repos/{HOME}/contents/analytics/ledger.csv", {**B64, "branch": "main"}),
    ("analytics file on a draft branch", "PUT", f"/repos/{HOME}/contents/analytics/ledger.csv", B64),
    ("analytics subfolder escape", "PUT", f"/repos/{HOME}/contents/analytics/x/../../.github/x.yml", {**B64, "branch": "analytics"}),
    ("post file on the analytics branch", "PUT", f"/repos/{HOME}/contents/site/_posts/x.md", {**B64, "branch": "analytics"}),
    ("other branch name", "POST", f"/repos/{HOME}/git/refs", {"ref": "refs/heads/analytics-x", "sha": "0"})]:
    check(f"blocked: {name}", raises((r.Blocked, r.Unsafe), r.gh, m, path, b))
check("nothing was sent", sent == [])
got = []
r._send = lambda m, u, b=None, h=None, timeout=30: got.append(u) or {}
r.gh("GET", "/search/issues?q=is%3Aissue%20repo%3Aaws/x")
check("reads may use an encoded query string", len(got) == 1)
r._send = fake
r.post_issue("t", "b", "radar")
check("allowed: new issue in your repo", len(sent) == 1 and sent[0][1].endswith(f"/repos/{HOME}/issues"))
r.ensure_label("problem")
check("allowed: new label in your repo", len(sent) == 2 and sent[1][1].endswith(f"/repos/{HOME}/labels"))
for name, m, path, b in [
    ("comment", "POST", f"/repos/{HOME}/issues/4/comments", {"body": "Draft ready in pull request no. 9"}),
    ("replicate label", "POST", f"/repos/{HOME}/issues/4/labels", {"labels": ["replicate"]}),
    ("draft branch", "POST", f"/repos/{HOME}/git/refs", {"ref": "refs/heads/draft/my-topic", "sha": "0"}),
    ("post file", "PUT", f"/repos/{HOME}/contents/site/_posts/2026-10-18-my-topic.md", {**B64, "branch": "draft/my-topic"}),
    ("lab evidence file", "PUT", f"/repos/{HOME}/contents/labs/my-topic/evidence/01-init.log", {**B64, "branch": "draft/my-topic"}),
    ("analytics branch", "POST", f"/repos/{HOME}/git/refs", {"ref": "refs/heads/analytics", "sha": "0"}),
    ("analytics ledger", "PUT", f"/repos/{HOME}/contents/analytics/ledger.csv", {**B64, "branch": "analytics", "sha": "1"}),
    ("draft PR closing its topic", "POST", f"/repos/{HOME}/pulls",
     {"title": "Draft: t", "body": "summary\n\nCloses #4", "head": "draft/my-topic", "base": "main"})]:
    n = len(sent); r.gh(m, path, b)
    check(f"allowed: {name}", len(sent) == n + 1)
r.PUBLISH = False
check("generation mode blocks every write",
      raises(r.Blocked, r.post_issue, "t", "b", "radar") and raises(r.Blocked, r.ensure_label, "radar"))
check("GitHub write via call() blocked", raises(r.Blocked, r.call, "POST", "https://api.github.com/repos/a/b/issues", {}))

# 3. confirmation rules
gi = {"src": "github", "complete": True, "labels": [], "comments": [
      {"id": "g1.c1", "by": "maintainer", "text": "yes, that is the cause"},
      {"id": "g1.c2", "by": "other", "text": "+1"}]}
sq = {"src": "stackoverflow", "complete": True, "answers": [{"id": "s1.a1", "accepted": True, "score": 5, "text": "x"}]}
by = {"G": gi, "S": sq}
J = lambda per: p.judge({"per_source": per}, ["G", "S"], by)[0]
sup = lambda u, ids: {"url": u, "verdict": "supports", "evidence_ids": ids}
check("maintainer comment -> confirmed", J([sup("G", ["g1.c1"])]) == "confirmed")
check("non-maintainer comment is not evidence", J([sup("G", ["g1.c2"])]) == "unconfirmed")
check("evidence from another source is rejected", J([sup("S", ["g1.c1"])]) == "unconfirmed")
check("'answered' with no cited answer is not evidence", J([sup("S", [])]) == "unconfirmed")
check("a disputing source can't be outvoted", J([{"url": "G", "verdict": "disputes"}, sup("S", ["s1.a1"])]) == "skip")
gi["complete"] = False
check("incomplete review -> unconfirmed", J([sup("G", ["g1.c1"])]) == "unconfirmed")
gi["complete"], gi["labels"] = True, ["triage/needs-information"]
check("needs-information label -> unconfirmed", J([sup("G", ["g1.c1"])]) == "unconfirmed")

# 4. startup
def run(code, env):
    q = subprocess.run([sys.executable, "-c", code], cwd=PIPE, env={**os.environ, **env},
                       capture_output=True, text=True)
    return q.stdout.strip(), q.returncode
check("radar/problems runs can't write", run("import radar; print(radar.PUBLISH)", {"HERO_PUBLISH": ""})[0] == "False")
check("wrong repo refuses to start", run("import radar", {"REPO": "someone/else"})[1] != 0)
print("\nALL PASSED" if not fails else f"\n{fails} FAILED"); sys.exit(1 if fails else 0)
