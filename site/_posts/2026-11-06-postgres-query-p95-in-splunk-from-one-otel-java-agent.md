---
title: "Postgres Query p95 in Splunk From One OTel Java Agent Setting, No Code Changes (db.client."
description: "One setting took db.client.operation.duration from 0 series to 14 per statement in Splunk on EKS, with no code changes. Each series uses all 36 dimensions."
image: /assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/cover.png
image_alt: "Cover: Postgres Query p95 in Splunk From One OTel Java Agent Setting, No Code Changes (db.client.operation.duration)"
tags: ["opentelemetry", "splunk", "postgresql", "kubernetes"]
channels: []
lab: labs/postgres-query-p95-in-splunk-from-one-otel-java-agent
linkedin: "A Postgres latency chart from a Java service usually means someone adds a timer.\nMine came from one environment variable and no code changes.\n\nThe OpenTelemetry Java agent records db.client.operation.duration once you opt in.\n\n→ OTEL_SEMCONV_STABILITY_OPT_IN=database on each service\n→ Splunk Observability Cloud stores the metric per statement\n→ One SignalFlow program reads the p95\n\nWhat I measured on real EKS, on the same jar and image:\n0 series before, 14 per statement after\n6,971 lookups counted, 0.8% from what the load sent\nA known-latency statement landed in the stored 10 to 50 ms bucket, the same bucket as my reference timer\n\nThat last one matters. A histogram p95 is a bucket-bounded estimate, so the post shows how to read it. The node ran short of CPU credits, so I treat the latency figures as context.\n\nEach bucket series used all 36 of the dimensions Splunk holds per series, so the post has a planning note on counting yours.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\nhttps://showmerit.com/abhimanyu/articles/postgres-query-p95-in-splunk-from-one-otel-java-agent/\n\nHow do you track query latency per statement today?\n\n#OpenTelemetry #Splunk #Kubernetes #Observability"
replies: [{"url": "https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/16419", "text": "For anyone landing here with the same question: I ran agent 2.33.1 on EKS with OTEL_SEMCONV_STABILITY_OPT_IN=database as an environment variable, and db.client.operation.duration reached Splunk Observability Cloud with 14 series per statement, from 0 before. The system property worked too. One caveat from my run: the bucket series used all 36 dimensions Splunk holds per series on EKS, so count them if a metric seems to be absent from your backend. I wrote up the full build here: {POST_URL}"}]
---
![Cover: Postgres Query p95 in Splunk From One OTel Java Agent Setting, No Code Changes (db.client.operation.duration)](https://abhimanyu9988.github.io/assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/cover.png)

You open Splunk to see how long your Java services spend in Postgres, and you want that as a chart per statement. The metric for it is `db.client.operation.duration`, and the OpenTelemetry Java agent records it once you opt in.

One setting did it. **`OTEL_SEMCONV_STABILITY_OPT_IN=database` took the metric from 0 series to 14 per statement in Splunk Observability Cloud, on the same jar and image, with no code changes.** One SignalFlow program then placed a known-latency Postgres statement in the stored 10 to 50 ms bucket, the same bucket as my reference timer.

The run also showed something the docs don't: on EKS with chart defaults, each bucket series uses exactly 36 dimensions, the number Splunk holds per series. So you can plan your own attributes around it.

> **TL;DR**
> - One environment variable gives you query latency per statement, with no code changes.
> - A p95 from a histogram is a bucket-bounded estimate, and this post shows how to read one.
> - Splunk counted 6,971 lookups against about 6,917 sent, a 0.8% gap.
> - Allow about 7 minutes for the cluster and 20 for the scripts, on two `t3.large` nodes.

## Why the chart starts empty, by design

The agent lets a team choose when it moves to the stable database conventions. The [agent's docs](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/v2.33.1/docs/supported-libraries.md) list database client metrics for those who opt in, and [this discussion](https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/16419) shows an engineer looking for this exact metric.

With agent 2.33.1 attached and no opt-in, pool metrics arrived from the load and this metric wasn't there yet:

```text
time series for db.client.operation.duration with no opt-in: 0
connection pool metric series for the same load: 1
```

## One environment variable, on the same jar

```bash
kubectl -n demo set env deployment/orders OTEL_SEMCONV_STABILITY_OPT_IN=database
kubectl -n demo set env deployment/inventory OTEL_SEMCONV_STABILITY_OPT_IN=database
```

The system property `otel.semconv-stability.opt-in=database` works too, and the lab ran both routes.

The setting also moves span attributes and pool metrics to their stable names, so plan for that if dashboards use the old ones. With it on, Splunk held 19 metric names under `db.client.connection`, such as `db.client.connection.count`. `database/dup` emits both sets of span attributes during a move, and the lab confirmed that too.

## Build it yourself in 3 steps

### Step 1: Create the cluster (about 7 minutes)

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent
export AWS_REGION=eu-central-1 LAB_NAME=pg-p95 TF_VAR_lab_name=pg-p95
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Measure before, then the setting alone (about 9 minutes)

Run `./reproduce.sh`. It installs the Splunk OTel Collector with chart 0.163.1, builds one jar, and starts `orders`, `inventory` and Postgres with the agent and no opt-in. After a load and a read of Splunk, it sets the opt-in and reads again. It allows about two minutes for that read, and the series was already there at the first look:

```text
opt-in alone in Splunk after a read of 1s: control series 4, audit statement bucket series 10, dimensions on them 36
```

The control is `db.client.connection.count`, which exists only with the opt-in, so a miss here couldn't be mistaken for a quiet run.

### Step 3: Run the main load and read the p95 (about 11 minutes)

Run `./fix.sh`. It aims for one request every 50 ms for nine minutes, and reads the metric back through the SignalFlow stream.

## What Splunk held: 14 series per statement

The app runs an audit count in `orders`, and an update, a lookup and a known-latency statement in `inventory`. That last one is `select pg_sleep(0.03), qty from reserved where id = ?`, on every fourth request.

![Line charts of requests and inventory latency across the three load stretches of the run](https://abhimanyu9988.github.io/assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/step-2.png)

| What | Result | Read or computed |
| --- | --- | --- |
| Series before the setting | 0 | read from Splunk |
| Series per statement after | 14: 10 `_bucket`, one per stored `le`, plus `_count`, `_sum`, `_min`, `_max` | read from Splunk |
| Lookups counted over 6 minutes | 6,971 against about 6,917 sent, 0.8% apart | computed |
| Load reached | 19.1 requests a second | computed |

By the load's own per-minute counts, export timing allows 6,863 to 6,971, and Splunk's count sits at the top of that range.

## Reading a p95 inside a 10 to 50 ms bucket

This is the program as it ran, with your environment name in it:

```text
data('db.client.operation.duration_bucket', filter=filter('deployment.environment', 'YOUR_ENV') and filter('db.query.summary', 'select reserved'), rollup='delta').sum(by=['le']).histogram_percentile(pct=95, bucket_name='le').scale(1000).publish('p95_ms')
```

The stored `le` edges around this statement are 0.01 and 0.05, so 10 and 50 ms. Over 6 one-minute readings:

```text
histogram_percentile() p95: median 47.99, min 47.97, max 48.52
timer p95: median 36.33, min 32.55, max 42.17
known statement mean: 31.88 ms from Splunk's sum and count, 30.85 ms from pg_stat_statements, a gap of 3.3%
```

![Per-minute lines of the SignalFlow p95 near 48 ms and the timer p95 falling from 42 to 33 ms, both between the stored edges at 10 and 50 ms](https://abhimanyu9988.github.io/assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/step-4.png)

Both p95 readings sit in that bucket, 32.1% apart. Straight-line arithmetic on the same bucket counts gave a median of 48.00. Nearly every call lands in one wide bucket, so the reading tells you the bucket, and that's how I'd quote it. For a closer figure use the mean, from `_sum` and `_count`. It came within 3.3% of `pg_stat_statements`, though Postgres times the server only, and its figure also covers the warm-up.

The timer is test scaffolding. It wraps that one JDBC call in `inventory` so the lab has a reference, so "no code changes" stands for your services.

## Plan around 36 dimensions per series

Splunk holds [36 dimensions per series](https://help.splunk.com/en/splunk-observability-cloud/administer/org-reference-info/per-product-system-limits-in-splunk-observability-cloud/infrastructure-system-limits), and this metric used all of them:

![Bars showing the metric's bucket series at 36 dimensions, its count series at 35, and test points at 30 and 36 held while 37 and 40 were not](https://abhimanyu9988.github.io/assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/step-3.png)

My test points at 30 and 36 dimensions were held, and those at 37 and 40 weren't. So from these numbers, one more resource attribute on this setup would take the bucket series past what Splunk holds. The count includes 20 resource attributes from the agent and 8 cloud and host attributes from the Collector.

[An earlier run of this lab](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/actions/runs/42125514934) sent me counting. It waited on an HTTP metric, with one extra environment attribute set, and read 0 series for 268 seconds. In this run `http.server.request.duration_count` is stored at 36 dimensions, and `http.server.request.duration_bucket` read 0 series at all three reads, across more than ten minutes. Its `le` would likely make 37, though the run can't show that for a series Splunk didn't store. So plan for that if you want HTTP percentiles on this setup.

## Use it for your own services

- **Count before you add attributes.** The `/v2/metrictimeseries` reply lists each series' dimensions. Leave out `sf_metric`, which isn't one.
- **Keep room when you need it.** `otel.java.disabled.resource.providers` can leave out the agent's process attributes. That took the agent's resource attributes from 20 to 14 in one short run.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Latency on a steady node.** The app node's CPU credits read between 0.0 and 0.87, with 19.21% steal during the load, so treat every latency figure here as context. Pick a non-burstable node to measure your own. Counts and dimensions don't depend on it.
- **The trim under load.** I counted attributes with it on, and didn't run the main load with it.
- **Other databases, connection pools or agent versions.**

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/k8s/20-load.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/reproduce.sh)
- [`tools.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/tools.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/postgres-query-p95-in-splunk-from-one-otel-java-agent/evidence): the raw output of my run

## Sources

- [Supported libraries, agent 2.33.1](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/v2.33.1/docs/supported-libraries.md)
- [Unable to collect db.client.operation.duration](https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/16419)
- [Infrastructure system limits (Splunk)](https://help.splunk.com/en/splunk-observability-cloud/administer/org-reference-info/per-product-system-limits-in-splunk-observability-cloud/infrastructure-system-limits)
- [Get histogram data in (Splunk)](https://help.splunk.com/en/splunk-observability-cloud/manage-data/metrics-metadata-and-events/metrics-events-and-metadata/get-histogram-data-in)
- [histogram_percentile() (Splunk)](https://dev.splunk.com/observability/docs/signalflow/functions/histogram_percentile_function)

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
