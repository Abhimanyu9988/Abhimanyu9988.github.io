#!/usr/bin/env python3
"""The team: one registry entry per role, so a lead can be replaced without touching the code that uses it.

  roles/registry.json     mission, module, model, prompt, outputs, data sources and budget for each role.
  roles/prompts/*.md      the versioned system prompt. Its hash is recorded with every output.
  ask(role, prompt, ...)  one budgeted, schema-validated answer from that role.
  check_registry()        what a role must satisfy to be "hired"; tests/test_agents.py runs it.

To replace a lead: change its registry entry or prompt, then run the tests. See AGENTS.md.
"""
import hashlib, json, os, pathlib
import contract, sources

HERE = pathlib.Path(__file__).parent
REGISTRY = HERE / "roles" / "registry.json"
_calls = {}

class BudgetExceeded(RuntimeError):
    """A role asked for more than its registry budget allows. Callers treat this as a skipped review."""

class BadOutput(RuntimeError):
    """A role's answer did not match its contract, even after one retry."""

def registry():
    return json.loads(REGISTRY.read_text())["roles"]

def get(role):
    return registry()[role]

def prompt_text(role):
    return (HERE / "roles" / "prompts" / get(role)["prompt"]).read_text()

def prompt_version(role):
    return f"{role}/{hashlib.sha256(prompt_text(role).encode()).hexdigest()[:10]}"

def model(role):
    r = get(role)
    return os.environ.get(r["model_env"], "").strip() or r["model"]

def data(role):
    """{name: value} for the role's registry data sources. This is where changing a source takes effect."""
    return {s: sources.ADAPTERS[s]() for s in get(role).get("data_sources", []) if s in sources.ADAPTERS}

def estimate_tokens(text):
    return len(text) // 3            # deliberately cautious for code and logs

def spend(role, prompt):
    """Charge one call against the role's budget, or raise before any money is spent."""
    b = get(role).get("budget") or {}
    calls = _calls.get(role, 0) + 1
    if b.get("max_calls") and calls > b["max_calls"]:
        raise BudgetExceeded(f"{role}: more than {b['max_calls']} calls in one run")
    size = estimate_tokens(prompt)
    if b.get("max_input_tokens") and size > b["max_input_tokens"]:
        raise BudgetExceeded(f"{role}: about {size:,} input tokens, limit {b['max_input_tokens']:,}")
    _calls[role] = calls

def ask(role, prompt, output):
    """Ask `role` for one object matching schema `output`. One retry when the answer breaks the contract."""
    import llm
    r, sch = get(role), contract.schema(output)
    system = prompt_text(role)
    for attempt in range(2):
        spend(role, prompt)
        got = llm.ask(r["vendor"], system, prompt, sch, tool=f"submit_{output}", model=model(role))
        problems = contract.validate(got, sch)
        if not problems:
            return got
        prompt = (f"{prompt}\n\nYour last answer broke the contract: {'; '.join(problems)[:600]}. "
                  "Answer again, matching the schema exactly.")
    raise BadOutput(f"{role}: {'; '.join(problems)[:300]}")

def check_registry():
    """Every problem that would stop a role being hired. Empty = the team is valid."""
    out, root = [], HERE.parent
    for name, r in registry().items():
        for key in ("mission", "module", "status", "vendor", "model_env", "data_sources", "budget", "outputs"):
            if key not in r:
                out.append(f"{name}: missing {key}")
        if r.get("module") and not (root / r["module"]).exists():
            out.append(f"{name}: module {r['module']} does not exist")
        for s in r.get("data_sources", []):
            if not sources.known(s):
                out.append(f"{name}: unknown data source {s}")
        if r.get("prompt"):
            if not (HERE / "roles" / "prompts" / r["prompt"]).exists():
                out.append(f"{name}: prompt {r['prompt']} does not exist")
            if not (r.get("budget") or {}).get("max_calls"):
                out.append(f"{name}: an AI role needs a call budget")
            if not r.get("model"):
                out.append(f"{name}: an AI role needs a pinned model")
        for o in r.get("outputs", []):
            if not (HERE / "schemas" / f"{o}.json").exists():
                out.append(f"{name}: output schema {o} does not exist")
    return out
