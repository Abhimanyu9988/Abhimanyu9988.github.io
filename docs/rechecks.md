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

## Paid, one lab a week (`recheck-aws.yml`, Wednesdays 01:47 UTC)

- Labs are taken oldest article first. Lab *i* has week *i* of a 13-week cycle starting Monday 9 Nov 2026, so each lab runs
  once a quarter and at most one runs a week. Past 13 labs, labs that share a week take turns.
- The lab runs once on the AWS lab account with the newest versions, through `lab.py --recheck`: the same sandbox, sweep,
  janitor, destroy and safety net as a replicate lab, with no repair. It queues behind any replicate lab.
- Before anything is created, the run checks the month's spend. If about $1 more would pass the limit in
  `analytics/budget.json`, it stops and says so in the run summary. The report job records the lab's estimate.
- To run one now: `gh workflow run recheck-aws.yml --ref main -f lab=<article slug>`. It costs about $1.

## Results

- `analytics/rechecks.json` holds dates, versions, pass or fail and run links. It is public, so no lab output goes there.
  showmerit's daily build copies it into its `data/rechecks.json` and shows one line per article.
- A failure opens one private issue in `team-admin`, labelled `recheck` and assigned to Abhi, with the article, lab,
  versions that passed and failed, the failing step, its error lines and the run. A repeat failure comments on the open
  issue. A pass opens nothing. Close the issue when it is dealt with.
- Nothing is ever sent to a vendor or an upstream project. Abhi decides that.
- A failed lab never turns a run red. Read the `verdict` job's name for the counts.
