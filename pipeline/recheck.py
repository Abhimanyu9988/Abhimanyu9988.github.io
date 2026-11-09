#!/usr/bin/env python3
"""Rechecks the labs behind published ShowMerit articles, so readers can see each one still works.

Free recheck (recheck.yml, the 1st and 16th of each month): every lab with a check.sh runs it twice on a GitHub runner,
once with the versions the article used and once with the newest releases of the agent, charts and images the lab pins.
Every external link each article cites is fetched again. No AWS and no model.
Paid recheck (recheck-aws.yml, the first Tuesday of each month): the labs due that month in a 3-month cycle run on the AWS
lab account one after another, with the newest versions, through lab.py's own runner. Each lab starts only if the monthly
budget has room for it. No model.

Inputs: a checkout of the showmerit repo (articles and labs), GitHub releases, Helm repository indexes, Docker Hub tags,
the results the workflow jobs saved, and the spend record on the analytics branch.
Outputs: analytics/rechecks.json on the public analytics branch (dates, versions, pass or fail and run links, nothing
private), which showmerit's build reads; and for a failure, one private issue in team-admin assigned to Abhi, or a comment
on the open one for that article.
Must not: contact or file anything with a vendor or an upstream project, call a model, run lab code (the workflow runs
check.sh in a job with no secrets), write anywhere but those two places, or fail a run because a lab failed.
"""
import datetime, hashlib, ipaddress, json, os, pathlib, re, socket, sys, time, urllib.error, urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lab_check import SAFE

SITE_URL = "https://showmerit.com/abhimanyu/articles"
SITE_REPO = "Abhimanyu9988/showmerit"
ADMIN_REPO = "Abhimanyu9988/team-admin"            # private: error lines and failure detail go only here
OWNER = "Abhimanyu9988"
RESULTS = "analytics/rechecks.json"
LABEL = "recheck"
CYCLE_START = (2026, 12)                           # month 0 of the paid cycle
CYCLE_MONTHS = 3                                   # each lab runs on AWS once a quarter
RUN_WEEKDAY = 1                                    # Tuesday: the paid run is the first Tuesday of the month
UA = {"User-Agent": "showmerit-recheck"}
AGENT_RX = re.compile(r"https://github\.com/([\w.-]+/[\w.-]+)/releases/download/v?(\d+\.\d+\.\d+)/([\w.-]+\.jar)")
REPO_ADD = re.compile(r"helm\s+repo\s+add\s+([\w.-]+)\s+(https://[^\s\"']+)")
VERSION_ARG = re.compile(r"--version[ =]\"?(\$\{?\w+\}?|\d+\.\d+\.\d+)\"?")
IMAGE_RX = re.compile(r"(?:\bimage:\s*|\bimage\s*=\s*|--image[= ])[\"']?((?:[a-z0-9.-]+\.[a-z]{2,}/)?[a-z0-9][a-z0-9._/-]*):(\d[\w.-]*)")
TAG_RX = re.compile(r"(\d+(?:[._]\d+)*)((?:-[a-z0-9.]+)*)")
FAILED_LINE = re.compile(r"CHECK FAILED|HERO_RESULT: not_|error|fail|refused|denied|timed out|not found|exception", re.I)
RUN_URL = re.compile(r"https://github\.com/Abhimanyu9988/Abhimanyu9988\.github\.io/actions/runs/\d+")

def resolve(host):
    """Every address a host name resolves to. Tests replace this."""
    return [a[4][0] for a in socket.getaddrinfo(host, None)]

def public(url):
    """True only for an http(s) URL whose host resolves to public addresses. An article's link must never make the
    runner reach its own network, a private range or a cloud metadata address."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return False
    try:
        found = resolve(parts.hostname)
    except OSError:
        return True                            # an unknown name reaches nothing, and the fetch reports it as broken
    return bool(found) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in found)

class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not public(newurl):
            raise urllib.error.URLError(f"redirect to a non-public address refused: {urllib.parse.urlsplit(newurl).hostname}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)

_OPENER = urllib.request.build_opener(_PublicRedirects)

def fetch(url, headers=None, method="GET", timeout=30):
    """(status, final url, body bytes). A redirect may only lead to a public address. Tests replace this, so no test
    reaches the network."""
    req = urllib.request.Request(url, method=method, headers={**UA, **(headers or {})})
    with _OPENER.open(req, timeout=timeout) as r:
        return r.status, r.geturl(), r.read()

def fetch_json(url, headers=None):
    return json.loads(fetch(url, headers)[2])

def gh_headers():
    tok = os.environ.get("GH_READ_TOKEN") or os.environ.get("GITHUB_TOKEN")
    return {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {tok}"} if tok else {})}

# ---------- articles and labs ----------
def front(text):
    """Top-level scalar keys of a Markdown front matter, without a YAML library. Lists and nested keys are skipped."""
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    out, key = {}, None
    for line in (m[1] if m else "").split("\n"):
        top = re.match(r"([A-Za-z_]+):\s*(.*)$", line)
        if top:
            key = top[1]
            out[key] = top[2]
        elif key and line.startswith("  ") and not line.lstrip().startswith("- "):
            out[key] = (out[key] + " " + line.strip()).strip()
        else:
            key = None if not line.startswith(" ") else key
    for k, v in out.items():
        if len(v) > 1 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1].replace("''", "'") if v[0] == "'" else v[1:-1]
        out[k] = v.strip()
    return out

def articles(site):
    """Every article with a lab in the showmerit repo, oldest first, so a new article never moves an older lab's week."""
    site, found = pathlib.Path(site), []
    for path in sorted((site / "content/abhimanyu/articles").glob("*.md")):
        meta = front(path.read_text())
        m = re.search(r"/tree/main/labs/([\w.-]+)/?$", meta.get("lab_url", ""))
        if not m or not (site / "labs" / m[1]).is_dir():
            continue
        found.append({"slug": path.stem, "title": meta.get("title") or path.stem, "date": meta.get("date", ""),
                      "lab": m[1], "has_check": (site / "labs" / m[1] / "check.sh").is_file()})
    return sorted(found, key=lambda a: (a["date"], a["slug"]))

def lab_files(site, lab):
    """The files a lab run needs. providers.tf is the reader's copy of what the runner adds, and evidence is output."""
    root = pathlib.Path(site) / "labs" / lab
    files = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_file() and SAFE.fullmatch(rel) and rel != "providers.tf":
            files.append({"path": rel, "content": p.read_text()})
    return files

# ---------- pinned versions ----------
def pins(files):
    """What a lab pins: the Java agent it downloads, each Helm chart version and each tagged image. Each pin keeps the
    file and line it was read from, so only that version is changed."""
    found, repos = [], {}
    for f in files:
        for m in REPO_ADD.finditer(f["content"]):
            repos.setdefault(m[1], m[2])
    for f in files:
        lines = f["content"].split("\n")
        for i, line in enumerate(lines):
            for m in AGENT_RX.finditer(line):
                found.append({"kind": "agent", "key": "agent", "repo": m[1], "jar": m[3], "version": m[2],
                              "path": f["path"], "line": i})
            for m in IMAGE_RX.finditer(line):
                found.append({"kind": "image", "key": m[1].rsplit("/", 1)[-1], "image": m[1], "version": m[2],
                              "path": f["path"], "line": i})
            if not f["path"].endswith(".sh"):
                continue
            for m in VERSION_ARG.finditer(line):
                start = i
                while start and lines[start - 1].rstrip().endswith("\\"):
                    start -= 1
                end = i
                while end < len(lines) - 1 and lines[end].rstrip().endswith("\\"):
                    end += 1
                command = " ".join(lines[start:end + 1])
                ref = next((c for c in re.findall(r"\b([\w.-]+)/([\w.-]+)\b", command) if c[0] in repos), None)
                if not ref:
                    continue
                where, version = i, m[1]
                if version.startswith("$"):           # --version "$VAR", with VAR="${VAR:-1.2.3}" set in the same file
                    var = version.strip("${}")
                    d = next(((j, x) for j, x in enumerate(lines) if re.match(
                        rf"\s*(?:export\s+)?{var}=\"?(?:\$\{{\w+:-)?\d+\.\d+\.\d+", x)), None)
                    if not d:
                        continue
                    where, version = d[0], re.search(r"\d+\.\d+\.\d+", d[1])[0]
                found.append({"kind": "chart", "key": f"{ref[1]} chart", "repo_url": repos[ref[0]], "chart": ref[1],
                              "version": version, "path": f["path"], "line": where})
    seen, out = set(), []
    for p in found:
        mark = (p["path"], p["line"], p["kind"], p["key"])
        if mark not in seen:
            seen.add(mark)
            out.append(p)
    return out

def versions(found, changes=None):
    """{name: version} for display, agent first, then charts, then images."""
    order = {"agent": 0, "chart": 1, "image": 2}
    out = {}
    for p in sorted(found, key=lambda p: order[p["kind"]]):
        out.setdefault(p["key"], (changes or {}).get(p["key"], {}).get("new") or p["version"])
    return out

def semver(v):
    return tuple(int(x) for x in re.findall(r"\d+", v))

def newest_agent(pin):
    """(version, sha256 or None). The checksum lets a lab that verifies its download keep doing so."""
    rel = fetch_json(f"https://api.github.com/repos/{pin['repo']}/releases/latest", gh_headers())
    tag = str(rel.get("tag_name", "")).lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", tag):
        raise ValueError(f"{pin['repo']}: latest release {tag!r} is not a version")
    asset = next((a for a in rel.get("assets") or [] if a.get("name") == pin["jar"]), {})
    digest = str(asset.get("digest") or "")
    return tag, digest.split(":", 1)[1] if digest.startswith("sha256:") else None

def jar_sha256(pin, version):
    url = f"https://github.com/{pin['repo']}/releases/download/v{version}/{pin['jar']}"
    return hashlib.sha256(fetch(url, timeout=120)[2]).hexdigest()

def newest_chart(pin):
    """The highest stable version in the chart's Helm repository index. Read line by line: no YAML library."""
    text = fetch(pin["repo_url"].rstrip("/") + "/index.yaml", timeout=60)[2].decode("utf-8", "replace")
    got, inside = [], False
    for line in text.split("\n"):
        if re.match(r"  [\w.-]+:\s*$", line):
            inside = line.strip()[:-1] == pin["chart"]
        elif inside:
            m = re.match(r"    version:\s*[\"']?(\d+\.\d+\.\d+)[\"']?\s*$", line)
            if m:
                got.append(m[1])
    if not got:
        raise ValueError(f"no stable version of {pin['chart']} in its index")
    return max(got, key=semver)

def tag_shape(tag):
    """'17.0.13_11-jdk' -> ('N.N.N_N', '-jdk', 17). A newer tag must keep the same shape, suffix and major version:
    a new major release of a base image is a different setup from the one the article describes."""
    m = TAG_RX.fullmatch(tag)
    if not m:
        return None
    return re.sub(r"\d+", "N", m[1]), m[2], int(re.match(r"\d+", m[1])[0])

def hub_repo(image):
    """The Docker Hub repository for an image, or None for a registry this does not look up."""
    if image.startswith("public.ecr.aws/docker/library/"):    # AWS's mirror of the Docker official images
        return "library/" + image.rsplit("/", 1)[1]
    if "." in image.split("/")[0]:
        return None
    return image if "/" in image else "library/" + image

def ecr_has(image, tag):
    token = fetch_json("https://public.ecr.aws/token/?scope=" + urllib.parse.quote(f"repository:{image.split('/', 1)[1]}:pull"))["token"]
    accept = ", ".join(["application/vnd.oci.image.index.v1+json", "application/vnd.docker.distribution.manifest.list.v2+json",
                        "application/vnd.docker.distribution.manifest.v2+json", "application/vnd.oci.image.manifest.v1+json"])
    try:
        return fetch(f"https://public.ecr.aws/v2/{image.split('/', 1)[1]}/manifests/{tag}",
                     {"Authorization": f"Bearer {token}", "Accept": accept}, method="HEAD")[0] == 200
    except urllib.error.HTTPError:
        return False

def newest_image(pin):
    shape, repo = tag_shape(pin["version"]), hub_repo(pin["image"])
    if not shape or not repo:
        return None
    filt = shape[1] or f"{shape[2]}."
    tags, url = [], (f"https://hub.docker.com/v2/namespaces/{repo.split('/')[0]}/repositories/{repo.split('/')[1]}/tags"
                     f"?page_size=100&ordering=last_updated&name={urllib.parse.quote(filt)}")
    for _ in range(3):
        page = fetch_json(url)
        tags += [t.get("name", "") for t in page.get("results") or []]
        url = page.get("next")
        if not url:
            break
    fits = sorted({t for t in tags if tag_shape(t) == shape} | {pin["version"]}, key=semver, reverse=True)
    for tag in fits[:5]:
        if tag == pin["version"] or not pin["image"].startswith("public.ecr.aws/") or ecr_has(pin["image"], tag):
            return tag
    return pin["version"]

def newest(found):
    """{key: {"old", "new", ...}} for every pin that has a newer release, and the keys that could not be looked up."""
    changes, missed, cache = {}, [], {}
    for p in found:
        if p["key"] in changes or p["key"] in cache:
            continue
        try:
            if p["kind"] == "agent":
                new, digest = newest_agent(p)
                if new != p["version"] and not digest:
                    digest = jar_sha256(p, new)
                cache[p["key"]] = (new, digest)
            elif p["kind"] == "chart":
                cache[p["key"]] = (newest_chart(p), None)
            else:
                cache[p["key"]] = (newest_image(p), None)
        except Exception as e:
            print(f"::warning title=recheck::could not look up the newest {p['key']}: {' '.join(str(e).split())[:160]}")
            missed.append(p["key"])
            cache[p["key"]] = (None, None)
        new, digest = cache[p["key"]]
        if new and semver(new) > semver(p["version"]):
            changes[p["key"]] = {"old": p["version"], "new": new, **({"sha256": digest} if digest else {})}
    return changes, missed

def apply(files, found, changes):
    """The lab's files with each changed pin replaced where it was read, and an agent's checksum with it."""
    files = [dict(f) for f in files]
    by_path = {f["path"]: f for f in files}
    for p in found:
        ch = changes.get(p["key"])
        if not ch or p["version"] != ch["old"]:
            continue
        if p["kind"] == "image":
            for f in files:
                f["content"] = re.sub(re.escape(f"{p['image']}:{ch['old']}") + r"(?![\w.-])", f"{p['image']}:{ch['new']}", f["content"])
            continue
        f = by_path[p["path"]]
        lines = f["content"].split("\n")
        if p["kind"] == "agent":
            lines[p["line"]] = re.sub(rf"/download/v?{re.escape(ch['old'])}/", f"/download/v{ch['new']}/", lines[p["line"]])
        else:
            lines[p["line"]] = re.sub(rf"(?<![\d.]){re.escape(ch['old'])}(?![\d.])", ch["new"], lines[p["line"]], count=1)
        f["content"] = "\n".join(lines)
    agent = next((p for p in found if p["kind"] == "agent" and changes.get("agent", {}).get("sha256")), None)
    if agent:                                      # a lab that checks the jar's sha256 must check the new jar's
        for f in files:
            if re.search(r"\b[0-9a-f]{64}\b", f["content"]) and AGENT_RX.search(f["content"]):
                f["content"] = re.sub(r"\b[0-9a-f]{64}\b", changes["agent"]["sha256"], f["content"], count=1)
    return files

# ---------- plan, prepare, outcome ----------
def plan(site, only=None):
    """What to recheck, with both version sets. The matrix runs check.sh twice only when a newer version exists.
    `only` limits it to some article slugs, given as a list or as one comma-separated string."""
    only = set(only.split(",") if isinstance(only, str) else only or [])
    out = []
    for a in articles(site):
        if only and a["slug"] not in only:
            continue
        found = pins(lab_files(site, a["lab"]))
        changes, missed = newest(found)
        out.append({**a, "article": versions(found), "newest": versions(found, changes), "changes": changes, "missed": missed})
    return out

def matrix(planned):
    rows = []
    for a in planned:
        if a["has_check"]:
            rows.append({"slug": a["slug"], "set": "article"})
            if a["changes"]:
                rows.append({"slug": a["slug"], "set": "newest"})
    return rows

def prepare(site, entry, which, dest):
    """Writes the lab, with the newest versions if asked, into dest. Returns the file list for lab.py."""
    files = lab_files(site, entry["lab"])
    if which == "newest":
        files = apply(files, pins(files), entry["changes"])
    dest = pathlib.Path(dest)
    for f in files:
        target = dest / f["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f["content"])
        if target.suffix == ".sh":
            target.chmod(0o755)
    return files

def redact(text):
    text = re.sub(r"\b\d{12}\b", "<account-id>", str(text or ""))
    text = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "<access-key>", text)
    return re.sub(r"(?i)\b(token|password|secret)(\s*[:=]\s*)\S+", r"\1\2<redacted>", text)

def failure(log):
    """(step, error lines) from a check's output: the last '== ' heading before the failure, and the lines that say why."""
    lines = [l.rstrip() for l in redact(log).split("\n") if l.strip()]
    stop = next((i for i in range(len(lines) - 1, -1, -1) if "CHECK FAILED" in lines[i]), len(lines) - 1)
    step = next((lines[i].strip("= ").strip() for i in range(stop, -1, -1) if lines[i].startswith("== ")), "")
    near = lines[max(0, stop - 40):stop + 1]
    picked = [l for l in near if FAILED_LINE.search(l)][-10:] or near[-10:]
    return step[:160] or "unknown", [l[:240] for l in picked]

def outcome(slug, which, passed, log):
    step, errors = ("", []) if passed else failure(log)
    return {"slug": slug, "set": which, "passed": bool(passed), "step": step, "errors": errors}

# ---------- links ----------
def cited_links(markdown):
    """External links in the article text. Code blocks are commands, not citations, so they are left out."""
    text = re.sub(r"```.*?```", "", markdown, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    got = re.findall(r"\]\((https?://[^)\s]+)\)", text) + re.findall(r"<(https?://[^>\s]+)>", text)
    return sorted({u for u in got if not (urllib.parse.urlsplit(u).hostname or "showmerit.com").endswith("showmerit.com")})

def link_status(url, tries=2, pause=10):
    """'ok', 'blocked' (the site refuses robots, so it is not judged), 'skipped' (not a public address, so it is never
    fetched) or 'broken: why'. A link is broken only if it fails twice, so one slow answer never opens an issue."""
    if not public(url):
        return "skipped"
    why = ""
    for n in range(tries):
        for method in ("HEAD", "GET"):            # some servers refuse HEAD but answer GET
            try:
                if fetch(url, method=method, timeout=25)[0] < 400:
                    return "ok"
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 429, 999) and method == "GET":
                    return "blocked"
                why = f"HTTP {e.code}"
            except Exception as e:
                why = " ".join(str(getattr(e, "reason", e)).split())[:80]
        if n < tries - 1:
            time.sleep(pause)
    return f"broken: {why}"

def links(site, only=None):
    out = {}
    for a in articles(site):
        if only and a["slug"] != only:
            continue
        text = (pathlib.Path(site) / "content/abhimanyu/articles" / f"{a['slug']}.md").read_text()
        got = {u: link_status(u) for u in cited_links(text)}
        out[a["slug"]] = {"checked": len(got), "broken": {u: s[8:] for u, s in got.items() if s.startswith("broken")},
                          "blocked": sorted(u for u, s in got.items() if s == "blocked"),
                          "skipped": sorted(u for u, s in got.items() if s == "skipped")}
    return out

# ---------- the paid cycle ----------
def cycle_month(day):
    """0, 1 or 2: where a month sits in the 3-month cycle that starts in December 2026."""
    return (day.year * 12 + day.month - (CYCLE_START[0] * 12 + CYCLE_START[1])) % CYCLE_MONTHS

def run_day(year, month):
    """The first Tuesday of a month: the paid run's day."""
    first = datetime.date(year, month, 1)
    return first + datetime.timedelta(days=(RUN_WEEKDAY - first.weekday()) % 7)

def due(listed, today, wanted="", scheduled=False):
    """The articles whose labs get their paid recheck this month, oldest first. Lab i belongs to month i % 3 of the
    cycle, so 8 labs run 3, 3 and 2 a month and each runs once a quarter. A new article joins its month without moving
    the others. The schedule fires every Tuesday, so a scheduled run only works on the first one."""
    if wanted:
        return [a for a in listed if a["slug"] == wanted]
    if scheduled and today != run_day(today.year, today.month):
        return []
    return [a for i, a in enumerate(listed) if i % CYCLE_MONTHS == cycle_month(today)]

def schedule(listed, today=None, months=CYCLE_MONTHS):
    """[(run day, [slugs])] for the next paid runs, for the run summary."""
    today = today or datetime.date.today()
    rows, (y, m) = [], (today.year, today.month)
    while len(rows) < months:
        day = run_day(y, m)
        if day >= today:
            rows.append((day.isoformat(), [a["slug"] for a in due(listed, day)]))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return rows

def budget_room(earlier=0, slug="this lab"):
    """(ok, message) before one lab. The labs that ran before it in the same run are recorded only at the end, so each
    counts at the lab estimate here. A lab may start only if the total, with it, stays inside the limit."""
    import budget
    extra = budget.AWS_LAB_USD * (earlier + 1)
    try:
        total, cap = budget.spent()[0], budget.limit()
    except Exception as e:
        return False, f"Skipped {slug}: the month's spend could not be read ({' '.join(str(e).split())[:120]}), so nothing was spent."
    earlier_text = f", plus about ${budget.AWS_LAB_USD * earlier:.2f} for the {earlier} lab(s) before it in this run" if earlier else ""
    if total + extra > cap:
        return False, (f"Skipped {slug}: ${total:.2f} of the ${cap:.2f} monthly budget is spent{earlier_text}, and this lab "
                       f"would add about ${budget.AWS_LAB_USD:.2f}. Nothing was started for it. It runs again next quarter, "
                       "or by hand when the budget allows.")
    return True, f"Budget ok for {slug}: ${total:.2f} of ${cap:.2f} spent{earlier_text}, and this lab adds about ${budget.AWS_LAB_USD:.2f}."

# ---------- report ----------
def admin(method, path, body=None):
    """Writes to the private team-admin repo with its own token: new issues, comments and the one label, nothing else."""
    allowed = {("GET", r"issues\?labels=recheck&state=open&per_page=100"), ("POST", r"issues"), ("POST", r"issues/\d+/comments"),
               ("POST", r"labels"), ("GET", r"labels/recheck")}
    if not any(m == method and re.fullmatch(p, path) for m, p in allowed):
        raise PermissionError(f"{method} {path}: not allowed for the recheck")
    status, _, data = admin_send(method, f"https://api.github.com/repos/{ADMIN_REPO}/{path}", body)
    return json.loads(data or b"null")

def admin_send(method, url, body):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={**UA, "Authorization": f"Bearer {os.environ['ADMIN_TOKEN']}",
                                          "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.geturl(), r.read()

def shown(vs):
    return ", ".join(f"{k} {v}" for k, v in vs.items()) or "the lab as published"

def issue_text(entry, kind, results, broken, run_url, day):
    """The private failure report. Lab output is untrusted text, so it is neutralized and fenced, and never followed."""
    from radar import neutralize
    where = "on the AWS lab account" if kind == "aws" else "on a GitHub runner (the lab's free check.sh)"
    lines = [f"Recheck {where} on {day}.", "",
             f"**Article:** {entry['title']} ({SITE_URL}/{entry['slug']}/)",
             f"**Lab:** labs/{entry['lab']} (https://github.com/{SITE_REPO}/tree/main/labs/{entry['lab']})"]
    for r in results:
        vs = entry["article"] if r["set"] == "article" else entry["newest"]
        label = "the article's versions" if r["set"] == "article" else "the newest versions"
        lines.append(f"**{'Passed' if r['passed'] else 'Failed'} with {label}:** {shown(vs)}")
    for r in [r for r in results if not r["passed"]]:
        errors = "\n".join(neutralize(l).replace("```", "'''") for l in r["errors"]) or "(no output)"
        lines += ["", f"**Failing step ({r['set']} versions):** {neutralize(r['step'])}", "```", errors, "```"]
    if broken:
        lines += ["", "**Cited links that failed twice:**"] + [f"- {u} ({neutralize(why)})" for u, why in sorted(broken.items())]
    if entry.get("missed"):
        lines += ["", f"Could not look up the newest version of: {', '.join(entry['missed'])}. Those stayed as the article pins them."]
    lines += ["", f"**Run:** {run_url}", "",
              "Nothing was sent to any vendor or project. You decide what happens next."]
    return "\n".join(lines)

def file_failure(entry, text):
    """One open issue per article. A repeat failure comments on it instead of opening another."""
    title = f"Recheck failed: {entry['slug']}"
    try:
        admin("GET", "labels/recheck")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            admin("POST", "labels", {"name": LABEL, "color": "d93f0b", "description": "A published lab failed its recheck"})
    open_ = admin("GET", "issues?labels=recheck&state=open&per_page=100") or []
    found = next((i for i in open_ if i.get("title") == title and "pull_request" not in i), None)
    if found:
        admin("POST", f"issues/{int(found['number'])}/comments", {"body": "It failed again.\n\n" + text})
        return "commented", found["number"]
    made = admin("POST", "issues", {"title": title, "body": text, "labels": [LABEL], "assignees": [OWNER]})
    return "opened", made.get("number")

REPORT_ISSUE = "Recheck report failed"

def to_summary(text):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n\n")

def report_problem(kind, what, run_url):
    """The report steps stay green, so a crash or a lost save must never be silent: it goes to the private issue, or
    when that fails too, to the run summary and log. Returns where it went."""
    from radar import neutralize
    run = run_url if RUN_URL.fullmatch(run_url or "") else "(no run link)"
    text = "\n".join([f"The {kind} recheck report did not finish. Results, issues or the AWS spend estimate may be missing for this run.",
                      "", "```", neutralize(str(what)).replace("```", "'''")[-1500:], "```", "", f"**Run:** {run}"])
    if os.environ.get("ADMIN_TOKEN"):
        try:
            open_ = admin("GET", "issues?labels=recheck&state=open&per_page=100") or []
            found = next((i for i in open_ if i.get("title") == REPORT_ISSUE and "pull_request" not in i), None)
            if found:
                admin("POST", f"issues/{int(found['number'])}/comments", {"body": "It happened again.\n\n" + text})
            else:
                admin("POST", "issues", {"title": REPORT_ISSUE, "body": text, "labels": [LABEL], "assignees": [OWNER]})
            print(f"::warning title=recheck::the report did not finish; see the private issue")
            return "issue"
        except Exception as e:
            text += f"\n\nThe private issue could not be written either: {' '.join(str(e).split())[:200]}"
    last = ([l for l in str(what).split("\n") if l.strip()] or ["unknown error"])[-1]
    print(f"::error title=recheck report::The {kind} recheck report did not finish: {' '.join(neutralize(last).split())[:300]}. See the run summary.")
    to_summary("## Recheck report problem\n\n" + text)
    return "summary"

def merge(saved, entry, kind, results, link_result, run_url, day):
    """The public record for one article. Only code-made fields: no lab output and no error text."""
    rec = saved.setdefault("articles", {}).setdefault(entry["slug"], {})
    if kind == "aws":
        r = results[0]
        rec["aws"] = {"date": day, "run": run_url, "versions": entry["newest"], "passed": r["passed"]}
        return rec
    free = {"date": day, "run": run_url, "results": []}
    for r in results:
        free["results"].append({"set": r["set"], "versions": entry["article"] if r["set"] == "article" else entry["newest"],
                                "passed": r["passed"]})
    if entry["has_check"] and not entry["changes"]:
        free["newest_is_article"] = True
    if link_result is not None:
        free["links"] = {"checked": link_result["checked"], "broken": len(link_result["broken"])}
    rec["free"] = free
    return rec

def save_results(update):
    """Reads, changes and writes rechecks.json, again if a parallel report wrote first."""
    import analytics
    analytics.ensure_branch()
    for n in range(3):
        text, sha = analytics.read_file(RESULTS)
        data = json.loads(text) if text.strip() else {"articles": {}}
        update(data)
        data["updated"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        try:
            analytics.write_file(RESULTS, json.dumps(data, indent=1, sort_keys=True) + "\n", sha, "recheck: results")
            return data
        except RuntimeError as e:
            if n == 2 or not re.search(r"HTTP (409|422)", str(e)):
                raise
    return None

def valid_results(planned, results):
    """Only the free results the plan expects, one per lab and version set, in the expected form. The report job holds
    tokens, so a file a lab's check.sh left behind can't add or change a record."""
    expected = {(r["slug"], r["set"]) for r in matrix(planned)}
    kept, seen = [], set()
    for r in results:
        key = (r.get("slug"), r.get("set")) if isinstance(r, dict) else None
        if key not in expected or key in seen or not isinstance(r.get("passed"), bool) or not isinstance(r.get("step"), str) \
                or not isinstance(r.get("errors"), list) or not all(isinstance(x, str) for x in r["errors"]):
            print(f"::warning title=recheck::left out an unexpected result file: {str(key)[:120]}")
            continue
        seen.add(key)
        kept.append({"slug": key[0], "set": key[1], "passed": r["passed"], "step": r["step"][:160], "errors": [x[:240] for x in r["errors"][:10]]})
    return kept

def report(kind, planned, results, link_results, run_url, day=None):
    """Saves the public results and files the private failures. Every write is tried, a failed one is reported in the
    log, and the run stays green. Returns counts for the verdict."""
    day = day or datetime.date.today().isoformat()
    if not RUN_URL.fullmatch(run_url or ""):
        raise SystemExit(f"not a run link of this repo: {run_url!r}")
    counts = {"passed": 0, "failed": 0, "broken_links": 0, "issues": 0}
    updates, failures = [], []
    for entry in planned:
        mine = [r for r in results if r["slug"] == entry["slug"]]
        if kind == "aws" and not mine:
            continue
        if kind == "free" and entry["has_check"] and not entry["changes"]:
            mine = [r for r in mine if r["set"] == "article"]
        if kind == "free" and entry["has_check"] and not any(r["set"] == "article" for r in mine):
            mine.append({"slug": entry["slug"], "set": "article", "passed": False, "step": "the check job",
                         "errors": ["the check job ended without saving a result"]})
        if kind == "free" and entry["changes"] and entry["has_check"] and not any(r["set"] == "newest" for r in mine):
            mine.append({"slug": entry["slug"], "set": "newest", "passed": False, "step": "the check job",
                         "errors": ["the check job ended without saving a result"]})
        lr = (link_results or {}).get(entry["slug"])
        broken = (lr or {}).get("broken") or {}
        counts["passed"] += sum(r["passed"] for r in mine)
        counts["failed"] += sum(not r["passed"] for r in mine)
        counts["broken_links"] += len(broken)
        updates.append((entry, mine, lr))
        if any(not r["passed"] for r in mine) or broken:
            failures.append((entry, issue_text(entry, kind, mine, broken, run_url, day)))
    try:
        save_results(lambda data: [merge(data, e, kind, m, lr, run_url, day) for e, m, lr in updates])
        print(f"saved {len(updates)} result(s) to {RESULTS}")
    except Exception as e:
        report_problem(kind, f"The results were not saved to {RESULTS}: {' '.join(str(e).split())[:300]}", run_url)
    for entry, text in failures:
        why = "there is no team-admin token"
        if os.environ.get("ADMIN_TOKEN"):
            try:
                what, number = file_failure(entry, text)
                counts["issues"] += 1
                print(f"{entry['slug']}: {what} the private issue")
                continue
            except Exception as e:
                why = f"the private issue could not be written: {' '.join(str(e).split())[:200]}"
        print(f"::warning title=recheck::{entry['slug']} failed, and {why}. The details are in the run summary")
        to_summary(f"## {entry['slug']} failed, not filed\n\n{why[0].upper() + why[1:]}.\n\n{text}")
    return counts

def summary(planned, counts=None, extra=""):
    rows = ["| Article | Free check | Article's versions | Newest versions |", "| --- | --- | --- | --- |"]
    for a in planned:
        rows.append(f"| {a['slug']} | {'yes' if a['has_check'] else 'no, paid only'} | {shown(a['article'])} | {shown(a['newest'])} |")
    text = "\n".join(rows) + ("\n\n" + extra if extra else "")
    if counts:
        text += f"\n\nPassed {counts['passed']}, failed {counts['failed']}, broken links {counts['broken_links']}, issues filed {counts['issues']}."
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(text + "\n")
    print(text)

def output(**values):
    path = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{k}={v}\n" for k, v in values.items())
    if path:
        with open(path, "a") as f:
            f.write(lines)
    print(lines, end="")

def aws_result(slug, path):
    """The paid run's lab.py result as a recheck outcome. Only a full reproduce-and-fix counts as working."""
    try:
        res = json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError):
        return {"slug": slug, "set": "newest", "passed": False, "step": "the AWS job", "aws_used": True,
                "errors": ["the lab job ended without saving a result"]}
    passed = res.get("status") == "reproduced_and_fixed"
    last = res.get("last_failure") or {}
    errors = [l for l in redact(last.get("output") or res.get("error") or "").split("\n") if l.strip()]
    picked = [l for l in errors if FAILED_LINE.search(l)][-10:] or errors[-10:]
    return {"slug": slug, "set": "newest", "passed": passed, "step": "" if passed else str(last.get("step") or res.get("status") or "unknown")[:160],
            "errors": [] if passed else [l[:240] for l in picked], "aws_used": bool(res.get("aws_used")),
            "destroyed": res.get("destroyed")}

def aws_results(planned, folder):
    """(results, skipped) for each lab of the run, from the folder each lab job uploaded. A lab stopped by the budget,
    or never started, is skipped: no issue and no spend. A lab that started but saved no result counts as a failure."""
    results, skipped = [], []
    for entry in planned:
        where = pathlib.Path(folder) / f"lab-{entry['slug']}"
        stop = where / "budget-stop.json"
        if stop.is_file():
            skipped.append((entry["slug"], json.loads(stop.read_text()).get("message", "the budget had no room")))
        elif (where / "started.json").is_file() or (where / "result.json").is_file():
            results.append(aws_result(entry["slug"], where / "result.json"))
        else:
            skipped.append((entry["slug"], "its job never started, so nothing was spent"))
    return results, skipped

def main(argv):
    cmd, args = argv[0], argv[1:]
    if cmd == "plan":                          # plan <site> <out.json> [slug]
        planned = plan(args[0], args[2] if len(args) > 2 else None)
        pathlib.Path(args[1]).write_text(json.dumps(planned, indent=1))
        rows = matrix(planned)
        summary(planned)
        output(matrix=json.dumps(rows), checks=len(rows))
    elif cmd == "prepare":                     # prepare <site> <plan.json> <slug> <article|newest> <dest> [lab.json]
        entry = next(a for a in json.loads(pathlib.Path(args[1]).read_text()) if a["slug"] == args[2])
        if args[3] not in ("article", "newest"):
            raise SystemExit("the version set is article or newest")
        files = prepare(args[0], entry, args[3], args[4])
        if len(args) > 5:
            pathlib.Path(args[5]).write_text(json.dumps({"slug": entry["slug"], "summary": f"Recheck of {entry['title']}",
                                                         "expected": "HERO_RESULT: fixed", "files": files}))
        print("\n".join(f"{k}: {v}" for k, v in (entry["newest"] if args[3] == "newest" else entry["article"]).items()))
    elif cmd == "outcome":                     # outcome <slug> <set> <success|failure|...> <log> <out.json>
        log = pathlib.Path(args[3]).read_text(errors="replace") if pathlib.Path(args[3]).is_file() else ""
        pathlib.Path(args[4]).write_text(json.dumps(outcome(args[0], args[1], args[2] == "success", log)))
    elif cmd == "links":                       # links <site> <out.json>
        pathlib.Path(args[1]).write_text(json.dumps(links(args[0]), indent=1))
    elif cmd == "due":                         # due <site> [slug] [event]: this month's paid labs
        listed = articles(args[0])
        wanted = args[1] if len(args) > 1 else ""
        if wanted and not re.fullmatch(r"[a-z0-9-]{1,100}", wanted):
            raise SystemExit("the lab must be an article slug")
        got = due(listed, datetime.date.today(), wanted, scheduled=len(args) > 2 and args[2] == "schedule")
        print("Next paid runs:\n" + "\n".join(f"- {d}: {', '.join(s)}" for d, s in schedule(listed)))
        output(labs=json.dumps([{"slug": a["slug"], "earlier": i} for i, a in enumerate(got)]),
               slugs=",".join(a["slug"] for a in got), count=len(got))
    elif cmd == "budget":                      # budget <slug> <labs before it in this run> <out dir>
        ok, message = budget_room(int(args[1]), args[0])
        print(f"::notice title=budget::{message}")
        output(budget="ok" if ok else "stopped")
        if not ok:
            pathlib.Path(args[2]).mkdir(parents=True, exist_ok=True)
            (pathlib.Path(args[2]) / "budget-stop.json").write_text(json.dumps({"slug": args[0], "message": message}))
        path = os.environ.get("GITHUB_STEP_SUMMARY")
        if path:
            with open(path, "a") as f:
                f.write(message + "\n")
    elif cmd == "report":                      # report <free|aws> <plan.json> <results dir> [links.json]
        try:
            report_command(args)
        except Exception:
            import traceback
            report_problem(args[0] if args else "?", traceback.format_exc(limit=6), os.environ.get("RUN_URL", ""))
    else:
        raise SystemExit(f"unknown command {cmd}")

def report_command(args):
    """report <free|aws> <plan.json> <results dir> [links.json]. main() turns any crash into a report_problem."""
    kind, planned = args[0], json.loads(pathlib.Path(args[1]).read_text())
    if kind == "free":
        results = []
        for p in sorted(pathlib.Path(args[2]).glob("*.json")):
            try:
                results.append(json.loads(p.read_text()))
            except ValueError:
                print(f"::warning title=recheck::{p.name} is not JSON, so it was left out")
        results = valid_results(planned, results)
        link_results = json.loads(pathlib.Path(args[3]).read_text()) if len(args) > 3 and pathlib.Path(args[3]).is_file() else None
    else:
        results, skipped = aws_results(planned, args[2])
        link_results = None
        used = [r for r in results if r["aws_used"]]
        if used:
            import budget                  # the lab jobs hold no bot token, so their AWS estimates are recorded here
            budget.EXTRA_AWS.extend([budget.AWS_LAB_USD] * len(used))
            budget.record()
        for r in results:
            if r.get("destroyed") is False:
                print(f"::warning title=recheck::{r['slug']} may not have been destroyed: read its safety-net step")
        for slug, why in skipped:
            print(f"::notice title=recheck::skipped {slug}: {why}")
    counts = report(kind, planned, results, link_results, os.environ.get("RUN_URL", ""))
    extra = ""
    if kind == "aws":
        counts["skipped"] = len(skipped)
        extra = "Skipped: " + ("; ".join(f"{s} ({why})" for s, why in skipped) if skipped else "none")
    summary(planned, counts, extra)
    output(**counts, skipped_labs=",".join(s for s, _ in skipped) if kind == "aws" else "")

if __name__ == "__main__":
    main(sys.argv[1:])
