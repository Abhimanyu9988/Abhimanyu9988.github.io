---
title: "ECS Fargate SQS workers stuck at zero with backlog-per-task scaling"
description: "I reproduced an ECS Fargate scale-to-zero issue where SQS messages waited but target tracking never started a task. The fix was to use FILL(running, 0) in the CloudWatch metric math expression."
tags: ["aws", "ecs", "sqs", "devops"]
channels: ["devto", "hashnode"]
lab: labs/scale-to-zero-sqs-workers-on-ecs-fargate
linkedin: "An ECS Fargate SQS worker can have 10 messages waiting and still sit at desired=0.\n\nThis hits teams trying to save idle Fargate cost with min capacity 0 and target tracking on backlog per task.\n\nI reproduced it in a lab: SQS had visible messages, but ECS stayed at desired=0 running=0 because Container Insights RunningTaskCount had no CloudWatch datapoint at zero tasks. The metric math produced no backlog-per-task datapoints, so the target-tracking alarms stayed in INSUFFICIENT_DATA.\n\nThe fix was to wrap the task count with FILL(running, 0). After updating the scaling policy, ECS scaled out to 5 tasks and drained the queue.\n\nHave you used a separate bootstrap policy for 0→1, or do you prefer fixing the metric math directly?\n\nFull write-up, every command and the lab: {POST_URL}\n\n#aws #ecs #sqs #devops"
replies: [{"url": "https://stackoverflow.com/questions/80006730/aws-ecs-fargate-sqs-autoscaling-with-min-capacity-0-how-to-bootstrap-from-0-tas", "text": "I reproduced this with an ECS Fargate service at min capacity 0 and the expression IF(running > 0, backlog / running, backlog). With 10 SQS messages waiting, RunningTaskCount had no datapoints, so the backlog-per-task expression also had 0 datapoints and both target-tracking alarms stayed in INSUFFICIENT_DATA. Updating the policy to IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog) fixed the bootstrap from 0 in my lab. Caveat: I did not test scale-in protection or the in-flight message variant here. I wrote up the full replication here: {POST_URL}"}]
---
A Stack Overflow asker was trying to do a sensible thing: run an ECS Fargate worker only when an SQS queue has work. The service had minimum capacity set to 0, and target tracking used the usual backlog-per-task idea: visible SQS messages divided by ECS RunningTaskCount.

The painful moment came after the service had scaled all the way down. New messages arrived, SQS showed work waiting, but ECS stayed at desired=0 running=0. The metric math looked like it should handle zero tasks: IF(running > 0, backlog / running, backlog).

This is the kind of failure I see in support work: the policy exists, the queue has messages, and nothing looks obviously broken. So I reproduced it in a disposable AWS lab in eu-central-1 and then tested the smallest fix.

## TL;DR

Problem: SQS messages wait, but an ECS Fargate worker with min capacity 0 stays at desired=0 running=0.

Cause: at zero tasks, Container Insights RunningTaskCount had no datapoint in my lab, so the backlog-per-task expression also returned no datapoints and the target-tracking alarms stayed in INSUFFICIENT_DATA.

Fix: use FILL(running, 0) so CloudWatch metric math can fall back to raw backlog when there are no running tasks.

## What you'll see

In my lab, I sent 10 messages and waited up to 8 minutes. The service never left zero:

```text
== Waiting up to 8 minutes for the service to leave 0 tasks
t+30s desired=0 running=0 visible_in_queue=10
t+60s desired=0 running=0 visible_in_queue=10
t+90s desired=0 running=0 visible_in_queue=10
t+120s desired=0 running=0 visible_in_queue=10
t+150s desired=0 running=0 visible_in_queue=10
t+180s desired=0 running=0 visible_in_queue=10
t+210s desired=0 running=0 visible_in_queue=10
t+240s desired=0 running=0 visible_in_queue=10
t+270s desired=0 running=0 visible_in_queue=10
t+300s desired=0 running=0 visible_in_queue=10
t+330s desired=0 running=0 visible_in_queue=10
t+360s desired=0 running=0 visible_in_queue=10
t+390s desired=0 running=0 visible_in_queue=10
t+420s desired=0 running=0 visible_in_queue=10
t+450s desired=0 running=0 visible_in_queue=10
t+480s desired=0 running=0 visible_in_queue=10
```

The target-tracking alarms were not in ALARM. They were stuck without usable data:

```text
== Policy alarms
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

The CloudWatch metric math showed the real problem. SQS backlog had datapoints, but running and bpt did not:

```text
== Metric math as the policy evaluates it (last 15 min, 60s period)
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
bpt datapoints: 0
```

The final state was still zero tasks with messages waiting:

```text
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
```

## Why it happens

The source question reported that RunningTaskCount has no CloudWatch datapoint when the service is at 0 running tasks. My lab confirmed that behavior for this setup.

The target-tracking policy used CloudWatch metric math. The backlog metric was SQS ApproximateNumberOfMessagesVisible. The running metric was ECS Container Insights RunningTaskCount. The expression was:

```text
IF(running > 0, backlog / running, backlog)
```

That protects you from dividing by zero. It does not protect you from missing data.

At zero running tasks, my lab saw no datapoints for RunningTaskCount. Because running had no datapoints, the expression did not choose the fallback backlog value. It produced no datapoints for bpt. Target tracking acts through CloudWatch alarms, and those alarms stayed in INSUFFICIENT_DATA, so Application Auto Scaling did not scale the service out.

The fix suggested in one Stack Overflow answer was confirmed by the lab:

```text
IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
```

FILL tells CloudWatch metric math what value to use when a metric is missing. Here, missing running becomes 0, so the expression can choose the raw backlog path.

I did not prove that adding ApproximateNumberOfMessagesNotVisible is better for scale-in behavior. It is likely useful if you want backlog to include messages currently being processed, but it does not protect an individual busy task from being stopped. ECS task scale-in protection is the specific tool for that.

I also did not test a separate 0-to-1 policy keyed on DesiredTaskCount. That pattern may still be useful if you want a dedicated bootstrap policy, but this lab fixed the bootstrap problem with FILL alone.

## Replicate it yourself, step by step

You need the AWS CLI, jq, GNU date, Terraform, and AWS credentials for eu-central-1. The lab README also notes that the IAM roles use var.permissions_boundary; set it to null in your own account.

### 1. Get the lab

Clone the repo and move into the lab directory:

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/scale-to-zero-sqs-workers-on-ecs-fargate
```

The lab files set the AWS region to eu-central-1. If your shell is not already using that region for AWS CLI calls, set it too:

```bash
export AWS_DEFAULT_REGION="${AWS_REGION:-eu-central-1}"
```

### 2. Create the infrastructure

Initialize Terraform:

```bash
terraform init
```

In my run, Terraform used the AWS provider version shown here:

```text
Initializing provider plugins...
- Finding hashicorp/aws versions matching "~> 5.80"...
- Installing hashicorp/aws v5.100.0...
- Installed hashicorp/aws v5.100.0 (signed by HashiCorp)

Terraform has been successfully initialized!
```

Apply the lab:

```bash
terraform apply
```

Terraform created 19 resources: the queue, VPC, two public subnets, ECS cluster with Container Insights, Fargate service, autoscaling target, and target-tracking policy.

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

The worker task uses 256 CPU and 512 memory. It receives SQS messages, sleeps 20 seconds, then deletes the message. The queue visibility timeout is 60 seconds.

### 3. Set the values you will use

These are the Terraform outputs the scripts use. Set them once if you are doing the run by hand:

```bash
CLUSTER=$(terraform output -raw cluster_name)
SERVICE=$(terraform output -raw service_name)
QURL=$(terraform output -raw queue_url)
QNAME=$(terraform output -raw queue_name)
RID=$(terraform output -raw resource_id)
POLICY=$(terraform output -raw policy_name)
```

These assignments print nothing.

### 4. Check the initial ECS and autoscaling state

Check desired and running task counts:

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
```

The script printed the combined result like this:

```text
== Initial state: desired=0 running=0
```

Check the scalable target:

```bash
aws application-autoscaling describe-scalable-targets --service-namespace ecs --resource-ids "$RID" --query 'ScalableTargets[].{min:MinCapacity,max:MaxCapacity}' --output json
```

Output:

```json
[
    {
        "min": 0,
        "max": 5
    }
]
```

### 5. Send work to SQS

Send 10 messages:

```bash
for i in $(seq 1 10); do
  aws sqs send-message --queue-url "$QURL" --message-body "job-$i" >/dev/null
done
```

The send command is redirected to /dev/null, so it prints no per-message output. The script printed this heading:

```text
== Sending 10 messages
```

### 6. Watch ECS stay at zero

The script checked desired tasks, running tasks, and visible SQS messages every 30 seconds for up to 8 minutes:

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
```

The repeated output is the 16-line loop shown in What you'll see. In this run, every line stayed at desired=0 running=0 visible_in_queue=10.

### 7. Inspect the target-tracking alarms

First get the alarm names from the scaling policy. This assignment matters; the next commands use $ALARMS:

```bash
ALARMS=$(aws application-autoscaling describe-scaling-policies --service-namespace ecs --resource-id "$RID" --query 'ScalingPolicies[0].Alarms[].AlarmName' --output text)
```

Now describe those alarms and the first alarm reason:

```bash
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[].[AlarmName,StateValue]' --output text
aws cloudwatch describe-alarms --alarm-names $ALARMS --query 'MetricAlarms[0].StateReason' --output text | cut -c1-300
```

Output:

```text
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmHigh-bca35fd1-1f83-47f3-bb4f-7e1460dd8a71	INSUFFICIENT_DATA
TargetTracking-service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker-AlarmLow-af7b562c-9e1e-4887-b89d-fae66aa4bd5e	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

### 8. Ask CloudWatch for the same metric math

Set the time window. This uses GNU date, as the README notes:

```bash
START=$(date -u -d '15 minutes ago' +%Y-%m-%dT%H:%M:%SZ)
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)
```

Create the CloudWatch metric-data query file. This heredoc expands $QNAME, $CLUSTER, and $SERVICE before writing the file:

```bash
cat > /tmp/q-broken.json <<EOF
[
 {"Id":"backlog","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"AWS/SQS","MetricName":"ApproximateNumberOfMessagesVisible","Dimensions":[{"Name":"QueueName","Value":"$QNAME"}]},"Period":60,"Stat":"Sum"}},
 {"Id":"running","ReturnData":true,"MetricStat":{"Metric":{"Namespace":"ECS/ContainerInsights","MetricName":"RunningTaskCount","Dimensions":[{"Name":"ClusterName","Value":"$CLUSTER"},{"Name":"ServiceName","Value":"$SERVICE"}]},"Period":60,"Stat":"Average"}},
 {"Id":"bpt","ReturnData":true,"Expression":"IF(running > 0, backlog / running, backlog)"}
]
EOF
```

Ask CloudWatch for the data, then use jq to show each series and count the bpt datapoints:

```bash
METRICS=$(aws cloudwatch get-metric-data --start-time "$START" --end-time "$END" --metric-data-queries file:///tmp/q-broken.json)
echo "$METRICS" | jq -c '.MetricDataResults[] | {Id, datapoints: (.Values|length), Values}'
BPT_N=$(echo "$METRICS" | jq '[.MetricDataResults[] | select(.Id=="bpt") | (.Values|length)] | add // 0' 2>/dev/null)
BPT_N="${BPT_N:-unknown}"
echo "bpt datapoints: $BPT_N"
```

Output:

```text
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
bpt datapoints: 0
```

Check scaling activities too:

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

In the reproduction run, nothing was printed under the scaling activities heading.

The final state was:

```text
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
```

### 9. Apply the fix

Before changing the policy, check the current state:

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
```

The script printed:

```text
== Before: desired=0 running=0 visible=10
```

Create the fixed target-tracking configuration. Use the heredoc so the queue, cluster, service, and expression are written with real values:

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

Put the updated scaling policy:

```bash
aws application-autoscaling put-scaling-policy --policy-name "$POLICY" --policy-type TargetTrackingScaling --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount --target-tracking-scaling-policy-configuration file:///tmp/policy-fixed.json --query 'PolicyARN' --output text
```

Output:

```text
== Updating policy with: IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
arn:aws:autoscaling:eu-central-1:<account-id>:scalingPolicy:0b5eb0c3-92be-46eb-9e09-d6e225ccfe6b:resource/ecs/service/scale-to-zero-sqs-worker-447482-cluster/scale-to-zero-sqs-worker-447482-worker:policyName/scale-to-zero-sqs-worker-447482-backlog-per-task
```

The Terraform equivalent is to change the expression of the bpt metric in main.tf.

### 10. Verify scale-out and queue drain

Watch desired count, running count, visible messages, and in-flight messages:

```bash
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].desiredCount' --output text
aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --query 'services[0].runningCount' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessages --query 'Attributes.ApproximateNumberOfMessages' --output text
aws sqs get-queue-attributes --queue-url "$QURL" --attribute-names ApproximateNumberOfMessagesNotVisible --query 'Attributes.ApproximateNumberOfMessagesNotVisible' --output text
```

The service scaled out from zero:

```text
== Waiting up to 10 minutes for the service to scale out from 0 and a task to reach RUNNING
t+30s desired=5 running=0 visible=10 in_flight=0
t+60s desired=5 running=5 visible=10 in_flight=0
```

Check scaling activities:

```bash
aws application-autoscaling describe-scaling-activities --service-namespace ecs --resource-id "$RID" --query 'ScalingActivities[].[StatusCode,Description]' --output text | head -5
```

Output:

```text
Successful	Setting desired count to 5.
```

Watch the queue drain:

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

That is the fix verification: the same service that stayed at zero with 10 messages waiting scaled to 5 desired tasks, reached 5 running tasks, and drained the queue.

### 11. Clean up

First remove the scaling policy so it does not relaunch tasks while you are cleaning up:

```bash
aws application-autoscaling delete-scaling-policy --service-namespace ecs --resource-id "$RID" --scalable-dimension ecs:service:DesiredCount --policy-name "$POLICY" >/dev/null 2>&1
```

Set the ECS service desired count to zero:

```bash
aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" --desired-count 0 >/dev/null 2>&1
```

Stop any remaining tasks:

```bash
for t in $(aws ecs list-tasks --cluster "$CLUSTER" --query 'taskArns[]' --output text 2>/dev/null); do
  aws ecs stop-task --cluster "$CLUSTER" --task "$t" >/dev/null 2>&1
done
```

Wait until no tasks remain. The cleanup script checks up to 18 times, 10 seconds apart:

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

Those cleanup commands print nothing in the successful run.

Now destroy the Terraform resources:

```bash
terraform destroy
```

Because cleanup already deleted the scaling policy, my destroy removed the remaining 18 resources:

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
aws_ecs_service.worker: Destruction complete after 1m11s
...
aws_sqs_queue.jobs: Destruction complete after 4s

Destroy complete! Resources: 18 destroyed.
```

## What I didn't test

I did not measure scale-in back to 0. The lab README notes that the target-tracking AlarmLow needs about 15 minutes of low data, and the script time budget did not cover that.

I did not test the expression that adds ApproximateNumberOfMessagesNotVisible to the backlog. That is still worth testing if your workers process long-running messages.

I did not test a separate scale-out-only policy for 0 to 1 using DesiredTaskCount. The FILL change alone was enough to bootstrap from zero in this lab.

I also did not test ECS task scale-in protection. If stopping a busy worker is dangerous for your workload, do not assume metric math alone solves that.

## Cost and time

This run was in eu-central-1 and took 19.1 minutes.

Terraform created 19 resources. The worker service started at 0, then the fixed policy set desired count to 5, and 5 tasks reached RUNNING. The task definition used 256 CPU and 512 memory. The queue visibility timeout was 60 seconds, and each message took 20 seconds in the worker loop.

The lab README describes the cost as a few minutes of 0.25 vCPU Fargate tasks, plus Container Insights metrics costing cents. Clean up when you are done.

## The takeaway

If your ECS SQS worker scales to zero, design for missing CloudWatch data, not just zero values. For backlog-per-task target tracking, FILL(running, 0) is the small change that let this service wake up when the queue got work.

## Sources

- [Stack Overflow: ECS Fargate SQS autoscaling with min capacity 0](https://stackoverflow.com/questions/80006730/aws-ecs-fargate-sqs-autoscaling-with-min-capacity-0-how-to-bootstrap-from-0-tas)

---
_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
