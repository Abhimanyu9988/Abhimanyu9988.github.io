---
title: "ECS Fargate SQS worker stuck at zero with backlog-per-task scaling"
description: "I reproduced an ECS Fargate SQS worker that stayed at zero while messages waited. In this lab, the backlog-per-task metric had no datapoints until I wrapped RunningTaskCount with FILL."
tags: ["aws", "ecs", "sqs", "terraform"]
channels: ["devto", "hashnode"]
lab: labs/scale-to-zero-sqs-workers-on-ecs-fargate-447482
linkedin: "An ECS Fargate SQS worker can sit at zero even while messages are waiting.\n\nThis hits teams trying to save idle Fargate cost with min capacity 0. The backlog-per-task metric looks sensible: SQS visible messages divided by ECS RunningTaskCount.\n\nIn my lab, a fresh service at 0 tasks had no RunningTaskCount datapoints. That made the metric math result empty too, so the target tracking alarms stayed in INSUFFICIENT_DATA and ECS never scaled out.\n\nThe fix was small: wrap the task count with FILL(running, 0), so the expression falls back to raw backlog when the task-count metric is missing. After that, the service went from 0 to 5 tasks and drained the queue.\n\nHave you handled ECS or Kubernetes workers that need to bootstrap cleanly from zero?\n\nFull write-up, every command and the lab: {POST_URL}\n\n#aws #ecs #sqs #terraform"
---
A developer on Stack Overflow wanted an ECS Fargate worker to process SQS messages only when there was work. Minimum capacity was 0, because the worker was idle most of the time. The target tracking metric was the usual backlog per task: SQS visible messages divided by ECS Container Insights `RunningTaskCount`.

The confusing part was the cold start. Messages arrived, but the service stayed at 0. The expression already had a guard for zero tasks, so it looked like it should fall back to raw backlog and start one or more tasks.

I reproduced the case in a disposable AWS lab in `eu-central-1`. This is the kind of edge I see in support work: every piece looks reasonable, but the autoscaler is waiting on a metric series that does not exist yet.

**TL;DR**

Problem: an ECS Fargate SQS worker with min capacity 0 stayed at 0 while 10 messages waited.

Cause: in this lab, a newly created service at 0 tasks had no `RunningTaskCount` datapoints, so `IF(running > 0, backlog / running, backlog)` produced no `bpt` datapoints.

Fix: use `FILL(running, 0)` so metric math can fall back to raw backlog when `RunningTaskCount` is missing.

## What you'll see

The service starts at zero, and the scalable target allows zero:

```text
== Initial state: desired=0 running=0
[
    {
        "min": 0,
        "max": 5
    }
]
```

After sending 10 SQS messages, it still does not move during the 8 minute wait:

```text
t+30s desired=0 running=0 visible_in_queue=10
...
t+480s desired=0 running=0 visible_in_queue=10
```

The target tracking alarms are not in `ALARM`. They do not have enough data:

```text
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

The key evidence is the metric math result. SQS backlog has datapoints. `RunningTaskCount` has none. The calculated backlog-per-task series, `bpt`, has none too:

```text
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
bpt datapoints: 0
```

Final result from the lab:

```text
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
HERO_RESULT: reproduced
```

## Why it happens

The source question uses this target tracking expression:

```text
IF(running > 0, backlog / running, backlog)
```

The idea is right: if tasks are running, divide backlog by task count. If there are no tasks, use raw backlog so the service can start.

What the lab confirms is narrower and important: for a newly created ECS service that had never run a task, `RunningTaskCount` did not publish datapoints while the service was at 0. CloudWatch metric math did not treat that as the number 0 for this expression. The result was an empty `bpt` series, so the target tracking alarm stayed in `INSUFFICIENT_DATA` and Application Auto Scaling did not change desired count.

The asker also reported the metric disappearing after the service reached 0 running tasks. I did not reproduce that N-to-0 path in this run. I reproduced the cold-start case from a fresh service at 0, which is enough to show why bootstrap can stall.

This expression fixed the lab:

```text
IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
```

`FILL` tells CloudWatch metric math what value to use when the metric is missing. Here, missing `RunningTaskCount` becomes 0, so the expression can choose the fallback branch and return raw backlog.

The source also suggests counting in-flight SQS messages and adding a separate 0-to-1 scale-out policy based on `DesiredTaskCount`. Those are likely useful patterns, especially around scale-in and Fargate startup time, but I did not test them here.

## Replicate it yourself, step by step

1. Get the lab.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/scale-to-zero-sqs-workers-on-ecs-fargate-447482
```

The IAM roles use `var.permissions_boundary`. Set it to `null` in your own account unless your account requires one.

2. Initialize and apply Terraform.

For a normal local run, use the README path:

```bash
terraform init
```

```bash
terraform apply
```

My captured pipeline used an S3 backend and applied a saved plan file:

```bash
terraform init -input=false -no-color -reconfigure -backend-config=bucket=hero-pipeline-tfstate-<account-id> -backend-config=key=labs/scale-to-zero-sqs-worker-447482/terraform.tfstate -backend-config=region=eu-central-1 -backend-config=use_lockfile=true
```

```text
Initializing the backend...
...
- Installing hashicorp/aws v5.100.0...
Terraform has been successfully initialized!
```

```bash
terraform apply -input=false -no-color -auto-approve tfplan
```

```text
Apply complete! Resources: 19 added, 0 changed, 0 destroyed.

Outputs:

cluster_name = "scale-to-zero-sqs-worker-447482-cluster"
policy_name = "scale-to-zero-sqs-worker-447482-backlog-per-task"
queue_name = "scale-to-zero-sqs-worker-447482-jobs"
queue_url = "https://sqs.eu-central-1.amazonaws.com/<account-id>/scale-to-zero-sqs-worker-447482-jobs"
resource_id = "service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker"
service_name = "scale-to-zero-sqs-worker-447482-worker"
```

Terraform creates an SQS queue, a VPC with two public subnets, an ECS cluster with Container Insights enabled, a Fargate service with desired count 0, IAM roles, a log group, a scalable target, and the target tracking policy.

3. Reproduce the failure by hand.

Read the Terraform outputs that the script uses:

```bash
CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
QURL=$(terraform output -raw queue_url)
QNAME=$(terraform output -raw queue_name)
RID=$(terraform output -raw resource_id)
```

Those values were:

```text
cluster_name = "scale-to-zero-sqs-worker-447482-cluster"
queue_name = "scale-to-zero-sqs-worker-447482-jobs"
queue_url = "https://sqs.eu-central-1.amazonaws.com/<account-id>/scale-to-zero-sqs-worker-447482-jobs"
resource_id = "service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker"
service_name = "scale-to-zero-sqs-worker-447482-worker"
```

These are the helper commands used by the script for desired count, running count, and visible queue messages:

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
```

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
```

```bash
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
```

The script prints the initial state and scalable target:

```bash
aws application-autoscaling describe-scalable-targets --service-namespace ecs --resource-ids "$RID" --query 'ScalableTargets[].{min:MinCapacity,max:MaxCapacity}' --output json
```

```text
== Initial state: desired=0 running=0
[
    {
        "min": 0,
        "max": 5
    }
]
```

Send messages. The script loops over this command 10 times and redirects each response to `/dev/null`:

```bash
aws sqs send-message --queue-url "$QURL" --message-body "job-$i" >/dev/null
```

Then it polls every 30 seconds. These lines are the script’s formatted output from the three helper commands above:

```text
t+30s desired=0 running=0 visible_in_queue=10
...
t+480s desired=0 running=0 visible_in_queue=10
```

Get the target tracking alarm names from the scaling policy:

```bash
ALARMS=$(aws application-autoscaling describe-scaling-policies --service-namespace ecs --resource-id "$RID" --query 'ScalingPolicies[0].Alarms[].AlarmName' --output text)
```

Describe those alarms:

```bash
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[].[AlarmName,StateValue]' --output text
```

```text
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
```

Check the alarm reason:

```bash
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[0].StateReason' --output text | cut -c1-300
```

```text
Unchecked: Initial alarm creation
```

Ask CloudWatch for the same metric math over the last 15 minutes:

```bash
START=$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
```

Create `/tmp/q-broken.json`:

```json
[
 {"Id":"backlog","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"scale-to-zero-sqs-worker-447482-jobs"}]},"Period":60,"Stat":"Sum"}},
 {"Id":"running","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"ECS/ContainerInsights","MetricName":"RunningTaskCount","Dimensions":[{"Name":"ClusterName","Value":"scale-to-zero-sqs-worker-447482-cluster"},{"Name":"ServiceName","Value":"scale-to-zero-sqs-worker-447482-worker"}]},"Period":60,"Stat":"Average"}},
 {"Id":"bpt","ReturnData":true,"Expression":"IF(running > 0, backlog / running, backlog)"}
]
```

Run the metric query:

```bash
METRICS=$(aws cloudwatch get-metric-data --start-time "$START" --end-time "$END" --metric-data-queries file:///tmp/q-broken.json)
```

Process it with the same jq filter:

```bash
echo "$METRICS" | jq -c '.MetricDataResults[] | {Id, datapoints: (.Values|length), Values}'
```

```text
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
```

Count `bpt` datapoints:

```bash
echo "$METRICS" | jq '[.MetricDataResults[] | select(.Id=="bpt") | (.Values|length)] | add // 0'
```

```text
bpt datapoints: 0
```

Check scaling activities:

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

```text

```

The reproduction ends this way:

```text
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
HERO_RESULT: reproduced
```

4. Apply the fix by hand.

Read the policy name too:

```bash
POLICY=$(terraform output -raw policy_name)
```

The script checks the state before changing the policy with the same helper commands:

```text
== Before: desired=0 running=0 visible=10
```

Create `/tmp/policy-fixed.json`:

```json
{
 "TargetValue": 2,
 "ScaleOutCooldown": 60,
 "ScaleInCooldown": 300,
 "CustomizedMetricSpecification": {
  "Metrics": [
   {"Id":"backlog","ReturnData":false,"MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"scale-to-zero-sqs-worker-447482-jobs"}]},"Stat":"Sum"}},
   {"Id":"running","ReturnData":false,"MetricStat":{"Metric":{"Namespace":"ECS/ContainerInsights","MetricName":"RunningTaskCount","Dimensions":[{"Name":"ClusterName","Value":"scale-to-zero-sqs-worker-447482-cluster"},{"Name":"ServiceName","Value":"scale-to-zero-sqs-worker-447482-worker"}]},"Stat":"Average"}},
   {"Id":"bpt","Label":"Backlog per task","ReturnData":true,"Expression":"IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)"}
  ]
 }
}
```

Update the existing target tracking policy:

```bash
aws application-autoscaling put-scaling-policy --policy-name "$POLICY" --policy-type TargetTrackingScaling --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount --target-tracking-scaling-policy-configuration file:///tmp/policy-fixed.json --query 'PolicyARN' --output text
```

```text
arn:aws:autoscaling:eu-central-1:<account-id>:scalingPolicy:0b5eb0c3-92be-46eb-9e09-d6e225ccfe6b:resource/ecs/service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker:policyName/scale-to-zero-sqs-worker-447482-backlog-per-task
```

The fix script polls with these commands: the desired count command, the running count command, and these two SQS attribute reads:

```bash
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
```

```bash
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessagesNotVisible --query 'Attributes.ApproximateNumberOfMessagesNotVisible' --output text
```

The formatted loop output was:

```text
t+30s desired=5 running=0 visible=10 in_flight=0
t+60s desired=5 running=5 visible=10 in_flight=0
```

Check scaling activities:

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

```text
Successful	Setting desired count to 5.
```

The queue-drain lines are also formatted script output from the service and queue attribute commands:

```text
visible=6 in_flight=0 desired=5 running=5
visible=0 in_flight=4 desired=5 running=5
visible=0 in_flight=0 desired=5 running=5
HERO_RESULT: fixed
```

5. Clean up.

The cleanup script is best effort and redirects most output to `/dev/null`, so the captured cleanup log was blank. These are the commands it runs.

It reads outputs:

```bash
CLUSTER=$(terraform output -raw cluster_name 2>/dev/null)
SERVICE=$(terraform output -raw service_name 2>/dev/null)
QURL=$(terraform output -raw queue_url 2>/dev/null)
RID=$(terraform output -raw resource_id 2>/dev/null)
POLICY=$(terraform output -raw policy_name 2>/dev/null)
```

It deletes the scaling policy:

```bash
aws application-autoscaling delete-scaling-policy --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount --policy-name "$POLICY" >/dev/null 2>&1
```

It sets desired count to 0:

```bash
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 >/dev/null 2>&1
```

It lists tasks and stops any that exist. If there are no task ARNs, the loop does nothing:

```bash
aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[]' --output text 2>/dev/null
```

```bash
aws ecs stop-task --cluster "$CLUSTER" --task "$t" >/dev/null 2>&1
```

It waits until no tasks remain:

```bash
aws ecs list-tasks --cluster "$CLUSTER" --query 'length(taskArns)' --output text 2>/dev/null
```

It purges the queue:

```bash
aws sqs purge-queue --queue-url "$QURL" >/dev/null 2>&1
```

Then destroy the Terraform stack:

```bash
terraform destroy
```

Because cleanup already removed the scaling policy, the captured destroy removed 18 resources:

```text
Plan: 0 to add, 0 to change, 18 to destroy.
...
Destroy complete! Resources: 18 destroyed.
```

## What I didn't test

I did not wait for scale-in back to 0. The lab README notes that the target-tracking low alarm needs about 15 minutes of low data, and each script had a 15 minute limit.

I did not reproduce the source question’s N-to-0 disappearance path. My lab proved the fresh-service-at-0 bootstrap failure.

I did not test adding `ApproximateNumberOfMessagesNotVisible` to the backlog expression. That may help scale-in behavior while tasks are processing messages, but it does not protect an individual busy task from being stopped.

I also did not test the separate 0-to-1 scale-out-only policy keyed on `DesiredTaskCount`, or ECS task scale-in protection. If your workers process long jobs, task scale-in protection is the part I would test next.

## Cost and time

This run was in `eu-central-1` and took 19.1 minutes end to end.

Terraform created 19 resources. The fixed policy scaled the service to 5 Fargate tasks. Each task used `cpu = 256` and `memory = 512`. The lab also used an SQS queue, an ECS cluster with Container Insights enabled, a CloudWatch log group, IAM roles, and a small public VPC with 2 subnets.

The actual Fargate runtime was only a few minutes. The README describes the cost as a few minutes of 0.25 vCPU Fargate tasks, with Container Insights metrics costing cents.

## Sources

- https://stackoverflow.com/questions/80006730/aws-ecs-fargate-sqs-autoscaling-with-min-capacity-0-how-to-bootstrap-from-0-tas

If your ECS worker has to scale from zero, make sure the metric that drives scale-out still produces a value at zero. In this lab, treating missing `RunningTaskCount` as 0 was the difference between a quiet worker and 10 messages sitting in the queue.

_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
