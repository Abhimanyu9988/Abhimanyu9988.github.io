---
title: "OTel Java Agent 3.0 Preview: SQS Receive Spans Now Stay in One Trace in Splunk"
description: "With receive telemetry on, today's OTel Java agent put 0 of 100 SQS consumers in the producer's trace. The 3.0 preview flag kept 100 of 100, linked every process span, and renamed the operations Splunk APM shows."
image: /assets/posts/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/cover.png
image_alt: "Cover: OTel Java Agent 3.0 Preview: SQS Receive Spans Now Stay in One Trace in Splunk"
tags: ["opentelemetry", "splunk", "aws", "java"]
channels: []
lab: labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in
linkedin: "SQS receive telemetry today gives each consumer its own trace.\nThe OpenTelemetry Java agent 3.0 preview keeps it in one.\n\nSame two Spring Boot services, same SQS queue, real EKS:\n→ One environment variable: OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true\n→ Same jar, same image, fresh pods for every run\n\nWhat I measured:\nToday's agent, receive telemetry on: 0 of 100 messages in the producer's trace\n3.0 preview, receive telemetry on: 100 of 100\nEvery process span linked back to its producer: 400 of 400, against 0 today\n\nThe operation names in Splunk APM change too:\n<queue> publish → send <queue>\n<queue> process → process <queue>\nSqs.DeleteMessageBatch → delete <queue>\n\nSo filters and charts built on the old names are worth a look before 3.0 lands. Tested on agent 2.33.1; the final 3.0 can still change.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\n{LINKS}\n\nDo your Splunk APM filters use the old messaging names?\n\n#OpenTelemetry #Splunk #AWS #Java"
replies: []
---
![Cover: OTel Java Agent 3.0 Preview: SQS Receive Spans Now Stay in One Trace in Splunk](https://abhimanyu9988.github.io/assets/posts/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/cover.png)

If you turned on receive telemetry to see SQS polling in your traces, today each consumer lands in a trace of its own. In my lab that was **0 of 100 messages in the producer's trace**. With the OpenTelemetry Java agent 3.0 preview on the same services and queue, it was **100 of 100**: one trace from the HTTP request to the consumer, receive spans included.

I tested **agent 2.33.1 with `OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true`**, not the final 3.0, and the project says preview behaviour can still change. The flag is one environment variable on the jar you already run, so you can check your own services the same way before 3.0 is the default.

> **TL;DR**
> - On the defaults, both agents kept every message in one trace: 400 of 400 today, 400 of 400 with the preview.
> - With receive telemetry on, today's agent put each consumer in its own linked trace (0 of 100 shared). The preview kept all 100 in the producer's trace.
> - With the preview, every process span also carries a link to its producer, receive telemetry or not: 400 of 400, against 0 of 400 today.
> - Span names flip to operation first: `<queue> publish` becomes `send <queue>`, `<queue> process` becomes `process <queue>`, and `Sqs.DeleteMessageBatch` becomes `delete <queue>`. Those are the operation names Splunk APM holds, so filters and charts on them need the new names.

## Why I looked at SQS

The OpenTelemetry project [asks Java agent users to preview 3.0 now](https://opentelemetry.io/blog/2026/java-agent-3.0-preview/). Its messaging examples are Kafka and Pulsar: new operation-first span names, and new rules for how a consumer's process span relates to the producer. SQS isn't among the examples, and maintainers have been [discussing how receive telemetry and trace structure fit together](https://github.com/open-telemetry/opentelemetry-java-instrumentation/issues/19414) under the new conventions.

My [earlier SQS article](https://showmerit.com/abhimanyu/articles/follow-sqs-messages-through-splunk-apm-with-the-otel-java/) measured today's agent on two Spring Boot services and a real SQS queue. Same services here, same queue setup, four runs.

## Four runs, one variable changed at a time

`orders` sends each order to SQS with the AWS SDK, and `fulfilment` handles it with `@SqsListener` (one message at a time, polling up to 10). Nothing in the code knows about tracing. Each run got fresh pods, an empty queue and its own order numbers, and the lab read the agent version and the `OTEL_` variables from inside each pod before any load.

The lab joins each message's producer span and process span on the SQS message ID, from the agent's own span output:

| Run | Messages joined | In the producer's trace | Process span linked to producer | Receive spans |
| --- | --- | --- | --- | --- |
| Today's defaults | 400 | 400 | 0 | 0 |
| Today + receive telemetry | 100 | 0 | 100 | 72 |
| 3.0 preview | 400 | 400 | 400 | 0 |
| Preview + receive telemetry | 100 | 100 | 100 | 72 |

Every order was sent and fulfilled in every run, and no span went unpaired.

## What changed in the trace

With receive telemetry on, today's agent starts each consumer trace at the receive span and hangs the process span under it, with a link back to the producer. One consumer trace from the agent's output:

```text
<queue> receive         CONSUMER  parent=none
  <queue> process       CONSUMER  parent=<receive span>   link=<producer trace>
  Sqs.DeleteMessageBatch CLIENT   parent=<receive span>
```

With the preview, the process span's parent is the producer's context, so it stays in the producer's trace, and it keeps the link as well. The receive span becomes `receive <queue>` of kind `CLIENT`, and those 72 receive spans carried links to all 100 producer traces:

```text
process <queue>         CONSUMER  parent=<producer context>   link=<producer trace>
delete <queue>          CLIENT    parent=<producer context>
```

That matches the direction the OpenTelemetry blog describes for Kafka, measured here on SQS.

![Two consumer trace shapes from the lab's agent output with receive telemetry on: today's agent starts each consumer trace at a <queue> receive span with the process span under it, 0 of 100 in the producer's trace; the 3.0 preview puts process <queue> in the producer's trace under send <queue>, 100 of 100, and receive <queue> becomes a CLIENT span](/abhimanyu/articles/otel-java-agent-3-0-preview-sqs-receive-spans-one-trace/step-1.png)

## What Splunk APM shows

I read 10 preview traces back through Splunk's trace API, spread across the 400-message run. All 10 held both `orders` and `fulfilment` in one trace. The service map for the preview environment ran from `orders` through the inferred `sqs` node to `fulfilment`.

These are the operation names Splunk APM held for 5 default traces and those 10 preview traces:

| Service | Today's agent | 3.0 preview |
| --- | --- | --- |
| orders | `POST /orders/{id}` | `POST /orders/{id}` |
| orders | `<queue> publish` | `send <queue>` |
| fulfilment | `<queue> process` | `process <queue>` |
| fulfilment | `Sqs.DeleteMessageBatch` | `delete <queue>` |

The HTTP operation stays the same. The three messaging names change, so any Splunk APM filter, chart or alert scoped to the old names is worth updating alongside the upgrade. I didn't read Splunk back for the receive telemetry runs.

## Run it in your own account in 3 steps

### Step 1: Create the cluster (the EKS cluster took 6m17s in my run)

```bash
git clone https://github.com/Abhimanyu9988/showmerit.git
cd showmerit/labs/otel-java-agent-3-0-preview-sqs-receive-spans-one-trace
export AWS_REGION=eu-central-1 TF_VAR_region=eu-central-1 LAB_NAME=sqs-v3-preview TF_VAR_lab_name=sqs-v3-preview
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Today's agent (about 6 minutes)

Run `./reproduce.sh`. It installs the Splunk OTel Collector (chart 0.163.1), builds the services in the cluster, attaches agent 2.33.1 on its defaults for 400 orders, then turns on receive telemetry for 100 more.

### Step 3: The 3.0 preview (about 5 minutes)

Run `./fix.sh`. It sets `OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true` on both services, repeats both runs, and reads sampled traces back from Splunk. The preview runs report under their own `deployment.environment` (your lab name plus `-v3`), so Splunk keeps the two modes apart.

To try it on your own service, add the one variable to a copy of its Deployment:

```yaml
env:
  - name: OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW
    value: "true"
```

## Before 3.0: a short checklist

- **Traces with receive telemetry on:** each message becomes one trace instead of two linked ones, so per-trace counts and durations that span the queue will look different.
- **Operation names:** update Splunk APM filters, charts and alerts from `<queue> publish`, `<queue> process` and `Sqs.DeleteMessageBatch` to `send <queue>`, `process <queue>` and `delete <queue>`.
- **Links:** every process span now carries a link to its producer, which gives you a second path from consumer to producer.
- **Receive spans:** they change kind from `CONSUMER` to `CLIENT`, so anything selecting consumer spans by kind will see process spans only.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **The final 3.0.** The preview can still change before 3.0, as the project says.
- **Batch listeners** (`List<Message>`), which follow a different model, and other SQS clients.
- **Database and code conventions**, which the same flag changes. My [preview article on Postgres](https://showmerit.com/abhimanyu/articles/preview-otel-java-agent-3-0-in-splunk-with-one-flag/) covers the database side.
- **Timing.** The runs were one after another on the same services; this measures span structure, not latency.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/README.md)
- [`base.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/base.sh)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/k8s/10-app.yaml)
- [`k8s/15-standin-sqs.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/k8s/15-standin-sqs.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/k8s/20-load.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/reproduce.sh)
- [`spans.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/spans.sh)
- [`splunk.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/splunk.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-now-stay-in/evidence): the raw output of my run

## Sources

- [The OpenTelemetry Java agent 3.0 is almost here: preview it today](https://opentelemetry.io/blog/2026/java-agent-3.0-preview/)
- [Revisit the messaging receive telemetry flag (issue 19414)](https://github.com/open-telemetry/opentelemetry-java-instrumentation/issues/19414)
- [Release v2.33.1](https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/tag/v2.33.1)

Disclosure: I work at Splunk, part of Cisco, and contribute to OpenTelemetry. This is my personal writing; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The [lab code and raw output](https://github.com/Abhimanyu9988/showmerit/tree/main/labs/otel-java-agent-3-0-preview-sqs-receive-spans-one-trace) are public. Drafted with AI assistance and reviewed by me._
