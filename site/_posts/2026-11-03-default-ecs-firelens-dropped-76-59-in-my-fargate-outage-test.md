---
title: "Default ECS FireLens Dropped 76.59% in My Fargate Outage Test"
description: "I stopped an HTTP log sink for 3 minutes behind an ECS Fargate FireLens task. The default path lost 76.59% of generated records; two custom Fluent Bit configs delivered all records in this single run."
image: /assets/posts/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/cover.png
image_alt: "Line chart of the default FireLens run during a 3-minute outage: the sink's n_records stays flat at 9180 while the generator seq climbs from 15284 to 46862 and the task stays RUNNING."
tags: ["aws", "ecs", "fargate", "devops"]
channels: ["devto", "hashnode"]
lab: labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test
linkedin: "FireLens looked healthy during my outage test.\nThe default path still lost 76.59% of generated records.\n\nI built a small ECS Fargate lab for a measured question:\nwhat happens when the HTTP log destination stops accepting logs?\n\n→ app writes 200 lines/s to stdout\n→ awsfirelens sends them through AWS for Fluent Bit\n→ an HTTP sink closes its ingest listener for about 3 minutes\n→ the sink counts records by sequence number after recovery\n\nThe proof from my run:\ndefault FireLens config: 76.59% lost\ncustom memory config: 0.0% lost, 19.0s drain\ncustom filesystem config: 0.0% lost, 19.2s drain\n\nOne caveat: the memory run saw one 73.39 ms max stdout write during the outage. I did not repeat the run, so I am not treating that as a repeatable latency cost.\n\nThis matters because \"logs are configured\" is not the same as \"logs survive a destination outage.\"\n\nI built and tested it in a throwaway AWS account, and the post has every step:\n{LINKS}\n\nDo you test your FireLens path with the destination actually down, or only check that logs flow when it is healthy?\n\n#aws #ecs #fargate #devops"
replies: []
---
![Line chart of the default FireLens run during a 3-minute outage: the sink's n_records stays flat at 9180 while the generator seq climbs from 15284 to 46862 and the task stays RUNNING.](https://abhimanyu9988.github.io/assets/posts/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/cover.png)

During a 3-minute destination outage, the default FireLens config lost 76.59% of my log records. The task stayed RUNNING the whole time.

The setup: an ECS Fargate task writes 200 lines/s, I stop the HTTP sink for 3 minutes, and I count records by sequence number. Then I harden Fluent Bit with retries, memory buffering and filesystem buffering, and run the same outage again.

In my run, two custom Fluent Bit configs lost **0.0%** in the same outage shape. Backlog drain took about 19 seconds after recovery.

My week is mostly spent on why something that looks correctly configured doesn't work in a real cluster. A healthy-looking task that drops logs fits that. I built this in a throwaway AWS account and kept the raw output.

> **TL;DR**
> - **What you get:** an ECS Fargate FireLens lab that sends 200 lines/s to an HTTP sink, closes the sink for about 3 minutes, then counts generated, delivered and missing records by sequence number.
> - **Method:** run the default FireLens-generated config once. Then run custom Fluent Bit configs that change retries, scheduler backoff, input buffering and filesystem storage together.
> - **Result:** default lost 76.59%; memory config lost 0.0%; filesystem config lost 0.0%. The hardened runs drained in 19.0s and 19.2s.
> - **Time and cost:** my full run took 35.8 minutes. Terraform apply took 43s. The README estimates well under 1 USD: one t3.medium for about an hour and a few Fargate task-minutes.

## Why a healthy task can still lose its logs

The usual mental model is simple. The app writes stdout, FireLens forwards it, the destination receives it. That model says nothing about the outage window, when the output can't flush.

In the default run, the sink listener was really closed. The task kept running and the generator kept writing. Fluent Bit started dropping chunks once retries were exhausted.

```text
== OUTAGE: closing the sink's ingest listener (180s) at 16:48:41
probe of ingest port during outage: curl exit code 7 (7=connection refused, 28=timeout; 0 would mean still up)
outage +180s [default] sink n_records=9180 generator seq=46862 task=RUNNING
--- log_router (Fluent Bit) log evidence [default] ---
version line: Fluent Bit v4.1.1
'failed to flush chunk' (retry scheduled) lines: 189 (cap 1000)
'chunk cannot be retried' (chunk DROPPED) lines: 181 (cap 1000)
sample: [2026/11/03 16:48:51.357642791] [error] [engine] chunk '1-1793724522.349284824.flb' cannot be retried: task_id=1, input=forward.1 > output=http.1
```

The [Fluent Bit backpressure docs](https://docs.fluentbit.io/manual/administration/backpressure) explain `mem_buf_limit` and pause behaviour in general terms. The [buffering and storage docs](https://docs.fluentbit.io/manual/administration/buffering-and-storage) cover memory and filesystem buffering. Neither measures the whole FireLens path on Fargate: stdout, `awsfirelens`, AWS for Fluent Bit, task memory and task ephemeral storage. This lab does.

## What the custom configs change

The generator writes `seq=N run=...` lines at 200 lines/s. The sink counts unique sequence numbers. Loss is never inferred from byte counts or timestamps.

The default run uses the FireLens-generated config. The memory and filesystem runs use custom Fluent Bit configs written before Fluent Bit starts. The input is still the FireLens forward socket, but these configs are not FireLens-generated.

I changed these settings together and did not test them one by one:

```text
# memory config changes
scheduler.base 2
scheduler.cap  20
Mem_Buf_Limit 64MB
Retry_Limit 60

# filesystem config changes
storage.path /var/fb-storage
storage.type filesystem
storage.total_limit_size 512M
Retry_Limit 60
```

So the result shows the custom configs worked in this run. It does not show that `Mem_Buf_Limit 64MB` alone did it. Router memory peaked at 16.6 MiB for `mem` and 18.3 MiB for `fs`, so the 64MB input limit was never shown to be exercised.

## Build it yourself in 3 steps

### Step 1: Create the disposable ECS lab (about 1 minute)

The Terraform creates one VPC, one public subnet, one EC2 HTTP sink, an ECS cluster and three task definitions. For your own run, declare the variables the hosted runner supplied.

```hcl
variable "region" { type = string }
variable "lab_name" { type = string }
variable "permissions_boundary" {
  type    = string
  default = null
}
```

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test
export LAB_NAME=fb-outage AWS_REGION=eu-central-1
terraform init && terraform apply -var region=eu-central-1 -var lab_name="$LAB_NAME" -var permissions_boundary=null
```

In my run, apply created 16 resources.

```text
Apply complete! Resources: 16 added, 0 changed, 0 destroyed.

Outputs:
cluster_name = "where-fluent-bit-logs-go-767398"
fluentbit_image = "public.ecr.aws/aws-observability/aws-for-fluent-bit:3.0.0"
log_group = "/where-fluent-bit-logs-go-767398/log-router"
task_def_default = "arn:aws:ecs:eu-central-1:<account-id>:task-definition/where-fluent-bit-logs-go-767398-default:1"
```

### Step 2: Reproduce default loss (about 6 minutes)

This starts the default task and waits for steady state. It then closes the sink listener, reopens it, and waits until the destination count has been unchanged for 60 seconds.

```bash
./reproduce.sh
```

```text
config    lost_pct  after_recov  drain_s  mem_base  mem_peak max_write_ms
default      76.59         1886     10.2      21.1      22.6          9.6

default config: generated=47266 delivered=11066 lost=36200 (76.59%) | generated during outage=38017, arrived after recovery=1886, lost in outage window=36131 | lost before outage=69
With default settings, 76.59% of the generated records never reached the destination after a 3-minute outage.
```

![default run: sink frozen at 9180 records while generator keeps writing](https://abhimanyu9988.github.io/assets/posts/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/step-1.png)

![default sink report: 76.59% lost](https://abhimanyu9988.github.io/assets/posts/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/step-2.png)

### Step 3: Run the two custom configs (under 14 minutes)

Run this after `reproduce.sh`. It reads the default loss saved in the lab directory and compares both hardened runs against it.

```bash
./fix.sh
```

```text
config    lost_pct  after_recov  drain_s  mem_base  mem_peak max_write_ms
mem            0.0        39844     19.0       4.9      16.6        73.39
fs             0.0        39785     19.2       4.9      18.3         1.82

mem: generated=49306 delivered=49306 lost=0 (0.0%) | generated during outage=39844, arrived after recovery=39844 | drain 19.0s | router mem 4.9 -> 16.6 MiB | max stdout write during outage 73.39 ms
fs: generated=48641 delivered=48641 lost=0 (0.0%) | generated during outage=39794, arrived after recovery=39785 | drain 19.2s | router mem 4.9 -> 18.3 MiB | max stdout write during outage 1.82 ms
```

![fix.sh: mem and fs configs lose nothing](https://abhimanyu9988.github.io/assets/posts/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/step-3.png)

## Results: 76.59 percentage points less loss

| Config | Lost records | Arrived after recovery | Drain time | Router memory | Max stdout write during outage |
|---|---:|---:|---:|---:|---:|
| default | 76.59% | 1,886 | 10.2s | 21.1 -> 22.6 MiB | 9.6 ms |
| mem | 0.0% | 39,844 | 19.0s | 4.9 -> 16.6 MiB | 73.39 ms |
| fs | 0.0% | 39,785 | 19.2s | 4.9 -> 18.3 MiB | 1.82 ms |

Against each custom config, the default lost 76.59 percentage points more records in this sample. The hardened configs grew about 12 to 13 MiB over 3 minutes at 200 lines/s.

The 73.39 ms is one maximum from one memory-buffer run. I didn't repeat it, so I wouldn't read it as a repeatable latency cost, or as proof of backpressure on its own.

The lab scripts reported these numbers. Saved sink reports and router log queries corroborate them. I did not re-run the calculations outside the scripts. This is one sample with roughly 170-byte log lines, not a benchmark.

## Sizing your own FireLens outage budget

- **Start with your log rate and outage target.** This lab generated about 38k to 40k records during the outage window. If your service writes more, size task memory and buffer space from your own record size and rate.
- **Pick the failure mode you can accept.** The Fluent Bit docs say buffering and pause behaviour move pressure upstream. The ECS docs describe the [FireLens Docker buffer limit](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/firelens-docker-buffer-limit.html) between the app container and FireLens. Depending on mode and buffer size, you may trade dropped logs for app-side blocking.
- **Don't treat Fargate filesystem buffering as durable storage.** It uses task ephemeral storage. It survived this output outage, but I didn't test task stop or replacement. If you need a stronger boundary, consider a durable intermediate such as Firehose or Kinesis before the HTTP destination.

## Clean up before the EC2 sink keeps billing

The scripts start ECS tasks outside Terraform, so stop them first. Then destroy the infrastructure.

```bash
./cleanup.sh && terraform destroy -var region=eu-central-1 -var lab_name="$LAB_NAME" -var permissions_boundary=null
```

My cleanup found no running tasks, and Terraform later reported `Destroy complete! Resources: 16 destroyed.`

## What I didn't test

- I did not test a real Splunk HEC endpoint. The sink is a small HTTP server that accepts Fluent Bit HTTP output.
- I did not test task stop or task replacement during the outage.
- I did not vary line size, log rate, `log-driver-buffer-limit`, task memory or Fargate ephemeral storage size.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/cleanup.sh)
- [`embedded_scripts.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/embedded_scripts.tf)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/fix.sh)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/default-ecs-firelens-dropped-76-59-in-my-fargate-outage-test/evidence): the raw output of my run

## Sources

- [Using FireLens for Amazon ECS logs](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_firelens.html)
- [Backpressure in the Fluent Bit manual](https://docs.fluentbit.io/manual/administration/backpressure)
- [Buffering and storage in the Fluent Bit manual](https://docs.fluentbit.io/manual/administration/buffering-and-storage)
- [FireLens Docker buffer limit in the Amazon ECS developer guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/firelens-docker-buffer-limit.html)
- [Avoiding data loss and backpressure problems with Fluent Bit](https://chronosphere.io/learn/avoiding-data-loss-and-backpressure-problems-with-fluent-bit/)
- [AWS for Fluent Bit 3.0.0 based on 4.1.1 announcement](https://aws.amazon.com/about-aws/whats-new/2025/10/aws-fluent-bit-3-0-0-based-4-1-1)

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
