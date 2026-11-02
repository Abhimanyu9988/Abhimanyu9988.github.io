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
