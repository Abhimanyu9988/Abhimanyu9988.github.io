# What's working (as of 2026-10-25)

Written by Claude, ChatGPT and Gemini from analytics/ledger.csv. The topic finders read this.

### What's working
- Data is a single snapshot (2026-10-25, no change column) with tiny numbers: 76 views, 2 reactions, 2 comments across 4 dev.to posts. Treat everything as directional.
- `eks-canary-releases-alb` is the only post with traction: 64 of 76 views (~84%), 2 reactions, 1 comment. Hands-on EKS + ALB release-safety content looks like the best lane so far. Post age isn't given, so exposure time may explain part of this.
- `trace-a-java-service-on-eks-...` has 2 views but 1 comment. That is too early to judge. Check that the comment isn't just the author replying.
- `from-broken-ingress-...` has 10 views and no engagement. Its long, Splunk-specific title may narrow the audience.
- `scale-to-zero-sqs-workers-on-ecs-fargate` has 0 views. That points to a distribution, indexing or timing problem, not a weak topic.

### Try next
- Write another incident-style EKS/ALB lab: break something, show the symptom, fix it, and verify the fix. Examples: a canary sending traffic to unready pods (502s), or 502/503 debugging with the AWS Load Balancer Controller. Include readiness gates, deregistration delay and rollback.
- Retitle tool-specific posts around the generic problem and put the vendor inside the post. For example, "EKS Ingress Broken? Debug ALBs…" with Splunk as the observability layer. Likewise, "Trace Java on EKS with no code changes (OpenTelemetry auto-instrumentation)".
- Check that the ECS/SQS post is published and indexed, then relaunch it. Use a cost-saving or "why it never scaled to zero" angle, with a diagram, a real failure mode, IaC and a repo. Give it about 2 weeks.
- Broaden distribution beyond dev.to. Cross-post to Hashnode or Medium with canonical URLs. Share on LinkedIn, X, r/aws, r/kubernetes, and AWS Community Builders and containers communities.
- Publish on a steady cadence, roughly every 1–2 weeks, and record publish dates. Log views at 48 hours, 7 days and 30 days so later reviews compare like with like. Also track talks, open-source work and re:Post answers for Container Hero evidence.

### Where they disagree
- Cadence and timing: Claude suggests a post every 1–2 weeks. Gemini suggests a strict bi-weekly cadence for 3 months before reviewing metrics. ChatGPT suggests publishing Tue–Thu mornings and checking at 48 hours and 7 days.
