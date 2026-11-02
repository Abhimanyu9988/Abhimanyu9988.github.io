#!/usr/bin/env python3
"""The monthly spending limit and record. Code owns every number here, and no model can change the limit.

Each job writes one file to the analytics branch: analytics/spend-<month>-...-c<cents>.json. Files cannot conflict, and
the listing alone gives the month's total. The first model call in a job checks the total, so a job stops before it
spends and a lab stops before it creates anything. Work already running finishes, so a month can pass the limit slightly.

Costs are list prices plus a fixed estimate for each AWS lab. They are not invoices.
"""
import datetime, json, math, os, re

LIMIT_USD = 50
AWS_LAB_USD = 1.0                              # the upper estimate quoted for a lab. Nothing reads AWS billing yet
LIMITS = "analytics/budget.json"
SPEND = re.compile(r"spend-(\d{4}-\d{2})-.*-c(\d+)\.json")
EXTRA, EXTRA_AWS = [], []                      # dollars this job adds: models from its lab, and that lab's AWS estimate
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

def spent(m=None):
    """(dollars, {job: dollars}) for the month, from file names alone. AWS reports late, so the higher of its figure and
    the lab estimates counts, and a lab that ran today is never missed."""
    from radar import HOME_REPO, gh
    jobs, actual = {}, {}
    for f in gh("GET", f"/repos/{HOME_REPO}/contents/analytics?ref=analytics") or []:
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
        EXTRA_AWS.extend([AWS_LAB_USD] if res.get("aws_used") else [])

def record():
    """At the end of a job. A lost record is reported, and never fails the job that earned it."""
    import llm
    if not in_pipeline():
        return
    usd = llm.cost_usd()[0] + sum(EXTRA)
    if usd <= 0 and not EXTRA_AWS:
        return
    print(f"::notice title=spend::${usd:.3f}")
    if not os.environ.get("BOT_TOKEN") or os.environ.get("HERO_PUBLISH") != "1":
        return
    try:
        import analytics
        job = re.sub(r"\W", "_", os.environ.get("GITHUB_JOB", "job"))
        run = f"{os.environ.get('GITHUB_RUN_ID', '0')}-{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}-{os.getpid()}"
        analytics.ensure_branch()
        for name, amount in ((job, usd), ("aws_estimate", sum(EXTRA_AWS))):
            if amount > 0:
                analytics.write_file(f"analytics/spend-{month()}-{run}-{name}-c{math.ceil(amount * 100)}.json",
                                     json.dumps({"usd": amount, "workflow": os.environ.get("GITHUB_WORKFLOW"), "job": name}),
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
