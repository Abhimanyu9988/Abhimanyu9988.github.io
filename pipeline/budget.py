#!/usr/bin/env python3
"""The monthly spending limit and record. Code owns every number here, and no model can change the limit.

Each job writes one file to the analytics branch: analytics/spend-<month>-...-c<cents>.json. Files cannot conflict, and
the listing alone gives the month's total. The first model call in a job checks the total, so a job stops before it
spends and a lab stops before it creates anything. Work already running finishes, so a month can pass the limit slightly.

Costs are list prices plus a fixed estimate for each AWS lab. They are not invoices.

Claude is paid from the $100 of Claude API credit that comes with Abhi's Max plan, which arrives on CLAUDE_CREDIT_DAY
and expires a cycle later. Claude's share of a job goes to analytics/claude-<date>-...-c<cents>.json instead, and only
the part of a cycle above the credit counts toward the monthly limit. A cycle that crosses a month boundary can count
its overflow in both months, which errs on the side of stopping early.
"""
import datetime, json, math, os, re

LIMIT_USD = 50
AWS_LAB_USD = 1.0                              # the upper estimate quoted for a lab. Nothing reads AWS billing yet
LIMITS = "analytics/budget.json"
SPEND = re.compile(r"spend-(\d{4}-\d{2})-.*-c(\d+)\.json")
CLAUDE_SPEND = re.compile(r"claude-(\d{4}-\d{2}-\d{2})-.*-c(\d+)\.json")
CLAUDE_CREDIT_USD = 100                        # Max 5x plan, monthly Claude API credit
CLAUDE_CREDIT_DAY = 29                         # the day the credit arrives (next one 29 Nov 2026)
EXTRA, EXTRA_AWS = [], []                      # dollars this job adds: models from its lab, and that lab's AWS estimate
EXTRA_CLAUDE = []                              # the Claude part of EXTRA, paid from the plan's credit
AWS_ACTUAL = re.compile(r"aws-(\d{4}-\d{2})-(\d{2})-c(\d+)\.json")
_checked = False

class BudgetStop(SystemExit):
    """Exits cleanly: `except Exception` cannot swallow it, and the stop is not a failed job."""
    def __init__(self):
        super().__init__(0)

def month():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")

def in_pipeline():
    return os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("GITHUB_WORKFLOW") != "tests"

def limit(m=None):
    import analytics
    text, _ = analytics.read_file(LIMITS)
    got = json.loads(text).get(m or month()) if text.strip() else None
    return got if isinstance(got, (int, float)) and got > 0 else LIMIT_USD

def cycle_start(today=None):
    """The first day of the current Claude credit cycle, as YYYY-MM-DD."""
    import calendar
    t = today or datetime.datetime.now(datetime.timezone.utc).date()
    def day_in(y, mo):
        return datetime.date(y, mo, min(CLAUDE_CREDIT_DAY, calendar.monthrange(y, mo)[1]))
    start = day_in(t.year, t.month)
    if start > t:
        y, mo = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
        start = day_in(y, mo)
    return start.isoformat()

def claude_used(names, today=None):
    """Dollars of Claude credit used this cycle, at list prices, from file names alone."""
    start = cycle_start(today)
    return sum(int(h[2]) for h in (CLAUDE_SPEND.fullmatch(n) for n in names) if h and h[1] >= start) / 100

def claude_credit():
    """What the CFO reports: Claude credit used and left this cycle, at list prices. Not the Console's own balance."""
    from radar import HOME_REPO, gh
    used = claude_used([f.get("name", "") for f in gh("GET", f"/repos/{HOME_REPO}/contents/analytics?ref=analytics") or []])
    return {"used": round(used, 2), "credit": CLAUDE_CREDIT_USD, "left": round(max(0, CLAUDE_CREDIT_USD - used), 2),
            "cycle_started": cycle_start(), "note": "list prices; the Console billing page has the real balance"}

def spent(m=None):
    """(dollars, {job: dollars}) for the month, from file names alone. AWS reports late, so the higher of its figure and
    the lab estimates counts, and a lab that ran today is never missed. Claude counts only above the plan's credit."""
    from radar import HOME_REPO, gh
    jobs, actual = {}, {}
    files = gh("GET", f"/repos/{HOME_REPO}/contents/analytics?ref=analytics") or []
    over = claude_used([f.get("name", "") for f in files]) - CLAUDE_CREDIT_USD
    if over > 0:
        jobs["claude_over_credit"] = math.ceil(over * 100)
    for f in files:
        hit, aws = SPEND.fullmatch(f.get("name", "")), AWS_ACTUAL.fullmatch(f.get("name", ""))
        if hit and hit[1] == (m or month()):
            job = f["name"].split("-")[-2]
            jobs[job] = jobs.get(job, 0) + int(hit[2])
        if aws and aws[1] == (m or month()):
            actual[aws[2]] = int(aws[3])
    if actual:
        jobs["aws"] = max(actual[max(actual)], jobs.pop("aws_estimate", 0))
    return sum(jobs.values()) / 100, {k: v / 100 for k, v in jobs.items()}

def check():
    """Once per job, before its first model call. If the total cannot be read, nothing is spent."""
    global _checked
    if _checked or not in_pipeline() or os.environ.get("BUDGET_EXEMPT") == "1":
        return
    _checked = True
    try:
        total, cap = spent()[0], limit()
    except Exception as e:
        print(f"::warning title=budget::no spending this run, the total could not be read: {' '.join(str(e).split())[:200]}")
        raise BudgetStop()
    if total >= cap:
        print(f"::notice title=budget::stopped at ${total:.2f} of ${cap:.2f}")
        announce(total, cap)
        raise BudgetStop()

def announce(total, cap):
    """One message per month. A job without a bot token, such as a lab, leaves this to the next job that has one."""
    import analytics
    from radar import comment
    path = f"analytics/budget-stop-{month()}.json"
    if not os.environ.get("BOT_TOKEN") or os.environ.get("HERO_PUBLISH") != "1":
        return
    try:
        if analytics.read_file(path)[1]:
            return
        number = desk()
        analytics.write_file(path, json.dumps({"spent": total, "limit": cap}), None, "budget: limit reached")
        text = (f"Monthly budget reached: ${total:.2f} of ${cap:.2f}. New paid work is paused until next month. "
                "To continue, reply with raise budget to and an amount.")
        import slack
        if not slack.say("spend_tracker", text) and number:
            comment(number, text)
    except Exception as e:
        print(f"::warning title=budget::stop message failed: {' '.join(str(e).split())[:200]}")

def desk():
    from radar import HOME_REPO, gh
    got = [i for i in gh("GET", f"/repos/{HOME_REPO}/issues?labels=cto-desk&state=open&per_page=5") or [] if "pull_request" not in i]
    return got[0]["number"] if got else None

def add_lab(res):
    """The lab job holds no bot token, so the draft job records the lab's spend for it."""
    if os.environ.get("GITHUB_WORKFLOW") == "replicate" and not os.environ.get("FROM_RUN"):
        EXTRA.append(float(res.get("usd") or 0))
        EXTRA_CLAUDE.append(float(res.get("claude_usd") or 0))
        EXTRA_AWS.extend([AWS_LAB_USD] if res.get("aws_used") else [])

def record():
    """At the end of a job. A lost record is reported, and never fails the job that earned it."""
    import llm
    if not in_pipeline():
        return
    claude, other = llm.cost_split()
    claude += sum(EXTRA_CLAUDE)
    other += sum(EXTRA) - sum(EXTRA_CLAUDE)
    usd = claude + other
    if usd <= 0 and not EXTRA_AWS:
        return
    print(f"::notice title=spend::${usd:.3f} (Claude credit ${claude:.3f}, paid per use ${other:.3f})")
    if not os.environ.get("BOT_TOKEN") or os.environ.get("HERO_PUBLISH") != "1":
        return
    try:
        import analytics
        job = re.sub(r"\W", "_", os.environ.get("GITHUB_JOB", "job"))
        run = f"{os.environ.get('GITHUB_RUN_ID', '0')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}-{os.getpid()}"
        analytics.ensure_branch()
        day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        files = ((f"spend-{month()}", job, other), (f"spend-{month()}", "aws_estimate", sum(EXTRA_AWS)),
                 (f"claude-{day}", job, claude))
        for prefix, name, amount in files:
            if amount > 0:
                analytics.write_file(f"analytics/{prefix}-{run}-{name}-c{math.ceil(amount * 100)}.json",
                                     json.dumps({"usd": amount, "workflow": os.environ.get("GITHUB_WORKFLOW"), "job": name,
                                                 "paid_by": "claude_credit" if prefix.startswith("claude") else "per_use"}),
                                     None, f"spend: {name}")
    except Exception as e:
        print(f"::warning title=budget::spend not recorded: {' '.join(str(e).split())[:200]}")

def requested_limit(text):
    """The new limit when Abhi's own words ask for one, else None. Only code reads this, never a model."""
    t = str(text or "")
    m = re.search(r"(?i)\b(?:raise|increase|set|extend|make)\b[^.?!\n]{0,30}\bbudget\b[^.?!\n\d]{0,20}\$?(\d{2,4})\b", t)
    if not m or "?" in t or not 1 <= int(m[1]) <= 1000:
        return None
    return int(m[1])

def set_limit(usd):
    """This month only, so a raise can never outlive the month it was agreed for."""
    import analytics
    text, sha = analytics.read_file(LIMITS)
    data = json.loads(text) if text.strip() else {}
    data[month()] = usd
    analytics.write_file(LIMITS, json.dumps(data, indent=1), sha, f"budget: {month()} limit {usd}")

def record_aws(path):
    """Saves AWS's month-to-date figure from a Cost Explorer reply. AWS reports with a delay, so it can trail by a day."""
    import analytics
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    usd = float(json.load(open(path))["ResultsByTime"][0]["Total"]["UnblendedCost"]["Amount"])
    name = f"analytics/aws-{day}-c{math.ceil(usd * 100)}.json"
    analytics.write_file(name, json.dumps({"usd": usd, "source": "cost-explorer"}), analytics.read_file(name)[1], "spend: AWS actual")

if __name__ == "__main__":
    import sys
    record_aws(sys.argv[1])
