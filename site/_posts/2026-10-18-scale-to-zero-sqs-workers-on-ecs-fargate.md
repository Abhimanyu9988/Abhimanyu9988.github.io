---
title: "Fargate SQS worker stuck at 0 tasks? The IF(running > 0) guard never gets to run"
description: "I reproduced an ECS Fargate scale-to-zero SQS worker that never bootstraps from zero. The fix was to wrap the missing RunningTaskCount metric with FILL(running, 0)."
tags: ["aws", "ecs", "fargate", "devops"]
channels: ["devto", "hashnode"]
lab: labs/scale-to-zero-sqs-workers-on-ecs-fargate
image: /assets/posts/scale-to-zero-sqs-workers-on-ecs-fargate/cover.png
image_alt: "Lab output: 10 messages waiting while the service stays at 0 tasks and the running series has no datapoints; after wrapping it in FILL(running, 0) the service scales to 5 running tasks and the queue drains."
linkedin: "My Fargate SQS worker had 10 messages waiting and still stayed at desired=0.\n\nThis hits when you use ECS target tracking for backlog per task and set min capacity to 0. The Terraform can look right, including IF(running > 0, backlog / running, backlog), but Container Insights may emit no RunningTaskCount datapoint at zero tasks.\n\nIn my lab, SQS backlog had datapoints, RunningTaskCount had none, and the metric-math result had none. The target-tracking alarms stayed in INSUFFICIENT_DATA.\n\nThe fix that worked: wrap the task count with FILL(running, 0). After one put-scaling-policy call, the service scaled from 0 to 5 tasks and drained the queue. I did not test scale-in back to zero in this run.\n\nHave you handled ECS queue-worker scale-to-zero with target tracking, step scaling, or a different bootstrap pattern?\n\nFull write-up, every command and the lab: {POST_URL}\n\n#aws #ecs #fargate #devops"
replies: [{"url": "https://stackoverflow.com/questions/80006730/aws-ecs-fargate-sqs-autoscaling-with-min-capacity-0-how-to-bootstrap-from-0-tas", "text": "I reproduced this in a disposable eu-central-1 lab with an ECS Fargate service at min capacity 0 and the same target-tracking expression: IF(running > 0, backlog / running, backlog). After sending 10 SQS messages, the service stayed at desired=0 for 8 minutes. SQS backlog had datapoints, but RunningTaskCount had none, and the bpt metric math result had none, so the alarms stayed INSUFFICIENT_DATA. Updating the policy to IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog) made it scale out and drain the queue. Caveat: I did not test scale-in back to zero. I wrote up the full replication here: {POST_URL}"}]
---
![Lab output: 10 messages waiting while the service stays at 0 tasks and the running series has no datapoints; after wrapping it in FILL(running, 0) the service scales to 5 running tasks and the queue drains.](https://abhimanyu9988.github.io/assets/posts/scale-to-zero-sqs-workers-on-ecs-fargate/cover.png)

I rebuilt a Stack Overflow setup in a throwaway account: an ECS Fargate worker with min capacity 0, scaled by target tracking on SQS backlog per task, with the guard IF(running > 0, backlog / running, backlog). I sent 10 messages and the service stayed at desired=0 for the whole 8-minute wait. The backlog series had datapoints, but RunningTaskCount and the metric-math result had none, so the guard never produced a value. Swapping in FILL(running, 0) with one put-scaling-policy call got the service scaling out from 0 and draining the queue. Scale-in back to 0 isn't covered by this run.

This hurts when you are trying to save idle Fargate cost for a worker that only wakes up when SQS has work. The Terraform can look reasonable. The expression even has an IF guard for zero tasks. But when messages arrive, nothing starts, the queue sits full, and the target-tracking alarms stay in INSUFFICIENT_DATA.

Most of my week goes on one question: why does something that looks correctly configured not work in a real cluster? I lead a support engineering team for observability agents that run inside Kubernetes clusters, and before I write about a problem, I reproduce it myself in a throwaway AWS account and keep the raw output.

This one was reproduced and fixed in eu-central-1.

**TL;DR**

Problem: an ECS Fargate SQS worker with min capacity 0 stayed at desired=0 even with 10 visible messages.

Cause: Container Insights `RunningTaskCount` had no datapoints at zero tasks, so `IF(running > 0, backlog / running, backlog)` returned no `bpt` datapoints.

Fix: use `IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)` so the expression falls back to raw backlog at zero tasks.

## What you'll see

The service starts at zero, as intended:

```text
== Initial state: desired=0 running=0
[
    {
        "min": 0,
        "max": 5
    }
]
```

After sending 10 SQS messages, it stays there for the whole wait:

```text
t+30s desired=0 running=0 visible_in_queue=10
...
t+480s desired=0 running=0 visible_in_queue=10
```

The target-tracking alarms never get enough data:

```text
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

The important part is the metric math. SQS backlog has data. `RunningTaskCount` does not. The computed backlog-per-task series, `bpt`, also has no data:

```text
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
bpt datapoints: 0
```

Final state from the failed run:

```text
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
```

## Why it happens

The source question reported this pattern: an ECS Fargate worker consumes SQS, target tracking uses backlog per task, and the service has minimum capacity 0. AWS commonly recommends a backlog-per-task metric for queue workers: visible messages divided by running workers.

That is fine while at least one task is running. The awkward edge is zero.

In my lab, Container Insights did not publish a `RunningTaskCount` datapoint for this service while it had zero running tasks. CloudWatch metric math then returned no datapoints for this expression:

```text
IF(running > 0, backlog / running, backlog)
```

So the fallback branch did not save the policy. The problem was not division by zero. The problem was missing data. The guard never had a real `running` datapoint to evaluate.

The Stack Overflow answers suggested wrapping `running` with `FILL(running, 0)`. My lab confirms that this fix was enough for scale-out from zero. After replacing the policy with:

```text
IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
```

Application Auto Scaling set desired count to 5, Fargate tasks reached RUNNING, and the queue drained.

What is only likely, not proven by this run: adding in-flight SQS messages may help avoid scaling in too early while messages are being processed, and a separate bootstrap policy keyed on `DesiredTaskCount` may be useful in some designs because Fargate takes time to move from desired to running. I did not test those variants here.

## Replicate it yourself, step by step

You need AWS CLI, Terraform, `jq`, and GNU `date`. The lab uses eu-central-1 through its provider config.

1. Get the lab.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/scale-to-zero-sqs-workers-on-ecs-fargate
```

In `providers.tf`, set `permissions_boundary` to `null` for your own account if you do not use an IAM permissions boundary.

2. Initialize Terraform and apply the lab.

```bash
terraform init
```

Real init output from the run:

```text
Initializing provider plugins...
- Finding hashicorp/aws versions matching "~> 5.80"...
- Installing hashicorp/aws v5.100.0...
- Installed hashicorp/aws v5.100.0 (signed by HashiCorp)

Terraform has been successfully initialized!
```

Now create the resources:

```bash
terraform apply
```

It creates an SQS queue, a VPC with two public subnets, an ECS cluster with Container Insights enabled, a Fargate service at desired count 0, IAM roles, a log group, an Application Auto Scaling target with min 0 and max 5, and the original target-tracking policy. The worker is the public `aws-cli` image looping on `receive-message`; each message takes 20 seconds in this lab.

The plan and apply showed 19 resources added:

```text
Plan: 19 to add, 0 to change, 0 to destroy.
...
Apply complete! Resources: 19 added, 0 changed, 0 destroyed.

Outputs:

cluster_name = "scale-to-zero-sqs-worker-447482-cluster"
policy_name = "scale-to-zero-sqs-worker-447482-backlog-per-task"
queue_name = "scale-to-zero-sqs-worker-447482-jobs"
queue_url = "https://sqs.eu-central-1.amazonaws.com/<account-id>/scale-to-zero-sqs-worker-447482-jobs"
resource_id = "service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker"
service_name = "scale-to-zero-sqs-worker-447482-worker"
```

3. Set the shell values the scripts read from Terraform outputs.

```bash
export AWS_DEFAULT_REGION="eu-central-1"
CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
QURL=$(terraform output -raw queue_url)
QNAME=$(terraform output -raw queue_name)
RID=$(terraform output -raw resource_id)
POLICY=$(terraform output -raw policy_name)
```

4. Check the initial ECS service state and scaling target.

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
aws application-autoscaling describe-scalable-targets --service-namespace ecs --resource-ids "$RID" --query 'ScalableTargets[].{min:MinCapacity,max:MaxCapacity}' --output json
```

Output:

```text
== Initial state: desired=0 running=0
[
    {
        "min": 0,
        "max": 5
    }
]
```

5. Send 10 messages to SQS.

```bash
for i in $(seq 1 10); do
  aws sqs send-message --queue-url "$QURL" --message-body "job-$i" >/dev/null
done
```

Output:

```text
== Sending 10 messages
```

6. Watch desired count, running count, and visible messages. The same reads are repeated every 30 seconds for up to 16 checks.

```bash
sleep 30
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
```

Output from the run:

```text
== Waiting up to 8 minutes for the service to leave 0 tasks
t+30s desired=0 running=0 visible_in_queue=10
t+60s desired=0 running=0 visible_in_queue=10
...
t+480s desired=0 running=0 visible_in_queue=10
```

7. Ask Application Auto Scaling for the target-tracking alarm names, then ask CloudWatch for their state and reason.

```bash
ALARMS=$(aws application-autoscaling describe-scaling-policies --service-namespace ecs --resource-id "$RID" --query 'ScalingPolicies[0].Alarms[].AlarmName' --output text)
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[].[AlarmName,StateValue]' --output text
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[0].StateReason' --output text | cut -c1-300
```

Output:

```text
== Policy alarms
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

8. Query the metric math directly. This is the same broken expression from the original policy.

```bash
START=$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > /tmp/q-broken.json <<EOF
[
 {"Id":"backlog","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"$QNAME"}]},"Period":60,"Stat":"Sum"}},
 {"Id":"running","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"ECS/ContainerInsights","MetricName":"RunningTaskCount","Dimensions":[{"Name":"ClusterName","Value":"$CLUSTER"},{"Name":"ServiceName","Value":"$SERVICE"}]},"Period":60,"Stat":"Average"}},
 {"Id":"bpt","ReturnData":true,"Expression":"IF(running > 0, backlog / running, backlog)"}
]
EOF
METRICS=$(aws cloudwatch get-metric-data --start-time "$START" --end-time "$END" --metric-data-queries file:///tmp/q-broken.json)
echo "$METRICS" | jq -c '.MetricDataResults[] | {Id, datapoints: (.Values|length), Values}'
BPT_N=$(echo "$METRICS" | jq '[.MetricDataResults[] | select(.Id=="bpt") | (.Values|length)] | add // 0')
echo "bpt datapoints: $BPT_N"
```

Output:

```text
== Metric math as the policy evaluates it (last 15 min, 60s period)
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
bpt datapoints: 0
```

9. Check scaling activities. There were none in the reproduced failure.

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

Output:

```text
== Scaling activities
```

The failed reproduction ends like this:

```text
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
```

10. Apply the fix: replace the target-tracking policy with the same metric, but wrap `running` in `FILL(running, 0)`.

First confirm the before state:

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
```

Output:

```text
== Before: desired=0 running=0 visible=10
```

Create the fixed policy configuration:

```bash
EXPR='IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)'
cat > /tmp/policy-fixed.json <<EOF
{
 "TargetValue": 2,
 "ScaleOutCooldown": 60,
 "ScaleInCooldown": 300,
 "CustomizedMetricSpecification": {
  "Metrics": [
   {"Id":"backlog","ReturnData":false,"MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"$QNAME"}]},"Stat":"Sum"}},
   {"Id":"running","ReturnData":false,"MetricStat":{"Metric":{"Namespace":"ECS/ContainerInsights","MetricName":"RunningTaskCount","Dimensions":[{"Name":"ClusterName","Value":"$CLUSTER"},{"Name":"ServiceName","Value":"$SERVICE"}]},"Stat":"Average"}},
   {"Id":"bpt","Label":"Backlog per task","ReturnData":true,"Expression":"$EXPR"}
  ]
 }
}
EOF
```

Output:

```text
== Updating policy with: IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
```

Push the fixed policy into Application Auto Scaling:

```bash
aws application-autoscaling put-scaling-policy \
  --policy-name "$POLICY" --policy-type TargetTrackingScaling \
  --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount \
  --target-tracking-scaling-policy-configuration file:///tmp/policy-fixed.json \
  --query 'PolicyARN' --output text
```

Output:

```text
arn:aws:autoscaling:eu-central-1:<account-id>:scalingPolicy:0b5eb0c3-92be-46eb-9e09-d6e225ccfe6b:resource/ecs/service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker:policyName/scale-to-zero-sqs-worker-447482-backlog-per-task
```

11. Watch the service scale out from zero. The same ECS and SQS reads are repeated every 30 seconds.

```bash
sleep 30
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessagesNotVisible --query 'Attributes.ApproximateNumberOfMessagesNotVisible' --output text
```

Output:

```text
== Waiting up to 10 minutes for the service to scale out from 0 and a task to reach RUNNING
t+30s desired=5 running=0 visible=10 in_flight=0
t+60s desired=5 running=5 visible=10 in_flight=0
```

12. Check scaling activities again.

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

Output:

```text
== Scaling activities
Successful	Setting desired count to 5.
```

13. Watch the queue drain.

```bash
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessagesNotVisible --query 'Attributes.ApproximateNumberOfMessagesNotVisible' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
```

Output:

```text
== Watching queue drain (up to 2.5 min, bounded by script time budget)
visible=6 in_flight=0 desired=5 running=5
visible=0 in_flight=4 desired=5 running=5
visible=0 in_flight=0 desired=5 running=5
```

14. Clean up the live service state before destroying Terraform resources.

Delete the scaling policy so the autoscaler does not relaunch tasks:

```bash
aws application-autoscaling delete-scaling-policy --service-namespace ecs --resource-id "$RID" \
  --scalable-dimension ecs:service:DesiredCount --policy-name "$POLICY"
```

Set the ECS service desired count back to zero:

```bash
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0
```

List any remaining tasks and stop each one:

```bash
for t in $(aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[]' --output text); do
  aws ecs stop-task --cluster "$CLUSTER" --task "$t"
done
```

Wait until no task ARNs are returned. The cleanup script repeats this check up to 18 times, 10 seconds apart:

```bash
for i in $(seq 1 18); do
  N=$(aws ecs list-tasks --cluster "$CLUSTER" --query 'length(taskArns)' --output text)
  [ -z "$N" ] || [ "$N" = "0" ] && break
  sleep 10
done
```

Purge the queue:

```bash
aws sqs purge-queue --queue-url "$QURL"
```

The cleanup script suppressed command output in my run.

15. Destroy the lab.

```bash
terraform destroy
```

Output from destroy:

```text
Plan: 0 to add, 0 to change, 18 to destroy.
...
Destroy complete! Resources: 18 destroyed.
```

## What I didn't test

I did not test scale-in back to zero. The lab notes say the target-tracking low alarm needs about 15 minutes of low data, and this run was bounded by the script time.

I did not test the in-flight-message expression using `ApproximateNumberOfMessagesNotVisible`.

I did not test a separate 0-to-1 scale-out policy keyed on `DesiredTaskCount`.

I did not test ECS task scale-in protection. That matters for a different question: whether a busy worker can be stopped during scale-in. If a task is stopped while processing, SQS messages can be redelivered after the 60 second visibility timeout in this lab.

## Cost and time

The run was in eu-central-1 and took 19.1 minutes end to end.

Terraform created 19 resources. After the cleanup step deleted the scaling policy, Terraform destroyed 18 resources.

The timed parts from the raw output were: init 6 seconds, apply 36 seconds, reproduce 572 seconds, fix 162 seconds, cleanup 17 seconds, and the later init 3 seconds. The workload used a few minutes of 0.25 vCPU Fargate tasks. Container Insights metrics cost cents.

## The takeaway

If your scale-to-zero ECS worker depends on a metric from running tasks, test the zero-task case directly. For SQS backlog per task, treat missing `RunningTaskCount` as zero with `FILL`, otherwise your careful `IF` guard may never get a datapoint to evaluate.

## Sources

- [Stack Overflow: ECS Fargate SQS autoscaling with min capacity 0](https://stackoverflow.com/questions/80006730/aws-ecs-fargate-sqs-autoscaling-with-min-capacity-0-how-to-bootstrap-from-0-tas)

---
_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
