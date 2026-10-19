---
title: "During ECS scale-in, RunningTaskCount drops tasks that LiveTaskCount still counts"
description: "I reproduced an ECS Fargate scale-in behind an ALB and watched LiveTaskCount stay higher than Container Insights RunningTaskCount while tasks drained in DEACTIVATING. The useful signal is LiveTaskCount beside RunningTaskCount, with Live minus Running treated as a signal that includes draining tasks,"
tags: ["aws", "ecs", "cloudwatch", "devops"]
channels: ["devto", "hashnode"]
lab: labs/ecs-livetaskcount-vs-runningtaskcount-co
linkedin: "ECS said my service was running 1 task, but three more tasks were still alive and draining.\n\nI reproduced this with an ECS Fargate service behind an ALB in eu-central-1. I scaled from 4 tasks to 1 with a 120-second deregistration delay.\n\nThe ECS API showed 3 tasks in DEACTIVATING. CloudWatch showed LiveTaskCount at 4.0 while Container Insights RunningTaskCount was already 1.0.\n\nThe fix I verified on the metric data was simple: put LiveTaskCount beside RunningTaskCount, and use LiveTaskCount - RunningTaskCount as a signal that includes draining tasks. Caveat: in my data, that gap was also positive during startup, so it is not a perfect draining-only alarm.\n\nHave you seen dashboards or alarms get confusing during ECS scale-in because the metric did not mean what the team thought it meant?\n\nFull write-up, every command and the lab: {POST_URL}\n\n#aws #ecs #cloudwatch #devops"
replies: [{"url": "https://aws.amazon.com/about-aws/whats-new/2026/10/ecs-service-metrics/", "text": "I reproduced the LiveTaskCount behavior in a small ECS Fargate lab behind an ALB. During scale-in from 4 tasks to 1, the ECS API showed 3 tasks in DEACTIVATING for the target group drain period. In the same window, LiveTaskCount stayed at 4.0 while Container Insights RunningTaskCount was already 1.0. What helped the dashboard gap was putting LiveTaskCount beside RunningTaskCount, plus metric math: LiveTaskCount - RunningTaskCount. Caveat: that gap was also positive during startup in my data, so it is not draining-only. I wrote up the full replication here: {POST_URL}"}]
---
I scaled an ECS Fargate service behind an ALB from 4 tasks to 1, with a 120-second deregistration delay. While the three extra tasks drained, they sat in DEACTIVATING. LiveTaskCount still counted them, and Container Insights RunningTaskCount did not. Here's the raw output from the scale-in, and a metric-math alarm (Live minus Running) that shows the draining tasks.

This hurts in the exact moment when a dashboard is supposed to calm you down. ECS says the service is desired 1 and running 1, but the ECS API still shows three tasks alive in DEACTIVATING. If your graph only uses `RunningTaskCount`, it can look like those tasks disappeared before they finished draining from the load balancer.

Most of my week goes into one question: why does something that looks correctly configured not work in a real cluster? Before I write about a problem like this, I reproduce it in a throwaway AWS account and keep the raw output. This one is small, but it is the kind of metric mismatch that makes an on-call dashboard harder to trust.

**TL;DR**

Problem: During ECS scale-in, `RunningTaskCount` dropped to 1 while three tasks were still in `DEACTIVATING`.
Cause: In this lab, `LiveTaskCount` included the live draining tasks and Container Insights `RunningTaskCount` did not.
Fix: Put `LiveTaskCount` beside `RunningTaskCount`, and use `LiveTaskCount - RunningTaskCount` as a signal that includes draining tasks. Do not treat it as draining-only without a caveat.

## What you'll see

The service starts at four running tasks:

```text
Initial: 4	4  (desired running)
```

Then I scaled the service down to one task while the target group had a 120-second deregistration delay:

```text
== Scale in: desiredCount 4 -> 1 (target group deregistration delay is 120s)
14:30:02 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:16 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:30 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:43 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:57 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:10 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:24 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:38 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:52 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:32:06 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:32:20 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:32:33 API: service desired/running=[1	1] tasks by lastStatus: RUNNING=1 STOPPING=3
Max tasks seen in DEACTIVATING (draining): 3
```

CloudWatch then showed the metric mismatch:

```text
-- attempt 1: 11 datapoints, minutes where Live > Running: 4
== LiveTaskCount namespace: AWS/ECS   RunningTaskCount namespace: ECS/ContainerInsights
== Per-minute averages (UTC)
2026-10-19T14:27:00+00:00  LiveTaskCount=1.0  RunningTaskCount=0.0
2026-10-19T14:28:00+00:00  LiveTaskCount=4.0  RunningTaskCount=3.0
2026-10-19T14:29:00+00:00  LiveTaskCount=4.0  RunningTaskCount=4.0
2026-10-19T14:30:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0
2026-10-19T14:31:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0
Observation: while tasks were DEACTIVATING, LiveTaskCount stayed higher than RunningTaskCount - the two metrics disagree during scale-in.
```

The important drain rows are `14:30` and `14:31`. `RunningTaskCount` is already `1.0`, but `LiveTaskCount` is still `4.0`.

Also notice the earlier rows. At `14:27`, Live was `1.0` and Running was `0.0`. At `14:28`, Live was `4.0` and Running was `3.0`. Those were startup minutes, not drain minutes. So `LiveTaskCount - RunningTaskCount > 0` is not a pure draining detector. It is a signal that includes draining tasks and can also be positive while tasks are starting.

## Why it happens

The AWS announcement is why I tested this now: ECS has a `LiveTaskCount` service metric, and Container Insights has a `RunningTaskCount` metric. My lab did not prove the full service-side implementation of either metric. It confirmed how they behaved in one Fargate plus ALB scale-in.

In that lab, the ECS API showed three tasks in `DEACTIVATING` for about two minutes. During the same window, CloudWatch showed `LiveTaskCount=4.0` and `RunningTaskCount=1.0` for the service.

The practical reading from this run is:

- `RunningTaskCount` matched the ECS service running count during the scale-in window.
- `LiveTaskCount` still counted the tasks that were alive while they drained.
- `LiveTaskCount - RunningTaskCount` highlighted the gap, but that gap was also positive during startup minutes.

What is likely, based on the setup, is that the 120-second target group deregistration delay made the difference visible for long enough to catch in one-minute CloudWatch metrics. The lab did not prove how every ECS deployment type, load balancer setup, launch type, or deregistration delay behaves.

One Terraform note from the lab: my first attempt used the full lab name for the ALB and target group. AWS rejected those names because they were longer than 32 characters:

```text
Error: "name" cannot be longer than 32 characters: "ecs-livetaskcount-vs-run-981521-alb"

Error: "name" cannot be longer than 32 characters
```

The lab now uses a short hashed name for those two resources.

## Replicate it yourself, step by step

### 1. Get the lab

Clone the repo and move into this lab directory:

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/ecs-livetaskcount-vs-runningtaskcount-co
```

If your environment uses IAM permission boundaries, check `providers.tf`. For a normal disposable account run, set `permissions_boundary` to `null`.

### 2. Create the ECS service

Initialize Terraform:

```bash
terraform init
```

My run used AWS provider `v5.100.0`:

```text
Initializing provider plugins...
- Finding hashicorp/aws versions matching "~> 5.0"...
- Installing hashicorp/aws v5.100.0...
- Installed hashicorp/aws v5.100.0 (signed by HashiCorp)

Terraform has been successfully initialized!
```

Apply the Terraform:

```bash
terraform apply
```

This creates a VPC, two public subnets, an internet gateway, a public route table, two route table associations, two security groups, an ALB, a listener, a target group with `deregistration_delay = 120`, an ECS cluster with Container Insights enabled, a Fargate task definition, and an ECS service with desired count `4`.

My successful apply created 15 resources:

```text
aws_ecs_cluster.this: Creation complete after 11s [id=arn:aws:ecs:eu-central-1:<account-id>:cluster/ecs-livetaskcount-vs-run-981521-cluster]
aws_lb.this: Creation complete after 2m24s [id=arn:aws:elasticloadbalancing:eu-central-1:<account-id>:loadbalancer/app/hl-9044b320cc-alb/38e39499b36f7eb2]
aws_lb_listener.http: Creation complete after 1s [id=arn:aws:elasticloadbalancing:eu-central-1:<account-id>:listener/app/hl-9044b320cc-alb/38e39499b36f7eb2/796b60a22f360052]
aws_ecs_service.web: Creation complete after 1m31s [id=arn:aws:ecs:eu-central-1:<account-id>:service/ecs-livetaskcount-vs-run-981521-cluster/ecs-livetaskcount-vs-run-981521-svc]

Apply complete! Resources: 15 added, 0 changed, 0 destroyed.

Outputs:

alb_dns_name = "hl-9044b320cc-alb-412759208.eu-central-1.elb.amazonaws.com"
cluster_name = "ecs-livetaskcount-vs-run-981521-cluster"
service_name = "ecs-livetaskcount-vs-run-981521-svc"
```

### 3. Reproduce the mismatch

Set the same shell values the lab uses. `LAB` controls the dashboard and alarm names later. It must match the `lab_name` you used; the scripts default it to `lab` if `LAB_NAME` is not set.

```bash
REGION="${AWS_REGION:-eu-central-1}"
LAB="${LAB_NAME:-lab}"
CLUSTER="$(terraform output -raw cluster_name)"
SVC="$(terraform output -raw service_name)"
export AWS_DEFAULT_REGION="$REGION"
echo "== Cluster=$CLUSTER Service=$SVC"
```

The lab printed:

```text
== Cluster=ecs-livetaskcount-vs-run-981521-cluster Service=ecs-livetaskcount-vs-run-981521-svc
```

Wait until the service is stable, then check the starting desired and running counts:

```bash
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SVC"
aws ecs describe-services --cluster "$CLUSTER" --services "$SVC" --query 'services[0].[desiredCount,runningCount]' --output text
```

Output:

```text
Initial: 4	4  (desired running)
```

Wait 90 seconds so CloudWatch has baseline datapoints with four tasks:

```bash
sleep 90
```

Scale in from four desired tasks to one:

```bash
aws ecs update-service --cluster "$CLUSTER" --service "$SVC" --desired-count 1
```

Now poll the task state. The key detail is that the lab lists tasks with desired status `RUNNING` and `STOPPED`, describes the ARNs that still exist, and groups the non-STOPPED tasks by `lastStatus` with `jq`:

```bash
arns=$( { aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SVC" --desired-status RUNNING --query 'taskArns' --output text; aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SVC" --desired-status STOPPED --query 'taskArns' --output text; } 2>/dev/null | tr '\t' '\n' | grep -v '^$' | grep -v '^None$' | head -100)
aws ecs describe-tasks --cluster "$CLUSTER" --tasks $arns --query 'tasks[].lastStatus' --output json | jq -r 'map(select(. != "STOPPED")) | group_by(.) | map("\(.[0])=\(length)") | join(" ")'
aws ecs describe-services --cluster "$CLUSTER" --services "$SVC" --query 'services[0].[desiredCount,runningCount]' --output text
```

Repeat that check while the target group drains. My sampled output was:

```text
14:30:02 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:16 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:30 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:43 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:30:57 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:10 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:24 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:38 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:31:52 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:32:06 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:32:20 API: service desired/running=[1	1] tasks by lastStatus: DEACTIVATING=3 RUNNING=1
14:32:33 API: service desired/running=[1	1] tasks by lastStatus: RUNNING=1 STOPPING=3
Max tasks seen in DEACTIVATING (draining): 3
```

Discover the matching CloudWatch metrics and write the query file. This is the part that makes the later `get-metric-data` command runnable by hand:

```bash
LIVE="$(aws cloudwatch list-metrics --metric-name LiveTaskCount --output json 2>/dev/null | jq -c --arg s "$SVC" --arg c "$CLUSTER" '[.Metrics[] | select(any(.Dimensions[]; .Name=="ServiceName" and .Value==$s)) | select(all(.Dimensions[]; (.Name!="ClusterName") or .Value==$c))] | first // empty')"
RUN="$(aws cloudwatch list-metrics --namespace ECS/ContainerInsights --metric-name RunningTaskCount --dimensions Name=ClusterName,Value="$CLUSTER" Name=ServiceName,Value="$SVC" --output json 2>/dev/null | jq -c '.Metrics | first // empty')"
jq -n --argjson l "$LIVE" --argjson r "$RUN" '[{Id:"live",Label:"LiveTaskCount",ReturnData:true,MetricStat:{Metric:{Namespace:$l.Namespace,MetricName:$l.MetricName,Dimensions:$l.Dimensions},Period:60,Stat:"Average"}},{Id:"running",Label:"RunningTaskCount",ReturnData:true,MetricStat:{Metric:{Namespace:$r.Namespace,MetricName:$r.MetricName,Dimensions:$r.Dimensions},Period:60,Stat:"Average"}}]' > /tmp/hero-queries.json
```

Fetch the one-minute metric data. The lab used the last 30 minutes through two minutes in the future to allow for CloudWatch timestamp delay:

```bash
aws cloudwatch get-metric-data --metric-data-queries file:///tmp/hero-queries.json --start-time "$(date -u -d '-30 minutes' +%Y-%m-%dT%H:%M:%SZ)" --end-time "$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ)" --output json
```

The lab found the mismatch on the first CloudWatch attempt:

```text
-- attempt 1: 11 datapoints, minutes where Live > Running: 4
== LiveTaskCount namespace: AWS/ECS   RunningTaskCount namespace: ECS/ContainerInsights
== Per-minute averages (UTC)
2026-10-19T14:27:00+00:00  LiveTaskCount=1.0  RunningTaskCount=0.0
2026-10-19T14:28:00+00:00  LiveTaskCount=4.0  RunningTaskCount=3.0
2026-10-19T14:29:00+00:00  LiveTaskCount=4.0  RunningTaskCount=4.0
2026-10-19T14:30:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0
2026-10-19T14:31:00+00:00  LiveTaskCount=4.0  RunningTaskCount=1.0
Observation: while tasks were DEACTIVATING, LiveTaskCount stayed higher than RunningTaskCount - the two metrics disagree during scale-in.
```

### 4. Add the dashboard and alarm

This is the dashboard/alarm fix I verified on the metric data. It creates an alarm on `live - running`, but remember the caveat: with one evaluation period and threshold `0`, it can also fire during startup because the gap can be positive there too.

Build the alarm metric file:

```bash
jq -n --argjson l "$LIVE" --argjson r "$RUN" '[{Id:"draining",Label:"DrainingTasks (Live - Running)",Expression:"live - running",ReturnData:true},{Id:"live",Label:"LiveTaskCount",ReturnData:false,MetricStat:{Metric:{Namespace:$l.Namespace,MetricName:$l.MetricName,Dimensions:$l.Dimensions},Period:60,Stat:"Average"}},{Id:"running",Label:"RunningTaskCount",ReturnData:false,MetricStat:{Metric:{Namespace:$r.Namespace,MetricName:$r.MetricName,Dimensions:$r.Dimensions},Period:60,Stat:"Average"}}]' > /tmp/hero-alarm-metrics.json
```

Create the alarm:

```bash
aws cloudwatch put-metric-alarm --alarm-name "${LAB}-draining-tasks" --alarm-description "Tasks still alive but no longer counted by RunningTaskCount (DEACTIVATING)" --metrics file:///tmp/hero-alarm-metrics.json --comparison-operator GreaterThanThreshold --threshold 0 --evaluation-periods 1 --treat-missing-data notBreaching
```

Output from my run:

```text
alarm ecs-livetaskcount-vs-run-981521-draining-tasks created
```

Build the dashboard body and create the dashboard:

```bash
L_NS=$(echo "$LIVE" | jq -r .Namespace)
DASH=$(jq -n --arg region "$REGION" --arg ln "$L_NS" --arg c "$CLUSTER" --arg s "$SVC" '{widgets:[{type:"metric",x:0,y:0,width:24,height:8,properties:{region:$region,stat:"Average",period:60,title:"Live vs Running tasks",view:"timeSeries",metrics:[[$ln,"LiveTaskCount","ClusterName",$c,"ServiceName",$s],["ECS/ContainerInsights","RunningTaskCount","ClusterName",$c,"ServiceName",$s]]}}]}')
aws cloudwatch put-dashboard --dashboard-name "${LAB}-tasks" --dashboard-body "$DASH"
```

Output from my run:

```text
dashboard ecs-livetaskcount-vs-run-981521-tasks created
```

Build the verification query with `ReturnData:true` for the expression and source metrics:

```bash
jq -n --argjson l "$LIVE" --argjson r "$RUN" '[{Id:"draining",Label:"DrainingTasks (Live - Running)",Expression:"live - running",ReturnData:true},{Id:"live",Label:"LiveTaskCount",ReturnData:true,MetricStat:{Metric:{Namespace:$l.Namespace,MetricName:$l.MetricName,Dimensions:$l.Dimensions},Period:60,Stat:"Average"}},{Id:"running",Label:"RunningTaskCount",ReturnData:true,MetricStat:{Metric:{Namespace:$r.Namespace,MetricName:$r.MetricName,Dimensions:$r.Dimensions},Period:60,Stat:"Average"}}]' > /tmp/hero-verify.json
```

Verify the expression over the last hour and check that the alarm exists:

```bash
aws cloudwatch get-metric-data --metric-data-queries file:///tmp/hero-verify.json --start-time "$(date -u -d '-60 minutes' +%Y-%m-%dT%H:%M:%SZ)" --end-time "$(date -u -d '+2 minutes' +%Y-%m-%dT%H:%M:%SZ)" --output json
aws cloudwatch describe-alarms --alarm-names "${LAB}-draining-tasks" --alarm-types MetricAlarm CompositeAlarm --query 'length(MetricAlarms)' --output text
```

Verification output:

```text
Max DrainingTasks (Live - Running) over the last hour: 3.0
Alarm exists: 1
Lesson: LiveTaskCount includes DEACTIVATING (draining) tasks; RunningTaskCount does not. Use Live for capacity/cost views, and Live-Running to see drain.
```

That proves the expression produced `3.0` on the scale-in data and that the alarm was created. I did not wait for or capture the alarm entering `ALARM` state.

### 5. Clean up

The dashboard and alarm are outside Terraform, so delete them first. Use the same `LAB` value you used when creating them:

```bash
aws cloudwatch delete-alarms --alarm-names "${LAB}-draining-tasks"
aws cloudwatch delete-dashboards --dashboard-names "${LAB}-tasks"
```

Output:

```text
cleanup done
```

Then destroy the Terraform resources:

```bash
terraform destroy
```

My cleanup destroyed all 15 Terraform-managed resources:

```text
aws_ecs_service.web: Destruction complete after 4m1s
aws_lb.this: Destruction complete after 18s
aws_internet_gateway.this: Destruction complete after 4m25s
aws_vpc.this: Destruction complete after 1s

Destroy complete! Resources: 15 destroyed.
```

## What I didn't test

I tested one ECS Fargate service in `eu-central-1`, using public subnets, an ALB, nginx, and a target group deregistration delay of 120 seconds.

I did not test ECS on EC2, private subnets with NAT, blue/green deployments, Service Connect, multiple target groups, or other deregistration delay values. I also did not test using this metric math as an autoscaling policy input.

Most importantly, I did not prove that `LiveTaskCount - RunningTaskCount > 0` means only draining. In my data, the gap was positive in four minutes, and two of those minutes were startup minutes before the scale-in. If you alarm on this, use an evaluation period and wording that matches that reality.

## Cost and time

The reproduced-and-fixed run took 18.8 minutes in `eu-central-1`.

Terraform created 15 resources and later destroyed 15 resources. The main billable pieces were one Application Load Balancer and four Fargate tasks running briefly. The lab README describes the cost as a few cents: four Fargate tasks for roughly 30 minutes, plus one ALB.

## The takeaway

If you care about “tasks still alive,” do not rely only on `RunningTaskCount` during scale-in. Put `LiveTaskCount` beside it, and treat `LiveTaskCount - RunningTaskCount` as a useful gap signal that includes draining tasks, not as proof that every positive point is draining.

## Sources

- [AWS What's New: Amazon ECS service metrics](https://aws.amazon.com/about-aws/whats-new/2026/10/ecs-service-metrics/)

---
_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
