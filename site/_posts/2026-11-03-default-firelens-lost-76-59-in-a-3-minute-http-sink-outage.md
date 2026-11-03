---
title: "Default FireLens Lost 76.59% in a 3-Minute HTTP Sink Outage"
description: "I closed an HTTP log sink for 3 minutes on ECS Fargate and counted source versus destination records. The default FireLens path lost 76.59%; memory and filesystem buffered configs lost 0.0% in this lab."
image: /assets/posts/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/cover.png
image_alt: "Line chart of the default FireLens run: the generator sequence number keeps climbing during the 3-minute outage while the sink's record count stays flat at 9180."
tags: ["aws", "ecs", "fluentbit", "devops"]
channels: ["devto", "hashnode"]
lab: labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage
linkedin: "An ECS task with a dead log destination still shows RUNNING.\nMine dropped 76.59% of its logs in a 3-minute outage.\n\nI closed the HTTP sink behind FireLens for 3 minutes while a Fargate task wrote 200 lines/second.\n\nThe app stayed up. Fluent Bit quietly logged \"chunk cannot be retried\" and moved on.\n\nHow I fixed it:\n→ Keep the same FireLens socket, change only the buffering and retries\n→ Retry_Limit 60 on the output\n→ Either Mem_Buf_Limit 64MB or filesystem buffering with a 512M cap\n\nThe numbers from my run:\n→ Default config: lost 36,200 of 47,266 records (76.59%)\n→ Memory buffering: lost 0 of 49,306\n→ Filesystem buffering: lost 0 of 48,641\n→ Both buffered configs drained the backlog in about 19 seconds\n\nThere is a cost. With memory buffering, stdout writes peaked at 73.39 ms. With filesystem buffering, 1.82 ms.\n\nWhy it matters at work: nothing at the ECS level tells you logs are going nowhere, so you find out during the incident.\n\nI built and tested this in a throwaway AWS account, and the post has every step, the configs and the raw output:\n{LINKS}\n\nDo you know what your FireLens setup does when the log destination is down, or have you never tested it?\n\n#AWS #ECS #FluentBit #Observability"
replies: []
---
![Line chart of the default FireLens run: the generator sequence number keeps climbing during the 3-minute outage while the sink's record count stays flat at 9180.](https://abhimanyu9988.github.io/assets/posts/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/cover.png)

When the HTTP endpoint behind ECS FireLens stops accepting logs, the default AWS for Fluent Bit config drops most of what your app writes. This lab measures how much. You close the sink for 3 minutes, reopen it, and compare source and destination counts for the default config, memory buffering and filesystem buffering.

At 200 lines/second, the default FireLens config lost **76.59%** of generated records after a 3-minute sink outage. The two buffered configs lost **0.0%** and drained the backlog in about 19 seconds.

Before I write about a problem, I reproduce it myself in a throwaway AWS account and keep the raw output. This post is that output, plus the steps to rebuild it.

> **TL;DR**
> - **What you get:** an ECS Fargate task writing 200 stdout lines/second through FireLens to an HTTP sink you can stop and restart.
> - **The trick:** compare the default config with `Retry_Limit 60` plus either `Mem_Buf_Limit 64MB` or filesystem buffering with `storage.total_limit_size 512M`.
> - **The proof:** default lost 36,200 of 47,266 records, or 76.59%; memory buffering lost 0 of 49,306; filesystem buffering lost 0 of 48,641.
> - **Time and cost:** my full run took 35.8 minutes. The lab README estimates well under 1 USD: one `t3.medium` for about an hour and a few Fargate task-minutes.

## Why the normal FireLens path can look alive while chunks are being dropped

Your app writes stdout, the `awsfirelens` log driver sends it to the FireLens router, and AWS for Fluent Bit sends it to your HTTP destination. The [Fluent Bit backpressure docs](https://docs.fluentbit.io/manual/administration/backpressure) explain memory limits, pause behavior and retry behavior setting by setting. What they don't give you is the combined Fargate result: how many sequence numbers make it through when the destination is actually down.

In the default run, the sink listener was closed. The probe got connection refused, the app task stayed running, and Fluent Bit logged dropped chunks:

```text
probe of ingest port during outage: curl exit code 7 (7=connection refused, 28=timeout; 0 would mean still up)
--- log_router (Fluent Bit) log evidence [default] ---
version line: Fluent Bit v4.1.1
'failed to flush chunk' (retry scheduled) lines: 189 (cap 1000)
sample: [2026/11/03 16:48:42.352391753] [ warn] [engine] failed to flush chunk '1-1793724521.349234698.flb', retry in 11 seconds: task_id=0, input=forward.1 > output=http.1 (out_id=1)
'chunk cannot be retried' (chunk DROPPED) lines: 181 (cap 1000)
sample: [2026/11/03 16:48:51.357642791] [error] [engine] chunk '1-1793724522.349284824.flb' cannot be retried: task_id=1, input=forward.1 > output=http.1
```

The task is healthy and the logs are going nowhere. Nothing at the ECS level tells you.

![Fluent Bit log evidence: chunks dropped while task stays RUNNING](https://abhimanyu9988.github.io/assets/posts/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/step-2.png)

## The trick: keep the FireLens socket, change buffering and retries

The lab runs three task definitions against the same HTTP sink: `default`, `mem`, and `fs`. The custom configs are not FireLens-generated configs. The router starts Fluent Bit with a custom config, but the input is still `/var/run/fluent.sock`, the forward socket that the `awsfirelens` log driver writes to.

The important memory-buffered lines are:

```text
[SERVICE]
    scheduler.base 2
    scheduler.cap  20

[INPUT]
    unix_path     /var/run/fluent.sock
    Mem_Buf_Limit 64MB

[OUTPUT]
    Retry_Limit 60
```

The important filesystem-buffered lines are:

```text
[SERVICE]
    storage.path              /var/fb-storage
    storage.backlog.mem_limit 64M

[INPUT]
    unix_path    /var/run/fluent.sock
    storage.type filesystem

[OUTPUT]
    Retry_Limit              60
    storage.total_limit_size 512M
```

In a real service, bake this config into an image or deliver it with the ECS FireLens config-file option from the [FireLens developer guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_firelens.html).

## Build it yourself in 3 steps

### Step 1: Create the Fargate lab and sink (Terraform apply took 43s in my run)

This creates the VPC, one public subnet, the EC2 sink, an ECS cluster, a CloudWatch log group, and three task definitions.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage
export LAB_NAME=fb-outage
export TF_VAR_lab_name=fb-outage
terraform init && terraform apply
```

Your resource names follow the `lab_name` you set. In my run, Terraform created 16 resources and used AWS for Fluent Bit 3.0.0:

```text
Apply complete! Resources: 16 added, 0 changed, 0 destroyed.

Outputs:
...
fluentbit_image = "public.ecr.aws/aws-observability/aws-for-fluent-bit:3.0.0"
```

### Step 2: Run the default config through the outage (357s in my run)

This starts one Fargate task, closes port 8080 for the outage, reopens it, and prints the source-versus-destination report.

```bash
./reproduce.sh
```

The generator keeps writing during the outage, and the task stays running:

```text
steady [default] sink n_records=8980 generator seq=9048
== OUTAGE: closing the sink's ingest listener (180s) at 16:48:41
outage +180s [default] sink n_records=9180 generator seq=46862 task=RUNNING
== RECOVERY: reopening ingest listener at 16:51:51
...
default config: generated=47266 delivered=11066 lost=36200 (76.59%) | generated during outage=38017, arrived after recovery=1886, lost in outage window=36131 | lost before outage=69
With default settings, 76.59% of the generated records never reached the destination after a 3-minute outage.
```

![default config after the 3-minute outage](https://abhimanyu9988.github.io/assets/posts/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/step-1.png)

### Step 3: Run memory and filesystem buffering side by side

This runs the two buffered task definitions through one shared outage, then compares both with the default result saved by `reproduce.sh`.

```bash
./fix.sh
```

Both buffered configs stayed at zero loss in this run:

```text
config    lost_pct  after_recov  drain_s  mem_base  mem_peak max_write_ms
mem            0.0        39844     19.0       4.9      16.6        73.39
fs             0.0        39785     19.2       4.9      18.3         1.82

mem: generated=49306 delivered=49306 lost=0 (0.0%) | generated during outage=39844, arrived after recovery=39844 | drain 19.0s | router mem 4.9 -> 16.6 MiB | max stdout write during outage 73.39 ms
fs: generated=48641 delivered=48641 lost=0 (0.0%) | generated during outage=39794, arrived after recovery=39785 | drain 19.2s | router mem 4.9 -> 18.3 MiB | max stdout write during outage 1.82 ms
```

![mem and fs buffering: zero loss after the same outage](https://abhimanyu9988.github.io/assets/posts/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/step-3.png)

## The proof: default dropped records; both buffered configs drained cleanly

The lab script reports these numbers from the sink's sequence-number report and the ECS task metadata stats. I did not re-run the counts outside the script. Treat them as one sample at this rate and line size, not as a benchmark.

| Config | Generated | Delivered | Lost | Lost % | Outage records delivered after recovery | Drain time | Router memory baseline → peak | Max stdout write during outage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| default | 47,266 | 11,066 | 36,200 | 76.59% | 1,886 | 10.2s | 21.1 → 22.6 MiB | 9.6 ms |
| mem | 49,306 | 49,306 | 0 | 0.0% | 39,844 | 19.0s | 4.9 → 16.6 MiB | 73.39 ms |
| fs | 48,641 | 48,641 | 0 | 0.0% | 39,785 | 19.2s | 4.9 → 18.3 MiB | 1.82 ms |

Two caveats. The default's 36,200 lost records include 69 lost before the outage window started. And the default run and the two buffered configs ran as separate experiments, with the same 200 lines/second rate and the same 3-minute outage.

There is a cost. The memory config saved the logs, but stdout write latency peaked at 73.39 ms. The filesystem config used a bit more router memory than the memory config in this run (18.3 vs 16.6 MiB peak), but app-side writes barely moved.

Filesystem buffering is covered in the [Fluent Bit buffering and storage docs](https://docs.fluentbit.io/manual/administration/buffering-and-storage). On Fargate it is still task ephemeral storage, so it can ride out an output outage but won't survive a task stop. I didn't test the task-stop case.

## Use it for your own FireLens service

- **Size the buffer from your outage math.** This lab used 200 lines/second and about 170-byte lines. If your service emits more, scale the buffer and task memory before copying the numbers.
- **Cap retry backoff deliberately.** The lab README's reason for `scheduler.cap 20` is that stale chunks can sit behind exponential retry backoff after the destination returns. I did not measure an uncapped run in this pass.
- **Pick your failure mode.** The [ECS FireLens Docker buffer limit page](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/firelens-docker-buffer-limit.html) covers the log driver buffer between the container and FireLens. Whatever you set, you are trading log loss against memory, ephemeral storage and app-side latency.

## Clean up (the EC2 sink and Fargate resources cost money while they run)

Stop any tasks the scripts started, then destroy the Terraform resources.

```bash
./cleanup.sh && terraform destroy
```

In my run, cleanup found no running or pending tasks before destroy, and Terraform destroyed 16 resources.

## What I didn't test

- Higher rates, larger log lines, or longer outages.
- A task stop during the outage. Filesystem buffering here is not durable beyond the Fargate task.
- HTTP 5xx or slow responses. This lab closed the listener, so the failure was connection refused.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/cleanup.sh)
- [`embedded_scripts.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/embedded_scripts.tf)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/fix.sh)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage/evidence): the raw output of my run

## Sources

- [Amazon ECS FireLens developer guide](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using_firelens.html)
- [AWS for Fluent Bit 3.0.0 announcement](https://aws.amazon.com/about-aws/whats-new/2025/10/aws-fluent-bit-3-0-0-based-4-1-1)
- [Fluent Bit backpressure manual](https://docs.fluentbit.io/manual/administration/backpressure)
- [Fluent Bit buffering and storage manual](https://docs.fluentbit.io/manual/administration/buffering-and-storage)
- [Chronosphere: avoiding data loss and backpressure problems with Fluent Bit](https://chronosphere.io/learn/avoiding-data-loss-and-backpressure-problems-with-fluent-bit/)
- [Amazon ECS FireLens Docker buffer limit](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/firelens-docker-buffer-limit.html)
- [Lab code and raw output](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/default-firelens-lost-76-59-in-a-3-minute-http-sink-outage)

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
