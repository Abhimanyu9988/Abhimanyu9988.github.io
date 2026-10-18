#!/usr/bin/env python3
"""Replicate one topic issue on the AWS lab account.

  1. Read the topic issue and re-fetch its sources (issue threads, Stack Overflow answers).
  2. Claude writes the lab (Terraform + reproduce.sh + fix.sh + cleanup.sh), ChatGPT reviews it,
     Gemini rules on disagreements (llm.refine).
  3. plan -> check_plan -> apply -> reproduce.sh -> fix.sh. On failure Claude gets the error and
     revises, up to MAX_ATTEMPTS times or until the time budget runs out.
  4. ALWAYS cleanup.sh + terraform destroy. An `active` marker in S3 (with the lab code) lets the
     next run destroy anything a crashed run left behind.

Runs in the aws-lab environment: short-lived AWS credentials and a read-only GitHub token.
AI-written scripts never see the model keys, GitHub tokens or the OIDC token-request variables.
Everything the draft job needs is written to out/.  `lab.py --destroy-only` is the safety-net step.
"""
import html, io, json, os, pathlib, re, shutil, signal, subprocess, sys, tarfile, time
from radar import HOME_REPO, LAB_LIMITS, gh, used_sources
import check_plan, llm

OUT, WORK = pathlib.Path("out"), pathlib.Path("work/lab")
RUN_ID = os.environ.get("RUN_ID", "local")
DRY = os.environ.get("DRY_RUN", "").lower() == "true"
REGION = os.environ.get("AWS_REGION", "eu-central-1")
BUCKET = os.environ.get("TF_STATE_BUCKET", "")
START, WORK_BUDGET, MAX_ATTEMPTS = time.time(), 70 * 60, 3
HIDE = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GH_READ_TOKEN", "BOT_TOKEN", "GITHUB_TOKEN",
        "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL")
MAINTAINERS = {"OWNER", "MEMBER", "COLLABORATOR"}
EVIDENCE = []

LAB_RULES = f"""{LAB_LIMITS}

Files you write (flat directory; Kubernetes manifests may go in k8s/):
- main.tf (plus any other *.tf). The runner adds _runner.tf, which declares the S3 backend, the default
  `provider "aws"` (region = var.region, default tags) and the variables `region`, `lab_name` and
  `permissions_boundary`. Do NOT declare a backend, an unaliased aws provider, or those variables. Prefix every
  resource name with var.lab_name. IAM roles must be named "hero-lab-${{var.lab_name}}-<purpose>" (max 64
  characters) and set permissions_boundary = var.permissions_boundary (readers' copies set it to null).
  Pin provider versions in required_providers. Expose what the scripts need as outputs.
  For EKS set access_config {{ authentication_mode = "API", bootstrap_cluster_creator_admin_permissions = true }}
  so kubectl in the scripts works, and give self-managed nodes an aws_eks_access_entry of type EC2_LINUX.
- reproduce.sh: bash, run from the lab directory right after apply. Read Terraform outputs with
  `terraform output -raw NAME`. For EKS: `aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)"`.
  Wait with explicit timeouts (kubectl wait --timeout=...); never wait forever. Print the evidence that shows the
  problem: the exact error, event, log line or status. End by printing exactly one line:
  `HERO_RESULT: reproduced` or `HERO_RESULT: not_reproduced`. Exit non-zero only if the script itself broke.
- fix.sh: apply the fix (kubectl, helm, aws CLI, or edit a .tf file and run `terraform apply -auto-approve`),
  verify it, and end with `HERO_RESULT: fixed` or `HERO_RESULT: not_fixed`.
- cleanup.sh: runs before terraform destroy, even after failures and even if the cluster never came up. Delete
  everything the scripts or Kubernetes created that makes AWS resources Terraform does not know about (Services of
  type LoadBalancer, Ingresses, PVCs, ECS tasks you started), wait until they are gone, and never fail hard.
- README.md: what the lab shows and how a reader runs it in their own account.
Each script must finish within 15 minutes. AWS_REGION and LAB_NAME are set in the environment. Never print
credentials. Use the cheapest design that genuinely reproduces the problem: a single EC2 instance with k3s, or ECS,
beats EKS when the problem is not EKS-specific. EKS control plane creation takes about 10 minutes."""

LAB_RUBRIC = """Blocking problems: the lab would fail to apply (breaks a LAB LIMIT or a file rule, IAM naming or
boundary missing, invalid Terraform or provider usage), would not actually reproduce the problem described in the
topic and sources, a script could hang or never print its HERO_RESULT line, or cleanup.sh would leave AWS
resources that block terraform destroy or keep costing money. Minor: clarity, cost trims, README wording."""

LAB_SCHEMA = {"type": "object", "required": ["summary", "expected", "files"], "properties": {
    "summary": {"type": "string", "description": "2-3 sentences: what the lab builds and what it demonstrates"},
    "expected": {"type": "string", "description": "what reproduce.sh prints when the problem reproduces"},
    "files": {"type": "array", "items": {"type": "object", "required": ["path", "content"], "properties": {
        "path": {"type": "string"}, "content": {"type": "string"}}}}}}

FILE_RX = re.compile(r"(?:k8s/)?[a-z0-9][a-z0-9_.-]{0,60}\.(?:tf|sh|ya?ml|json|tpl|md)", re.I)
REQUIRED = ("main.tf", "reproduce.sh", "fix.sh", "cleanup.sh", "README.md")

# ---------- helpers ----------
def redact(s):
    s = re.sub(r"\b\d{12}\b", "<account-id>", s)
    s = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "<access-key>", s)
    return re.sub(r"(?i)\b(aws_secret_access_key|aws_session_token|secretaccesskey|sessiontoken|password)"
                  r"(\"?\s*[:=]\s*\"?)[^\s\",]+", r"\1\2<redacted>", s)

def tail(s, n=12000):
    return s if len(s) <= n else "[... earlier output cut ...]\n" + s[-n:]

def run(step, cmd, timeout, cwd=None, env=None, record=True):
    """Run a command in its own process group (so a timeout kills every child). Returns (code, output)."""
    cwd = cwd or WORK
    e = {k: v for k, v in os.environ.items() if k not in HIDE}
    e.update({"TF_IN_AUTOMATION": "1", "AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION, **(env or {})})
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=cwd, env=e, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        code, out = 124, (out or "") + f"\n[timed out after {timeout}s]"
    if not record:
        return code, out
    out = redact(out or "")
    EVIDENCE.append({"step": step, "cmd": " ".join(cmd), "code": code, "seconds": int(time.time() - t0),
                     "output": tail(out)})
    print(f"::group::[{step}] exit {code} after {int(time.time() - t0)}s\n{tail(out, 6000)}\n::endgroup::", flush=True)
    return code, out

def hero_result(out):
    got = re.findall(r"^HERO_RESULT:\s*([a-z_]+)\s*$", out or "", re.M)
    return got[-1] if got else None

def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:40].strip("-") or "lab"

def runner_tf():
    return """# Added by the runner. Readers: see providers.tf in the published lab instead.
terraform {
  backend "s3" {}
}
variable "region" { type = string }
variable "lab_name" { type = string }
variable "permissions_boundary" { type = string }
provider "aws" {
  region = var.region
  default_tags { tags = { "hero-lab" = var.lab_name } }
}
"""

def validate(lab):
    errs, seen, size = [], set(), 0
    for f in lab.get("files") or []:
        path, body = str(f.get("path", "")), str(f.get("content", ""))
        size += len(body)
        if not FILE_RX.fullmatch(path) or path.startswith("_runner"):
            errs.append(f"file name not allowed: {path!r}")
        if path in seen:
            errs.append(f"duplicate file {path}")
        seen.add(path)
        if path.endswith(".tf"):
            if re.search(r'\bbackend\s+"', body):
                errs.append(f"{path}: declares a backend (the runner does that)")
            for m in re.finditer(r'provider\s+"aws"\s*\{(.*?)\n\}', body, re.S):
                if "alias" not in m.group(1):
                    errs.append(f"{path}: declares the default aws provider (the runner does that; aliases are fine)")
            if re.search(r'variable\s+"(region|lab_name|permissions_boundary)"', body):
                errs.append(f"{path}: declares variable region/lab_name/permissions_boundary (the runner does that)")
    errs += [f"missing required file {r}" for r in REQUIRED if r not in seen]
    if size > 200_000:
        errs.append("lab is over 200 KB")
    return errs

def write_lab(lab, where):
    if where.exists():
        for p in where.iterdir():                  # keep .terraform so init stays fast
            if p.name != ".terraform":
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    where.mkdir(parents=True, exist_ok=True)
    for f in lab["files"]:
        dst = where / f["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f["content"])
        if dst.suffix == ".sh":
            dst.chmod(0o755)
    (where / "_runner.tf").write_text(runner_tf())

def s3(*args):
    return run("s3", ["aws", "s3", *args], 300, cwd=".", record=False)

def upload_code(lab_id, where):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for p in where.rglob("*"):
            if ".terraform" not in p.parts and p.name not in ("tfplan",) and p.is_file():
                t.add(p, arcname=str(p.relative_to(where)))
    tmp = pathlib.Path("work") / f"{lab_id}.tgz"
    tmp.write_bytes(buf.getvalue())
    code, out = s3("cp", str(tmp), f"s3://{BUCKET}/labs/{lab_id}/code.tgz")
    marker = pathlib.Path("work") / "active"
    marker.write_text(RUN_ID)
    code2, out2 = s3("cp", str(marker), f"s3://{BUCKET}/labs/{lab_id}/active")
    if code or code2:
        raise RuntimeError(f"could not save lab code to S3: {redact(out + out2)[-500:]}")

def tf_init(lab_id, where):
    return run("init", ["terraform", "init", "-input=false", "-no-color", "-reconfigure",
                        f"-backend-config=bucket={BUCKET}", f"-backend-config=key=labs/{lab_id}/terraform.tfstate",
                        f"-backend-config=region={REGION}"], 600, cwd=where)

def tf_env(lab_id):
    return {"LAB_NAME": lab_id, "TF_VAR_region": REGION, "TF_VAR_lab_name": lab_id,
            "TF_VAR_permissions_boundary": os.environ.get("BOUNDARY_ARN", "")}

def destroy(lab_id, where):
    env = tf_env(lab_id)
    if (where / "cleanup.sh").exists():
        run("cleanup", ["bash", "cleanup.sh"], 15 * 60, cwd=where, env=env)
    for _ in range(2):
        code, _ = run("destroy", ["terraform", "destroy", "-input=false", "-no-color", "-auto-approve",
                                  f"-var=region={REGION}", f"-var=lab_name={lab_id}"], 40 * 60, cwd=where, env=env)
        if code == 0:
            s3("rm", f"s3://{BUCKET}/labs/{lab_id}/active")
            return True
    return False

def sweep():
    """Destroy labs that a crashed earlier run left behind (they still have an `active` marker)."""
    code, out = run("sweep-list", ["aws", "s3api", "list-objects-v2", "--bucket", BUCKET, "--prefix", "labs/",
                                   "--query", "Contents[?ends_with(Key, '/active')].Key", "--output", "json"],
                    120, cwd=".", record=False)
    try:
        keys = json.loads(out) or [] if code == 0 else []
    except ValueError:
        keys = []
    swept = []
    for k in keys:
        lab_id = k.split("/")[1]
        where = pathlib.Path("work/sweep") / lab_id
        where.mkdir(parents=True, exist_ok=True)
        if s3("cp", f"s3://{BUCKET}/labs/{lab_id}/code.tgz", str(where / "code.tgz"))[0]:
            swept.append({"lab": lab_id, "destroyed": False, "note": "no code saved"}); continue
        with tarfile.open(where / "code.tgz") as t:
            t.extractall(where, filter="data")
        ok = tf_init(lab_id, where)[0] == 0 and destroy(lab_id, where)
        swept.append({"lab": lab_id, "destroyed": ok})
        print(f"sweep: {lab_id} destroyed={ok}")
    return swept

# ---------- sources ----------
def so_get(path):
    import problems
    return problems.so_get("https://api.stackexchange.com/2.3" + path)

def strip_html(s):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())

def fetch_sources(urls):
    out = []
    for u in list(urls)[:6]:
        try:
            m = re.match(r"https://github\.com/([\w.-]+/[\w.-]+)/issues/(\d+)", u)
            q = re.search(r"stackoverflow\.com/questions/(\d+)", u)
            if m:
                i = gh("GET", f"/repos/{m[1]}/issues/{m[2]}")
                cs = gh("GET", f"/repos/{m[1]}/issues/{m[2]}/comments?per_page=100")
                out.append({"url": u, "title": i["title"], "body": (i.get("body") or "")[:5000], "comments": [
                    {"by": "maintainer" if c.get("author_association") in MAINTAINERS else "other",
                     "text": (c.get("body") or "")[:2000]}
                    for c in cs if (c.get("user") or {}).get("type") != "Bot"][:40]})
            elif q:
                qq = so_get(f"/questions/{q[1]}?site=stackoverflow&filter=withbody")["items"][0]
                ans = so_get(f"/questions/{q[1]}/answers?site=stackoverflow&sort=votes&order=desc&filter=withbody")
                out.append({"url": u, "title": html.unescape(qq["title"]), "body": strip_html(qq.get("body"))[:5000],
                            "answers": [{"accepted": a.get("is_accepted", False), "score": a.get("score", 0),
                                         "text": strip_html(a.get("body"))[:2500]} for a in ans.get("items", [])[:4]]})
            else:
                out.append({"url": u})
        except Exception as e:
            print(f"warn: source {u}: {e}")
            out.append({"url": u, "error": str(e)[:200]})
    return out

# ---------- main ----------
def save(result, lab=None):
    OUT.mkdir(exist_ok=True)
    if lab:
        d = OUT / "lab"
        shutil.rmtree(d, ignore_errors=True)
        for f in lab.get("files") or []:
            if FILE_RX.fullmatch(str(f.get("path", ""))):
                (d / f["path"]).parent.mkdir(parents=True, exist_ok=True)
                (d / f["path"]).write_text(redact(str(f.get("content", ""))))
    ev = OUT / "evidence"
    ev.mkdir(exist_ok=True)
    for n, e in enumerate(EVIDENCE, 1):
        (ev / f"{n:02d}-{e['step']}.log").write_text(f"$ {e['cmd']}\n# exit {e['code']} after {e['seconds']}s\n\n{e['output']}\n")
    result["steps"] = [{k: e[k] for k in ("step", "code", "seconds")} for e in EVIDENCE]
    result["usage"] = llm.usage_summary()
    result["minutes"] = round((time.time() - START) / 60, 1)
    (OUT / "result.json").write_text(json.dumps(result, indent=1))

def main():
    issue_no = int(os.environ["ISSUE"])
    OUT.mkdir(exist_ok=True)
    result = {"issue": issue_no, "run_id": RUN_ID, "status": "failed", "destroyed": None, "attempts": 0}
    issue = gh("GET", f"/repos/{HOME_REPO}/issues/{issue_no}")
    labels = {l["name"] for l in issue.get("labels", [])}
    if "pull_request" in issue or not labels & {"radar", "problem"}:
        raise SystemExit("refusing: that issue is not a radar/problem topic")
    slug = slugify(issue["title"])
    lab_id = f"{slug[:24].strip('-')}-{RUN_ID[-6:]}"
    result.update(title=issue["title"], slug=slug, lab_id=lab_id)
    sources = fetch_sources(used_sources(issue.get("body") or ""))
    (OUT / "topic.json").write_text(json.dumps({"title": issue["title"], "body": issue.get("body"), "sources": sources}))
    context = (f"TOPIC ISSUE TITLE: {issue['title']}\n\nTOPIC ISSUE BODY:\n{issue.get('body')}\n\n"
               f"SOURCES:\n{json.dumps(sources)}")

    lab = llm.claude_json("You are an AWS container engineer. You write small, reliable, disposable labs that "
                          "reproduce a real problem on AWS and then fix it.",
                          f"Write the lab for this topic.\n\nLAB RULES:\n{LAB_RULES}\n\n{context}",
                          LAB_SCHEMA, tool="submit_lab")
    lab, review = llm.refine("AWS replication lab", lab, LAB_RUBRIC, f"LAB RULES:\n{LAB_RULES}\n\n{context}", LAB_SCHEMA)
    result["review"] = review
    result["summary"], result["expected"] = lab.get("summary", ""), lab.get("expected", "")
    save(result, lab)
    if DRY:
        result["status"] = "dry_run"
        print(f"dry run: lab written and reviewed, AWS not touched. {llm.usage_summary()}")
        save(result, lab); return
    if not BUCKET:
        raise SystemExit("TF_STATE_BUCKET is not set")

    code, acct = run("whoami", ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                     60, cwd=".", record=False)
    if code or not re.fullmatch(r"\d{12}", acct.strip()):
        raise SystemExit("could not read the lab account id")
    os.environ["BOUNDARY_ARN"] = f"arn:aws:iam::{acct.strip()}:policy/hero-pipeline-boundary"
    result["swept"] = sweep()
    (OUT / "state.json").write_text(json.dumps({"lab_id": lab_id, "work": str(WORK), "destroyed": False}))
    env = tf_env(lab_id)
    applied = False
    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            result["attempts"], result["status"] = attempt, "failed"
            failed, out = None, ""
            errs = validate(lab)
            if errs:
                failed, out = "validate", "\n".join(errs)
            else:
                write_lab(lab, WORK)
                upload_code(lab_id, WORK)
                for step, cmd, t in (("init", None, 0),
                                     ("plan", ["terraform", "plan", "-input=false", "-no-color", "-out=tfplan"], 900)):
                    code, out = tf_init(lab_id, WORK) if step == "init" else run(step, cmd, t, env=env)
                    if code:
                        failed = step; break
                if not failed:
                    code, raw = run("show", ["terraform", "show", "-json", "tfplan"], 300, env=env, record=False)
                    errs = check_plan.problems(json.loads(raw)) if code == 0 else [f"terraform show failed: {raw[-300:]}"]
                    if errs:
                        failed, out = "plan-check", "\n".join(errs)
                        EVIDENCE.append({"step": "plan-check", "cmd": "check_plan.py", "code": 1, "seconds": 0, "output": out})
                if not failed:
                    applied = True
                    code, out = run("apply", ["terraform", "apply", "-input=false", "-no-color", "-auto-approve", "tfplan"],
                                    40 * 60, env=env)
                    if code:
                        failed = "apply"
                if not failed:
                    code, out = run("reproduce", ["bash", "reproduce.sh"], 16 * 60, env=env)
                    res = hero_result(out)
                    if code or res not in ("reproduced", "not_reproduced"):
                        failed = "reproduce"
                    elif res == "not_reproduced":
                        failed, result["status"] = "reproduce (problem did not appear)", "not_reproduced"
                if not failed:
                    code, out = run("fix", ["bash", "fix.sh"], 16 * 60, env=env)
                    res = hero_result(out)
                    if code or res not in ("fixed", "not_fixed"):
                        failed, result["status"] = "fix", "reproduced_fix_failed"
                    else:
                        result["status"] = "reproduced_and_fixed" if res == "fixed" else "reproduced_fix_failed"
                        if res == "fixed":
                            break
                        failed = "fix (the fix did not work)"
            print(f"attempt {attempt}: failed at {failed}")
            result["last_failure"] = {"step": failed, "output": tail(redact(out), 3000)}
            if attempt == MAX_ATTEMPTS or time.time() - START > WORK_BUDGET:
                break
            lab = llm.claude_json(
                "You are an AWS container engineer fixing your own lab after a failed run.",
                f"Attempt {attempt} of {MAX_ATTEMPTS} failed at step: {failed}.\n\nEVIDENCE (output tail):\n"
                f"{tail(redact(out), 8000)}\n\nFix the lab so it runs and genuinely reproduces the problem. If the "
                f"problem did not appear, rethink the reproduction. Return the full lab.\n\nLAB RULES:\n{LAB_RULES}\n\n"
                f"YOUR LAB:\n{json.dumps(lab)}\n\n{context}", LAB_SCHEMA, tool="submit_lab")
            result["summary"], result["expected"] = lab.get("summary", ""), lab.get("expected", "")
        if result["status"] == "reproduced_and_fixed":
            result.pop("last_failure", None)
    finally:
        if applied or (WORK / ".terraform").exists():
            result["destroyed"] = destroy(lab_id, WORK)
        else:
            result["destroyed"] = True
            s3("rm", f"s3://{BUCKET}/labs/{lab_id}/active")
        (OUT / "state.json").write_text(json.dumps({"lab_id": lab_id, "work": str(WORK),
                                                    "destroyed": result["destroyed"]}))
        save(result, lab)
        print(f"status={result['status']} destroyed={result['destroyed']} {llm.usage_summary()}")

def destroy_only():
    st = OUT / "state.json"
    if not st.exists():
        print("nothing to clean up"); return
    s = json.loads(st.read_text())
    if s.get("destroyed"):
        print("already destroyed"); return
    where = pathlib.Path(s["work"])
    ok = where.exists() and tf_init(s["lab_id"], where)[0] == 0 and destroy(s["lab_id"], where)
    s["destroyed"] = ok
    st.write_text(json.dumps(s))
    r = OUT / "result.json"
    if r.exists():
        res = json.loads(r.read_text()); res["destroyed"] = ok; r.write_text(json.dumps(res, indent=1))
    print(f"safety-net destroy: {ok}")
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    destroy_only() if "--destroy-only" in sys.argv else main()
