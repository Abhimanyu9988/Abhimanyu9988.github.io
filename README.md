# Abhimanyu9988.github.io

Real problems on AWS containers, **replicated in a disposable AWS lab** and explained:
[abhimanyu9988.github.io](https://abhimanyu9988.github.io). Every post links to the exact lab
(Terraform + scripts) and the raw output it produced, under [`labs/`](labs/).

## How a post gets made

Three AIs work as a panel, Claude, ChatGPT and Gemini, and I review and edit every post before it goes out.

1. **Choose (Monday and Thursday).** Each AI proposes a how-to of the shape *deploy X on EKS or ECS with Y, then
   monitor it*, with Splunk Observability Cloud wherever it fits (Argo CD with self-heal, Helm, KEDA, the OpenTelemetry Operator...),
   from official docs, AWS What's New, project releases and Hacker News, scored on "would an ECS/EKS engineer think
   *I want to set this up*?". All three vote on the pooled list; topics with 2 of 3 votes open as issues, and the
   best one gets `replicate`. (The old "fix a real problem" finder still exists; run problems.yml by hand.)
2. **Lab (I reply "approve" to the topic's email).** The bot marks the panel's pick; nothing runs until I reply
   **approve** to the issue email (or comment it). Then For a build topic the lab shows the "before", builds the thing and
   proves it works with real output; for a problem it reproduces the problem and fixes it. Claude writes the
   Terraform and scripts, ChatGPT reviews them
   and Gemini settles disputes. The lab runs: plan → plan check → apply → reproduce → fix. Destroy always runs.
3. **Confirm.** All three read the real output and say whether it genuinely replicated the problem. 2 of 3 is
   needed to write.
4. **Write.** The three pick a title that names the reader's symptom. ChatGPT writes a short, problem-first article
   in my voice (about 800 words: symptom, cause, fix, one block that runs the whole lab), plus a "problem I solved"
   LinkedIn post. Claude checks the technical details against the real output and Gemini settles disputes. Posts
   that run long are cut to length. A cover image is drawn from the lab's real output (every line and number is
   checked against the logs). The bot opens a PR that shows the cover and the post. `/redraft <notes>` on the PR
   rewrites it from the same lab run.
5. **Publish (when I approve the draft PR; it merges itself).** A "Ready to post" email then has the LinkedIn text
   to copy, the picture, the Medium import link and the full article for copy-paste. The site and dev.to (with the cover) are automatic, and Hashnode is too once a token
   is saved. A report issue gives the Medium import link. After importing, I comment `/medium <story link>` (or
   `/linkedin`) on it, and my LinkedIn draft opens in my profile repo with the dev.to and Medium links and the cover;
   `/post` there publishes it.
6. **Monthly open-source summary (1st of the month).** Every PR I got merged last month, outside my own repos:
   what it changed, how it's used today (the release that ships it, how many people asked for it) and what it
   changes next. ChatGPT writes it from the PR data only, Claude fact-checks, Gemini settles disputes, and it
   opens as a draft post like the others.
7. **Measure (weekly).** dev.to numbers come in automatically; LinkedIn and Medium numbers via a `/stats` comment.
   Everything goes into a dated, append-only ledger (`analytics/ledger.csv` on the `analytics` branch). The three
   AIs write what's working (`analytics/insights.md`), and the next topic choice reads it.

| Piece | File |
|---|---|
| Panel (ask, vote, verdict, review/revise) | `pipeline/llm.py` |
| Topic finders + GitHub write rules | `pipeline/radar.py`, `pipeline/problems.py` |
| Lab runner | `pipeline/lab.py`, `pipeline/check_plan.py`, `pipeline/lab.Dockerfile` |
| Drafter | `pipeline/draft.py` |
| Publishing + share links | `pipeline/publish.py` |
| Impact log | `pipeline/analytics.py` |
| AWS lab account guardrails | `infra/bootstrap/main.tf` |

To rewrite a draft without another AWS run, run `replicate` with `issue` and `from_run` (the earlier run's id).

## The team around the pipeline

`AGENTS.md` is the map and rulebook for every AI working here. New pieces, all replaceable through `pipeline/roles/registry.json`:

- **Topic Investment Gate and Evidence Contract:** before a lab, the Editor names the reader and the measurements the lab must produce. Afterward, code checks them.
- **Editor:** scores a draft on separate technical, editorial and marketing dimensions, once `EDITOR_REVIEW=on`.
- **Tech Head:** reviews every pipeline PR with code rules, scanners and one AI reviewer.
- **Executive Assistant:** comment `/goal <what you want>` on any issue, and the team's priorities follow. `/goals` lists them.

`EDITOR_MODE` defaults to `shadow`, which reports without changing labs, drafts or publishing.

## Beyond the posts (AWS Hero helpers, `pipeline/hero.py`)

- **Community Builders watcher (Mondays):** when the application window opens, or in early January when it
  usually does, an issue opens with an application kit: every post with its reach.
- **Monthly kits (1st of the month):** last month's best lab becomes a 10-minute talk kit (abstract, CFP blurb,
  timing, live demo) and a 60-minute student session pack (goals, cost safety, agenda, hands-on, quiz, feedback).
- **`/reply <question link>`:** comment it on any issue to get a drafted answer for a re:Post, Stack Overflow or
  GitHub question, linking your lab when it genuinely helps. You post it yourself.
- Talks, sessions and answers you post go into the impact log with `/stats <post> talk|teaching|repost ...`.

## Guardrails

- **AWS:** labs run in a separate, dedicated account through a short-lived OIDC role with a permissions boundary
  (one region, small instance types and volumes, default tenancy, no reservations, dedicated hosts, NAT gateways,
  node groups or ASGs, no IAM escalation). There is an allowlist plan check before every apply and a $10/month
  budget that pauses the lab role at 80%. Destroy always runs, from the last config that planned cleanly. A sweep
  and a janitor remove anything billable that a crashed run left behind. Only one lab runs at a time, and a third
  queued request replaces the second.
- **Sandbox:** Terraform and every AI-written script run in a throwaway container (`pipeline/lab.Dockerfile`)
  that gets only the AWS credentials. It never sees the model keys, GitHub tokens or the runner. Logs and evidence
  are scrubbed of account IDs and keys before they're published.
- **GitHub:** the bot can only open issues, comment, label, create `draft/*` branches with files under
  `site/_posts/` and `labs/`, and open PRs into `main` that I must approve. Nothing it writes to an issue,
  comment, PR or commit message can contain a GitHub link, reference or @mention, so other projects never get
  pinged. `tests/test_safety.py` checks every one of these rules.
- **Accuracy:** posts may only show output that appears in the lab's evidence logs, and Claude checks that
  before I see the draft.

## Knobs (repository variables)

`CLAUDE_MODEL`, `RADAR_MODEL`, `OPENAI_MODEL`, `GEMINI_MODEL` pin models. Left empty, the pipeline picks the
newest suitable model from each provider. Secrets: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`BOT_PRIVATE_KEY` (repo) and `DEVTO_API_KEY`, `HASHNODE_TOKEN`, `HASHNODE_PUB_ID` (environment `publish`).
