# Scale-to-zero SQS workers on ECS Fargate with target tracking

Based on a Stack Overflow question (80006730). A Fargate worker uses target tracking on SQS backlog per task (`ApproximateNumberOfMessagesVisible / RunningTaskCount` from Container Insights). The minimum capacity is 0. The asker reports that the service never leaves 0 when messages arrive.

## What the lab builds (eu-central-1)
- An SQS queue and a VPC with two public subnets.
- An ECS cluster with Container Insights enabled.
- A Fargate service, min 0, max 5. The worker is the `aws-cli` public image looping on `receive-message`. Each message takes 20 s.
- The asker's target-tracking policy (target 2 messages per task). Its expression is `IF(running > 0, backlog / running, backlog)`.

## Run
```
terraform init && terraform apply
./reproduce.sh   # sends 10 messages, waits up to 8 min, prints desired/running, alarm state,
                 # and the metric-math datapoints. Ends with HERO_RESULT: reproduced only if the
                 # service stays at 0, messages are still waiting and the bpt series has no datapoints.
./fix.sh         # replaces the policy via put-scaling-policy, using FILL(running, 0)
                 # ends with HERO_RESULT: fixed if the service scales out
./cleanup.sh && terraform destroy
```

Requires the AWS CLI, `jq` and GNU `date`. The IAM roles use `var.permissions_boundary`. Set it to `null` in your own account.

## Expected
- reproduce: `desired=0 running=0` for the whole wait while messages sit in the queue. The `backlog` series has datapoints, but `running` and `bpt` return no datapoints (the script prints `bpt datapoints: 0`). The target-tracking alarms stay in INSUFFICIENT_DATA, so Application Auto Scaling does not scale out.
- fix: with `FILL(running, 0)` the expression falls back to the raw backlog. AlarmHigh fires after about 3 minutes, desired count goes up, tasks reach RUNNING and the queue drains.

The fix is applied with the CLI because replacing an existing resource's configuration can't live in a separate `fix.tf`. The Terraform equivalent is to change the `expression` of the `bpt` metric in `main.tf`.

## Not measured here (each script has a 15 minute limit)
- Scale-in back to 0. The target-tracking AlarmLow needs about 15 minutes of low data.
- The in-flight (`ApproximateNumberOfMessagesNotVisible`) variant, the 0-to-1 step policy keyed on `DesiredTaskCount`, and ECS task scale-in protection. Scale-in protection is the only thing that stops a busy task from being stopped. Messages whose task is stopped are redelivered after the 60 s visibility timeout. To test these, edit the expression or add a policy and re-run the steps by hand.

Cost: a few minutes of 0.25 vCPU Fargate tasks. Container Insights metrics cost cents.
