---
title: "ECS Fargate: 3 Tasks, 3 Splunk Metric Series, One OTel Sidecar"
description: "Add a pinned Splunk OpenTelemetry Collector sidecar to an ECS Fargate service, scale it from 1 to 3 tasks, and verify the task metric series through the Splunk API."
image: /assets/posts/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/cover.png
image_alt: "Terminal output showing Splunk found 0 series before the sidecar, then 3 tasks in Splunk matching the desired count of 3 after the rollout and scale-out."
tags: ["aws", "ecs", "opentelemetry", "splunk"]
channels: ["devto", "hashnode"]
lab: labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar
linkedin: "I built a small ECS Fargate lab to prove one thing:\n\nCan I add an OpenTelemetry sidecar, scale a service from 1 to 3 tasks, and see all 3 running task IDs as Splunk metric series?\n\nThe answer from my run was yes.\n\nThe service starts with one BusyBox app container, Container Insights disabled, and no telemetry sidecar.\n\nThen I add a pinned Splunk OpenTelemetry Collector sidecar and scale the service from 1 to 3 tasks.\n\nWhat the sidecar does:\n\n→ runs inside each Fargate task\n→ reads the ECS task metadata endpoint\n→ sends metrics tagged with ecs.cluster.name, ecs.service.name, and aws.ecs.task.id\n→ lets the proof query use the Splunk metric time series API\n\nThe numbers from my run:\n\nBefore: series found 0\nSidecar rollout: 156 seconds for 1 task\nScale-out: 1 to 3 running tasks in 35 seconds\nFinal proof: desired 3, running 3, task IDs in Splunk 3\n\nThe post has the commands, the Collector config, and the output I checked:\n{LINKS}\n\nHow are you collecting per-task metrics from ECS Fargate today?\n\n#aws #ecs #opentelemetry #devops"
replies: []
---
![Terminal output showing Splunk found 0 series before the sidecar, then 3 tasks in Splunk matching the desired count of 3 after the rollout and scale-out.](https://abhimanyu9988.github.io/assets/posts/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/cover.png)

By the end of this lab, an ECS Fargate service scaled to 3 tasks has all 3 task IDs visible as metric series in Splunk Observability. You will add a pinned Splunk OpenTelemetry Collector sidecar, roll the service, scale it out, and verify the series through the Splunk API.

Fargate removes the host from your life. That is useful, but it also means there is no EC2 node where you can install a host agent and scrape every task from the outside.

The proof in this lab is deliberately narrow: before the sidecar, Splunk returned `series found: 0`; after the rollout and scale-out, Splunk returned the same 3 running ECS task IDs under the lab cluster name, using the `aws.ecs.task.id` dimension. I reproduced this in a disposable AWS account and kept the raw output.

Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** per-task ECS Fargate metric series in Splunk Observability, tagged with cluster, service, and task ID.
> - **The trick:** run the Splunk OpenTelemetry Collector as a sidecar and enable the `awsecscontainermetrics` receiver.
> - **The proof:** before the sidecar, Splunk returned `series found: 0`; after rollout and scale-out, ECS had desired `3`, running `3`, and Splunk had all 3 running task IDs.
> - **Time and cost:** my run took 12.9 minutes. The lab uses public-IP Fargate tasks, CloudWatch Logs, and Splunk ingest while it runs, with no NAT gateway.

## Why the ECS console is not enough for per-task series in Splunk

The starting point is one ECS service with one BusyBox app container. Container Insights is disabled, and there is no telemetry sidecar in the task definition.

`reproduce.sh` shows that state before we add anything:

```text
== Cluster settings (Container Insights)
[
...
]
== Containers in the task definition (names and images only)
{"name":"app","image":"public.ecr.aws/docker/library/busybox:1.36"}
== Splunk Observability: metric time series with ecs.cluster.name:watch-ecs-fargate-task-c-986662
series found: 0
```

ECS knows the task is running. Splunk has no metric series for this lab cluster yet.

## The trick: put the Collector beside the app, not on a host

The Splunk OpenTelemetry Collector runs as a second container in the same Fargate task. The receiver is `awsecscontainermetrics`, which reads the ECS task metadata endpoint that Fargate exposes to containers.

The lab pins the sidecar image to `quay.io/signalfx/splunk-otel-collector:0.163.1`. The Collector config is passed through `SPLUNK_CONFIG_YAML`, so there is no image build and no config bucket. `fix.sh` fills in the cluster and service names at run time.

```yaml
receivers:
  awsecscontainermetrics:
    collection_interval: 20s
...
exporters:
  signalfx:
    access_token: "${env:SPLUNK_ACCESS_TOKEN}"
    realm: "${env:SPLUNK_REALM}"
```

For your own service, change the resource attributes and use a real secret source for the ingest token. This lab uses environment variables so the moving parts stay visible.

## Build it yourself in 3 steps

### Step 1: Create the app-only Fargate service (Terraform apply: 31 seconds)

This creates the VPC, two public subnets, an ECS cluster, a task definition with only the app container, and a service with one task.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar

export LAB_NAME=ecs-otel-lab
export TF_VAR_lab_name=$LAB_NAME

terraform init && terraform apply
```

In my run, Terraform created 14 resources. The apply finished in 31 seconds, and the outputs included the cluster name, service name, task family, log group, and region.

### Step 2: Prove the before-state (44 seconds)

Run the reproduction script before adding the sidecar. It proves you are starting from one app container and zero Splunk series for this cluster.

```bash
export SPLUNK_REALM=us1
export SPLUNK_ACCESS_TOKEN=...   # ingest token used by the sidecar
export SPLUNK_API_TOKEN=...      # API token used by the proof query

./reproduce.sh
```

The important line is `series found: 0`. That keeps the later result honest.

![reproduce.sh: one app container, zero Splunk series](https://abhimanyu9988.github.io/assets/posts/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/step-1.png)

### Step 3: Add the sidecar, scale to 3, and verify Splunk (235 seconds)

Run `./fix.sh` once. It registers a new task definition revision with the Collector, rolls the service, waits for the first task's series, scales the service from 1 to 3, and checks Splunk again.

```text
== Sidecar image: quay.io/signalfx/splunk-otel-collector:0.163.1
== Registered arn:aws:ecs:eu-central-1:<account-id>:task-definition/watch-ecs-fargate-task-c-986662-app:2
{"name":"app","image":"public.ecr.aws/docker/library/busybox:1.36","essential":true}
{"name":"otel-collector","image":"quay.io/signalfx/splunk-otel-collector:0.163.1","essential":false}
== Rollout with sidecar finished in 156s (1 task)
== Waiting for the first task's series in Splunk
== 1 task visible in Splunk
== Scaled 1 -> 3 running tasks in 35s
== Waiting for all 3 tasks in Splunk
...
```

In my run, ECS reached 3 running tasks before Splunk had all matching task series, so the script waited and queried again.

![fix.sh: rollout, scale-out, Splunk still catching up](https://abhimanyu9988.github.io/assets/posts/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/step-2.png)

## The proof: the 3 running ECS task IDs matched the 3 Splunk task IDs

Here is the lab as a small scoreboard.

| Stage | ECS state | Splunk state |
|---|---:|---:|
| Before sidecar | desired 1, running 1 | series found 0 |
| After sidecar rollout | 1 task running | 1 task visible |
| After scale-out | desired 3, running 3 | 3 running task IDs found |

The final proof compared ECS running task IDs with task IDs returned by the Splunk metric time series API for `ecs.cluster.name`.

```text
Service: desired=3 running=3
ECS running task ids:
  9ba026c56d4a46f3b6b89f579b4e0a4d
  c201b7ffc29740f28492441b5bdf227e
  f0cdf4770e7f4670922e531c939e526d
Splunk task ids for ecs.cluster.name=watch-ecs-fargate-task-c-986662:
  9ba026c56d4a46f3b6b89f579b4e0a4d
  c201b7ffc29740f28492441b5bdf227e
  f0cdf4770e7f4670922e531c939e526d
```

The script also printed recent CPU and memory values returned from the API:

```text
...
ecs.task.cpu.utilized per task (last 1-min value):
  c201b7ffc29740f28492441b5bdf227e  last=0.7770959435996764
ecs.task.memory.utilized per task (last 1-min value):
  c201b7ffc29740f28492441b5bdf227e  last=35
Tasks in Splunk: 3, desired count: 3
```

The value output depends on the time window and which task has a recent datapoint at query time. The stronger proof is the ID match: the three running ECS task IDs were also present in Splunk for the cluster.

![ECS running task IDs vs Splunk task IDs](https://abhimanyu9988.github.io/assets/posts/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/step-3.png)

## Use it for your own ECS service

- **Move the token to Secrets Manager or SSM.** The lab uses plain environment variables. For a real service, use the ECS `secrets` field.
- **Keep the sidecar pinned.** This lab used `quay.io/signalfx/splunk-otel-collector:0.163.1`, which makes the result repeatable.
- **Add the tags your team filters by.** The lab upserts `ecs.cluster.name`, `ecs.service.name`, and `deployment.environment`. Add team, app, or release labels if those are how you debug.

## Clean up (the cleanup script took 14 seconds)

Run the cleanup script before destroying Terraform. It scales the service down, stops tasks, and deregisters task definition revisions created by the scripts.

```bash
./cleanup.sh
terraform destroy
```

## What I didn't test

- I did not test ECS service auto scaling policies; this lab uses a direct desired count change from 1 to 3.
- I did not test private subnets or NAT gateway paths. The lab uses public subnets with assigned public IPs.
- I did not test Secrets Manager or SSM for the Splunk token.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/fix.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar/evidence): the raw output of my run

## Sources

- [Lab code and raw output](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/ecs-fargate-3-tasks-3-splunk-metric-series-one-otel-sidecar)

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.
---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
