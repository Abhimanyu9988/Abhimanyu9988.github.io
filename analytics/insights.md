# What's working (as of 2026-11-01)

Written by Claude, ChatGPT and Gemini from analytics/ledger.csv. The topic finders read this.

### What's working
- **Too early to call.** The five posts have about 136 views, 3 reactions and 2 comments in total. Gaps of 10–20 views are noise. Only dev.to is tracked, and no publish dates are given.
- **`eks-canary-releases-alb` is the benchmark.** It has 64 views, 2 reactions and 1 comment. It is the only post with both a reaction and a comment. It may also be the oldest post.
- **The KEDA/Splunk latency post got 40 views and no engagement.** The "slow but CPU fine" hook seems to draw clicks. Nothing suggests readers engaged with the content.
- **The broken-ingress ALB post and the scale-to-zero SQS/Fargate post each gained +10 views.** That is a small sign of early momentum for "broken scenario → debug → fix" posts.
- **The Java tracing post has 2 views and 1 comment.** Don't judge the topic yet.
- **Engagement is near zero everywhere.** Organic discovery on dev.to isn't enough, so distribution needs work.

### Try next
- **Follow up on EKS + ALB.** For example, a post on ALB 502s or a failed canary rollback. Open with the symptom. Show the controller events, target group health, readiness and the fix. Include a measurable failure such as 5xx errors.
- **Use symptom-first, specific titles.** For example, "HPA said CPU was fine, users still waited." Show before/after graphs and the exact KEDA YAML. Make Splunk optional and add a CloudWatch or Prometheus version.
- **Test one more ECS/Fargate post** to see whether that area has an audience beyond EKS.
- **Add a GitHub repo to every post.** Use Terraform or eksctl with the broken setup so readers can reproduce the failure. Put a "what broke" TL;DR at the top.
- **Widen distribution.**
  - Cross-post to Hashnode or Medium with a canonical URL.
  - Share on LinkedIn, r/aws and r/kubernetes. Frame these as labs, not self-promotion.
  - Use the tags #aws, #eks and #kubernetes.
  - Re-share after about 48 hours.
- **Keep a schedule.**
  - Publish roughly weekly, Tuesday to Thursday mornings.
  - Record publish dates.
  - Re-check each post at 14 and 30 days.
  - Reply to every comment, including the one on the Java post.
  - Hold off on changing direction until posts reach about 100 views.

### Where they disagree
- **Which topics are doing well.** Gemini says EKS infrastructure and troubleshooting topics beat language-specific tutorials, and it treats the Java post's 2 views as evidence against that topic. Claude and ChatGPT say it is too early to judge the Java post, and they blame its title or distribution.
- **Next post ideas.** Gemini suggests new EKS topics (IRSA, Karpenter upgrades). ChatGPT and Claude prefer to build on existing posts (ALB, KEDA, Fargate).
