#!/usr/bin/env python3
"""Tech Head: reviews every code change before a human merges it.

  1. rules(diff)      repo-specific checks on added lines, in code. Free, exact, and they run even with no API key.
  2. scanner reports  bandit and zizmor output from the workflow, passed in as data.
  3. review(...)      one model from a different vendor than the author reads the diff, AGENTS.md and the reports.
  4. verdict(...)     code decides. Findings on files outside the diff are dropped as unsupported.

TECH_HEAD_MODE: advisory (default) comments only; blocking also fails the check on a blocker.
Run from the base branch's checkout: code in the pull request is data here and is never executed.
"""
import json, os, pathlib, re, subprocess, sys

MAX_DIFF = 60_000
# (id, severity, category, pattern on an added line, problem, fix, only these paths or None)
RULES = (
    ("hard-secret", "blocker", "security", re.compile(r"AKIA[0-9A-Z]{16}|ASIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|"
     r"sk-ant-[A-Za-z0-9-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY"),
     "A credential appears in the change.", "Remove it, rotate it, and use a secret.", None),
    ("pr-target", "blocker", "security", re.compile(r"^\s*pull_request_target\s*:"),
     "pull_request_target runs with secrets on pull request events.",
     "Use pull_request, or prove no PR code runs in that job.", ".github/workflows/"),
    ("workflow-injection", "blocker", "security", re.compile(r"\brun:.*\$\{\{\s*github\.(event|head_ref)"),
     "Event data is expanded inside a shell script.", "Pass it through env and quote it.", ".github/workflows/"),
    ("write-all", "major", "security", re.compile(r"permissions:\s*write-all"),
     "The workflow grants every write permission.", "List only the permissions the job needs.", ".github/workflows/"),
    ("shell-true", "major", "security", re.compile(r"shell\s*=\s*True"),
     "A subprocess uses a shell.", "Pass an argument list.", ".py"),
    ("dynamic-exec", "blocker", "security", re.compile(r"(?<![\w.])(eval|exec)\(|pickle\.loads|yaml\.load\((?!.*SafeLoader)"),
     "Dynamic execution or unsafe parsing.", "Parse data with a safe loader; never execute text.", ".py"),
    ("github-write-bypass", "blocker", "security", re.compile(r"api\.github\.com.*(POST|PUT|PATCH|DELETE)|"
     r"(POST|PUT|PATCH|DELETE).*api\.github\.com"), "A GitHub write may bypass WRITE_RULES.",
     "Send every bot write through radar.gh().", "pipeline/"),
    ("unbounded-loop", "major", "reliability", re.compile(r"^\s*while True:"),
     "An unbounded loop can run a job or its model bill forever.", "Add a limit or deadline.", ".py"),
    ("tls-off", "blocker", "security", re.compile(r"verify\s*=\s*False|_create_unverified_context|--insecure\b"),
     "TLS verification is turned off.", "Keep verification on.", None),
)

def added_lines(diff):
    """[(file, line number in the new file, text)] for every added line of a unified diff."""
    out, path, n = [], None, 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else None
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            n = int(m[1]) if m else 0
        elif line.startswith("+") and path:
            out.append((path, n, line[1:])); n += 1
        elif not line.startswith("-"):
            n += 1
    return out

def files_in(diff):
    return set(re.findall(r"^\+\+\+ b/(.+)$", diff, re.M))

def rules(diff):
    found = []
    for path, n, text in added_lines(diff):
        if path.startswith("tests/") or path == "pipeline/tech_head.py":
            continue                               # fixtures and this file's own patterns are not findings
        for rid, sev, cat, rx, problem, fix, only in RULES:
            if (only is None or path.startswith(only) or path.endswith(only)) and rx.search(text):
                found.append({"severity": sev, "category": cat, "file": path, "line": n, "problem": problem,
                              "fix": fix, "rule": rid})
    return found

def read_reports(folder):
    """Summaries of scanner output, so a noisy scanner cannot flood the prompt."""
    out, root = {}, pathlib.Path(folder or ".")
    for name in ("bandit.json", "zizmor.json"):
        p = root / name
        if p.exists():
            try:
                data = json.loads(p.read_text() or "{}")
            except ValueError:
                out[name] = "unreadable"; continue
            items = data.get("results") if isinstance(data, dict) else data
            out[name] = (items or [])[:40]
    return out

def review(diff, reports, agents_md):
    import roles
    cut = diff[:MAX_DIFF]
    note = "" if len(diff) <= MAX_DIFF else f"\nThe diff was cut at {MAX_DIFF:,} characters of {len(diff):,}: say so."
    return roles.ask("tech_head", f"Review this change.{note}\n\nAGENTS.md:\n{agents_md}\n\nSCANNER REPORTS:\n"
                                  f"{json.dumps(reports)[:8000]}\n\nDIFF:\n{cut}", "techreview")

def verdict(rule_findings, ai, diff):
    """(findings, dropped count, blocked). The file check stops a model reporting problems in code it never saw."""
    allowed, kept, dropped = files_in(diff), list(rule_findings), 0
    for f in (ai or {}).get("findings") or []:
        if f.get("file") in allowed:
            kept.append(f)
        else:
            dropped += 1
    rank = {"blocker": 0, "major": 1, "minor": 2}
    kept.sort(key=lambda f: rank.get(f.get("severity"), 3))
    return kept, dropped, any(f.get("severity") == "blocker" for f in kept)

def markdown(findings, ai, dropped, blocked, skipped=""):
    head = "changes needed before merge" if blocked else ("notes to read" if findings else "no findings")
    lines = [f"**Tech Head review: {head}.**", ""]
    if (ai or {}).get("summary"):
        lines += [str(ai["summary"]), ""]
    if skipped:
        lines += [f"AI review skipped: {skipped}. Code rules still ran.", ""]
    for f in findings[:20]:
        lines.append(f"- **{f.get('severity')} / {f.get('category')}** `{f.get('file')}:{f.get('line')}`: "
                     f"{f.get('problem')} Fix: {f.get('fix')}")
    if dropped:
        lines += ["", f"{dropped} finding(s) dropped because they named files outside this change."]
    return "\n".join(lines)

def git_diff(repo, base, head):
    return subprocess.run(["git", "-C", repo, "diff", "--no-color", f"{base}...{head}"], check=True,
                          capture_output=True, text=True).stdout

def main():
    import sources
    from radar import comment, safe_text
    diff = git_diff(os.environ["PR_DIR"], os.environ["BASE_SHA"], os.environ["HEAD_SHA"])
    found, ai, skipped = rules(diff), None, ""
    try:
        ai = review(diff, read_reports(os.environ.get("REPORTS_DIR")), sources.agents_md())
    except Exception as e:                         # no key, budget, or a model outage: code rules still stand
        skipped = str(e)[:200]
    findings, dropped, blocked = verdict(found, ai, diff)
    comment(int(os.environ["PR_NUMBER"]), safe_text(markdown(findings, ai, dropped, blocked, skipped)))
    if blocked and os.environ.get("TECH_HEAD_MODE", "advisory") == "blocking":
        sys.exit("Tech Head found a blocker")

if __name__ == "__main__":
    main()
