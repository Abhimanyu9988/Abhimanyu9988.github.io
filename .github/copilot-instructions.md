# Review instructions for Copilot

This repo is Abhimanyu's blog about AWS containers. Every post replicates a real problem in a disposable AWS
lab and shows the fix. Draft PRs come from a bot and contain a post (`site/_posts/*.md`), the lab
(`labs/<slug>/`: Terraform, `reproduce.sh`, `fix.sh`, `cleanup.sh`, README) and the raw output
(`labs/<slug>/evidence/*.log`).

## On draft post PRs (branch `draft/...`)
- **Accuracy:** every command, output, error message, version and number in the post must appear in the lab
  files or the evidence logs. Flag anything that doesn't match, with the line where it should come from.
- **Can a reader follow it?** The "Replicate it yourself" section must work by hand, in order, using plain
  `terraform init` / `terraform apply`. Flag missing steps, files the reader would need but isn't given,
  commands that differ from the scripts, and anything that only works inside the pipeline.
- **Claims:** the cause must not be stated more strongly than the evidence and sources support.
- **Tone:** it should read like a person explaining to a colleague: plain words, short paragraphs, no AI
  clichés ("delve", "seamless", "robust", "game-changer", "in today's..."). Point out sentences that sound
  robotic, and suggest a plainer version.
- **Lab safety:** scripts must not run `terraform apply/destroy`; IAM roles must use `var.permissions_boundary`;
  nothing should print credentials.
- Don't nitpick formatting of the evidence logs; they are raw output.

## On pipeline PRs (`pipeline/`, `.github/workflows/`, `infra/`)
- The bot must never write GitHub links, `#N` / `owner/repo#N` references or @mentions into issues,
  comments, PR text or commit messages (see `assert_safe` / `WRITE_RULES` in `pipeline/radar.py`).
  Flag any new write path that skips those checks.
- AI-written lab code must run only in the sandbox container (`run(..., sandbox=True)` in `pipeline/lab.py`)
  and must never see model keys or GitHub tokens.
- AWS cost guardrails live in `infra/bootstrap/main.tf` (permissions boundary) and `pipeline/check_plan.py`.
  Flag anything that loosens them.
