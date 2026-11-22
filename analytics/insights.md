# What's working (as of 2026-11-22)

Written by Claude, ChatGPT and Gemini from analytics/ledger.csv. The topic finders read this.

### What's working
- **Treat all of this as directional.** Whole-site views are 109 (+25), and the best dev.to post has 64 views. A few views is noise.
- **EKS posts about a concrete traffic or scaling problem lead on dev.to.**
  - `eks-canary-releases-alb` has 64 views, 2 reactions and 1 comment. It is the only post with both reactions and a comment.
  - The KEDA latency-scaling post is second with 40 views and no engagement.
- **On the personal site, practical OTel/Java-on-EKS posts lead.** The Java agent env-var post has 13 views, the Helm-value post 12, the retry-storm post 9, Flink restarts 8, and the agent 3.0 preview 7 (+2).
- **Generic summaries aren't working.** `open-source-summary-2026-10` has 0 dev.to views and 2 site views. The hands-on "break it in a lab, then fix it" format fits the AWS Container Hero goal better.

### Try next
- **Follow up on the canary post.** Example: "EKS canary went wrong: roll back ALB weighted traffic using metrics and traces". Open with the failure symptom, then show the evidence and the fix.
- **Write a sibling ALB post.** Example: "ALB 502/504 after EKS deploy: reproduce the target-health or readiness gap". If both posts do well, treat that as a real signal.
- **Lead titles with the AWS service and the symptom, and make Splunk/OTel the supporting tool.** Candidate topics are Karpenter stalls, ECS Fargate tasks stuck in PROVISIONING or OOM-killed, and an SQS backlog where KEDA doesn't scale.
- **Reframe scaling posts around user pain.** Example: "CPU looks fine but the app is slow".
- **Re-promote the ECS Fargate scale-to-zero post.** Add a short "what broke" intro and a diagram, share it in AWS communities, and re-check after 2 weeks.
- **Drop the monthly summaries.**
- **Keep a steady cadence, with about one lab post per week or two.**
  - Check views, reactions and comments at 7 and 14 days.
  - End each post with a question to draw comments.
  - Wait for about 4–5 posts per theme before judging.

### Where they disagree
- **Distribution:** Gemini wants to add Hashnode and AWS Community platforms. ChatGPT prefers dev.to first with the site as canonical, plus LinkedIn. Claude prefers AWS Community Builders, r/aws and dev.to tags.
- **Focus:** Gemini says to spend 100% of writing time on lab posts. Claude and ChatGPT only say to drop the summaries.
- **Cadence:** ChatGPT suggests weekly posts for 4 weeks. Claude suggests one every 1–2 weeks.
