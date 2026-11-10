# What's working (as of 2026-11-10)

Written by Claude, ChatGPT and Gemini from analytics/ledger.csv. The topic finders read this.

### What's working
- **Treat everything as directional.** This is one snapshot: the Change column is +0 everywhere, site views total 69, and the top post has 64 views.
- **EKS + ALB release content is the only clear early signal.** `eks-canary-releases-alb` has 64 dev.to views, 2 reactions and 1 comment. That is still only three interactions.
- **dev.to is doing the discovery.** Its top posts have 64, 40, 22 and 20 views. Most individual site posts have 1–13 views.
- **A few Java/OTel posts get some site traffic.** The OTel Java agent post has 13 site views and the Helm value post has 9. The Java annotation trace post got a comment on dev.to.
- **Monthly recap posts aren't pulling readers.** `open-source-summary-2026-10` has 0 dev.to views and 1 site view.

### Try next
- **Write a sequel to the canary post.** Run a bad canary deploy on EKS, show the ALB weights, catch it with metrics or traces, and roll back. Include a public repo for the lab.
- **Keep the break-fix format with symptom-led titles.** Put the exact error in the title or first line, for example "502s from ALB on EKS: 4 causes I reproduced". Other topic ideas are a sharper CPU-vs-latency KEDA scaling post, IRSA or Pod Identity failures, and CoreDNS or ImagePullBackOff.
- **Retry ECS once, framed as an incident.** For example, an SQS queue backed up overnight and Fargate workers scale up from zero. At 10 views, the existing ECS post is too small to judge.
- **Lead with the AWS container problem and mention Splunk as the tool.** End each post with one question to invite comments.
- **Use one distribution routine for the next three posts.** Publish on the site first, cross-post to dev.to with a canonical URL, then share on LinkedIn and one community channel. Check results at 7 and 14 days, and change one variable at a time.
- **Fold the monthly summary into other posts, or pause it.**

### Where they disagree
- **Splunk in headlines.** Claude would drop Splunk from some headlines to lead with ECS/EKS/Fargate. ChatGPT keeps Splunk in its proposed titles.
- **ECS.** ChatGPT would deprioritize ECS for a few weeks if one more ECS post underperforms. Claude lists an ECS follow-up as a next idea without that condition.
