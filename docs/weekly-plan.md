# The weekly plan, written in Claude chat

Planning here adds no API cost. A chat session does the research and writing, and one ChatGPT call gives the second opinion.

1. Clone `Abhimanyu9988/Abhimanyu9988.github.io` and read `AGENTS.md`, `pipeline/week.py`, `pipeline/schemas/brief.json` and `pipeline/roles/prompts/editor.md`.
2. Find real complaints from the last 30 days in the repos in `week.HOT_REPOS` and `radar.WATCH`, with `gh api search/issues`, and on the web. Skip feature requests. Record real counts.
3. Read his goals and ideas on the analytics branch (`analytics/goals.json`, `analytics/market-brief.md`) and existing topics in open `radar` issues.
4. Write `week.target() + 1` topics: three a week unless `WEEK_TARGETS` says otherwise, plus one spare. Each serves a goal and has a lane from `week.LANES`, at most two per lane.
5. Follow the Editor's rules: a real finding the docs don't give, one setting changed per run, the effective config shown, measured inside the problem's window, and no wait that won't fit a 14-minute script.
6. Save `{"topics": [...]}`. Each topic has `title`, `titles` (at least three real options), `hook`, `payoff`, `proof`, `lab`, `sources`, `kind`, `lane`, `demand_url`, `signal` and `brief`. `brief` must match `pipeline/schemas/brief.json`, with `existing_pages` from pages you really found and `verdict` set to `go`.
7. Run `REPO=Abhimanyu9988/Abhimanyu9988.github.io BOT_TOKEN=x GH_READ_TOKEN=x python3 pipeline/week.py --check plan.json` and fix everything it refuses.
8. Start it with `gh workflow run radar.yml --repo Abhimanyu9988/Abhimanyu9988.github.io --ref main -f plan_json="$(cat plan.json)"`. ChatGPT approves topics and adds a headline option. Marketing and the Editor then do marketing analysis only: both must confirm a topic, and Marketing chooses its title. Code opens the issues, and the first confirmed lab starts.

Every measurement in a contract must be a number, because the evidence scanner refuses words.

Never invent a count, a page or a result. If fewer topics pass, send fewer and say so.
