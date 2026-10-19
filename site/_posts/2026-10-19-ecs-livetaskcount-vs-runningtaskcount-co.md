---
title: "ECS RunningTaskCount says 1 while 3 tasks still drain"
description: "ECS LiveTaskCount and Container Insights RunningTaskCount can disagree during Fargate scale-in behind an ALB. I reproduced the mismatch and used metric math to make the gap visible."
image: /assets/posts/ecs-livetaskcount-vs-runningtaskcount-co/cover.png
image_alt: "Line chart of per-minute LiveTaskCount staying at 4 while RunningTaskCount drops to 1 during scale-in from 4 tasks to 1."
tags: ["aws", "ecs", "fargate", "devops"]
channels: ["devto", "hashnode"]
lab: labs/ecs-livetaskcount-vs-runningtaskcount-co
linkedin: "ECS RunningTaskCount says 1, but three old tasks are still draining.\n\nI reproduced this with a Fargate service behind an ALB. During scale-in, the ECS API showed 3 tasks in DEACTIVATING while Container Insights RunningTaskCount had already dropped to 1.\n\nThe fix was to stop using RunningTaskCount as “all live tasks.” I graphed LiveTaskCount and used LiveTaskCount - RunningTaskCount to show tasks alive but not counted as running. One caveat: that gap can also appear during startup, so alarm carefully.\n\nI reproduced it in a throwaway AWS lab and wrote up the fix with every command.\n\n{LINKS}\n\n#aws #ecs #fargate #devops"
replies: [{"url": "https://aws.amazon.com/about-aws/whats-new/2026/10/ecs-service-metrics/", "text": "I reproduced the LiveTaskCount vs Container Insights RunningTaskCount difference with a Fargate service behind an ALB. After scaling from 4 tasks to 1, the ECS API still showed 3 tasks in DEACTIVATING while RunningTaskCount was already 1. Graphing LiveTaskCount and adding metric math for LiveTaskCount - RunningTaskCount made the gap visible. Caveat: the gap can also appear during startup, so alarm thresholds need care. I wrote up the full replication here: {POST_URL}"}]
---
![Line chart of per-minute LiveTaskCount staying at 4 while RunningTaskCount drops to 1 during scale-in from 4 tasks to 1.](https://abhimanyu9988.github.io/assets/posts/ecs-livetaskcount-vs-runningtaskcount-co/cover.png)

You scale an ECS Fargate service from 4 tasks to 1 behind an ALB, and the dashboard says there is only one running task.

The ECS API still shows the old tasks in `DEACTIVATING`, connection draining is not done, and `LiveTaskCount` stays higher than Container Insights `RunningTaskCount`. I reproduced the mismatch in a throwaway AWS account, then used metric math to show the tasks that are alive but no longer counted as running.

That gap matched the draining tasks during scale-in in this lab. It is also a gap you should treat carefully, because it can appear during startup too.

**TL;DR**

Problem: ECS `RunningTaskCount` says `1` while old tasks are still draining.

Cause: in this lab, `LiveTaskCount` counted `DEACTIVATING` tasks, while Container Insights `RunningTaskCount` did not.

Fix: graph `LiveTaskCount`, and use `LiveTaskCount - RunningTaskCount` to show tasks alive but not counted as running. Do not page on one startup datapoint without checking your threshold or evaluation periods.

## What you'll see

I deployed 4 Fargate tasks behind an ALB, then scaled the service down to 1. The target group had a `120` second deregistration delay.

```text
Initial: 4	4  (desired running)
== Scale in: desiredCount 4 -> 1 (target group deregistration delay is 120s)
14:30:02 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
...
Max tasks seen in DEACTIVATING (draining): 3
2026-10-19T14:27:00+00:00  LiveTaskCount=1.0  RunningTaskCount=0.0
2026-10-19T14:28:00+00:00  LiveTaskCount=4.0  RunningTaskCount=3.0
2026-10-19T14:30:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0
2026-10-19T14:31:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0
Observation: while tasks were DEACTIVATING, LiveTaskCount stayed higher than RunningTaskCount - the two metrics disagree during scale-in.
```

The important part is the scale-in window. The service says desired/running is `1 1`, but the ECS task API still has 3 old tasks in `DEACTIVATING`. CloudWatch shows `LiveTaskCount=4.0` and `RunningTaskCount=1.0` for those minutes.

The earlier `14:27` and `14:28` rows matter too: Live and Running also differed briefly while the service was starting. So the subtraction is useful, but it is not a perfect “only draining” signal in every minute of a service’s life.

## Why it happens

`DEACTIVATING` is where tasks sit until connection draining ends. In this lab, the ALB target group had a `120` second deregistration delay, so the old targets did not disappear immediately after scale-in.

The AWS announcement introduces `LiveTaskCount`. The lab confirms the operational bit I cared about here: while tasks were in `DEACTIVATING`, `LiveTaskCount` still counted them. Container Insights `RunningTaskCount` did not.

So `RunningTaskCount` is not a safe “how many tasks still exist?” metric during scale-in. It can be the right metric for “how many tasks are running,” but it hides the draining tail.

What is only likely: the exact number of minutes where the metrics disagree depends on your deregistration delay, how quickly tasks move through ECS states, and CloudWatch’s `60` second metric periods. The behavior to plan around is the same: scale-in can create a window where `LiveTaskCount > RunningTaskCount`.

## The fix

Do not treat `RunningTaskCount` as “all live tasks.” For capacity and drain visibility, graph `LiveTaskCount`, then add metric math for the difference.

`fix.sh` builds a metric-math query with three entries: `live` (LiveTaskCount), `running` (RunningTaskCount) and `draining`, whose expression is `live - running`. It writes that query to `/tmp/hero-alarm-metrics.json` and creates the alarm on it:

```bash
aws cloudwatch put-metric-alarm --alarm-name "${LAB}-draining-tasks" \
  --alarm-description "Tasks still alive but no longer counted by RunningTaskCount (DEACTIVATING)" \
  --metrics file:///tmp/hero-alarm-metrics.json \
  --comparison-operator GreaterThanThreshold --threshold 0 --evaluation-periods 1 \
  --treat-missing-data notBreaching
```

Then I checked it against the same scale-in data:

```text
alarm ecs-livetaskcount-vs-run-981521-draining-tasks created
dashboard ecs-livetaskcount-vs-run-981521-tasks created
Max DrainingTasks (Live - Running) over the last hour: 3.0
Alarm exists: 1
Lesson: LiveTaskCount includes DEACTIVATING (draining) tasks; RunningTaskCount does not. Use Live for capacity/cost views, and Live-Running to see drain.
```

Caveat: an alarm on `GreaterThanThreshold 0` can also trip during startup or scale-out, so use more evaluation periods or a higher threshold if that would be noisy for your service.

## Try it yourself

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/ecs-livetaskcount-vs-runningtaskcount-co
export LAB_NAME=ecs-drain-demo      # names the alarm and dashboard the scripts create
export AWS_REGION=eu-central-1
terraform init && terraform apply -var lab_name="$LAB_NAME"
./reproduce.sh      # shows the problem
./fix.sh            # applies the fix and checks it
./cleanup.sh && terraform destroy -var lab_name="$LAB_NAME"
```

My run took `18.8` minutes, and the lab README estimates it costs a few cents.

Every command is in those short scripts, and the raw output of my run is in the lab's evidence folder.

## What I didn't test

- ECS services without an ALB target group.
- EC2 launch type instead of Fargate.
- Production autoscaling policies that already use custom metric math.

## Sources

- [Amazon ECS service metrics announcement](https://aws.amazon.com/about-aws/whats-new/2026/10/ecs-service-metrics/)

---
_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
