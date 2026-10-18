# Abhimanyu9988.github.io

Real problems on AWS containers, **replicated in a disposable AWS lab** and explained:
[abhimanyu9988.github.io](https://abhimanyu9988.github.io). Every post links to the exact lab
(Terraform + scripts) and the raw output it produced, under [`labs/`](labs/).

## How a post gets made

Three AIs work as a panel: **Claude** does the main work, **ChatGPT** reviews it, and **Gemini** breaks ties.
I review and edit every post before it goes out.

```
 radar (Mon) / problems (Thu)          replicate (on the `replicate` label)                 publish (on merge)
 ─────────────────────────────         ──────────────────────────────────────────           ──────────────────
 AWS What's New, releases, HN,   ──▶   I approve the aws-lab deployment (one tap)    ──▶   GitHub Pages
 GitHub issues of AWS container        Claude writes Terraform + reproduce/fix scripts      + dev.to
 projects, Stack Overflow              ChatGPT reviews · Gemini rules on disputes           + Hashnode
 Claude proposes · ChatGPT votes       plan → plan check → apply → reproduce → fix          (canonical link
 Gemini breaks ties                    always: cleanup → destroy                            back to the site)
 → topic issues; best → `replicate`    Claude drafts the post from the REAL output
                                       ChatGPT checks it · Gemini rules → draft PR → I edit & merge
```

| Piece | File |
|---|---|
| Panel (vote, review, tiebreak) | `pipeline/llm.py` |
| Topic finders + GitHub write rules | `pipeline/radar.py`, `pipeline/problems.py` |
| Lab runner | `pipeline/lab.py`, `pipeline/check_plan.py` |
| Drafter | `pipeline/draft.py` |
| Syndication | `pipeline/publish.py` |
| AWS lab account guardrails | `infra/bootstrap/main.tf` |

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
