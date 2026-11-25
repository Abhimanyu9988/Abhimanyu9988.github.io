# Batch processor deprecated? Move batching to sending_queue, measured through a 30 s outage

The OpenTelemetry Collector's `batch` processor is deprecated. The migration in the OpenTelemetry blog is one line: remove
the processor and turn on batching in the exporter's `sending_queue`. The blog also warns that the queue then holds smaller,
unbatched requests, so the same `queue_size` can hold much less data. This lab measures what that means for spans when the
gateway's export target goes away for 30 seconds.

## What runs

- EKS 1.34 with two t3.large nodes (public subnets, no NAT), the Splunk OTel Collector chart 0.163.1 as the agent.
- A gateway Collector (contrib 0.165.0) that exports over OTLP to a relay Collector (contrib 0.165.0), which forwards to
  the Splunk agent on its node. Scaling the relay to 0 is the outage.
- telemetrygen v0.165.0 sending 60 spans a second as single-span requests (`--batch=false`), like many small clients. Each trace is a
  parent and one child span (telemetrygen's default), and its rate limit counts spans. Each worker logs how many traces it
  generated, so the sender's own count is 2 spans a trace; `check.sh` confirms the gateway accepts exactly that many when
  nothing is refused or dropped.

Six runs, one change at a time (the gateway restarts for each):

| Runs | Gateway config |
| --- | --- |
| batch-1, batch-2 | `batch` processor with its defaults, default `sending_queue` |
| migrated-1, migrated-2 | no `batch` processor, `sending_queue: { batch: { enabled: true } }`, queue left at its defaults |
| sized-1, sized-2 | the migrated config with `sizer: items` and `queue_size: 100000` |

Each run: 50 s of load, the relay down from 10 s to 40 s, then up to 60 s for the queue to drain. All three configs cap the
exporter's retry interval at 5 s (`retry_on_failure.max_interval`, default 30 s), so the drain isn't measured against a long
backoff. With the default, a drain can take longer than measured here. Counts: spans the sender generated, and from the gateway's own metrics, spans accepted and refused by its receiver
(refused counts attempts, since the sender retries), spans its exporter couldn't enqueue, spans sent, and its queue size
against capacity, read every 2 s, with accepted and sent. From those reads: the accept rate before the outage, and the
seconds from the relay's return until the queue is back at its pre-outage level. Lost = generated minus sent. `fix.sh` then reads, for all six runs, how many spans Splunk
APM holds for the load service from the run's load start to 20 s after its drain; runs are at least 30 s apart.

## Run it in your own account

Export `SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN` (ingest) and `SPLUNK_API_TOKEN` (read), and `AWS_REGION` and `LAB_NAME`, then:

```bash
terraform init && terraform apply
./reproduce.sh
./fix.sh
./cleanup.sh && terraform destroy
```

`check.sh` runs the same configs, load and stats script in Docker, with no AWS account and no Splunk tokens.

## Limits

One load shape (many small requests), one outage length and one queue size per config. A persistent queue
(`file_storage`) is a separate change and isn't measured here.
