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
| Covers and screenshots | Filter `HERO_RESULT` lines | Also filter `HERO_MEASURE` lines | None needed | `HERO_MEASURE` filter |
| Topic prompts | No goals text | Adds a goals list and a recent Splunk mix | `GOAL_RANKING=off` removes goals | `rollback: GOAL_RANKING=off` |
| Failed labs | The lab reads earlier failure comments | Unchanged, and shadow comments are skipped | None needed | `shadow: a lab never reads` |
| `tests` workflow | Pipeline and workflow changes | Also runs on `labs/**` and `AGENTS.md` | Revert the path filter | Workflow tests |
| LinkedIn with fewer than 2 candidates | Writer's post ships unchecked | No post ships | `LINKEDIN_MIN_VOTES=0` | `LinkedIn:` tests |
| PR body | Technical checks | Adds a folded shadow review and a dollar estimate. `EDITOR_MODE=off` removes the review; the estimate stays | `EDITOR_MODE=off` | `shadow: a failed check only reports` |
| Radar and `problems` runs | No Editor | Up to 2 briefs per run, after issues open, as issue comments | `EDITOR_MODE=off` | `shadow:` tests |
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
| **Any branch workflow can read repository secrets** | **Open, the main remaining risk** | Follow the phased migration below. Never delete a repository secret first |

`pull_request_target` is flagged as a blocker by Tech Head's own code rule whenever it is added. For `tech-head.yml` that is
the intended design: it stops a pull request editing its own review, and no job runs PR code. Change it only with that in mind.

`TECH_HEAD_MODE=blocking` needs care. The check runs are named `scan` and `review`. A required `review` stays pending on PRs the path filter skips, and fork PRs show it as skipped. A required check therefore needs an always-run gate job first, which this PR does not add.

## Manual workflows

`live-check` and `editor-eval` hold model keys and run the code they check out, so both refuse any ref except `main`, check out `main` explicitly, and use the `live-check` environment. That stops a mistaken branch run. It does not stop a hostile branch, because `workflow_dispatch` reads the workflow file from the selected branch. Keys that exist only in a main-only environment close that gap.

## Secret inventory and migration

Observed from job logs and workflow files on 2 November 2026. Environment secrets reach only jobs that name that environment, and a job can name only one.

| Secret | Scope today | Used by |
| --- | --- | --- |
| `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` | Repository | `radar`, `problems`, `hero`, `monthly-oss`, `redraft`, `replicate` lab and draft, `analytics`. After PR 89: `assistant`, `live-check`, `editor-eval` (Anthropic only), `tech-head` (OpenAI only) |
| `BOT_PRIVATE_KEY` | Repository | All of the above except the lab job, plus `linkedin` and `merge-on-approve`. After PR 89: `assistant`, `live-check`, `tech-head` |
| `SPLUNK_ACCESS_TOKEN`, `SPLUNK_API_TOKEN` | Repository | `replicate` lab, `splunk-check`, and a presence check in `radar` |
| `DEVTO_API_KEY` | `publish` environment | `publish` syndicate, `analytics` |
| `GOATCOUNTER_TOKEN`, `HASHNODE_TOKEN`, `HASHNODE_PUB_ID`, `MEDIUM_TOKEN` | Empty in the last run | `analytics`, `publish` syndicate |

**Do not move repository secrets yet.** Deleting a repository key stops every job that has no environment holding it.

| Trigger | `GITHUB_REF` | Can use a main-only environment |
| --- | --- | --- |
| `schedule`, `issue_comment`, `issues`, `pull_request_target` | Default branch | Yes |
| `workflow_dispatch` | The selected ref | Only from `main` |
| `push` | The pushed ref | Only for pushes to `main` |
| `pull_request_review` (`merge-on-approve`) | `refs/pull/N/merge` | **No. The job would be refused and approved posts would stop merging** |

**Phase 1, before PR 89 merges. Nothing existing is touched.**

1. Create `live-check` and `tech-head`, each limited to the selected branch `main`. Add no required reviewers to `tech-head`.
2. Create new provider keys with spend limits, and a second private key on the GitHub App. Secrets are write-only, so you cannot copy an existing value out of GitHub, and new keys are what gain protection: a value that also sits at repository level stays exposed through that copy.
3. Add new `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` and `BOT_PRIVATE_KEY` values to `live-check`. Add new `OPENAI_API_KEY` and `BOT_PRIVATE_KEY` values to `tech-head`. An environment secret takes precedence over a repository secret of the same name.
4. Leave every repository secret in place.

**Phase 2, after PR 89 merges and passes `live-check`.** Create a `pipeline` environment limited to `main`. Limit `publish` and `aws-lab` to `main` too, because otherwise a branch could still reach keys placed there. Add new model keys to `pipeline`, `publish` and `aws-lab`. Add `environment: pipeline` to `radar`, `problems`, `hero`, `monthly-oss`, `redraft`, `replicate` draft and `assistant`. `analytics` and the lab job cannot name a second environment, and the lab job must keep `aws-lab`, because its AWS role trusts that environment by name. Confirm one successful run of each job, with Splunk checks included, before the next phase.

**Phase 3, once every job has passed.** Delete the three repository model keys, confirm scheduled jobs still run, and only then revoke the old keys. Revoking first would stop anything still using them. To roll back before revocation, add the repository secrets back.

**`BOT_PRIVATE_KEY` stays at repository level for now.** `merge-on-approve` needs it and cannot use a main-only environment. Allowing `refs/pull/*/merge` would also let any branch's pull request reach the key. Closing this needs a separate design change, such as a narrower app for merges.

`splunk-check` and `radar` read Splunk secrets at repository level, so those stay until they are given an environment too.

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
