# Rechecking published labs

Every lab behind a live ShowMerit article keeps being tested, and each article shows its last recheck, linked to the run.
`pipeline/recheck.py` does the work. No model is called.

## Free, on the 1st and 16th of each month (`recheck.yml`, 02:37 UTC)

- Each lab with a `check.sh` runs it on a GitHub runner, in a job with no secrets: once with the versions the article used,
  and once with the newest releases of what it pins (the Java agent, Helm charts and tagged images). The second run happens
  only when something newer exists.
- A newer image keeps the tag's shape and major version, so `postgres:16.4-alpine` can become `16.10-alpine` but not
  `17.6-alpine`. The agent and charts move to their newest stable release.
- Every link the article cites is fetched again, twice before it counts as broken. A site that refuses robots (401, 403,
  429) is not judged. Links inside code blocks are commands, not citations, and are skipped.
- Labs with no `check.sh` get only the link check and their quarterly paid run.

## Paid, the first Tuesday of each month (`recheck-aws.yml`, 02:23 UTC)

- Labs are taken oldest article first. Lab *i* belongs to month *i* % 3 of a 3-month cycle that starts in December 2026,
  so each lab runs once a quarter. Eight labs run 3, 3 and 2 a month, and a new article joins a month without moving the
  others. The schedule fires every Tuesday, and only the first Tuesday of the month runs labs.
- The month's labs run one after another, never in parallel, because the janitor clears the whole account. Each one waits
  behind any replicate lab. Each runs once on the AWS lab account with the newest versions, through `lab.py --recheck`:
  the same sandbox, sweep, janitor, destroy and safety net as a replicate lab, with no repair.
- The budget is checked before each lab, counting the labs before it in the same run at about $1 each. If the next lab
  would pass the limit in `analytics/budget.json`, it and the labs after it are skipped, and the run summary and the
  verdict list which ones. A skipped lab opens no issue. The report job records each lab's estimate.
- First quarter: 1 Dec 2026 Fargate scale-to-zero, broken ingress, SQS. 5 Jan 2027 canary ALB, KEDA, one Helm value.
  2 Feb 2027 one-annotation Java, Java agent. Then again from 2 Mar 2027.
- To run one now: `gh workflow run recheck-aws.yml --ref main -f lab=<article slug>`, or leave `lab` empty for this
  month's labs. Each lab costs about $1.

## Results

- `analytics/rechecks.json` holds dates, versions, pass or fail and run links. It is public, so no lab output goes there.
  showmerit's daily build copies it into its `data/rechecks.json` and shows one line per article.
- Each lab keeps its own result, issue and site line. A failure opens one private issue in `team-admin`, labelled `recheck` and assigned to Abhi, with the article, lab,
  versions that passed and failed, the failing step, its error lines and the run. A repeat failure comments on the open
  issue. A pass opens nothing. Close the issue when it is dealt with.
- Nothing is ever sent to a vendor or an upstream project. Abhi decides that.
- A failed lab never turns a run red. Read the `verdict` job's name for the counts.
