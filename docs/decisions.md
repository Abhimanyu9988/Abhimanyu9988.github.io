# Decisions

Newest first. Each entry says what was decided and why, so a future agent does not undo it by accident.

## 2026-11-02: Enforce requires a successful Editor scorecard

`EDITOR_REVIEW` cannot switch the Editor off in enforce. An outage stops the draft.

## 2026-11-02: Measurements are graded, not trusted

A lab script prints its own evidence, so code can catch lost, typed-in and unsupported values but cannot prove a value.
Stronger proof would need a runner-side re-query, which is out of scope for now.

## 2026-11-02: Tech Head uses pull_request_target with no PR code execution

A PR must not be able to edit its own review. Scanners run in a separate job with no secrets.

## 2026-11-02: Milestone 1 builds gates before the Editor

The Topic Investment Gate and Evidence Contract come first, because code can check them. The Editor's scorecard is
built but stays off until those two have been evaluated. A scorecard from another AI is not proof of quality.

## 2026-11-02: Shadow mode must not change production behaviour

In shadow mode, briefs go in issue comments, which labs do not read. Review records go to the analytics branch.

## 2026-11-02: Low views do not prove low quality

Three separate questions: are posts worth reading, are the right people finding them, and do readers act afterward?
The Editor addresses only the first. With so little traffic, milestone success uses human ratings and saved cases.

## 2026-11-02: Cost per accepted worthwhile article is the main cost measure

Model fees per article hide the cost of rejected topics and failed labs. Stopping a weak topic early can save more
than using a cheaper model.

## 2026-11-02: Splunk is used wherever it fits

Splunk is no longer required in every topic. The aim is about half of posts over time, and it is never forced.

## 2026-11-02: Reviews, goals and costs live on the analytics branch

Git keeps history and needs no new service. The existing bot rule already allows `analytics/*`.

## 2026-11-02: Fixed workflow, no LLM orchestrator

A fixed order is testable, costs nothing per decision, and cannot delegate recursively.

## 2026-11-02: One approval per article

Success means excellent articles with minimal effort from Abhi. Routine decisions stay with agents inside budgets and
permissions, and each article reaches him as one PR to approve or reject once.

## 2026-11-02: The final live check runs on merge

`live-check` holds keys, so it runs only from `main`. A merge that changes `pipeline/live-check.request` starts it, so the
check needs no manual command. A push to `main` is trusted code, and a branch cannot start this trigger.

## 2026-11-02: Tech Head findings accepted after the merge

The first OpenAI review of Milestone 1 ran on merge. These were judged and left as they are:

- `pull_request_target` in `tech-head.yml` is the design. No job runs code from the PR.
- `live-check` starting on a push to `main` stays, because only an approved merge can change the request file.
- `docs/merge-behavior.md` keeps the secret names. They are already public in the workflow files.
- The brief marker stays outside `safe_text`. It is base64 inside an HTML comment, and bot writes are still checked as a whole.

## 2026-11-02: One CTO, one budget, few emails

Abhi set a $50 monthly limit and asked for a CTO he can argue with by email. The CTO lives in one issue, because a reply to
a GitHub email already becomes a comment, so this needs no new service or credential. The limit stops new paid work at
the first model call in a job, so a lab stops before it creates anything. With that limit in place, the panel's pick runs
a lab without an approve reply. Topic issues stay unassigned, so an article reaches him once, as its PR.

The public team page shows the roster and articles only. Spending and his requests stay private.

## 2026-11-02: Slack by polling, and a read-only AWS billing role

Abhi chose Slack and the cheapest setup. A free workspace, one bot token and a 10-minute scheduled poll need no server, so
replies take minutes rather than seconds. Internal apps keep Slack's higher `conversations.history` limits.

He asked for the CTO to log in with his credentials. It uses a new role that can only read costs, through OIDC with no
stored secret, because a stored personal login would hand an agent the whole account and would stop at MFA.

## 2026-11-02: Private admin page and an Operations Manager

Abhi asked for an admin page and a manager who checks that effort produces results. This repo is public, so its analytics
branch and issues are too. The admin page, daily log and task list go to the private `team-admin` repository instead: GitHub
shows its README as a page on any device, with no server or new login. Monthly spend amounts stay on the public branch,
because every job reads them. The Operations Manager reviews the week and only gives advice.

## 2026-11-02: Topics must teach, not walk through setup

Abhi rejected "Scale ECS Fargate on ALB request count and prove it in CloudWatch" as useless. The cause was the Scout's own
brief, which asked for "deploy X, then monitor it". Topics now need a hook the docs do not give: a failure, a surprise or a
measured number. Thursday's run now looks for real failures people hit, and the same rule applies to Monday's topics.

## 2026-11-02: A CEO sets direction, and the CMO researches it

Abhi wants to give one person goals with timelines, text any C-level leader, and get exact steps when they need him. A
message goes to the CEO unless it starts with CTO, CMO or CFO. His own words become the goal, so a model cannot invent one.
The CMO researches how real people reached the top goal, weekly and on each new goal, and the Topic Scout works from it.
Goals and the market brief are on the public analytics branch, because scheduled jobs read them.

## 2026-11-02: A Voice Editor improves writing and never hides how it was made

Abhi asked for an agent that makes drafts read like a person wrote them. It removes AI patterns, and code rejects any
rewrite that changes a number or code line, or drops the AI note. It cannot invent his experience, so it asks him one
optional question per post, and his real answers and `field-notes.md` are what make a post his own.

## 2026-11-02: Leaders can act on Abhi's own words

Leaders could only talk, so asking one to close a draft did nothing. They can now close an open draft and queue a topic,
each only when his own message asks for it. Closing is limited to open draft PRs by exact title. The digest and market brief
now run only from the Monday schedule, because manual test runs were repeating them. OSS Scout answers from his oss-scout
contribution board, which is public, and can't act on GitHub.

## 2026-11-02: One article a day for a training week

Abhi wants to review an article every day until 9 November, so the daily job also starts the Topic Scout on days without
their own run. Code blocks on his site now look like a terminal: black, white text and a green directory prompt that can't
be copied. dev.to and Medium use their own styling.

## 2026-11-02: A Lambda wakes the team on each Slack message

Abhi approved faster replies. A small function checks Slack's signature and starts `cto.yml`, so it holds no model keys and
reads nothing. It runs in his main account, because the lab account is disposable. The 10-minute check stays as a fallback.

## 2026-11-02: The live board is a pinned Slack message

Abhi asked for a board he can open from Slack. A web page would be public, because this repo and its site are, and the board
shows private tasks. Slack's help says canvases are for paid plans. One message edited in place works on any plan, needs no new
Slack permission and costs nothing, because no model runs. It refreshes on every Slack message and every 10 minutes.
GitHub's search needs `is:issue` or `is:pull-request`, and his own repos filled the results with the team's topics and LinkedIn
drafts. The searches now skip his own repos, so OpenTelemetry assignments and code owner pings show up. A mention stays under
"Worth a look", because code can't tell whether it waits on him. Leaders still can't write code, and they now say so.

## 2026-11-02: The board is Abhi's outside work only

He cares about open source and what goes public, and the first board mixed that with the team's own plumbing. It now shows
articles and his PRs in other projects. Pipeline PRs, failed runs and agent tasks stay with the CTO and the admin page.

## 2026-11-02: The board is a web page of movable cards

Abhi wanted a page with blocks he can move, opened from Slack. The board now holds only public GitHub items and his topic ideas,
which are already on the public branch, so the page can be public. It isn't in the site menu or search. A claude.ai artifact
can't load live data from GitHub, and a GitHub Project needs a new token and can't be tested here. Moves stay in his browser,
so they don't follow him between devices. Syncing them needs somewhere to save them, such as a Lambda with a passphrase.
The Slack message now gives counts and a link. `board.json` changes only when a card does, to avoid a commit every 10 minutes.

## 2026-11-02: The Secretary answers, and the CTO keeps build notes

Abhi's unnamed messages now go to a Secretary. She asks up to two people and gives him one answer, so he doesn't chase anyone.
The CEO told him there was no board link because leaders couldn't see shipped work. They now see the board address, open PRs
and recent merges, and their prompts tell them to find the way forward. They still can't claim something exists when it doesn't.

New features are built with Abhi in a Claude chat, not by agents or through the API, because that costs less. A coding agent was
built for this and removed before merging. Leaders save each feature request as a CTO note, and code lists the notes when he asks.

IT Support checks the notes against merged PRs each morning and whenever he asks for them. A model suggests the match, and code
closes a note only if the PR really merged, so he never has to mark anything done. FACTS also gives leaders tomorrow's real plan.

## 2026-11-03: Labs get a pre-flight check before AWS

The rolling-deploy lab failed three times, and each cause could have been found first. IAM refused an instance profile without the
`hero-lab-` prefix, an API loop ran until its 16-minute limit, and the contract asked for a count one hour later in a 14-minute script.
Pre-flight now checks for these with no AWS, and gives the lab up to two rewrites. The topic gate rejects contracts that need an
hour's wait. Lab rules also cover Splunk's series lag, which showed 207 new series on one rollout and zero on the other four.
This can't promise success, because some failures only appear on real infrastructure. It stops the failures we can predict.

## 2026-11-03: One weekly plan replaces daily topic runs

Abhi wants three articles a week, five this week, from one planning step. Topic runs cost about $0.80 each. The Editor now proposes
the week in one call, Lab Check reviews all topics in one call, and code applies the rules. Only survivors get the searched brief
and Evidence Contract, so review quality stays. `cto.py` starts one lab a day from the plan until the target is met. One spare
covers a failed lab. The Thursday finder and training-week runs are off, and `radar.py` remains as a library.

A lab's model cost was about $1.70, of which $1.40 was output. Prompt caching only discounts input, so it would save little.
Repairs now return only changed files. Free API checks run before any model call, so a lab that can't start costs nothing.

Abhi wants quality kept and models compared with real numbers. `week.py` rotates Sonnet, Opus and Haiku as the model that writes each
lab and article. The reviewers and the Editor's scoring stay the same, so the scores compare like with like. Each lab saves a trial
record, and code works out the stats by model. Every article still needs the same gates and his approval.

Topics and headlines matter most to Abhi, so the three-model vote stayed. The Editor writes three headlines for each topic and keeps the
strongest, code refuses step-style headlines, and two of three models must agree before a topic gets a brief.

The first test plan refused most topics and left two, both on ALB. The plan now reuses passed topics that never ran a lab, then gives the
Editor a second round with the reasons, and asks for variety, because Abhi rejected repeated ALB posts.

## 2026-11-03: Abhi can ask for an article

He should be able to name a subject and have the team do the rest. A leader passes it to `radar.yml`, and `week.py` runs the same checks on
three angles. Lab Check still decides what a lab can do, but the vote only ranks angles, because he chose the subject. The lab starts straight
away, ahead of the plan and on top of the week's target. If no lab can do it, he hears why and no lab runs.

A repair that returned only changed files couldn't remove a file, so a banned `sink.py` survived two rewrites and the run stopped with no AWS
used. Repairs can now list files to delete, and the rules name the allowed file types up front. A lab stopped before apply adds no AWS estimate.

The third Fluent Bit run measured every config, but a comparison came out null because `reproduce.sh` kept its result in /tmp, and each script runs in
a fresh container. The rules now say so, and pre-flight refuses a /tmp path shared by both scripts.

## 2026-11-03: Hot problems and a 5 of 5 bar

Abhi wants hot topics and 5 of 5 from editorial and marketing. Every planned topic must now answer a recent real complaint, taken with free API
calls from GitHub issues and Stack Overflow, and code checks the link. Marketing above 3 needs that evidence, so a 5 can't come from a guess. Evidence
Contracts must change one setting per run, show the effective config and measure inside the problem's window, which is what held the FireLens post at 3 of 5.
Drafts below the bar get one automatic revision. In warn mode the PR still opens, because the lab is already paid for, and Slack says not to approve it.

Abhi set the focus: AI monitoring, Kubernetes, AWS services, and observability on Splunk Observability Cloud. Every planned topic names one of these lanes, and no lane
takes more than two topics in a week. AI monitoring labs run a small open model, because the lab has no paid AI API key.

Abhi's current goals are OpenTelemetry auto-instrumentation, Splunk MVP and AWS Hero, so auto-instrumentation has its own lane. AI monitoring uses Galileo, through its OpenTelemetry
endpoint. It needs a `GALILEO_API_KEY` repository secret. Until that exists, the planner offers no AI monitoring topics, and nothing else changes.

## 2026-11-03: The weekly plan moves to Claude chat

API calls are most of the spend, and planning cost about $2 a week. A Monday chat session now does the research and writes topics and briefs, using `docs/weekly-plan.md`. Code still checks everything, and one
ChatGPT call approves topics and sharpens headlines, as Abhi asked. The Monday schedule on `radar.yml` is off. The API planner stays for a manual run, and for articles he requests in Slack.

## 2026-11-03: Marketing and the Editor confirm before any lab

Abhi didn't like the first titles, and a lab started before his marketing team had seen them. Marketing and the Editor now do marketing analysis only: both must confirm a topic, and Marketing picks the title from real options.
A lab can't start on an unconfirmed topic. An old plan row has no `confirmed` flag, so it can't start either.

The KEDA lab worked, but `hpa_target_type` was a word in a contract I wrote in chat, and the scanner only accepts numbers, so no PR opened. The topic gate now refuses word measurements before a lab is paid for.
Cancelling a run does not stop a lab quickly, because its cleanup steps always run, so a lab that has started should be treated as spent.

## 2026-11-04: Abhi picks before any lab

Abhi asked for a shortlist from the Editor before any lab starts, with planning done in Claude chat. A plan row now needs both `confirmed` and `picked`, so nothing starts on its own. His Slack messages are kept in the private `team-admin` repo, so a chat can read them.
My 5/5 change also stopped paid-for drafts in warn mode, which I had said would still open. Two labs ended with no PR because of it. Warn mode now opens the PR, and only enforce stops one.

On 4 November the Secretary told Abhi his Java article had started, but his message had no word the code accepted, so nothing ran. It also named the wrong lab and an old PR. Leaders now get the real recent labs and open PRs, code alone confirms a start, and he's told when one didn't.

## 2026-11-04: Positive framing, 4 of 5, and writing in chat

Abhi works at Cisco on Splunk and doesn't want content that reads as negative about the product, so prompts ask for positive framing and code refuses negative headlines naming these products. He accepts 4 of 5 for now, with 5 as the target.
He wants the best output and chat before API. With `chat_work`, a Claude chat writes the lab and article on branch `chat/<issue>`. No model writes or repairs it, and the panel, evidence checks and Editor still run. Pod Identity is dropped.

## 2026-11-04: Slack never starts a lab

A Slack note saying to create the lab tomorrow started an API lab that minute, on a headline nobody picked. Labs and articles are written in chat, so Slack requests now wait in `slack-inbox.md`, and chat-planned topics start only from chat.

## 2026-11-04: A free check comes before AWS

Two labs failed on things a free run could have caught. `lab-check.yml` now builds and runs the app on a GitHub runner, and proves Splunk APM can be read back, before a lab pays for AWS.

## 2026-11-05: Two checks from the Java agent lab

A pod pinned with `nodeName` asked for 500m of CPU, and the rollout never finished once the Collector was on the node. Pre-flight now refuses that. A leftover network interface also held a subnet through three destroy attempts, so `destroy` now deletes available interfaces first.

## 2026-11-05: A stuck old lab can't use up a new lab's time

The sweep spent 40 minutes on a leftover VPC, and one 504 on a download then failed a paid lab. `destroy` now also clears leftover security groups, the sweep tries a lab once, and lab rules say every download retries.

## 2026-11-05: Labs rehearse on Kubernetes before AWS

The free check ran in Docker, so a failure that only appears on a cluster still cost a paid run. `lab-check.yml` now runs the real scripts on a small cluster with a t3.large's CPU. It can't test Terraform or EKS.

## 2026-11-05: Claude chat can read the Slack conversation

Abhi wants the team's replies available to the chat and for later use. `slack-chat.jsonl` in the private repo now keeps his messages and the team's, and leaves out anyone else in the workspace.

## 2026-11-05: Marketing and the Editor review the plan, not only the draft

They used to see a topic before a lab and a draft after it, so faults in the lab design cost a paid run. `plan_review.py` now gives them the plan before AWS. A draft also uses `post.json` only in the article step, because a lab run once paired an old article with new evidence.
