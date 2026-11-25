---
title: "Batch Processor Deprecated? Size sending_queue in Items Before an Outage"
description: "The OpenTelemetry Collector's batch processor is deprecated. On EKS with a 30 s outage, exporter batching with sending_queue sized in items delivered all 3,008 spans in both runs; with the default queue it filled and the app sent 1,712 and 1,840."
image: /assets/posts/batch-processor-deprecated-size-sending-queue-in-items/cover.png
image_alt: "Cover: Batch Processor Deprecated? Size sending_queue in Items Before an Outage"
tags: ["opentelemetry", "observability", "kubernetes", "splunk"]
channels: []
lab: labs/batch-processor-deprecated-size-sending-queue-in-items
linkedin: "The batch processor is going away. Check your queue first.\n\nThe OpenTelemetry Collector is moving batching into the exporter.\nThe migration is one line: drop the processor, turn on batching in the exporter.\n\nI tested it with my backend switched off for 30 seconds.\nSame app, same 50 seconds, two runs each:\n→ batch processor: 3,008 spans, all through\n→ one-line migration, default queue: 1,712 and 1,840\n→ migration + queue sized in spans: 3,008, all through\n\nWhy: batching now happens after the waiting line (the sending queue).\nAbout 60 small requests a second for 30 seconds is about 1,800 places.\nThe default queue holds 1,000. It filled, the Collector refused 4 and 3 requests,\nand my app sent fewer spans. (The lab measured the drop, not its cause.)\n\nThe fix is one more setting:\nsending_queue:\n  sizer: items\n  queue_size: 100000\nThe queue never passed 2,072 spans.\n\nWhat I took away:\n→ The migration is easy. Change the queue in the same commit.\n→ Count your queue in spans, not requests.\n→ Size it: spans a second × longest outage, with room on top.\n\nThe lab, every config and the raw counts:\n\n{LINKS}\n\nHave you planned your batch processor migration yet?\n\nI work at Splunk, part of Cisco. Views are my own.\n\n#OpenTelemetry #Observability #Kubernetes #showmerit"
replies: []
---
![Cover: Batch Processor Deprecated? Size sending_queue in Items Before an Outage](https://abhimanyu9988.github.io/assets/posts/batch-processor-deprecated-size-sending-queue-in-items/cover.png)

The OpenTelemetry Collector's batch processor is being retired, and batching moves into the exporter. I tested the move with my backend switched off for 30 seconds. With the exporter's queue sized in spans (`sizer: items`, `queue_size: 100000`), every one of my app's 3,008 spans got through in both runs, and the queue never passed 2,072 spans of its 100,000.

That setting is the part to add when you migrate. With the queue left at its default, the same outage went differently: the queue filled, and my app sent fewer spans.

![Bar chart: spans the app sent in 50 s with a 30 s outage. Batch processor: 3,008 in both runs, queue peak 172 and 162 of 1,000 requests. One-line migration with the default queue: 1,712 and 1,840, queue at 1,000 of 1,000. Migration with the queue sized in spans: 3,008 in both runs, queue peak 1,960 and 2,072 of 100,000](/abhimanyu/articles/batch-processor-deprecated-size-sending-queue-in-items/cover.png)

## What I tested, in plain words

My app sends its traces to a gateway Collector, a small service that forwards them to Splunk Observability Cloud. A span is one timed step of work in a trace. My test app sent about 60 spans a second, one span per request, like many small apps talking to one gateway.

When the backend goes away, the Collector parks what it can't send in a waiting line, the sending queue. By default it holds 1,000 requests.

The [OpenTelemetry blog](https://opentelemetry.io/blog/2026/collector-batching-migration/) gives the migration as one line: drop the batch processor and turn on batching in the exporter. It also warns that the same queue size can then hold much less data. I ran three configs, two runs each:

- **Batch processor (before):** the small spans became a few bigger requests. The queue peaked at 172 and 162 of 1,000, and all 3,008 spans went through.
- **One-line migration, default queue:** batching now happens after the queue, so each small request took its own place. The queue reached 1,000 of 1,000, and the Collector refused 4 and 3 requests back to my app. In the same 50 seconds, my app sent 1,712 and 1,840 spans instead of 3,008. Every span the Collector accepted was sent on: nothing was lost inside the Collector. The lab measured that the app sent fewer, not why. My test app sends each span and waits for the answer, so waiting on those refused requests is a likely cause, not a measured one.
- **Migration, queue sized in spans (the fix):** all 3,008 spans in both runs, queue peak 1,960 and 2,072 of 100,000.

## Why 1,000 wasn't enough, and how to size it

The arithmetic is short. About 60 single-span requests a second for a 30-second outage is about 1,800 requests, and the default queue holds 1,000. With the batch processor in front, the same spans arrived as a few bigger requests, and the queue peaked at 172.

Sized in spans, the queue peaked at 1,960 and 2,072. That's close to 60 spans a second times the 30-second outage (1,800), plus what was still waiting to go out. So a starting point is: your spans a second at the gateway, times your longest outage in seconds, with room on top. 100,000 left plenty here.

## What I took away

The migration is easy, and the exporter is where batching lives from now on. Change the queue in the same commit: count it in spans and size it for your longest outage.

## The numbers

50 s of load, the backend off from 10 s to 40 s. "App sent" is my test app's own count; the rest are the gateway Collector's own metrics.

| Run | App sent | Accepted | Refused | Failed to queue | Sent on | Queue peak |
| --- | --- | --- | --- | --- | --- | --- |
| batch-1 | 3,008 | 3,008 | 0 | 0 | 3,008 | 172 / 1,000 requests |
| batch-2 | 3,008 | 3,008 | 0 | 0 | 3,008 | 162 / 1,000 requests |
| migrated-1 | 1,712 | 1,712 | 4 | 4 | 1,712 | 1,000 / 1,000 requests |
| migrated-2 | 1,840 | 1,840 | 3 | 3 | 1,840 | 1,000 / 1,000 requests |
| sized-1 | 3,008 | 3,008 | 0 | 0 | 3,008 | 1,960 / 100,000 spans |
| sized-2 | 3,008 | 3,008 | 0 | 0 | 3,008 | 2,072 / 100,000 spans |

Before the outage, the gateway took in 60.0 to 60.6 spans a second in every run. From the 2-second reads of its queue, the queue was back to its normal level 8 to 10 seconds after the backend returned, in every run.

From Splunk APM, as a second source: the span count for the app moved with the gateway's count and was exactly half of it in every run. It was 1,504 for all four batch and sized runs (against 3,008), 856 for migrated-1 (against 1,712) and 920 for migrated-2 (against 1,840). Each trace from my test app has two spans, a client span and a server span, so counting one span per trace is the likely reason for the half. The lab didn't check that. The program the lab read for each run's window (with a fallback to `histogram('spans', ...).count().publish()` when it reads 0, which it didn't here):

```text
data('spans.count', filter=filter('sf_environment', '<lab name>') and filter('sf_service', 'checkout-load'), rollup='delta').sum().publish()
```

## Try it yourself

The lab runs on Amazon EKS with two t3.large nodes, the Splunk OTel Collector Helm chart 0.163.1 as the agent, and the upstream Collector (contrib 0.165.0) as the gateway. A second small Collector, the relay, sits between the gateway and the agent. Scaling it to 0 is the outage. The load is telemetrygen v0.165.0.

The batch processor config (before):

```yaml
processors:
  batch: {}
exporters:
  otlphttp:
    endpoint: http://relay:4318
    retry_on_failure:
      max_interval: 5s
```

The one-line migration:

```yaml
exporters:
  otlphttp:
    endpoint: http://relay:4318
    retry_on_failure:
      max_interval: 5s
    sending_queue:
      batch:
        enabled: true
```

The fix, the queue sized in spans:

```yaml
exporters:
  otlphttp:
    endpoint: http://relay:4318
    retry_on_failure:
      max_interval: 5s
    sending_queue:
      sizer: items
      queue_size: 100000
      batch:
        enabled: true
```

The load, about 60 spans a second as single-span requests:

```bash
telemetrygen traces --otlp-http --otlp-endpoint=gateway.demo.svc.cluster.local:4318 --otlp-insecure \
  --batch=false --workers=4 --rate=15 --duration=50s --service=checkout-load --allow-export-failures
```

Things to plan for:

- I capped the retry interval at 5 s in all three configs so drains were comparable. The default is 30 s, so with defaults a drain can take longer.
- A queue sized in spans uses more memory. I didn't measure memory here.
- This is one load shape (many small requests) and one outage length, two runs each.
- A queue on disk (`file_storage`) also survives a Collector restart. That's a separate change I didn't test here.

Every script and the raw output of every run are in the [lab folder](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/batch-processor-deprecated-size-sending-queue-in-items).


## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/fix.sh)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/k8s/20-load.yaml)
- [`k8s/30-gateway.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/k8s/30-gateway.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/main.tf)
- [`measure.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/measure.sh)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/reproduce.sh)
- [`splunk.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/batch-processor-deprecated-size-sending-queue-in-items/splunk.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/batch-processor-deprecated-size-sending-queue-in-items/evidence): the raw output of my run
