# Follow SQS messages through Splunk APM with the OTel Java agent and zero code

## What it shows

Two Spring Boot services run on EKS and exchange messages over a real Amazon SQS queue. `orders` sends each order with `SqsTemplate`, and `fulfilment` handles it with `@SqsListener`. Both use the same jar on the same image, and neither has any tracing code.

* `reproduce.sh` (BEFORE): installs the Splunk OTel Collector, builds the jar once in the cluster, and sends 400 orders with no agent. It also finds one waiting message on the queue by its ID, to show what a message carries by default.
* `fix.sh` (BUILD): sets one environment variable on each service and sends the same 400 orders. It joins each message's producer and consumer spans on the SQS message ID, reads the trace header from one message on the queue, and reads a sample of 10 traces back from Splunk Observability Cloud.

The headline number is how many messages have producer and consumer spans that share one trace ID, of those sent. The producer span is the publish span, and the consumer span is the process span: kind `CONSUMER`, named `<queue> process`. A message that doesn't join is reported as no process span, no producer span, or a different trace.

The run sets no SQS or messaging option. The agent writes the trace into the `AWSTraceHeader` message system attribute when it sends, and the lab shows that header on a waiting message next to the producer span's trace ID.

`fix.sh` then changes one setting on `fulfilment`, `otel.instrumentation.common.messaging.experimental.receive-telemetry.enabled=true`, and sends 100 more orders. It counts the process spans that stay in the producer's trace, and the ones with a span link to it. Default settings suit a reader who wants one trace per message, and receive telemetry suits one who wants each consumer in a trace of its own, linked to its producer.

Each run prints what decided its result: the jar and agent hashes, the agent version, and the receive telemetry and propagator settings on each pod. `console` and `logging-otlp` are on so the lab can count every message from the pod logs and read span links.

`@SqsListener` here takes one message at a time and polls up to 10 messages, set in `SPRING_CLOUD_AWS_SQS_LISTENER_MAX_MESSAGES_PER_POLL`. The results apply to this listener style. Spring Cloud AWS observation is left off.

## What the checks rest on

`lib.sh` and `k8s/00-build.yaml` hold these details.

* **Agent:** the download is pinned to v2.33.1 and checked against its SHA-256, and `fix.sh` stops if the logged version differs. The defaults here are that version's.
* **Setting name:** `otel.instrumentation.common.messaging.experimental.receive-telemetry.enabled` is copied from the agent's `metadata.yaml` at v2.33.1. As a positive control, the lab counts `<queue> receive` spans in each run and stops if the receive telemetry run has none.
* **Spans:** only `<queue> process` spans of kind `CONSUMER` count as consumer spans, so a receive span can't double-count a message. A link counts only when its trace ID equals that message's producer trace ID, read from `logging-otlp` output because the console format leaves links out. Links are counted on process spans and on receive spans separately, and the receive telemetry run prints one whole consumer trace, so the output names the span that holds the link.
* **Probe:** the message is found by its own ID, read with the `AWSTraceHeader` system attribute requested and a visibility timeout of 0, then deleted, so no later run handles it.
* **Trace IDs:** the X-Ray `Root=1-xxxxxxxx-yyyy...` value is converted to a 32-digit trace ID, and both raw values are printed.
* **Splunk:** the lab samples exactly 10 trace IDs spread evenly across the default run. `fixed` needs all 10 to hold both services, and `orders` and `fulfilment` on the map as real services, not inferred ones.
* **Percent:** the share of messages in one trace is a measured result, not a pass mark. It doesn't decide `fixed`. Under 100%, the brief's rule picks the fallback title.

Each setting runs once, at 400 messages on the defaults and 100 with receive telemetry. `messages_sent` counts orders that returned HTTP 200, and `orders` only returns that after `SqsTemplate` has sent the message.

The variable a reader ships is smaller than the lab's, because the extra exporters are only here for counting:

```text
JAVA_TOOL_OPTIONS=-javaagent:/path/to/opentelemetry-javaagent.jar -Dotel.service.name=orders -Dotel.exporter.otlp.endpoint=http://$(HOST_IP):4318
```

The agent jar has to reach the container. This lab mounts it from the node, so no image is rebuilt and no code changes.

## How the pods reach SQS

`main.tf` creates a role that may use this one queue, and EKS Pod Identity gives it to the `sqs-lab` service account. The pods hold no AWS keys, and the AWS SDK picks up the credentials through its default chain.

## Run it

Set `AWS_REGION`, `LAB_NAME`, `TF_VAR_lab_name` (the same value as `LAB_NAME`), `SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN` (ingest) and `SPLUNK_API_TOKEN` (read). Then run:

```bash
terraform init && terraform apply -auto-approve
./reproduce.sh
./fix.sh
./cleanup.sh
terraform destroy -auto-approve
```

The run uses two `t3.large` nodes, one SQS queue and an EKS control plane for about an hour.

`k8s/15-standin-sqs.yaml` and `check.sh` are for free checks only, where ElasticMQ stands in for SQS. A real run never applies that file.
