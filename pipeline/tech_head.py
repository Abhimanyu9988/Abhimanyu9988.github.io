#!/usr/bin/env python3
"""Tech Head: reviews every code change before a human merges it.

  1. rules(diff)      repo-specific checks on added lines, in code. Free, exact, and they run even with no API key.
  2. scanner reports  bandit and zizmor output from the workflow, passed in as data.
  3. review(...)      one model from a different vendor than the author reads the diff, AGENTS.md and the reports.
  4. verdict(...)     code decides. Findings on files outside the diff are dropped as unsupported.

TECH_HEAD_MODE: advisory (default) comments only; blocking also fails the check on a blocker.
tech-head.yml runs from the base branch through pull_request_target, so a pull request cannot edit its own review.
Code in the pull request is data here and is never executed. See docs/merge-behavior.md for the trust boundary.
"""
import json, os, pathlib, re, subprocess, sys

MAX_DIFF = 100_000                             # one batch
MAX_BATCHES = 4                                # bounds a single review at about $1 at today's prices
# Riskiest first, so a size limit cuts tests and fixtures, never workflows or pipeline code.
ORDER = (".github/", "infra/", "pipeline/", "AGENTS.md", "docs/", "README", "tests/test_")
SKIP = ("tests/fixtures/", "tests/eval/", "labs/", "site/")
SCANNERS = ("bandit", "zizmor")
# (id, severity, category, pattern on an added line, problem, fix, only these paths or None)
RULES = (
    ("hard-secret", "blocker", "security", re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,}|"
     r"AIza[0-9A-Za-z_-]{30,}|sk-ant-[A-Za-z0-9-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY"),
     "A credential appears in the change.", "Remove it, rotate it, and use a secret.", None),
    ("pr-target", "blocker", "security", re.compile(r"\bpull_request_target\b"),
     "pull_request_target runs with secrets on pull request events.",
     "Use pull_request, or prove no PR code runs in that job.", ".github/workflows/"),
    ("workflow-injection", "blocker", "security",
     re.compile(r"^(?!\s*-?\s*(?!(?:run|script)\b)[A-Za-z_][\w-]*:\s*[\"']?\$\{\{)(?!\s*-?\s*(?:env|concurrency)\s*:)"
                r".*\$\{\{.*?\bgithub\.(event\b|head_ref\b)"),
     "Event data may be expanded inside a shell script.", "Pass it through env and quote it.", ".github/"),
    ("write-all", "major", "security", re.compile(r"permissions:\s*write-all"),
     "The workflow grants every write permission.", "List only the permissions the job needs.", ".github/workflows/"),
    ("shell-true", "major", "security", re.compile(r"shell\s*=\s*(True|1)\b"),
     "A subprocess uses a shell.", "Pass an argument list.", ".py"),
    ("dynamic-exec", "blocker", "security", re.compile(r"(?<![\w.])(eval|exec)\s*\(|\bbuiltins\.(eval|exec)\b|__built" r"ins__|\bos\.(system|popen)\s*\(|pickle\.loads?\b|yaml\.unsafe_load\b|"
                r"yaml\.load\((?!.*SafeLoader)"),
     "Dynamic execution or unsafe parsing.", "Parse data with a safe loader; never execute text.", ".py"),
    ("github-write-bypass", "blocker", "security", re.compile(r"api\.github\.com.*(POST|PUT|PATCH|DELETE)|"
     r"(POST|PUT|PATCH|DELETE).*api\.github\.com"), "A GitHub write may bypass WRITE_RULES.",
     "Send every bot write through radar.gh().", "pipeline/"),
    ("unbounded-loop", "major", "reliability", re.compile(r"^\s*while True:"),
     "An unbounded loop can run a job or its model bill forever.", "Add a limit or deadline.", ".py"),
    ("tls-off", "blocker", "security", re.compile(r"verify\s*=\s*False|_create_" r"unverified_context|--in" r"secure\b"),
     "TLS verification is turned off.", "Keep verification on.", None),
)

def _name(field):
    """A path from a +++ or --- line: git may quote it, and adds a tab after a name with spaces."""
    return field.split("\t")[0].strip().strip('"')

def added_lines(diff):
    """[(file, line number in the new file, text)] for every added line of a unified diff. Hunk lengths decide what
    is a header, so an added line that happens to start with ++ cannot hide the lines after it."""
    out, path, n, old_left, new_left = [], None, 0, 0, 0
    for line in diff.split("\n"):                # never splitlines: a form feed in code would desync the hunk
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                if path:
                    out.append((path, n, line[1:]))
                n += 1; new_left -= 1
            elif line.startswith("-"):
                old_left -= 1
            elif not line.startswith("\\"):
                n += 1; old_left -= 1; new_left -= 1
            continue
        if line.startswith("+++ "):
            name = _name(line[4:])
            path = name[2:] if name.startswith("b/") else None
        else:
            m = re.match(r"@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
            if m:
                old_left, n, new_left = int(m[1] or 1), int(m[2]), int(m[3] or 1)
    return out

def _path(part):
    """The file a per-file diff belongs to, parsed from its header, never matched as free text."""
    m = re.search(r"^\+\+\+ (.+)$", part, re.M)
    name = _name(m[1]) if m else ""
    if name.startswith("b/"):
        return name[2:]
    m = re.search(r"^--- (.+)$", part, re.M)
    name = _name(m[1]) if m else ""
    return name[2:] if name.startswith("a/") else ""

def _ordered(diff):
    """Whole-file diffs, riskiest first, without saved data."""
    parts = [p for p in re.split(r"(?m)^(?=diff --git )", diff) if p]
    rank = lambda part: next((i for i, o in enumerate(ORDER) if _path(part).startswith(o)), len(ORDER))
    return sorted((p for p in parts if not _path(p).startswith(SKIP)), key=rank)

def batches(diff):
    """(bounded batches of the diff, characters left out). Files stay whole unless one is larger than a batch."""
    out, cur, seen = [], "", 0
    for part in _ordered(diff):
        for start in range(0, len(part), MAX_DIFF):
            piece = part[start:start + MAX_DIFF]
            if cur and len(cur) + len(piece) > MAX_DIFF:
                out.append(cur); cur = ""
            cur += piece
    out += [cur] if cur else []
    kept = out[:MAX_BATCHES]
    return kept, sum(len(b) for b in out[MAX_BATCHES:])

def prioritise(diff):
    """(the text that will be reviewed, characters left out)."""
    kept, left = batches(diff)
    return "".join(kept), left

def files_in(diff):
    return {p for p in map(_path, re.split(r"(?m)^(?=diff --git )", diff)) if p}

def rules(diff):
    found = []
    for path, n, text in added_lines(diff):
        for piece in text.split("\r"):             # Python treats a carriage return as a new line
            comment_line = piece.lstrip().startswith("#")
            for rid, sev, cat, rx, problem, fix, only in RULES:
                if path.startswith("tests/") and rid != "hard-secret":
                    continue                       # test fixtures are not findings
                if comment_line and rid not in ("hard-secret", "workflow-injection"):
                    continue                       # a comment naming a risk is not the risk; expressions still expand
                if (only is None or path.startswith(only) or path.endswith(only)) and rx.search(piece):
                    found.append({"severity": sev, "category": cat, "file": path, "line": n, "problem": problem,
                                  "fix": fix, "rule": rid})
    return found

def read_reports(folder):
    """{scanner: {status, items}}. status is clean, findings, partial or unavailable. A scanner that did not finish is
    unavailable, which is never the same as clean."""
    out, root = {}, pathlib.Path(folder or ".")
    for name in SCANNERS:
        p = root / f"{name}.json"
        code = root / f"{name}.exit"
        try:
            exit_code = int(code.read_text().strip())
            data = json.loads(p.read_text())
        except (OSError, ValueError):
            out[name] = {"status": "unavailable", "items": []}; continue
        if name == "bandit":
            items, errors = data.get("results") or [], data.get("errors") or []
            ran = exit_code in (0, 1)
        else:
            items, errors = (data if isinstance(data, list) else []), []
            ran = isinstance(data, list)
        status = "unavailable" if not ran else "partial" if errors else "findings" if items else "clean"
        out[name] = {"status": status, "items": items, "errors": errors[:5]}
    return out

def relevant(reports, changed):
    """Only findings on files in this change, in a compact form, so the model sees them all within its limit."""
    out = {}
    for name, r in reports.items():
        kept = []
        for item in r.get("items") or []:
            text = json.dumps(item)
            hit = next((p for p in changed if p in text), None)
            if hit:
                kept.append({"file": hit, "id": item.get("test_id") or item.get("ident"),
                             "text": str(item.get("issue_text") or item.get("desc"))[:160],
                             "line": item.get("line_number"), "severity": item.get("issue_severity") or
                             (item.get("determinations") or {}).get("severity")})
        out[name] = {"status": r.get("status"), "items": kept}
    return out

def scanner_blockers(reports, changed):
    """A confident, High script-injection finding on a changed workflow blocks like a code rule, because it is one.
    It matches by file, so an old finding in a workflow a PR touches also blocks. That is advisory until blocking mode."""
    out = []
    for item in (reports.get("zizmor") or {}).get("items") or []:
        grade = item.get("determinations") or {}
        spot = next((l for l in item.get("locations") or [] if (l.get("symbolic") or {}).get("kind") == "Primary"), {})
        local = ((spot.get("symbolic") or {}).get("key") or {}).get("Local") or {}
        hit = str(local.get("verbatim_path") or local.get("given_path") or "").removeprefix("pr/")
        if item.get("ident") == "template-injection" and hit in changed and grade.get("severity") == "High" and grade.get("confidence") != "Low":
            row = (((spot.get("concrete") or {}).get("location") or {}).get("start_point") or {}).get("row")
            out.append({"severity": "blocker", "category": "security", "file": hit, "rule": "zizmor-template-injection",
                        "line": row + 1 if isinstance(row, int) else 0,
                        "problem": str(item.get("desc"))[:200], "fix": "Pass the value through env and quote it."})
    return out

def complete(reports, ai, left=0):
    """Reasons this review is incomplete. An incomplete review must never read as a clean one."""
    gaps = [f"{n} {r['status']}" for n, r in reports.items() if r["status"] in ("unavailable", "partial")]
    gaps += [f"{left:,} characters of the diff were not reviewed"] if left else []
    gaps += [f"{ai['failed_batches']} review batch(es) failed"] if (ai or {}).get("failed_batches") else []
    gaps += [f"{ai['scanner_items_cut']} scanner item(s) were not sent to the reviewer"] if (ai or {}).get("scanner_items_cut") else []
    return gaps + ([] if ai else ["AI review did not run"])

def fit_scanners(rel, limit=12000):
    """(reports, items cut). Each scanner gets its own share, so one cannot push another out of the prompt."""
    out, cut = {}, 0
    for name, r in rel.items():
        kept, size = [], 0
        for item in r.get("items") or []:
            size += len(json.dumps(item))
            if size > limit:
                break
            kept.append(item)
        cut += len(r.get("items") or []) - len(kept)
        out[name] = {**r, "items": kept}
    return out, cut

def review(diff, reports, agents_md):
    """One model call per batch. A failed batch keeps earlier findings and counts as unreviewed, never as clean."""
    import roles
    kept, left = batches(diff)
    fitted, cut = fit_scanners(relevant(reports, files_in(diff)))
    merged = {"summary": "", "findings": [], "failed_batches": 0, "batches": len(kept), "scanner_items_cut": cut if kept else 0}
    for i, chunk in enumerate(kept, 1):
        note = f"\nThis is batch {i} of {len(kept)}." + (f" {left:,} characters stay unreviewed: say so." if left else "")
        scanners = json.dumps(fitted) if i == 1 else "see batch 1"
        try:
            got = roles.ask("tech_head", f"Review this change.{note}\n\nAGENTS.md:\n{agents_md}\n\nSCANNER REPORTS:\n"
                                         f"{scanners}\n\nDIFF:\n{chunk}", "techreview")
        except Exception as e:
            merged["failed_batches"] += 1
            merged["error"] = str(e)[:200]
            if i == 1 and len(kept) == 1:
                raise
            continue
        merged["summary"] = " ".join(x for x in (merged["summary"], str(got.get("summary", ""))) if x)[:800]
        merged["findings"] += got.get("findings") or []
    if kept and merged["failed_batches"] == len(kept):
        raise RuntimeError(merged.get("error") or "no batch was reviewed")
    return merged

def verdict(rule_findings, ai, diff):
    """(findings, dropped count, blocked). The file check stops a model reporting problems in code it never saw.
    Only code rules and scanner_blockers can block. A model's blocker is shown to a person, who decides."""
    allowed, kept, dropped = files_in(diff), list(rule_findings), 0
    for f in (ai or {}).get("findings") or []:
        if f.get("file") in allowed:
            kept.append({k: v for k, v in f.items() if k != "rule"})   # only code may mark a finding as a code rule
        else:
            dropped += 1
    rank = {"blocker": 0, "major": 1, "minor": 2}
    kept.sort(key=lambda f: rank.get(f.get("severity"), 3))
    return kept, dropped, any(f.get("severity") == "blocker" and f.get("rule") for f in kept)

def markdown(findings, ai, dropped, blocked, skipped="", reports=None, left=0):
    claimed = any(f.get("severity") == "blocker" for f in findings)
    gaps = complete(reports or {}, ai, left)
    head = ("code rules found a blocker" if blocked else "the reviewer raised a possible blocker, please judge it" if claimed
            else "notes to read" if findings else "incomplete, this is not a clean result" if gaps
            else "only saved data changed, so no model read this" if ai and not ai.get("batches") else "no findings")
    lines = [f"**Tech Head review: {head}.**", ""]
    if reports:
        lines += ["Scanners: " + ", ".join(f"{n} {r['status']}" for n, r in reports.items()) + ".", ""]
    if gaps:
        lines += [f"**Incomplete:** {'; '.join(gaps)}.", ""]
    if (ai or {}).get("summary"):
        lines += [str(ai["summary"]), ""]
    if skipped:
        lines += [f"AI review skipped: {skipped}. Code rules still ran.", ""]
    if len(findings) > 20:
        lines += [f"Showing 20 of {len(findings)} findings, most severe first.", ""]
    for f in findings[:20]:
        lines.append(f"- **{f.get('severity')} / {f.get('category')}** `{f.get('file')}:{f.get('line')}`: "
                     f"{f.get('problem')} Fix: {f.get('fix')}")
    if dropped:
        lines += ["", f"{dropped} finding(s) dropped because they named files outside this change."]
    return "\n".join(lines)

def git_diff(repo, base, head):
    """Raw bytes, decoded tolerantly: one bad byte in a PR must not crash its own review. No renames, so a moved file
    shows every line. Real paths, so a non-ASCII name cannot dodge a rule."""
    return subprocess.run(["git", "-c", "core.quotePath=false", "-C", repo, "diff", "--no-color", "--text", "--no-ext-diff",
                           "--no-textconv", "--no-renames", f"{base}...{head}"], check=True,
                          capture_output=True).stdout.decode("utf-8", "replace")

def main():
    import roles
    from radar import comment, safe_text
    diff = git_diff(os.environ["PR_DIR"], os.environ["BASE_SHA"], os.environ["HEAD_SHA"])
    reports = read_reports(os.environ.get("REPORTS_DIR"))
    found, ai, skipped = rules(diff) + scanner_blockers(reports, files_in(diff)), None, ""
    if any(f["rule"] == "hard-secret" for f in found):
        skipped = "the change appears to contain a credential, so it was not sent to a model"
    else:
        try:
            ai = review(diff, reports, roles.data("tech_head").get("agents_md", ""))
        except Exception as e:                     # no key, budget, or a model outage: code rules still stand
            skipped = str(e)[:200]
    findings, dropped, blocked = verdict(found, ai, diff)
    left = prioritise(diff)[1]
    text = markdown(findings, ai, dropped, blocked, skipped, reports, left)
    print("\n".join(f"| {line}" for line in text.replace("##[", "# #[").splitlines()))   # keeps model text from being a runner command
    gaps = complete(reports, ai, left)
    fallback = (f"**Tech Head review: {'code rules found a blocker' if blocked else 'finished'}.** {len(findings)} finding(s), "
                f"{sum(f.get('severity') == 'blocker' for f in findings)} marked blocker. "
                + (f"**Incomplete, {len(gaps)} gap(s).** " if gaps else "")
                + "The full text failed the link-safety check, so read it in the workflow run log.")
    comment(int(os.environ["PR_NUMBER"]), safe_text(text, fallback))
    if os.environ.get("TECH_HEAD_MODE", "advisory") == "blocking" and (blocked or complete(reports, ai, left)):
        sys.exit("Tech Head found a blocker, or could not finish its review")

if __name__ == "__main__":
    main()
