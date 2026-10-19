#!/usr/bin/env python3
"""Replicate one topic issue on the AWS lab account.

  1. Read the topic issue and re-fetch its sources (issue threads, Stack Overflow answers).
  2. Claude writes the lab (Terraform + reproduce.sh + fix.sh + cleanup.sh, optional fix.tf), ChatGPT
     reviews it, Gemini rules on disagreements (llm.refine).
  3. plan -> check_plan -> apply -> reproduce.sh -> [fix.tf: plan -> check -> apply] -> fix.sh.
     On failure Claude gets the error and revises, up to MAX_ATTEMPTS or the work deadline.
  4. ALWAYS cleanup.sh + terraform destroy, from the last config that planned cleanly (so a broken
     revision can't block destroy). That config and an `active` marker live in S3, so the next run (or the
     safety-net step) can destroy anything a crashed run left. A janitor then removes stragglers.

Isolation: Terraform and every AI-written script run in a throwaway container (lab.Dockerfile) that gets
only the AWS credentials and lab variables, as the runner's uid, with no access to the runner's processes,
files or GitHub tokens. The model keys stay in this orchestrator process.
Everything the draft job needs is written to out/.  `lab.py --destroy-only` is the safety-net step.
"""
import html, io, json, os, pathlib, re, shutil, signal, subprocess, sys, tarfile, time
from radar import HOME_REPO, LAB_LIMITS, gh, used_sources
import check_plan, llm

OUT, WORK, GOOD = pathlib.Path("out"), pathlib.Path("work/lab"), pathlib.Path("work/good")
TFCACHE = pathlib.Path("work/.tfcache")
IMAGE = os.environ.get("LAB_IMAGE", "hero-lab:local")
RUN_ID = os.environ.get("RUN_ID", "local")
DRY = os.environ.get("DRY_RUN", "").lower() == "true"
REGION = os.environ.get("AWS_REGION", "eu-central-1")
BUCKET = os.environ.get("TF_STATE_BUCKET", "")
START = time.time()
WORK_END = START + 60 * 60      # no new attempt or step starts after this
HARD_END = START + 106 * 60     # nothing runs past this (the workflow step is killed at 112 minutes)
MAX_ATTEMPTS, MAX_SWEEP = 3, 2
HIDE = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GH_READ_TOKEN", "BOT_TOKEN", "GITHUB_TOKEN",
        "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_URL")
SECRET_VARS = HIDE + ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
AWS_VARS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN")
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
- fix.tf (optional): only if the fix is a Terraform change. The runner holds it back, and applies it (with the
  same plan checks) after reproduce.sh has shown the problem.
- reproduce.sh: bash, run from the lab directory right after apply. Read Terraform outputs with
  `terraform output -raw NAME`. Scripts must never run terraform apply/destroy/import/state or init.
  Every script that uses kubectl starts with `aws eks update-kubeconfig --name "$(terraform output -raw cluster_name)"`.
  Wait with explicit timeouts (kubectl wait --timeout=...); never wait forever. Print the evidence that shows the
  problem: the exact error, event, log line or status. End by printing exactly one line:
  `HERO_RESULT: reproduced` or `HERO_RESULT: not_reproduced`. Exit non-zero only if the script itself broke.
- fix.sh: apply the fix with kubectl, helm or the aws CLI (Terraform changes go in fix.tf, already applied when
  fix.sh runs), verify it, and end with `HERO_RESULT: fixed` or `HERO_RESULT: not_fixed`.
- cleanup.sh: runs before terraform destroy, even after failures and even if the cluster never came up. Delete
  everything the scripts or Kubernetes created that makes AWS resources Terraform does not know about (Services of
  type LoadBalancer, Ingresses, PVCs, ECS tasks you started), wait until they are gone, and never fail hard.
- README.md: what the lab shows and how a reader runs it in their own account.
Each script must finish within 15 minutes. AWS_REGION and LAB_NAME are set in the environment. Never print
credentials. Use the cheapest design that genuinely reproduces the problem: a single EC2 instance with k3s, or ECS,
beats EKS when the problem is not EKS-specific. EKS control plane creation takes about 10 minutes."""

BUILD_RULES = """THIS IS A BUILD TOPIC: the lab builds something useful and proves it works; it is not a bug hunt.
- reproduce.sh is the BEFORE: deploy the starting point (for example the sample app with no telemetry, a worker
  that doesn't scale, a secret hard-coded in a task definition) and print evidence of the gap the build closes.
  End with `HERO_RESULT: reproduced` once the before-state is shown.
- fix.sh is the BUILD: install and configure the thing, then PROVE it works end to end with real output a reader
  would want to see (traces or metrics arriving with real names, pods or tasks scaling from N to M with timings, a
  rollout finishing, a request succeeding). "Installed successfully" is not proof. End with `HERO_RESULT: fixed`
  only when the proof is printed.
- Keep it copy-paste friendly: pinned chart and image versions, a small sample app, readable script steps."""

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
SKIP_COPY = {".terraform", ".home", "tfplan", ".terraform.tfstate.lock.info"}
SECRET_RX = [r"sk-ant-[\w-]{20,}", r"\bsk-(?:proj-)?[\w-]{20,}", r"\bAIza[\w-]{30,}", r"\bgh[pousr]_[A-Za-z0-9]{30,}",
             r"\bgithub_pat_\w{30,}", r"\beyJ[\w-]{10,}\.[\w-]{10,}\.[\w-]{10,}",
             r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"]

# ---------- helpers ----------
def redact(s):
    s = str(s or "")
    for k in SECRET_VARS:
        v = os.environ.get(k, "")
        if len(v) >= 8:
            s = s.replace(v, f"<{k.lower()}>")
    for rx in SECRET_RX:
        s = re.sub(rx, "<secret>", s)
    s = re.sub(r"\b\d{12}\b", "<account-id>", s)
    s = re.sub(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", "<access-key>", s)
    return re.sub(r"(?i)\b(aws_secret_access_key|aws_session_token|secretaccesskey|sessiontoken|password)"
                  r"(\"?\s*[:=]\s*\"?)[^\s\",]+", r"\1\2<redacted>", s)

def tail(s, n=12000):
    return s if len(s) <= n else "[... earlier output cut ...]\n" + s[-n:]

def remaining(end=None):
    return (end or HARD_END) - time.time()

def run(step, cmd, timeout, cwd=None, env=None, record=True, sandbox=False):
    """Run a command (in the lab sandbox if sandbox=True). A timeout kills it and everything it started.
    Never runs past HARD_END. Returns (exit code, output)."""
    cwd = pathlib.Path(cwd or WORK)
    timeout = int(max(30, min(timeout, remaining())))
    host_env = {k: v for k, v in os.environ.items() if k not in HIDE}
    name = None
    if sandbox:
        (cwd / ".home").mkdir(parents=True, exist_ok=True)
        TFCACHE.mkdir(parents=True, exist_ok=True)
        name = f"hero-lab-{os.getpid()}-{len(EVIDENCE)}-{int(time.time() * 1000) % 10**8}"
        full = ["docker", "run", "--rm", "--name", name, "--user", f"{os.getuid()}:{os.getgid()}",
                "-v", f"{cwd.resolve()}:/lab", "-v", f"{TFCACHE.resolve()}:/tfcache", "-w", "/lab",
                "-e", "HOME=/lab/.home", "-e", "TF_IN_AUTOMATION=1", "-e", "TF_PLUGIN_CACHE_DIR=/tfcache",
                "-e", f"AWS_REGION={REGION}", "-e", f"AWS_DEFAULT_REGION={REGION}"]
        full += [x for k in AWS_VARS if k in os.environ for x in ("-e", k)]     # value inherited, not on argv
        full += [x for k, v in (env or {}).items() for x in ("-e", f"{k}={v}")]
        full += [IMAGE, *cmd]
    else:
        full = cmd
        host_env.update({"AWS_REGION": REGION, "AWS_DEFAULT_REGION": REGION, **(env or {})})
    t0 = time.time()
    p = subprocess.Popen(full, cwd=cwd, env=host_env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                         start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        if name:
            subprocess.run(["docker", "kill", name], capture_output=True, timeout=60)
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        code, out = 124, (out or "") + f"\n[timed out after {timeout}s]"
    if not record:
        return code, out
    out = redact(out)
    EVIDENCE.append({"step": step, "cmd": redact(" ".join(cmd)), "code": code, "seconds": int(time.time() - t0),
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

def _strip_comments(hcl):
    hcl = re.sub(r"/\*.*?\*/", "", hcl, flags=re.S)
    return re.sub(r'(?m)^((?:[^"#\n]|"(?:[^"\\\n]|\\.)*")*?)\s*(?:#|//).*$', r"\1", hcl)

def _blocks(hcl, header):
    """Bodies of every `header { ... }` block, matched by brace counting."""
    out = []
    for m in re.finditer(header + r"\s*\{", hcl):
        depth, i = 1, m.end()
        while i < len(hcl) and depth:
            depth += {"{": 1, "}": -1}.get(hcl[i], 0); i += 1
        out.append(hcl[m.end():i - 1])
    return out

def validate(lab):
    errs, seen, size = [], set(), 0
    for f in lab.get("files") or []:
        path, body = str(f.get("path", "")), str(f.get("content", ""))
        size += len(body)
        if not FILE_RX.fullmatch(path) or path.startswith("_runner") or path.endswith("override.tf") \
                or path == "providers.tf":
            errs.append(f"file name not allowed: {path!r}")
        if path in seen:
            errs.append(f"duplicate file {path}")
        seen.add(path)
        if path.endswith(".tf"):
            code = _strip_comments(body)
            if re.search(r'\bbackend\s+"', code):
                errs.append(f"{path}: declares a backend (the runner does that)")
            if any(not re.search(r"(?m)^\s*alias\s*=", b) for b in _blocks(code, r'\bprovider\s+"aws"')):
                errs.append(f"{path}: declares the default aws provider (the runner does that; aliases are fine)")
            if re.search(r'variable\s+"(region|lab_name|permissions_boundary)"', code):
                errs.append(f"{path}: declares variable region/lab_name/permissions_boundary (the runner does that)")
            if re.search(r'data\s+"external"|provisioner\s+"(local-exec|remote-exec)"', code):
                errs.append(f"{path}: external data sources and exec provisioners are not allowed; use the scripts")
        if path.endswith(".sh") and re.search(r"\bterraform\s+(apply|destroy|import|state|taint|untaint|init|"
                                              r"workspace|force-unlock|console|plan)\b", body):
            errs.append(f"{path}: scripts may only run `terraform output`")
    errs += [f"missing required file {r}" for r in REQUIRED if r not in seen]
    if size > 200_000:
        errs.append("lab is over 200 KB")
    return errs

def _clear(where, keep=(".terraform", ".home")):
    if where.exists():
        for p in where.iterdir():
            if p.name not in keep:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    where.mkdir(parents=True, exist_ok=True)

def write_lab(lab, where, with_fix=False):
    _clear(where)
    for f in lab["files"]:
        if f["path"] == "fix.tf" and not with_fix:
            continue
        dst = where / f["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(f["content"])
        if dst.suffix == ".sh":
            dst.chmod(0o755)
    (where / "_runner.tf").write_text(runner_tf())

def add_fix(lab, where):
    fix = next((f for f in lab["files"] if f["path"] == "fix.tf"), None)
    if fix:
        (where / "fix.tf").write_text(fix["content"])
    return bool(fix)

def s3(*args):
    return run("s3", ["aws", "s3", *args], 300, cwd=".", record=False)

def snapshot_good(lab_id):
    """Keep the config that just planned cleanly as the one to destroy from, locally and in S3."""
    _clear(GOOD)
    for p in WORK.iterdir():
        if p.name not in SKIP_COPY:
            shutil.copytree(p, GOOD / p.name) if p.is_dir() else shutil.copy2(p, GOOD / p.name)
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for p in GOOD.rglob("*"):
            if p.is_file() and not set(p.relative_to(GOOD).parts) & SKIP_COPY:
                t.add(p, arcname=str(p.relative_to(GOOD)))
    tmp = pathlib.Path("work") / f"{lab_id}.tgz"
    tmp.write_bytes(buf.getvalue())
    (pathlib.Path("work") / "active").write_text(RUN_ID)
    c1, o1 = s3("cp", str(tmp), f"s3://{BUCKET}/labs/{lab_id}/code.tgz")
    c2, o2 = s3("cp", "work/active", f"s3://{BUCKET}/labs/{lab_id}/active")
    if c1 or c2:
        raise RuntimeError(f"could not save lab code to S3: {redact(o1 + o2)[-500:]}")

def tf_env(lab_id):
    return {"LAB_NAME": lab_id, "TF_VAR_region": REGION, "TF_VAR_lab_name": lab_id,
            "TF_VAR_permissions_boundary": os.environ.get("BOUNDARY_ARN", "")}

def tf_init(lab_id, where):
    return run("init", ["terraform", "init", "-input=false", "-no-color", "-reconfigure",
                        f"-backend-config=bucket={BUCKET}", f"-backend-config=key=labs/{lab_id}/terraform.tfstate",
                        f"-backend-config=region={REGION}", "-backend-config=use_lockfile=true"],
               600, cwd=where, env=tf_env(lab_id), sandbox=True)

def plan_and_check(lab_id):
    """init + plan + check_plan in WORK. Returns (failed_step or None, output)."""
    env = tf_env(lab_id)
    code, out = tf_init(lab_id, WORK)
    if code:
        return "init", out
    code, out = run("plan", ["terraform", "plan", "-input=false", "-no-color", "-out=tfplan"], 900, env=env, sandbox=True)
    if code:
        return "plan", out
    code, raw = run("show", ["terraform", "show", "-json", "tfplan"], 300, env=env, record=False, sandbox=True)
    try:
        errs = check_plan.problems(json.loads(raw)) if code == 0 else [f"terraform show failed: {redact(raw)[-300:]}"]
    except ValueError:
        errs = ["terraform show did not return JSON"]
    if errs:
        out = "\n".join(errs)
        EVIDENCE.append({"step": "plan-check", "cmd": "check_plan.py", "code": 1, "seconds": 0, "output": out})
        return "plan-check", out
    snapshot_good(lab_id)
    return None, ""

def apply(lab_id):
    return run("apply", ["terraform", "apply", "-input=false", "-no-color", "-auto-approve", "tfplan"],
               40 * 60, env=tf_env(lab_id), sandbox=True)

def destroy(lab_id, cleanup_dir, tf_dir):
    """cleanup.sh from cleanup_dir (the newest scripts), then terraform destroy from tf_dir (the last good config)."""
    env = tf_env(lab_id)
    if (cleanup_dir / "cleanup.sh").exists():
        run("cleanup", ["bash", "cleanup.sh"], 10 * 60, cwd=cleanup_dir, env=env, sandbox=True)
    if tf_init(lab_id, tf_dir)[0]:
        return False
    for _ in range(2):
        code, _ = run("destroy", ["terraform", "destroy", "-input=false", "-no-color", "-auto-approve"],
                      35 * 60, cwd=tf_dir, env=env, sandbox=True)
        if code == 0:
            s3("rm", f"s3://{BUCKET}/labs/{lab_id}/active")
            return True
        if remaining() < 120:
            break
    return False

def fetch_code(lab_id, where):
    _clear(where, keep=())
    if s3("cp", f"s3://{BUCKET}/labs/{lab_id}/code.tgz", str(where / "code.tgz"))[0]:
        return False
    with tarfile.open(where / "code.tgz") as t:
        t.extractall(where, filter="data")
    (where / "code.tgz").unlink()
    return True

def sweep():
    """Destroy labs that an earlier run left behind (they still have an `active` marker in S3)."""
    code, out = run("sweep-list", ["aws", "s3api", "list-objects-v2", "--bucket", BUCKET, "--prefix", "labs/",
                                   "--query", "Contents[?ends_with(Key, '/active')].Key", "--output", "json"],
                    120, cwd=".", record=False)
    try:
        keys = (json.loads(out) or []) if code == 0 else []
    except ValueError:
        keys = []
    swept = []
    for k in keys[:MAX_SWEEP]:
        lab_id = k.split("/")[1]
        where = pathlib.Path("work/sweep") / lab_id
        ok = fetch_code(lab_id, where) and destroy(lab_id, where, where)
        swept.append({"lab": lab_id, "destroyed": bool(ok)})
        print(f"sweep: {lab_id} destroyed={ok}")
    if len(keys) > MAX_SWEEP:
        swept.append({"lab": f"{len(keys) - MAX_SWEEP} more", "destroyed": False})
    return swept

def _aws_json(*args):
    code, out = run("janitor", ["aws", *args, "--output", "json"], 120, cwd=".", record=False)
    try:
        return json.loads(out) if code == 0 and out.strip() else None
    except ValueError:
        return None

def _aws(*args):
    return run("janitor", ["aws", *args], 180, cwd=".")[0] == 0

def janitor():
    """The lab account runs one lab at a time, so between labs nothing billable should exist. Remove it."""
    done = []
    r = _aws_json("ec2", "describe-instances", "--filters",
                  "Name=instance-state-name,Values=pending,running,stopping,stopped")
    ids = [i["InstanceId"] for x in (r or {}).get("Reservations", []) for i in x.get("Instances", [])]
    if ids and _aws("ec2", "terminate-instances", "--instance-ids", *ids):
        done.append(f"terminated {len(ids)} EC2 instance(s)")
    for lb in (_aws_json("elbv2", "describe-load-balancers") or {}).get("LoadBalancers", []):
        if _aws("elbv2", "delete-load-balancer", "--load-balancer-arn", lb["LoadBalancerArn"]):
            done.append(f"deleted load balancer {lb.get('LoadBalancerName')}")
    for lb in (_aws_json("elb", "describe-load-balancers") or {}).get("LoadBalancerDescriptions", []):
        if _aws("elb", "delete-load-balancer", "--load-balancer-name", lb["LoadBalancerName"]):
            done.append(f"deleted classic load balancer {lb['LoadBalancerName']}")
    for v in (_aws_json("ec2", "describe-volumes", "--filters", "Name=status,Values=available") or {}).get("Volumes", []):
        if _aws("ec2", "delete-volume", "--volume-id", v["VolumeId"]):
            done.append(f"deleted volume {v['VolumeId']}")
    for a in (_aws_json("ec2", "describe-addresses") or {}).get("Addresses", []):
        if not a.get("AssociationId") and _aws("ec2", "release-address", "--allocation-id", a["AllocationId"]):
            done.append(f"released Elastic IP {a.get('PublicIp')}")
    for c in (_aws_json("ecs", "list-clusters") or {}).get("clusterArns", []):
        for s in (_aws_json("ecs", "list-services", "--cluster", c) or {}).get("serviceArns", []):
            _aws("ecs", "delete-service", "--cluster", c, "--service", s, "--force")
        if _aws("ecs", "delete-cluster", "--cluster", c):
            done.append(f"deleted ECS cluster {c.split('/')[-1]}")
    for c in (_aws_json("eks", "list-clusters") or {}).get("clusters", []):
        for fp in (_aws_json("eks", "list-fargate-profiles", "--cluster-name", c) or {}).get("fargateProfileNames", []):
            _aws("eks", "delete-fargate-profile", "--cluster-name", c, "--fargate-profile-name", fp)
        if _aws("eks", "delete-cluster", "--name", c):
            done.append(f"deleting EKS cluster {c} (if it had Fargate profiles, the next run finishes the job)")
    roles = _aws_json("iam", "list-roles", "--query", "Roles[?starts_with(RoleName, 'hero-lab-')].RoleName") or []
    for role in roles:
        for p in (_aws_json("iam", "list-attached-role-policies", "--role-name", role) or {}).get("AttachedPolicies", []):
            _aws("iam", "detach-role-policy", "--role-name", role, "--policy-arn", p["PolicyArn"])
        for p in (_aws_json("iam", "list-role-policies", "--role-name", role) or {}).get("PolicyNames", []):
            _aws("iam", "delete-role-policy", "--role-name", role, "--policy-name", p)
        for ip in (_aws_json("iam", "list-instance-profiles-for-role", "--role-name", role) or {}).get("InstanceProfiles", []):
            _aws("iam", "remove-role-from-instance-profile", "--role-name", role,
                 "--instance-profile-name", ip["InstanceProfileName"])
            _aws("iam", "delete-instance-profile", "--instance-profile-name", ip["InstanceProfileName"])
        if _aws("iam", "delete-role", "--role-name", role):
            done.append(f"deleted IAM role {role}")
    print("janitor: " + ("; ".join(done) or "nothing left over"))
    return done

def set_boundary():
    code, acct = run("whoami", ["aws", "sts", "get-caller-identity", "--query", "Account", "--output", "text"],
                     60, cwd=".", record=False)
    if code or not re.fullmatch(r"\d{12}", acct.strip()):
        raise SystemExit("could not read the lab account id")
    os.environ["BOUNDARY_ARN"] = f"arn:aws:iam::{acct.strip()}:policy/hero-pipeline-boundary"

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
    shutil.rmtree(ev, ignore_errors=True)
    ev.mkdir()
    for n, e in enumerate(EVIDENCE, 1):
        (ev / f"{n:02d}-{e['step']}.log").write_text(f"$ {e['cmd']}\n# exit {e['code']} after {e['seconds']}s\n\n{e['output']}\n")
    result["steps"] = [{k: e[k] for k in ("step", "code", "seconds")} for e in EVIDENCE]
    result["usage"] = llm.usage_summary()
    result["minutes"] = round((time.time() - START) / 60, 1)
    (OUT / "result.json").write_text(json.dumps(result, indent=1))

def attempt_once(lab, lab_id, result):
    """One full try. Returns (failed_step or None, output). Sets result['status'] as it learns things."""
    env = tf_env(lab_id)
    errs = validate(lab)
    if errs:
        return "validate", "\n".join(errs)
    write_lab(lab, WORK)
    failed, out = plan_and_check(lab_id)
    if failed:
        return failed, out
    result["applied"] = True
    code, out = apply(lab_id)
    if code:
        return "apply", out
    if remaining(WORK_END) < 0:
        return "deadline", "work deadline reached after apply"
    code, out = run("reproduce", ["bash", "reproduce.sh"], 16 * 60, env=env, sandbox=True)
    res = hero_result(out)
    if code or res not in ("reproduced", "not_reproduced"):
        return "reproduce", out
    if res == "not_reproduced":
        result["status"] = "not_reproduced"
        return "reproduce (problem did not appear)", out
    result["status"] = "reproduced_fix_failed"
    if add_fix(lab, WORK):
        failed, out = plan_and_check(lab_id)
        if failed:
            return f"fix.tf {failed}", out
        code, out = apply(lab_id)
        if code:
            return "fix.tf apply", out
    code, out = run("fix", ["bash", "fix.sh"], 16 * 60, env=env, sandbox=True)
    res = hero_result(out)
    if code or res not in ("fixed", "not_fixed"):
        return "fix", out
    if res == "not_fixed":
        return "fix (the fix did not work)", out
    result["status"] = "reproduced_and_fixed"
    return None, out

def main():
    issue_no = int(os.environ["ISSUE"])
    OUT.mkdir(exist_ok=True)
    result = {"issue": issue_no, "run_id": RUN_ID, "status": "failed", "destroyed": None, "attempts": 0}
    lab, lab_id = None, None
    try:
        issue = gh("GET", f"/repos/{HOME_REPO}/issues/{issue_no}")
        labels = {l["name"] for l in issue.get("labels", [])}
        if "pull_request" in issue or not labels & {"radar", "problem"}:
            raise RuntimeError("that issue is not a radar/problem topic")
        slug = slugify(issue["title"])
        kind = "build" if "build" in labels else "problem"
        rules = LAB_RULES + ("\n\n" + BUILD_RULES if kind == "build" else "")
        goal = ("build something useful on AWS and prove it works with real output" if kind == "build" else
                "reproduce a real problem on AWS and then fix it")
        lab_id = f"{slug[:24].strip('-')}-{RUN_ID[-6:]}"
        result.update(title=issue["title"], slug=slug, lab_id=lab_id)
        sources = fetch_sources(used_sources(issue.get("body") or ""))
        (OUT / "topic.json").write_text(json.dumps({"title": issue["title"], "body": issue.get("body"), "sources": sources,
                                                     "kind": kind}))
        context = (f"TOPIC ISSUE TITLE: {issue['title']}\n\nTOPIC ISSUE BODY:\n{issue.get('body')}\n\n"
                   f"SOURCES:\n{json.dumps(sources)}")
        lab = llm.claude_json("You are an AWS container engineer. You write small, reliable, disposable labs that "
                              f"{goal}.",
                              f"Write the lab for this topic.\n\nLAB RULES:\n{rules}\n\n{context}",
                              LAB_SCHEMA, tool="submit_lab")
        rubric = LAB_RUBRIC + (" For a BUILD topic, blocking also: fix.sh doesn't print real proof that the build "
                               "works end to end." if kind == "build" else "")
        lab, review = llm.refine("AWS lab", lab, rubric, f"LAB RULES:\n{rules}\n\n{context}", LAB_SCHEMA)
        result.update(review=review, summary=lab.get("summary", ""), expected=lab.get("expected", ""))
        save(result, lab)
        if DRY:
            result["status"] = "dry_run"
            print(f"dry run: lab written and reviewed, AWS not touched. {llm.usage_summary()}")
            return
        if not BUCKET:
            raise RuntimeError("TF_STATE_BUCKET is not set")
        set_boundary()
        result["swept"] = sweep()
        result["janitor_before"] = janitor()
        (OUT / "state.json").write_text(json.dumps({"lab_id": lab_id, "destroyed": False}))
        for attempt in range(1, MAX_ATTEMPTS + 1):
            if result.get("applied") and (WORK / "cleanup.sh").exists():
                run("cleanup", ["bash", "cleanup.sh"], 10 * 60, env=tf_env(lab_id), sandbox=True)  # last try's leftovers
            result["attempts"], result["status"] = attempt, "failed"
            failed, out = attempt_once(lab, lab_id, result)
            if not failed:
                result.pop("last_failure", None)
                break
            print(f"attempt {attempt}: failed at {failed}")
            result["last_failure"] = {"step": failed, "output": tail(redact(out), 3000)}
            if attempt == MAX_ATTEMPTS or remaining(WORK_END) < 10 * 60 or failed == "deadline":
                break
            lab = llm.claude_json(
                "You are an AWS container engineer fixing your own lab after a failed run.",
                f"Attempt {attempt} of {MAX_ATTEMPTS} failed at step: {failed}.\n\nEVIDENCE (output tail):\n"
                f"{tail(redact(out), 8000)}\n\nFix the lab so it runs and genuinely does what the topic needs (reproduce "
                f"the problem, or for a build topic, show the before-state and prove the build works). Return the full "
                f"lab.\n\nLAB RULES:\n{rules}\n\n"
                f"YOUR LAB:\n{json.dumps(lab)}\n\n{context}", LAB_SCHEMA, tool="submit_lab")
            result.update(summary=lab.get("summary", ""), expected=lab.get("expected", ""))
    except Exception as e:
        print(f"error: {type(e).__name__}: {e}")
        result["error"] = redact(f"{type(e).__name__}: {e}")[:800]
    finally:
        if lab_id and not DRY and BUCKET:
            if (GOOD / "_runner.tf").exists():
                result["destroyed"] = destroy(lab_id, WORK if WORK.exists() else GOOD, GOOD)
            else:
                result["destroyed"] = True        # nothing ever planned cleanly, so nothing was created
            if result["destroyed"]:
                result["janitor_after"] = janitor()
            (OUT / "state.json").write_text(json.dumps({"lab_id": lab_id, "destroyed": result["destroyed"]}))
        save(result, lab)
        print(f"status={result['status']} destroyed={result['destroyed']} {llm.usage_summary()}")

def destroy_only():
    """Safety net (fresh credentials): kill leftover sandboxes, then destroy from the saved good config."""
    ids = subprocess.run(["docker", "ps", "-q", "--filter", "name=hero-lab-"], capture_output=True, text=True).stdout.split()
    if ids:
        subprocess.run(["docker", "kill", *ids], capture_output=True)
    st = OUT / "state.json"
    if not st.exists():
        print("nothing to clean up"); return
    s = json.loads(st.read_text())
    if s.get("destroyed"):
        print("already destroyed"); return
    set_boundary()
    where = pathlib.Path("work/safety")
    ok = fetch_code(s["lab_id"], where) and destroy(s["lab_id"], WORK if (WORK / "cleanup.sh").exists() else where, where)
    jan = janitor() if ok else []
    s["destroyed"] = bool(ok)
    st.write_text(json.dumps(s))
    r = OUT / "result.json"
    if r.exists():
        res = json.loads(r.read_text()); res["destroyed"] = bool(ok); res["janitor_after"] = jan
        r.write_text(json.dumps(res, indent=1))
    ev = OUT / "evidence"
    ev.mkdir(parents=True, exist_ok=True)
    for n, e in enumerate(EVIDENCE, 1):
        (ev / f"90-safety-{n:02d}-{e['step']}.log").write_text(f"$ {e['cmd']}\n# exit {e['code']}\n\n{e['output']}\n")
    print(f"safety-net destroy: {ok}")
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    destroy_only() if "--destroy-only" in sys.argv else main()
