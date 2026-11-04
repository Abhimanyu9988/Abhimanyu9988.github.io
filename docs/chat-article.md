# Writing a lab and article in Claude chat

This keeps the writing off the API. The workflow still runs the lab, checks evidence and scores the draft.

1. Abhi picks a topic from the Editor's shortlist, and Marketing and the Editor confirm it. Requests in Slack only reach `slack-inbox.md`, and no lab starts from Slack.
2. Create branch `chat/<issue>`. Write `chat-work/<issue>/lab.json` with `summary`, `expected` and `files`, following LAB RULES in `pipeline/lab.py`. Run the checks in `lab.preflight` yourself first.
3. Start it: `gh workflow run replicate.yml --ref main -f issue=<issue> -f chat_work=true`. Any failure stops the run and comes back to the chat, with no API repair.
4. Read the run's log and write `chat-work/<issue>/post.json` with `title`, `description`, `tags`, `body`, `linkedin` and `source_replies`, following the article rules in `pipeline/draft.py`. Use only what the evidence shows. Stay positive about Splunk, OpenTelemetry and Galileo.
5. Start the article step: `gh workflow run replicate.yml --ref main -f issue=<issue> -f from_run=<run> -f chat_work=true`. The panel, evidence checks and Editor run, then a PR opens. If the Editor wants changes, revise `post.json` and start step 5 again.
6. Only fixes that make a real change get a second run, because a lab costs money even with no API writing.
