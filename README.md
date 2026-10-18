# Abhimanyu9988.github.io

Real problems on AWS containers, **replicated in a disposable AWS lab** and explained:
[abhimanyu9988.github.io](https://abhimanyu9988.github.io). Every post links to the exact lab
(Terraform + scripts) and the raw output it produced, under [`labs/`](labs/).

## How a post gets made

Three AIs work as a panel, Claude, ChatGPT and Gemini, and I review and edit every post before it goes out.

1. **Choose (Mon radar, Thu problems).** Each AI reads the week's sources and proposes its own picks: AWS What's
   New, releases and issues of AWS container projects, and Stack Overflow. It also sees what has performed well so
   far. Then all three vote on the pooled list. Topics with 2 of 3 votes open as issues showing who proposed them
   and how each AI voted. The best one gets `replicate`.
2. **Replicate (I approve the `aws-lab` deployment).** Claude writes the Terraform and scripts, ChatGPT reviews them
   and Gemini settles disputes. The lab runs: plan → plan check → apply → reproduce → fix. Destroy always runs.
3. **Confirm.** All three read the real output and say whether it genuinely replicated the problem. 2 of 3 is
   needed to write.
4. **Write.** ChatGPT writes the article, story-first and in my voice, with every command to replicate it by hand,
   plus a LinkedIn post. Claude checks the technical details against the real output and Gemini settles disputes.
   The bot opens a PR that shows the post itself.
5. **Publish (when I merge).** The site and dev.to are automatic, and Hashnode is too once a token is saved. A
   report issue gives a one-tap Medium import and a LinkedIn link that opens the post box already filled in.
6. **Measure (weekly).** dev.to numbers come in automatically; LinkedIn and Medium numbers via a `/stats` comment.
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
- **Accuracy:** posts may only show output that appears in the lab's evidence logs, and ChatGPT checks that
  before I see the draft.

## Knobs (repository variables)

`CLAUDE_MODEL`, `RADAR_MODEL`, `OPENAI_MODEL`, `GEMINI_MODEL` pin models. Left empty, the pipeline picks the
newest suitable model from each provider. Secrets: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`,
`BOT_PRIVATE_KEY` (repo) and `DEVTO_API_KEY`, `HASHNODE_TOKEN`, `HASHNODE_PUB_ID` (environment `publish`).
