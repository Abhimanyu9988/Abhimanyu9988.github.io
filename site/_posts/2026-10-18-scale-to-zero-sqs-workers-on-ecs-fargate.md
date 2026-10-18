---
title: "ECS Fargate SQS workers stuck at zero with backlog-per-task scaling"
description: "I reproduced an ECS Fargate SQS worker that stayed at 0 tasks even with messages waiting. Updating the target-tracking metric math to use FILL(running, 0) made the service scale out from 0 in the lab."
tags: ["aws", "ecs", "sqs", "devops"]
channels: ["devto", "hashnode"]
lab: labs/scale-to-zero-sqs-workers-on-ecs-fargate
linkedin: "An ECS Fargate SQS worker can sit at 0 tasks while messages are waiting.\n\nThis hits teams trying to save idle Fargate cost with min capacity 0 and target tracking on backlog per task.\n\nIn my lab, SQS had 10 visible messages, but ECS stayed at desired=0 running=0. Container Insights RunningTaskCount had no datapoints at 0 tasks, so the backlog-per-task metric had no datapoints and the target-tracking alarms stayed in INSUFFICIENT_DATA.\n\nThe fix I tested was to wrap the task count with FILL(running, 0):\nIF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)\n\nAfter updating the scaling policy, desired count moved to 5, tasks reached RUNNING, and the queue drained.\n\nHave you handled ECS scale-to-zero workers with target tracking, step scaling, or a separate bootstrap policy?\n\nFull write-up, every command and the lab: {POST_URL}\n\n#aws #ecs #sqs #devops"
---
A worker service looks quiet, the queue is empty, and scaling down to zero feels like the right thing to do. That is what the Stack Overflow asker was trying to build: an ECS Fargate service consuming SQS, with minimum capacity set to 0 so they do not pay for an idle worker.

The confusing part shows up later. Messages arrive in SQS, but the service stays at 0 tasks. The metric math already says `IF(running > 0, backlog / running, backlog)`, so it looks like it should fall back to the raw backlog when there are no running tasks.

This is the kind of autoscaling edge case I see trip people up in AWS support work. I reproduced it in a disposable lab in `eu-central-1`: SQS queue, ECS cluster with Container Insights, Fargate worker service, and the same target-tracking shape.

**TL;DR**

Problem: ECS Fargate SQS worker with min capacity 0 stayed at `desired=0 running=0` even with 10 messages waiting.

Cause: at 0 tasks, `ECS/ContainerInsights RunningTaskCount` had no datapoints, so the backlog-per-task expression had no datapoints either.

Fix: use `FILL(running, 0)` in the CloudWatch metric math expression, then update the scaling policy.

## What you'll see

The service does not leave zero while SQS has messages:

```text
t+30s desired=0 running=0 visible_in_queue=10
...
t+480s desired=0 running=0 visible_in_queue=10
```

The target-tracking alarms stay in `INSUFFICIENT_DATA`:

```text
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

CloudWatch has SQS backlog datapoints, but no `running` datapoints and no `bpt` datapoints:

```text
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
bpt datapoints: 0
```

After updating the policy, desired count was already 5 at the first 30-second check. The tasks reached `RUNNING` at the 60-second check:

```text
t+30s desired=5 running=0 visible=10 in_flight=0
t+60s desired=5 running=5 visible=10 in_flight=0
```

## Why it happens

Target tracking means Application Auto Scaling tries to keep a metric near a target value. In this lab, the target was 2 messages per task.

The original metric math used two CloudWatch metrics:

- `AWS/SQS ApproximateNumberOfMessagesVisible`, the visible queue backlog.
- `ECS/ContainerInsights RunningTaskCount`, the number of running ECS tasks for the service.

The expression was:

```text
IF(running > 0, backlog / running, backlog)
```

At first glance, that looks safe. If `running` is greater than zero, divide by it. Otherwise, use `backlog`.

The lab confirms the painful detail: when the ECS service had 0 running tasks, `RunningTaskCount` from Container Insights had no datapoints. It was not a datapoint with value `0`; it was missing.

Because `running` was missing, the metric-math result `bpt` was also missing. That left the target-tracking alarms in `INSUFFICIENT_DATA`, so Application Auto Scaling did not get an alarm transition that told it to scale out.

The Stack Overflow answer suggested using `FILL()` to turn missing `running` data into zero:

```text
IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
```

I tested that idea. I did not run a second `get-metric-data` query for the fixed expression, so I am not claiming that from direct metric output here. What I did observe is that after the policy update, the service scaled from 0 to desired count 5, 5 tasks reached `RUNNING`, and the queue drained. The mechanism is inferred from the expression and that observed scale-out.

The other answer suggested adding in-flight messages, `ApproximateNumberOfMessagesNotVisible`, and possibly adding a separate 0-to-1 scale-out policy keyed on `DesiredTaskCount`. Those are reasonable patterns, but I did not test them in this run. It is likely that including in-flight messages can reduce premature scale-in while tasks are processing, but it does not protect a busy task from being stopped. ECS task scale-in protection is the feature that protects individual busy tasks.

## Replicate it yourself, step by step

### 1. Get the lab

Clone the repo and move into this lab folder:

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/scale-to-zero-sqs-workers-on-ecs-fargate
```

If your account does not require an IAM permissions boundary, open `providers.tf` and set `permissions_boundary` to `null`. The lab provider already sets the region to `eu-central-1`.

### 2. Create the lab

Initialize Terraform:

```bash
terraform init
```

Relevant output from my run, with backend details trimmed:

```text
Initializing the backend...
...
Initializing provider plugins...
- Finding hashicorp/aws versions matching "~> 5.80"...
- Installing hashicorp/aws v5.100.0...
- Installed hashicorp/aws v5.100.0 (signed by HashiCorp)

Terraform has created a lock file .terraform.lock.hcl to record the provider
selections it made above. Include this file in your version control repository
so that Terraform can guarantee to make the same selections by default when
you run "terraform init" in the future.

Terraform has been successfully initialized!
```

Apply the Terraform:

```bash
terraform apply
```

This creates the VPC, two public subnets, an SQS queue, an ECS cluster with Container Insights enabled, IAM roles, a Fargate task definition, an ECS service with desired count 0, an Application Auto Scaling target with min 0 and max 5, and the original target-tracking policy.

```text
aws_iam_role.exec: Creating...
aws_iam_role.task: Creating...
aws_sqs_queue.jobs: Creating...
aws_cloudwatch_log_group.worker: Creating...
aws_ecs_cluster.this: Creating...
aws_vpc.this: Creating...
...
aws_appautoscaling_policy.worker_backlog: Creation complete after 1s [id=scale-to-zero-sqs-worker-447482-backlog-per-task]

Apply complete! Resources: 19 added, 0 changed, 0 destroyed.

Outputs:

cluster_name = "scale-to-zero-sqs-worker-447482-cluster"
policy_name = "scale-to-zero-sqs-worker-447482-backlog-per-task"
queue_name = "scale-to-zero-sqs-worker-447482-jobs"
queue_url = "https://sqs.eu-central-1.amazonaws.com/<account-id>/scale-to-zero-sqs-worker-447482-jobs"
resource_id = "service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker"
service_name = "scale-to-zero-sqs-worker-447482-worker"
```

Set the Terraform outputs as shell variables:

```bash
CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
QURL=$(terraform output -raw queue_url)
QNAME=$(terraform output -raw queue_name)
RID=$(terraform output -raw resource_id)
POLICY=$(terraform output -raw policy_name)
```

### 3. Reproduce the stuck-at-zero behavior

These helper functions make the later loops readable. The AWS CLI commands inside them return bare values; the loop prints the combined status lines you see in the output.

```bash
desired() { aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text; }
running() { aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text; }
visible() { aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text; }
echo "== Initial state: desired=$(desired) running=$(running)"
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

Send 10 messages:

```bash
for i in $(seq 1 10); do
  aws sqs send-message --queue-url "$QURL" --message-body "job-$i" >/dev/null
done
```

Wait up to 8 minutes for the service to leave zero:

```bash
LEFT=0
for i in $(seq 1 16); do
  sleep 30
  D=$(desired)
  echo "t+$((i*30))s desired=$D running=$(running) visible_in_queue=$(visible)"
  if [ "$D" != "0" ] && [ "$D" != "None" ]; then LEFT=1; break; fi
done
```

Output, trimmed to the first and last checks:

```text
t+30s desired=0 running=0 visible_in_queue=10
...
t+480s desired=0 running=0 visible_in_queue=10
```

Get and describe the target-tracking alarms:

```bash
ALARMS=$(aws application-autoscaling describe-scaling-policies --service-namespace ecs --resource-id "$RID" --query 'ScalingPolicies[0].Alarms[].AlarmName' --output text)
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[].[AlarmName,StateValue]' --output text
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[0].StateReason' --output text | cut -c1-300
```

Output:

```text
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

Ask CloudWatch for the metric math data:

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
BPT_N=$(echo "$METRICS" | jq '[.MetricDataResults[] | select(.Id=="bpt") | (.Values|length)] | add // 0' 2>/dev/null)
echo "bpt datapoints: ${BPT_N:-unknown}"
```

Output:

```text
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
bpt datapoints: 0
```

Check scaling activities:

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

Output:

```text

```

Final check:

```bash
FD=$(desired); FR=$(running); FV=$(visible)
echo "Final: desired=$FD running=$FR messages_waiting=$FV bpt_datapoints=$BPT_N"
```

Output:

```text
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
```

### 4. Apply the fix and verify scale-out

Use the same helper shape, with one more queue attribute for in-flight messages:

```bash
attr() { aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names "$1" --query "Attributes.$1" --output text; }
echo "== Before: desired=$(desired) running=$(running) visible=$(attr ApproximateNumberOfMessages)"
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

Update the existing scaling policy:

```bash
echo "== Updating policy with: $EXPR"
aws application-autoscaling put-scaling-policy --policy-name "$POLICY" --policy-type TargetTrackingScaling --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount --target-tracking-scaling-policy-configuration file:///tmp/policy-fixed.json --query 'PolicyARN' --output text
```

Output:

```text
== Updating policy with: IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
arn:aws:autoscaling:eu-central-1:<account-id>:scalingPolicy:0b5eb0c3-92be-46eb-9e09-d6e225ccfe6b:resource/ecs/service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker:policyName/scale-to-zero-sqs-worker-447482-backlog-per-task
```

Wait for desired count and running count:

```bash
for i in $(seq 1 20); do
  sleep 30
  D=$(desired); R=$(running)
  echo "t+$((i*30))s desired=$D running=$R visible=$(attr ApproximateNumberOfMessages) in_flight=$(attr ApproximateNumberOfMessagesNotVisible)"
  if [ "$D" != "0" ] && [ "$D" != "None" ] && [ "$R" != "0" ] && [ "$R" != "None" ]; then break; fi
done
```

Output:

```text
t+30s desired=5 running=0 visible=10 in_flight=0
t+60s desired=5 running=5 visible=10 in_flight=0
```

Confirm Application Auto Scaling acted:

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

Output:

```text
Successful	Setting desired count to 5.
```

Watch the queue drain:

```bash
for i in $(seq 1 5); do
  V=$(attr ApproximateNumberOfMessages); N=$(attr ApproximateNumberOfMessagesNotVisible)
  echo "visible=$V in_flight=$N desired=$(desired) running=$(running)"
  [ "$V" = "0" ] && [ "$N" = "0" ] && break
  sleep 30
done
```

Output:

```text
visible=6 in_flight=0 desired=5 running=5
visible=0 in_flight=4 desired=5 running=5
visible=0 in_flight=0 desired=5 running=5
```

For Terraform, the equivalent fix is to change the `bpt` expression in `main.tf` from:

```text
IF(running > 0, backlog / running, backlog)
```

to:

```text
IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
```

### 5. Cleanup

Delete the scaling policy first so it cannot relaunch tasks during cleanup:

```bash
aws application-autoscaling delete-scaling-policy --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount --policy-name "$POLICY" >/dev/null 2>&1
```

Set desired count to zero:

```bash
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 >/dev/null 2>&1
```

Stop any remaining tasks:

```bash
for t in $(aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[]' --output text 2>/dev/null); do
  aws ecs stop-task --cluster "$CLUSTER" --task "$t" >/dev/null 2>&1
done
```

Wait until no tasks are left:

```bash
for i in $(seq 1 18); do
  N=$(aws ecs list-tasks --cluster "$CLUSTER" --query 'length(taskArns)' --output text 2>/dev/null)
  [ -z "$N" ] || [ "$N" = "0" ] && break
  sleep 10
done
```

Purge the queue:

```bash
aws sqs purge-queue --queue-url "$QURL" >/dev/null 2>&1
```

The cleanup command produced no output in my run.

Destroy the Terraform resources:

```bash
terraform destroy
```

Because the scaling policy was already deleted during cleanup, Terraform destroyed 18 resources here:

```text
Plan: 0 to add, 0 to change, 18 to destroy.

Changes to Outputs:
  - cluster_name = "scale-to-zero-sqs-worker-447482-cluster" -> null
  - policy_name  = "scale-to-zero-sqs-worker-447482-backlog-per-task" -> null
  - queue_name   = "scale-to-zero-sqs-worker-447482-jobs" -> null
  - queue_url    = "https://sqs.eu-central-1.amazonaws.com/<account-id>/scale-to-zero-sqs-worker-447482-jobs" -> null
  - resource_id  = "service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker" -> null
  - service_name = "scale-to-zero-sqs-worker-447482-worker" -> null
...
aws_sqs_queue.jobs: Destruction complete after 4s

Destroy complete! Resources: 18 destroyed.
```

## What I didn't test

I did not measure scale-in back to 0 after the queue drained. The lab README notes that the target-tracking `AlarmLow` needs about 15 minutes of low data, and each script had a 15 minute limit.

I did not test the expression that adds `ApproximateNumberOfMessagesNotVisible` to include in-flight messages.

I did not test a separate 0-to-1 scale-out policy keyed on `DesiredTaskCount`.

I did not test ECS task scale-in protection. That matters if your worker must finish a message before it can be stopped. In this lab, the queue visibility timeout was 60 seconds and each message took 20 seconds.

## Cost and time

The reproduced-and-fixed run took 19.1 minutes in `eu-central-1`.

Terraform created 19 resources: VPC, internet gateway, two public subnets, route table and associations, security group, SQS queue, IAM roles and policies, CloudWatch log group, ECS cluster, task definition, ECS service, Application Auto Scaling target, and target-tracking policy.

The worker task size was `256` CPU units and `512` MiB memory. During the fixed run, target tracking set desired count to 5 and 5 Fargate tasks reached `RUNNING`.

Cost should be small for a short lab: a few minutes of Fargate, SQS requests, CloudWatch logs, and Container Insights metrics. Still, destroy it when you are done.

## Sources

- https://stackoverflow.com/questions/80006730/aws-ecs-fargate-sqs-autoscaling-with-min-capacity-0-how-to-bootstrap-from-0-tas

If you are scaling ECS workers from SQS, test the zero-task case on purpose. A metric that is missing is not the same thing as a metric with value zero, and autoscaling cares about that difference.

_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance
and reviewed by me._
