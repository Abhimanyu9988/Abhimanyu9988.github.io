---
title: "Postgres Query p95 in Splunk From One OTel Java Agent Setting, No Code Changes (db.client."
description: "One setting took db.client.operation.duration from 0 series to 14 per statement in Splunk on EKS, with no code changes, and SignalFlow read the p95."
image: /assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/cover.png
image_alt: "Cover: Postgres Query p95 in Splunk From One OTel Java Agent Setting, No Code Changes (db.client.operation.duration)"
tags: ["opentelemetry", "splunk", "postgresql", "kubernetes"]
channels: []
lab: labs/postgres-query-p95-in-splunk-from-one-otel-java-agent
linkedin: "A Postgres latency chart from a Java service usually means someone adds a timer.\nMine came from one environment variable and no code changes.\n\nThe OpenTelemetry Java agent records db.client.operation.duration once you opt in.\n\n→ OTEL_SEMCONV_STABILITY_OPT_IN=database on each service\n→ Splunk Observability Cloud stores the metric per statement\n→ One SignalFlow program reads the p95\n\nWhat I measured on real EKS, on the same jar and image:\n0 series before, 14 per statement after\n6,971 lookups counted, 0.8% from what the load sent\nA p95 of 47.99 ms for a statement with a known 30 ms sleep, inside the stored 10 to 50 ms bucket\n\nThat last one is worth knowing. A histogram p95 is a bucket-bounded estimate, so the post shows how to read it.\n\nThe series used all 36 of the dimensions Splunk holds per series on EKS, so the post has a planning note on counting them.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\nhttps://showmerit.com/abhimanyu/articles/postgres-query-p95-in-splunk-from-one-otel-java-agent/\n\nHow do you track query latency per statement today?\n\n#OpenTelemetry #Splunk #Kubernetes #Observability"
replies: [{"url": "https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/16419", "text": "For anyone landing here with the same question: I ran agent 2.33.1 on EKS with OTEL_SEMCONV_STABILITY_OPT_IN=database as an environment variable, and db.client.operation.duration reached Splunk Observability Cloud with 14 series per statement, from 0 before. The system property worked too. One caveat from my run: the bucket series used all 36 dimensions Splunk holds per series on EKS, so count them if a metric seems to be absent from your backend. I wrote up the full build here: {POST_URL}"}]
---
![Cover: Postgres Query p95 in Splunk From One OTel Java Agent Setting, No Code Changes (db.client.operation.duration)](https://abhimanyu9988.github.io/assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/cover.png)

You open Splunk to see how long your Java services spend in Postgres, and you want that as a chart. The metric for it is `db.client.operation.duration`, and the OpenTelemetry Java agent records it once you opt in.

One setting did it. **`OTEL_SEMCONV_STABILITY_OPT_IN=database` took the metric from 0 series to 14 per statement in Splunk Observability Cloud, on the same jar and image, with no code changes.** One SignalFlow program then read a query p95 of 47.99 ms, as a bucket-bounded estimate.

I ran it on real EKS in a throwaway AWS account, and I checked the reading against a statement with a known sleep in it. Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** Postgres query latency per statement in Splunk, from the Java agent, with no code changes.
> - **The trick:** one environment variable, then one SignalFlow program for the p95.
> - **The proof:** 0 series before, 14 per statement after. Splunk counted 6,971 lookups against about 6,917 sent, a 0.8% gap.
> - **Time and cost:** about 7 minutes to create the cluster and 20 minutes of scripts, on two `t3.large` nodes.

## Why the chart starts empty

The agent opts you in to stable database conventions one setting at a time, so a team moves to the new names when it's ready. The [agent's docs](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/v2.33.1/docs/supported-libraries.md) list database client metrics for those who opt in. [This discussion](https://github.com/open-telemetry/opentelemetry-java-instrumentation/discussions/16419) shows an engineer looking for this exact metric.

With the agent attached and no opt-in, connection pool metrics arrive and this one doesn't, by design. Agent 2.33.1, on EKS:

```text
time series for db.client.operation.duration with no opt-in: 0
connection pool metric series for the same load: 1
```

## The trick: one environment variable

```bash
kubectl -n demo set env deployment/orders OTEL_SEMCONV_STABILITY_OPT_IN=database
kubectl -n demo set env deployment/inventory OTEL_SEMCONV_STABILITY_OPT_IN=database
```

The system property `otel.semconv-stability.opt-in=database` works too, and the lab ran both routes. The jar hash and the image stayed the same.

The setting also moves span attributes and connection pool metrics to their stable names, which is worth planning for if dashboards use the old ones. The lab showed 19 pool metric names under `db.client.connection`. `database/dup` emits both sets during a move, and the lab confirmed that too.

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

Run `./reproduce.sh`. It installs the Splunk OTel Collector with chart 0.163.1 and builds one jar. Then it starts `orders`, `inventory` and Postgres with the agent and no opt-in, runs a load, and reads Splunk. Last, it sets the opt-in and reads again:

```text
stored from the opt-in alone, series and statement:      10 select audit, 36 dimensions
stored from the opt-in alone, series and statement:      10 select stock, 36 dimensions
stored from the opt-in alone, series and statement:      10 update stock, 36 dimensions
opt-in alone in Splunk after a read of 1s: control series 4, audit statement bucket series 10, dimensions on them 36
```

### Step 3: Run the main load and read the p95 (about 11 minutes)

Run `./fix.sh`. It aims for one request every 50 ms for nine minutes, and reads the metric back through the SignalFlow stream.

## The proof: four statements, read from Splunk

The app runs an audit count in `orders`, and an update, a lookup and a known-latency statement in `inventory`. That last one is `select pg_sleep(0.03), qty from reserved where id = ?`, run on every fourth request.

![Line charts of requests and inventory latency across the three load stretches of the run](https://abhimanyu9988.github.io/assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/step-2.png)

| What | Result | How I got it |
| --- | --- | --- |
| Series before the setting | 0 | read from Splunk |
| Series per statement after | 14 | read from Splunk |
| Lookups counted | 6,971 against about 6,917 sent, 0.8% apart | computed over 6 minutes |
| Load reached | 19.1 requests a second | computed from the load's count |

The load printed what it sent each minute. An export can land on either side of a minute boundary, so those minutes allow 6,863 to 6,971, and Splunk's count sits at the top of that range.

## Reading the p95 for what it is

This is the program, with your environment name in it:

```text
data('db.client.operation.duration_bucket', filter=filter('deployment.environment', 'YOUR_ENV') and filter('db.query.summary', 'select reserved'), rollup='delta').sum(by=['le']).histogram_percentile(pct=95, bucket_name='le').scale(1000).publish('p95_ms')
```

It gave 47.99 ms for the `pg_sleep(0.03)` statement, the median of 6 one-minute readings. The stored bucket edges around it are 10 and 50 ms, and both this reading and the p95 from my reference timer fell inside that bucket. The two sit 32.1% apart. A histogram places a percentile by straight line inside its bucket, so treat this as a bucket-bounded estimate.

For a closer figure, the mean from `_sum` and `_count` came within 3.3% of the mean from `pg_stat_statements`, which Postgres records itself. Those aren't the same clock: the agent's time includes the driver and the network.

The timer is test scaffolding. It wraps that one JDBC call in `inventory` so the lab has a reference, and your services don't need it, so "no code changes" stands for yours.

## Plan for 36 dimensions per series

Splunk holds [36 dimensions per series](https://help.splunk.com/en/splunk-observability-cloud/administer/org-reference-info/per-product-system-limits-in-splunk-observability-cloud/infrastructure-system-limits), and that's room to spend with care. On EKS, this metric's bucket series used all of it:

![Bars showing the metric's bucket series at 36 dimensions, its count series at 35, and test points at 30 and 36 held while 37 and 40 were not](https://abhimanyu9988.github.io/assets/posts/postgres-query-p95-in-splunk-from-one-otel-java-agent/step-3.png)

My test points at 30 and 36 dimensions were held, and those at 37 and 40 weren't. So on this setup, from these numbers, one more resource attribute on the agent or the Collector would take this series past what Splunk holds. The Collector's 8 cloud and host attributes count toward it, and so do the 20 resource attributes from the agent.

[An earlier run of this lab](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/actions/runs/42125514934) is what sent me counting. It waited on an HTTP metric, with one extra environment attribute set, and read 0 series for 268 seconds. In this run `http.server.request.duration_count` is stored at 36 dimensions. `http.server.request.duration_bucket` read 0 series, two reads over two minutes apart, while that count series was there. Adding its `le` makes 37 by my count, so plan for that one if you want HTTP percentiles from this metric on this setup.

## Use it for your own services

- **Count before you add attributes.** The `/v2/metrictimeseries` reply lists each series' dimensions, and `sf_metric` in that list isn't one.
- **Keep room if you need it.** `otel.java.disabled.resource.providers` can leave out the agent's process attributes, which took the agent's count from 20 to 14 in one short run.
- **Move dashboards with `database/dup`.** Both sets of names report while you switch.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **A long run.** This is one run at 19.1 requests a second on burstable nodes, with Postgres and both services on one node, so read it as one cluster's result.
- **The trim under load.** I counted attributes with it on, and I didn't run the main load with it.
- **Other databases, ORMs or connection pools, or other agent versions.**

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
