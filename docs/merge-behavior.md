# What changes when PR 89 merges

Everything below changes on merge, even with `EDITOR_MODE=shadow`. All variables are repository variables.

| Behaviour | Before | After merge | Undo | Test |
| --- | --- | --- | --- | --- |
| Model choice | Newest model picked at run time, unless a variable is set | Pinned in `pipeline/prices.json`. Existing model variables still win | `MODEL_AUTO=true`, or set the model variable | `default: models are pinned`, `rollback: MODEL_AUTO=true` |
| Scout model | Newest Haiku listed | `claude-haiku-5-5` | Same as above | Same as above |
| Gemini order | Newest listed first | Pin first, listed models as quota fallback | Same as above | `pin:` tests |
| Topic ranking | Panel votes and self-scores | Same, until a goal exists. A goal's weight then outranks votes, only for titles matching its focus areas | `GOAL_RANKING=off`, or `/goal drop <id>` | `default: with no goals`, `change: an active goal`, `rollback: GOAL_RANKING=off` |
| Topic prompts | Every topic must feature Splunk | Splunk wherever it fits | Revert `SPLUNK_RULE` in `pipeline/radar.py` | `Splunk is no longer mandatory` |
| LinkedIn choice, including the monthly post | Best-voted post that passes the fact check, even with 0 votes; the writer's text if none pass | Needs 2 of 3 votes and the fact check. Otherwise the post ships without LinkedIn text, and the PR shows the writer's text | `LINKEDIN_MIN_VOTES=0` | `LinkedIn:` tests, `rollback: LINKEDIN_MIN_VOTES=0` |
| Cover screenshots | Filter `HERO_RESULT` lines | Also filter `HERO_MEASURE` lines | None needed | `HERO_MEASURE` filter |
| PR body | Technical checks | Adds a folded shadow review and a dollar estimate | `EDITOR_MODE=off` | `shadow: a failed check only reports` |
| Radar runs | No Editor | Up to 2 briefs per run, after issues open, as issue comments | `EDITOR_MODE=off` | `shadow:` tests |
| Draft job | No record | Writes `analytics/review-<slug>.json` on the analytics branch | `EDITOR_MODE=off` | Pipeline suite in shadow mode |
| Labs, article prompts, publishing | Unchanged | Unchanged in shadow | Not needed | `shadow: no rule reaches the lab`, `shadow: nothing reaches the writer` |
| New workflows | None | `tech-head` on pipeline PRs, `assistant` on `/goal`, manual `editor-eval` and `live-check` | Disable the workflow in the Actions tab | Workflow tests |
| Analytics weekly job | Unchanged | Unchanged, apart from pinned models | Not needed | Pipeline suite |

**Rollback of everything:** set `EDITOR_MODE=off`, `MODEL_AUTO=true`, `GOAL_RANKING=off` and `LINKEDIN_MIN_VOTES=0`, then disable `tech-head` and `assistant`. To remove the code, revert the merge commit. Files on the analytics branch are harmless and can stay.

## Tech Head trust boundary

| Risk | State | Hardening |
| --- | --- | --- |
| A PR edits its own review | Fixed. `pull_request_target` loads the workflow from the base branch | Keep the rule that no job runs PR code |
| A scanner runs beside secrets | Fixed. Scanners run in a job with no secrets, and pass only a report file | None |
| A PR silences a scanner | Fixed for `.bandit`, `# nosec`, zizmor config, inline ignores and `.gitignore` | None |
| A scanner fails silently | Fixed. Reports say clean, findings, partial or unavailable, and blocking mode fails on anything incomplete | None |
| A secret in the diff reaches a model | Partly fixed. The diff is not sent when a known key pattern fires. Other key formats are missed | Add a real secret scanner later |
| Unpinned actions and scanner hashes | Open | Pin actions to commit SHAs. Add pip hashes |
| **Any branch workflow can read repository secrets** | **Open, the main remaining risk** | Move `OPENAI_API_KEY` and `BOT_PRIVATE_KEY` for this job into a `tech-head` environment limited to `main`. Do the same for other keys when convenient. This is a repository setting, so only you can change it |

`pull_request_target` is flagged as a blocker by Tech Head's own code rule whenever it is added. For `tech-head.yml` that is
the intended design: it stops a pull request editing its own review, and no job runs PR code. Change it only with that in mind.

`TECH_HEAD_MODE=blocking` needs care. The check runs are named `scan` and `review`. A required `review` stays pending on PRs the path filter skips, and fork PRs show it as skipped. A required check therefore needs an always-run gate job first, which this PR does not add.

## Manual workflows

`live-check` and `editor-eval` hold model keys and run the code they check out, so both refuse any ref except `main`, check
out `main` explicitly, and use the `live-check` environment. That stops a mistaken branch run. It does not stop a hostile
branch, because `workflow_dispatch` reads the workflow file from the selected branch, and that branch can delete the guard.

**To close it:** in Settings, Environments, open `live-check`, set deployment branches to `main` only, and move the keys into
that environment. Repository-level keys stay readable from any branch. The older manual workflows (`radar`, `problems`,
`replicate`, `hero`, `monthly-oss`, `analytics`) have the same exposure and predate this PR.

## Live validation record

Run on 2 November 2026 in GitHub Actions with the repository's real keys. Costs use list prices.

| Step | Model | Seconds | Tokens in | Tokens out | Cost |
| --- | --- | ---: | ---: | ---: | ---: |
| Model IDs listed, all 5 found | None | 1.7 | 0 | 0 | $0 |
| Ping, Haiku | claude-haiku-5-5 | 0.9 | 471 | 47 | under $0.001 |
| Ping, Sonnet | claude-sonnet-5-5 | 1.9 | 471 | 47 | $0.001 |
| Ping, OpenAI | gpt-5.5 | 2.4 | 92 | 23 | $0.001 |
| Ping, Gemini | gemini-3.1-pro-preview | 2.1 | 84 | 9 | under $0.001 |
| Editor brief with web search | claude-opus-5-5, claude-sonnet-5-5 | 52.6 | 36,455 | 6,496 | $0.219 |
| Editor scorecard on PR 88 | claude-opus-5-5 | 37.1 | 10,375 | 4,104 | $0.124 |
| Tech Head, first run | gpt-5.5 | 26.1 | 18,583 | 1,841 | $0.148 |
| Tech Head, risk-ordered run | gpt-5.5 | 48.8 | 29,704 | 3,670 | $0.259 |

Total: $0.753. These runs predate the final fixes, including Tech Head batching, so that code has only offline tests. The
`tech-head` and `assistant` workflows have not run on GitHub, because they cannot run until they exist on `main`.
