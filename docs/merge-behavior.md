# What changes when PR 89 merges

Everything below changes on merge, even with `EDITOR_MODE=shadow`. All variables are repository variables.

| Behaviour | Before | After merge | Undo | Test |
| --- | --- | --- | --- | --- |
| Model choice | Newest model picked at run time, unless a variable is set | Pinned in `pipeline/prices.json`. Existing model variables still win | `MODEL_AUTO=true`, or set the model variable | `default: models are pinned`, `rollback: MODEL_AUTO=true` |
| Scout model | Newest Haiku listed | `claude-haiku-5-5` | Same as above | Same as above |
| Gemini order | Newest listed first | Pin first, listed models as quota fallback | Same as above | `pin:` tests |
| Topic ranking | Panel votes and self-scores | Same, until a goal exists. A goal's weight then outranks votes | `GOAL_RANKING=off`, or `/goal drop <id>` | `default: with no goals`, `change: an active goal`, `rollback: GOAL_RANKING=off` |
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
| A PR silences a scanner | Fixed for `.bandit`, `# nosec` and zizmor config | None |
| A scanner fails silently | Fixed. Reports say clean, findings, partial or unavailable, and blocking mode fails on anything incomplete | None |
| A secret in the diff reaches a model | Fixed. The diff is not sent when the secret rule fires | Add a real secret scanner later |
| Unpinned actions and scanner hashes | Open | Pin actions to commit SHAs. Add pip hashes |
| **Any branch workflow can read repository secrets** | **Open, the main remaining risk** | Move `OPENAI_API_KEY` and `BOT_PRIVATE_KEY` for this job into a `tech-head` environment limited to `main`. Do the same for other keys when convenient. This is a repository setting, so only you can change it |

Before enabling `TECH_HEAD_MODE=blocking`, also make `tech-head` a required check on `main`.
