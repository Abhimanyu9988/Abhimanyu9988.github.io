---
title: "After One Collector Change, My App Sent 43% Fewer Spans in an Outage"
description: "With the OpenTelemetry Collector's batch processor replaced by exporter batching and a default queue, a 30 s outage cut what my app sent from 3,008 to 1,712 spans. Sizing the queue in spans fixed it, on EKS with Splunk."
image: /assets/posts/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/cover.png
image_alt: "Cover: After One Collector Change, My App Sent 43% Fewer Spans in an Outage"
tags: ["opentelemetry", "observability", "kubernetes", "splunk"]
channels: []
lab: labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an
linkedin: "My app sent 43% fewer spans after one config change.\n\nThe OpenTelemetry Collector's batch processor is being retired.\nThe migration is one line: drop the processor, turn on batching in the exporter.\n\nI made that change and switched off the backend for 30 seconds.\nSame app, same 50 seconds:\n→ batch processor: 3,008 spans, all delivered\n→ one-line migration: 1,712 spans\n\nWhy: batching now happens after the waiting line (the sending queue).\nEach tiny request takes a place. The default queue of 1,000 filled up.\nThe Collector said \"not now\", and my app waited instead of sending.\n\nThe fix is one more setting:\nsending_queue:\n  sizer: items\n  queue_size: 100000\n3,008 spans again, all through the same outage.\n\nWhat I took away:\n→ The migration is easy. Check your queue before you delete that line.\n→ Count your queue in spans, not requests.\n→ Size it for your longest outage.\n\nThe lab, every config and the raw counts:\n\n{LINKS}\n\nHave you planned your batch processor migration yet?\n\nI work at Splunk, part of Cisco. Views are my own.\n\n#OpenTelemetry #Observability #Kubernetes #showmerit"
replies: []
---
![Cover: After One Collector Change, My App Sent 43% Fewer Spans in an Outage](https://abhimanyu9988.github.io/assets/posts/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/cover.png)

My app sends its traces to an OpenTelemetry Collector, a small service that collects them and forwards them to Splunk Observability Cloud. The Collector's batch processor, the part that groups traces into bigger packages before sending, is being retired. The [OpenTelemetry blog](https://opentelemetry.io/blog/2026/collector-batching-migration/) gives the move as one line: drop the processor and turn on batching inside the exporter.

So I made that one change and switched off the Collector's backend for 30 seconds. In the same 50 seconds, my app sent 1,712 spans instead of 3,008. That's 43% fewer.

![Bar chart: spans the app sent in 50 s with a 30 s outage. Batch processor: 3,008 in both runs, queue peak 172 and 162 of 1,000 requests. One-line migration with the default queue: 1,712 and 1,840, queue at 1,000 of 1,000. Migration with the queue sized in spans: 3,008 in both runs, queue peak about 2,000 of 100,000](/abhimanyu/articles/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/cover.png)

## What happened, in plain words

A span is one timed step of work in a trace. My test app sent 60 spans a second, one span per request, like many small apps talking to one gateway Collector.

When the backend goes away, the Collector parks what it can't send in a waiting line called the sending queue. By default the queue holds 1,000 requests.

- **With the batch processor**, 60 small spans a second became a few big requests. The queue peaked at 172 of 1,000 and every span got through.
- **After the one-line migration**, batching happens after the queue, so each tiny request takes its own place in line. The queue hit 1,000 of 1,000. The Collector then told my app "not now" (4 and 3 refused requests in the two runs). My test app sends each span and waits for the answer, so it waited instead of sending. It sent 1,712 and 1,840 spans, against 3,008 before.
- The Collector lost nothing it accepted. Every span it took in was sent on, and Splunk's counts match (below).

The OpenTelemetry blog warns about exactly this: after the migration, the same queue size can hold much less data.

## The fix: size the queue in spans

One more setting: measure the queue in spans instead of requests, and give it room. With `sizer: items` and `queue_size: 100000`, as the blog suggests, both runs sent all 3,008 spans through the same 30-second outage. The queue peaked at 1,960 and 2,072 spans of 100,000.

## What I took away

The migration is easy, and it's the future: batching moves into the exporter. Before you delete that one line, look at your queue. Count it in spans, and size it for your longest outage.

## The numbers

Two runs per config, 50 s of load, the backend off from 10 s to 40 s. Counts are from the gateway Collector's own metrics and from my test app's own count:

| Run | App sent | Accepted | Refused | Sent on | Queue peak |
| --- | --- | --- | --- | --- | --- |
| batch-1 | 3,008 | 3,008 | 0 | 3,008 | 172 / 1,000 requests |
| batch-2 | 3,008 | 3,008 | 0 | 3,008 | 162 / 1,000 requests |
| migrated-1 | 1,712 | 1,712 | 4 | 1,712 | 1,000 / 1,000 requests |
| migrated-2 | 1,840 | 1,840 | 3 | 1,840 | 1,000 / 1,000 requests |
| sized-1 | 3,008 | 3,008 | 0 | 3,008 | 1,960 / 100,000 spans |
| sized-2 | 3,008 | 3,008 | 0 | 3,008 | 2,072 / 100,000 spans |

Before the outage, the gateway took in about 60 spans a second in every run (60.0 to 60.6). After the backend came back, the queue was back to normal in 8 to 10 seconds in every run.

Splunk APM agreed as a second source: its span count for the app was 1,504, 1,504 and 1,504 for the batch and sized runs, and 856 and 920 for the two migrated runs. That's exactly one per trace (each trace here has two spans), in every run.

## Try it yourself

The lab runs on Amazon EKS with two t3.large nodes, the Splunk OTel Collector Helm chart 0.163.1 as the agent, and the upstream Collector (contrib 0.165.0) as the gateway. A second small Collector, the relay, sits between the gateway and the agent; scaling it to 0 is the outage. The load is telemetrygen v0.165.0.

The batch processor config (the before):

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

The load, 60 spans a second as single-span requests:

```bash
telemetrygen traces --otlp-http --otlp-endpoint=gateway.demo.svc.cluster.local:4318 --otlp-insecure \
  --batch=false --workers=4 --rate=15 --duration=50s --service=checkout-load --allow-export-failures
```

Things to plan for:

- I capped the retry interval at 5 s in all three configs so drains were comparable. The default is 30 s, so with defaults a drain can take longer.
- A queue sized in spans uses more memory. I didn't measure memory here.
- This is one load shape (many small requests) and one outage length, two runs each.
- A queue on disk (`file_storage`) also survives a Collector restart. That's a separate change I didn't test here.

Every script and the raw output of every run are in the [lab folder](https://github.com/Abhimanyu9988/showmerit/tree/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/evidence).


## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/fix.sh)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/k8s/20-load.yaml)
- [`k8s/30-gateway.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/k8s/30-gateway.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/main.tf)
- [`measure.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/measure.sh)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/reproduce.sh)
- [`splunk.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/splunk.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/after-one-collector-change-my-app-sent-43-fewer-spans-in-an/evidence): the raw output of my run
