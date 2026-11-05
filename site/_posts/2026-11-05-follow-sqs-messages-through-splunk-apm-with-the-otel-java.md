---
title: "Follow SQS Messages Through Splunk APM With the OTel Java Agent, Zero Code"
description: "One environment variable put 400 of 400 SQS messages in one trace on EKS, with both services in Splunk APM. No code change, and here's how to build it."
image: /assets/posts/follow-sqs-messages-through-splunk-apm-with-the-otel-java/cover.png
image_alt: "Terminal output: a queued message with no agent carries no AWSTraceHeader; with the agent, the header is present and 400 of 400 messages share one trace."
tags: ["opentelemetry", "sqs", "eks", "splunk"]
channels: []
lab: labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java
linkedin: "An SQS queue splits a request across two services.\nMine stayed one trace for 400 of 400 messages, with zero code changes.\n\nI attached the OpenTelemetry Java agent to two Spring Boot services on EKS and counted every message on a real queue.\n\n→ One environment variable loads the agent\n→ The agent writes the trace into the message's AWSTraceHeader\n→ The consumer's span joins the producer's trace\n\nWhat I measured:\n400 of 400 messages in one trace, 0 misses\n10 of 10 sampled traces in Splunk APM held both services\nThe trace ID on a waiting message matched the producer span\n\nOne experimental setting gives you a second shape: each consumer gets its own trace, linked back. With it on, 100 of 100 process spans carried that link.\n\nSo a platform team can show queue tracing before asking anyone to change code.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\nhttps://showmerit.com/abhimanyu/articles/follow-sqs-messages-through-splunk-apm-with-the-otel-java/\n\nDo you follow a message as one trace, or as linked traces per consumer?\n\n#OpenTelemetry #AWS #Java #Observability"
replies: [{"url": "https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/14193", "text": "For anyone finding this later, I measured the default on a real SQS queue on EKS with agent 2.33.1, Spring Boot and a single-message SqsListener. With no SQS setting, 400 of 400 messages had producer and process spans in one trace. With receive telemetry on, none of 100 stayed in the producer's trace, and all 100 process spans carried a link to it. I wrote up the full build here: {POST_URL}"}]
---
![Terminal output: a queued message with no agent carries no AWSTraceHeader; with the agent, the header is present and 400 of 400 messages share one trace.](https://abhimanyu9988.github.io/assets/posts/follow-sqs-messages-through-splunk-apm-with-the-otel-java/cover.png)

You want to follow one order from the HTTP request, into an SQS queue and out of it in another service, as a single trace. On Spring Boot, the OpenTelemetry Java agent gave me that from one environment variable: **400 of 400 messages had their producer and consumer spans in one trace**, on a real queue on EKS, and all 10 traces I sampled in Splunk APM held both services.

No tracing code, no SQS setting, and the same jar as before. The pods needed the agent jar, which I mounted from the node, the variable that loads it, and a Splunk OTel Collector in the cluster to receive the spans.

I built this in a throwaway AWS account and counted it message by message. Below: how the trace crosses the queue, the one setting that changes its shape, and every step.

![Dashboard charted from real lab data: orders, an inferred sqs node and fulfilment on a service map, 400 of 400 messages in one trace, 10 of 10 traces read in Splunk, and the trace ID on a waiting SQS message matching the producer span](/abhimanyu/articles/follow-sqs-messages-through-splunk-apm-with-the-otel-java/cover.png)

> **TL;DR**
> - **What you get:** one trace from the HTTP request through SQS to your `@SqsListener` method, and both services on a Splunk APM service map.
> - **How:** the agent puts the trace in the message's `AWSTraceHeader` system attribute.
> - **The proof:** 400 of 400 messages in one trace, 0 misses. All 10 sampled traces in Splunk held both services.
> - **Time and cost:** about 20 minutes from `terraform apply` through `terraform destroy` in my run, with two `t3.large` nodes, the EKS control plane and one SQS queue running throughout. The README's one hour is a safe budget.

## Why the queue is the part worth proving

A queue splits a request in two. One service sends, another picks the message up later, and nothing connects them over HTTP. A maintainer says the two should share a trace by default, and I wanted a count from a real queue to go with that.

The lab has two services from one jar, with Spring Boot 3.5.6 and Spring Cloud AWS 3.4.0. This is all the messaging code, and none of it knows about tracing:

```java
@PostMapping("/orders/{id}")
public Map<String, Object> order(@PathVariable("id") long id) {
  String messageId = sqs.send(queue, "order-" + id).messageId().toString();
  return Map.of("order", id, "messageId", messageId);
}

@SqsListener("${queue.name}")
public void handle(String body) {
  log.info("fulfilled {}", body);
}
```

With no agent attached, every order arrives, and the message carries no trace:

```text
no_agent run: sent 400, fulfilled 400, producer spans 0, consumer spans 0
message 47d4f7f7-c828-44d6-8bc2-ac32ee16cfdd AWSTraceHeader: none
no_agent run, messages carrying the header: 0
```

The second line is one waiting message, read directly from the queue while `fulfilment` was paused.

## The trace rides on the message itself

SQS has a message system attribute built for trace headers, called `AWSTraceHeader`. The Java instrumentation [uses it by default](https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/14193), and [AWS notes](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-message-metadata.html) that the attribute doesn't count towards message size.

I ran the same read with agent 2.33.1 attached:

```text
message d5a18fb0-fa79-47c5-8f1c-dfc5dcb98c65 AWSTraceHeader: Root=1-354df141-feaef1cc58d93649bca2af18;Parent=7475d1a4838a35dd;Sampled=1
trace ID in the header: 354df141feaef1cc58d93649bca2af18, on the producer span: 354df141feaef1cc58d93649bca2af18
trace header root matches the producer span: 1
```

The header uses the AWS X-Ray format, and it holds the same trace ID as the producer span once you remove the dashes.

![One order as one trace in three spans across orders and fulfilment, above the AWSTraceHeader on a waiting message and the matching producer span trace ID](/abhimanyu/articles/follow-sqs-messages-through-splunk-apm-with-the-otel-java/step-3.png)

## Build it yourself in 3 steps

My run's logged phases: `terraform apply` 481 s, `reproduce.sh` 213 s, `fix.sh` 277 s, `cleanup.sh` 9 s, `terraform destroy` 181 s.

### Step 1: Create the cluster and the queue (about 8 minutes)

```bash
git clone https://github.com/Abhimanyu9988/showmerit.git
cd showmerit/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-javaith-the-otel-java
export AWS_REGION=eu-central-1 LAB_NAME=sqs-trace-lab TF_VAR_lab_name=sqs-trace-lab
export SPLUNK_REALM=<realm> SPLUNK_ACCESS_TOKEN=<ingest-token> SPLUNK_API_TOKEN=<read-token>
terraform init && terraform apply -auto-approve
```

The pods hold no AWS keys. EKS Pod Identity gives the `sqs-lab` service account a role that can only use this queue:

```hcl
resource "aws_eks_pod_identity_association" "sqs" {
  cluster_name    = aws_eks_cluster.main.name
  namespace       = "demo"
  service_account = "sqs-lab"
  role_arn        = aws_iam_role.sqs.arn
  depends_on      = [aws_eks_addon.pod_identity]
}
```

### Step 2: Run both services with no agent (about 4 minutes)

Run `./reproduce.sh`. It installs the Splunk OTel Collector with chart 0.163.1, builds the jar once in the cluster, and sends 400 orders.

![Before: no agent settings, jar recorded, no spans](https://abhimanyu9988.github.io/assets/posts/follow-sqs-messages-through-splunk-apm-with-the-otel-java/step-1.png)

### Step 3: Attach the agent and count (about 5 minutes)

Run `./fix.sh`. The whole change is one variable on each Deployment. This is what you'd ship, with `HOST_IP` set from `status.hostIP` so the agent can reach the Collector on its node:

```text
JAVA_TOOL_OPTIONS=-javaagent:/path/to/opentelemetry-javaagent.jar -Dotel.service.name=orders -Dotel.exporter.otlp.endpoint=http://$(HOST_IP):4318
```

Set `deployment.environment` in `otel.resource.attributes` too, as the lab does, so your services appear under an environment in Splunk APM. The lab mounts the jar from the node to keep the image unchanged. An init container or the OpenTelemetry Operator are common ways to deliver it in production, though I didn't test those here.

The script also adds `console` and `logging-otlp` exporters, for counting only. It then joins each message's publish span and process span on the SQS message ID.

![fix.sh: agent 2.33.1 on defaults, same jar as before](https://abhimanyu9988.github.io/assets/posts/follow-sqs-messages-through-splunk-apm-with-the-otel-java/step-2.png)

## The proof: 400 messages, 400 joined, 0 misses

```text
default run: sent 400, fulfilled 400, producer spans 400, consumer spans 400, joined on message ID 400, sharing one trace 400, distinct traces 400
default run, misses: no process span 0, no producer span 0, different trace 0. Most process spans for one message: 1
one trace percent, default run: 100.0
default run, receive spans: 0
-- three of the joined rows: message ID, producer trace, consumer trace
  0127de22-485b-428a-acc7-034f2e4dcf5e c5cbf7e49265aebe6aacdeb6bdad9d02 c5cbf7e49265aebe6aacdeb6bdad9d02
  012bcc56-c649-433e-812b-63b9f712ed34 f90a7d13ce7bb5e8e60c0262a34f49ad f90a7d13ce7bb5e8e60c0262a34f49ad
  01771e24-4d49-4a6a-abdb-12586654acef 368cd3268d19dbf632c4a0b16e5657b2 368cd3268d19dbf632c4a0b16e5657b2
sampled traces with both services in one trace in Splunk: 10 of 10
```

The two trace IDs on each row match. Each order got its own trace and exactly one process span. Neither pod had an instrumentation setting for SQS, and the jar's hash matched the run with no agent.

Splunk APM agreed. Both services appeared on the map, with `sqs` as an inferred node and a path from `orders` to `fulfilment`.

![Service map of orders, an inferred sqs node and fulfilment, above a chart of requests per 10 seconds showing a burst of 400 orders and a burst of 100 orders, charted from real Splunk series](/abhimanyu/articles/follow-sqs-messages-through-splunk-apm-with-the-otel-java/step-2.png)

![One trace read back from Splunk APM](https://abhimanyu9988.github.io/assets/posts/follow-sqs-messages-through-splunk-apm-with-the-otel-java/step-3.png)

## One experimental setting gives each consumer a linked trace

The defaults give you one trace per message. The agent also offers a second shape, where each consumer gets a trace of its own, linked back to the producer. I set `otel.instrumentation.common.messaging.experimental.receive-telemetry.enabled=true` on `fulfilment` and sent 100 more orders:

```text
receive_telemetry run: sent 100, fulfilled 100, joined on message ID 100, sharing one trace 0, in their own trace 100. Most process spans for one message: 1
receive_telemetry run, consumer spans linked to the producer: 100
receive_telemetry_receive_span links to the producer: 0
receive_telemetry run, process spans whose parent is a receive span: 100
receive_telemetry run, receive spans: 76
```

| Run | Messages | In the producer's trace | Linked to the producer | Receive spans |
| --- | --- | --- | --- | --- |
| Defaults | 400 | 400 | 0 | 0 |
| Receive telemetry | 100 | 0 | 100 | 76 |

Receive spans going from 0 to 76 shows the agent applied the setting. The link sits on the process span, not the receive span. One consumer trace from the agent's output shows why there are fewer receive spans than messages, with one receive span holding two messages:

```text
  follow-sqs-messages-thro-738351-orders receive kind=5 span=3c4aa35a087891e3 parent=none links=
    follow-sqs-messages-thro-738351-orders process kind=5 span=88f2e0f96c7c481c parent=3c4aa35a087891e3 links=3d4b7a86685e7351af999a6440905f9c
    follow-sqs-messages-thro-738351-orders process kind=5 span=2e2a9857c6bf3525 parent=3c4aa35a087891e3 links=344012b64ebe2e596b7c31a64d93b17c
    Sqs.DeleteMessageBatch kind=3 span=f956636b787d5a63 parent=3c4aa35a087891e3 links=
```

Keep the default to see a whole order end to end. Try this setting when you want each consumer's work in a trace of its own, and recheck it when you upgrade, because it's experimental.

![Side by side: defaults with 400 of 400 messages sharing the producer's trace, and receive telemetry with 100 of 100 process spans linked, 76 receive spans and one receive span holding two process spans](/abhimanyu/articles/follow-sqs-messages-through-splunk-apm-with-the-otel-java/step-4.png)

## Use it on your own queues

- **Start with one pair of services.** If they use `SqsTemplate` and single-message `@SqsListener` methods, try the variable before asking anyone for code.
- **Know the other carrier.** `otel.instrumentation.aws-sdk.experimental-use-propagator-for-messaging` [defaults to `false`](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/main/instrumentation/aws-sdk/README.md). Turning it on moves context into message attributes with your configured propagator. The maintainer says it changes how context travels, not whether spans share a trace, and suggests it for a stand-in that doesn't keep `AWSTraceHeader`.

## Clean up (about 3 minutes, and the cluster costs money until it's done)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Batch listeners.** My listener takes one message at a time, polling up to 10. The maintainer discussion notes exceptions for batch receives, so measure your own if a method takes a `List`.
- **Anything beyond this setup.** I used agent 2.33.1 and standard SQS sends of one message at a time. I didn't try FIFO queues, `SendMessageBatch` or the propagator setting.
- **Overhead.** Both services ran with extra span logging for counting, on one `t3.large`, so this lab says nothing about latency. Each setting ran once.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/k8s/10-app.yaml)
- [`k8s/15-standin-sqs.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/k8s/15-standin-sqs.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/k8s/20-load.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/reproduce.sh)
- [`splunk.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/splunk.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-java/evidence): the raw output of my run

## Sources

- [Springboot sqs instrumentation, the maintainer discussion](https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/14193)
- [Message metadata for Amazon SQS](https://docs.aws.amazon.com/AWSSimpleQueueService/latest/SQSDeveloperGuide/sqs-message-metadata.html)
- [Settings for the AWS SDK instrumentation](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/main/instrumentation/aws-sdk/README.md)
- [AWS SDK instrumentation metadata at v2.33.1](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/v2.33.1/instrumentation/aws-sdk/aws-sdk-2.2/metadata.yaml)
- [Spring Cloud AWS reference](https://docs.awspring.io/spring-cloud-aws/docs/3.4.0/reference/html/index.html)

Disclosure: I work at Splunk, part of Cisco. This is my personal writing; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The [lab code and raw output](https://github.com/Abhimanyu9988/showmerit/tree/main/labs/follow-sqs-messages-through-splunk-apm-with-the-otel-javaith-the-otel-java) are public. Drafted with AI assistance and reviewed by me._
