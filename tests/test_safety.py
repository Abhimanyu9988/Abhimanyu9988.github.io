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
for name, m, path, b in [
    ("other repo", "POST", "/repos/aws/karpenter-provider-aws/issues", ok),
    ("comment", "POST", f"/repos/{HOME}/issues/1/comments", {"body": "x"}),
    ("edit", "PATCH", f"/repos/{HOME}/issues/1", {"body": "x"}),
    ("delete", "DELETE", f"/repos/{HOME}/issues/1", None),
    ("path trick", "POST", f"/repos/{HOME}/issues/../../aws/x/issues", ok)]:
    check(f"blocked: {name}", raises(r.Blocked, r.gh, m, path, b))
for bad in BAD:
    check(f"post_issue refuses body {bad!r}", raises(r.Unsafe, r.post_issue, "t", bad, "radar"))
    check(f"gh refuses title {bad!r}", raises(r.Unsafe, r.gh, "POST", f"/repos/{HOME}/issues", {"title": bad, "body": "b"}))
check("nothing was sent", sent == [])
r.post_issue("t", "b", "radar")
check("allowed: new issue in your repo", len(sent) == 1 and sent[0][1].endswith(f"/repos/{HOME}/issues"))
r.ensure_label("problem")
check("allowed: new label in your repo", len(sent) == 2 and sent[1][1].endswith(f"/repos/{HOME}/labels"))
r.PUBLISH = False
check("generation mode blocks every write",
      raises(r.Blocked, r.post_issue, "t", "b", "radar") and raises(r.Blocked, r.ensure_label, "radar"))
check("GitHub write via call() blocked", raises(r.Blocked, r.call, "POST", "https://api.github.com/repos/a/b/issues", {}))
P = {"label": "radar", "title": "t", "body": "b"}
check("proposal id changes with the body", r.proposal_id(P) != r.proposal_id({**P, "body": "b2"}))

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
