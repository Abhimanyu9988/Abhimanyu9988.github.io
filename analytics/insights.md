# What's working (as of 2026-11-08)

Written by Claude, ChatGPT and Gemini from analytics/ledger.csv. The topic finders read this.

### What's working
- Numbers are tiny (max 64 views, 3 reactions and 2 comments across 6 posts), so treat everything as directional only.
- `eks-canary-releases-alb` leads on views (64) and is the only post with both a reaction (2) and a comment (1). Hands-on EKS/ALB deployment and traffic topics seem to resonate. Age and title effects can't be separated yet.
- `trace-a-java-service-on-eks-with-one-annotation-zero-code` is the only post with recent movement (+20 views, now 22) and has 1 comment. The concrete "one annotation, zero code" promise may help.
- The KEDA/Splunk post (40 views), the ingress post (20) and the ECS post (10) have no engagement. This probably reflects little distribution rather than weak topics.
- `open-source-summary-2026-10` has 0 views and blank change values. This may be a tracking gap, so check it before drawing conclusions.

### Try next
- **Break it, then fix it, on EKS traffic:** e.g. "My EKS canary sent 100% of traffic to the wrong version" (misconfigure ALB weights, inspect, fix). Alternatively, a canary follow-up using Argo Rollouts or Gateway API with auto-rollback. Link it to the original post.
- **Observability series:** "EKS pod is slow but CPU looks fine" using OpenTelemetry and Splunk, building on the tracing post.
- **Retry ECS/Fargate with a pain-led hook:** idle worker cost, SQS backlog metric, scale to zero.
- **Fix distribution first:** cross-post to Hashnode and LinkedIn (diagram or terminal screenshot), and share in AWS/K8s Slack and Reddit threads where the problem is being asked about. Record views by platform and date.
- **Consistent format:** symptom-led, punchy titles, TL;DR, repo link, cleanup section, cost or time result, and a closing question to invite comments. Publish every 1–2 weeks, Tuesday to Thursday mornings.
- **Judge after about 4 weeks and 100+ views per post.** Keep a log of talks, answers and OSS work for the Hero nomination.

### Where they disagree
- **Open-source summaries:** Gemini says drop them for now. Claude says check the tracking first. ChatGPT only calls them a weak growth format.
- **Titles:** Gemini says the KEDA/Splunk title is too long. Claude and ChatGPT focus on symptom-led wording instead.
