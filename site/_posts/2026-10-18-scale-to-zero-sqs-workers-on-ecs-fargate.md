---
title: "SQS worker on Fargate stuck at 0 tasks: fixing backlog-per-task with FILL()"
description: "A Fargate service with min capacity 0 and a backlog-per-task target-tracking policy never left 0 tasks while messages waited. I reproduced it in eu-central-1 and fixed it by wrapping RunningTaskCount in FILL(running, 0)."
tags: ["aws", "ecs", "fargate", "sqs"]
channels: ["devto", "hashnode"]
lab: labs/scale-to-zero-sqs-workers-on-ecs-fargate
---
An ECS Fargate worker reads from an SQS queue. It uses target tracking on backlog per task, with minimum capacity 0. Messages arrive, but the service stays at 0 tasks and never scales out. This is based on a Stack Overflow question, and I reproduced it and fixed it in a small lab.

## What you'll see

The service stays at zero for as long as you wait while the queue holds messages. This is from my run, with 10 messages sent and a wait of 8 minutes:

```
== Initial state: desired=0 running=0
...
t+30s desired=0 running=0 visible_in_queue=10
t+60s desired=0 running=0 visible_in_queue=10
...
t+480s desired=0 running=0 visible_in_queue=10
```

The two target-tracking alarms never leave `INSUFFICIENT_DATA`:

```
TargetTracking-service/...-AlarmHigh-bca35fd1-...	INSUFFICIENT_DATA
TargetTracking-service/...-AlarmLow-af7b562c-...	INSUFFICIENT_DATA
Unchecked: Initial alarm creation
```

When I evaluate the same metric math with `get-metric-data`, the `backlog` series has data. The `running` and `bpt` series are empty:

```
{"Id":"backlog","datapoints":8,"Values":[10.0,10.0,10.0,10.0,10.0,10.0,10.0,10.0]}
{"Id":"running","datapoints":0,"Values":[]}
{"Id":"bpt","datapoints":0,"Values":[]}
```

Application Auto Scaling also records no scaling activities.

## Why it happens

**What the lab confirms:**
- The policy's expression is `IF(running > 0, backlog / running, backlog)`.
- `RunningTaskCount` from `ECS/ContainerInsights` has no datapoints when the service has no running tasks.
- A missing input makes the whole `bpt` series empty. The `IF` guard does not help, because `running > 0` is itself evaluated on a missing value. The guard only covers a value of 0, not an absent datapoint.
- With no datapoints, the alarms have nothing to evaluate, and nothing scales out.

**Limits of what I tested:**
- In my lab the service started at 0 and never ran a task. I did not test whether `RunningTaskCount` also disappears after a scale-in from N to 0. The asker describes it that way, and it is plausible, but I did not confirm it.
- I did not look up why Container Insights publishes nothing at 0 tasks. I only observed that it does not.

## Reproduce it

The lab is here: [labs/scale-to-zero-sqs-workers-on-ecs-fargate](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/scale-to-zero-sqs-workers-on-ecs-fargate). It runs in eu-central-1 and builds:

- An SQS queue with a 60 s visibility timeout, and a VPC with two public subnets.
- An ECS cluster with Container Insights enabled.
- A Fargate service with min 0 and max 5. The worker is the public `aws-cli` image, looping on `receive-message`. Each message takes 20 s.
- The asker's target-tracking policy, with a target of 2 messages per task.

The cluster setting that produces the `ECS/ContainerInsights` metrics:

```hcl
resource "aws_ecs_cluster" "this" {
  name = "${var.lab_name}-cluster"
  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}
```

The service starts at 0, and Terraform ignores later changes to the desired count so the autoscaler owns it:

```hcl
resource "aws_ecs_service" "worker" {
  ...
  desired_count   = 0
  launch_type     = "FARGATE"
  ...
  lifecycle {
    ignore_changes = [desired_count]
  }
}
```

The policy, with the key metric-math pieces:

```hcl
metrics {
  id          = "running"
  return_data = false
  metric_stat {
    stat = "Average"
    metric {
      namespace   = "ECS/ContainerInsights"
      metric_name = "RunningTaskCount"
      ...
    }
  }
}
metrics {
  id          = "bpt"
  label       = "Backlog per task"
  expression  = "IF(running > 0, backlog / running, backlog)"
  return_data = true
}
```

Run it:

```
terraform init && terraform apply
./reproduce.sh
```

`terraform apply` took 36 s and created 19 resources. `reproduce.sh` sends 10 messages and waits up to 8 minutes. It then prints the alarm states and the metric-math datapoints. It prints `HERO_RESULT: reproduced` only if all of these hold:
- the service is still at 0;
- messages are still waiting;
- the `bpt` series has no datapoints.

My run ended with:

```
Final: desired=0 running=0 messages_waiting=10 bpt_datapoints=0
Messages are waiting but the 'bpt' series has no datapoints (RunningTaskCount missing), so the alarm never fires and the service stays at 0.
HERO_RESULT: reproduced
```

## The fix

Wrap the task count in `FILL(running, 0)`. When the datapoint is missing, it becomes 0, so the `IF` takes its else branch and returns the raw backlog:

```
IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
```

I applied it with `fix.sh`, which calls `put-scaling-policy` with the same target (2), cooldowns and metrics. I used the CLI because replacing the configuration of an existing resource doesn't fit a separate `fix.tf`. In Terraform you would change the `expression` on the `bpt` metric in `main.tf`. I did not run that Terraform change.

Result:

```
== Before: desired=0 running=0 visible=10
== Updating policy with: IF(FILL(running, 0) > 0, backlog / FILL(running, 0), backlog)
...
t+30s desired=5 running=0 visible=10 in_flight=0
t+60s desired=5 running=5 visible=10 in_flight=0

== Scaling activities
Successful	Setting desired count to 5.
== Watching queue drain (up to 2.5 min, bounded by script time budget)
visible=6 in_flight=0 desired=5 running=5
visible=0 in_flight=4 desired=5 running=5
visible=0 in_flight=0 desired=5 running=5
HERO_RESULT: fixed
```

Within 30 s of the policy update the desired count was 5, which is 10 messages divided by the target of 2. Within 60 s, five tasks were running, and the queue drained.

### What I did not test

My scripts have a time limit of about 15 minutes, so some things are untested:

- **Scale-in back to 0.** The target-tracking low alarm needs roughly 15 minutes of low data. The desired count was still 5 when the script ended.
- **The in-flight variant.** One Stack Overflow answer adds `ApproximateNumberOfMessagesNotVisible` to the backlog. I did not run it. Its aim is less premature scale-in, but it does not protect an individual task from being stopped.
- **A separate 0-to-1 step policy** keyed on `DesiredTaskCount`. I did not test it either.
- **ECS task scale-in protection.** As far as I know it is the only thing that keeps a busy task from being stopped during scale-in. I did not test it. If a task is stopped mid-message, the message should come back after the visibility timeout, but I did not observe that.

So I confirmed the 0-to-N bootstrap only. Check the scale-in behavior yourself before you rely on this for long-running jobs.

## Clean up

`cleanup.sh` deletes the scaling policy, sets the desired count to 0, stops any tasks and purges the queue. It took 17 s. Then run `terraform destroy`. It destroyed 18 resources; the ECS service took about 1 m 11 s of that.

```
./cleanup.sh && terraform destroy
```

## Cost and time

The whole run, including apply, reproduce, fix, cleanup and destroy, took 19.1 minutes in eu-central-1. The reproduce script ran 572 s and the fix script 162 s. Cost is a few minutes of five 0.25 vCPU Fargate tasks, plus Container Insights metrics. The lab README says that comes to cents. I did not check the bill.

The IAM roles use a `permissions_boundary` variable. Set it to `null` in your own account.

## Sources

- https://stackoverflow.com/questions/80006730/aws-ecs-fargate-sqs-autoscaling-with-min-capacity-0-how-to-bootstrap-from-0-tas

_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
