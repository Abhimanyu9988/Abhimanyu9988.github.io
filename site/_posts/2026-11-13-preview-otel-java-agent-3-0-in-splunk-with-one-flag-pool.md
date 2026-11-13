---
title: "Preview OTel Java Agent 3.0 in Splunk With One Flag: Pool Metrics and DB Spans"
description: "One environment variable on agent 2.33.1 showed 3.0's database names in Splunk on EKS: pool metrics under db.client.connection, timings stored about 1,000 times smaller, and new span keys."
image: /assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/cover.png
image_alt: "Terminal output showing pool metrics moving from db.client.connections.* to db.client.connection.* with the preview flag, and the stored use time dropping about 1,000 times in three rounds."
tags: ["opentelemetry", "splunk", "java", "kubernetes"]
channels: []
lab: labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool
linkedin: "Agent upgrades usually mean finding out what broke after the rollout.\nFor OpenTelemetry Java agent 3.0, I looked first, with one flag.\n\nOTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true on the 2.33.1 agent shows 3.0's database names in Splunk today.\n\n→ Same jar, same image, same load\n→ A preview copy of each service runs next to the default one\n→ Splunk Observability Cloud shows both side by side\n\nWhat I measured on real EKS:\n65 pool series per service under db.client.connection, 0 under the old names\nThe same pool timing stored about 1,000 times smaller, in seconds (989.4, 1009.7 and 992.5 across three rounds)\ndb.client.operation.duration from 0 series to 112\n\nThe nice surprise: Splunk APM already shows the new span keys on 2.x spans, so new-style searches work for both.\n\nWorth a look before you change a single dashboard or threshold.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\n{LINKS}\n\nWhich of your dashboards would you check first?\n\n#OpenTelemetry #Splunk #Java #Kubernetes"
replies: []
---
![Terminal output showing pool metrics moving from db.client.connections.* to db.client.connection.* with the preview flag, and the stored use time dropping about 1,000 times in three rounds.](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/cover.png)

Every agent upgrade comes with one quiet question: will my Splunk charts still find their data? For the OpenTelemetry Java agent 3.0, you can answer it before the upgrade.

One environment variable on the 2.33.1 agent you already run shows 3.0's database names in Splunk Observability Cloud today. I ran it side by side with the default agent on EKS, same jar, same load, same minutes. **All 19 connection pool metric names moved to `db.client.connection.*`, and the same pool timing was stored about 1,000 times smaller, because it's now in seconds.**

Here's what changes, and how to see it on your own services.

> **TL;DR**
> - **What you get:** 3.0's database names in Splunk now, from `OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true`.
> - **The trick:** a second copy of the services with the flag, next to the default ones, so you compare like for like.
> - **The proof:** 65 pool series per service under the new names and 0 under the old; a stored-value ratio of 989.4, 1009.7 and 992.5 in three rounds; `db.client.operation.duration` going from 0 series to 112.
> - **Time:** about 7 minutes for the cluster, then 7 and 12 minutes for the two scripts, on two `t3.large` nodes.

## Why your dashboards deserve a preview first

The OpenTelemetry project shipped 2.32.0 as the release candidate for 3.0 and [asks users to preview it now](https://opentelemetry.io/blog/2026/java-agent-3.0-preview/). Their post lists the renames for database, code and messaging, and a pool timing unit change from milliseconds to seconds.

I wanted to see that in Splunk, on a real system. With the default agent, this is what Splunk held for two Spring Boot services on Postgres:

```text
default run, orders: pool series under db.client.connections.* 65, under db.client.connection.* 0
default run, inventory: pool series under db.client.connections.* 65, under db.client.connection.* 0
default run: db.client.operation.duration series 0
```

## The trick: one variable, and a second pair right next to the first

The preview is a single environment variable on the same agent jar. On one of your own services it's one line:

```bash
kubectl set env deployment/your-service OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true
```

To compare fairly, `fix.sh` starts `orders-v3` and `inventory-v3` as copies of the running pair, on the same node, from the same jar and image. The only other difference is `deployment.environment`, which gets a `-v3` suffix so Splunk keeps the two apart. Three rounds of the same load then hit both pairs at once.

## Build it yourself in 3 steps

### Step 1: Create the cluster (about 7 minutes)

```bash
git clone https://github.com/Abhimanyu9988/showmerit.git
cd showmerit/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-poolth-one-flag
export AWS_REGION=eu-central-1 LAB_NAME=agent3-preview TF_VAR_lab_name=agent3-preview
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Record what the default agent sends (about 7 minutes)

Run `./reproduce.sh`. It installs the Splunk OTel Collector (chart 0.163.1), builds one jar, starts `orders`, `inventory` and Postgres with agent 2.33.1 on its defaults, runs a load, and reads Splunk back: metric names, pool series per service, and every database key on one trace.

### Step 3: Start the preview pair and compare (about 12 minutes)

Run `./fix.sh`. It starts the preview pair, warms it up, then runs three 90-second rounds against both pairs together, with a quiet minute between rounds. Each round's stored mean comes from Splunk through SignalFlow, with the programs printed in the log:

```text
data('db.client.connection.use_time_sum', filter=filter('deployment.environment', 'YOUR_ENV-v3'), rollup='delta').sum().publish('s')
data('db.client.connection.use_time_count', filter=filter('deployment.environment', 'YOUR_ENV-v3'), rollup='delta').sum().publish('c')
```

![Splunk readback for the preview pair](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-1.png)

## The proof: new names, a 1,000x stored value, new span keys

**Pool metrics.** With the preview, each service had 65 pool series under `db.client.connection.*` and 0 under the old `db.client.connections.*`.

**The same timing, stored 1,000 times smaller.** Both pairs held connections for the same work in the same minutes. Splunk stored the default pair's mean in milliseconds and the preview pair's in seconds:

| Round | Default (`db.client.connections.use_time`) | Preview (`db.client.connection.use_time`) | Ratio |
| --- | --- | --- | --- |
| 1 | 3.073044 | 0.003106 | 989.4 |
| 2 | 2.829065 | 0.002802 | 1009.7 |
| 3 | 2.837665 | 0.002859 | 992.5 |

So a threshold written on the old values is worth checking before you upgrade.

**A new metric.** `db.client.operation.duration` went from 0 series to 112, with no other change.

**Span names and keys.** These are real Splunk APM screenshots from the first EKS run of this same lab. Span names move from `SELECT postgres.stock` to `select stock`, and the database node reads `postgresql:postgres|postgres`:

![Splunk APM trace waterfalls from the lab: the default agent's spans named SELECT postgres.audit and UPDATE postgres.stock on postgresql:postgres, and the preview's spans named select audit and update stock on postgresql:postgres|postgres](/abhimanyu/articles/preview-otel-java-agent-3-0-in-splunk-with-one-flag/step-1.png)

On the preview trace, Splunk showed four database keys: `db.namespace`, `db.query.summary`, `db.query.text` and `db.system.name`. `db.namespace` changes from `postgres` to `postgres|postgres`:

![Splunk APM span tags from the lab: the default span with db.statement, db.sql.table and db.name next to db.query.text and db.namespace, and the preview span with db.namespace postgres|postgres, db.query.summary, db.query.text and db.system.name](/abhimanyu/articles/preview-otel-java-agent-3-0-in-splunk-with-one-flag/step-2.png)

![Same pool timing, stored in ms vs s, per round](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-2.png)

![Database span keys in Splunk APM, default vs preview](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-3.png)

## Good news: Splunk already speaks the new keys

On spans from the default 2.x agent, Splunk APM showed new-style keys such as `db.system.name`, `db.query.text` and `db.namespace` next to the old ones, while the agent itself sent only the old keys (the free check printed the agent's own output). So searches and filters built on the new keys work for both agents today.

## Use it before your own upgrade

- **Charts on pool metrics:** move them from `db.client.connections.*` to `db.client.connection.*`.
- **Thresholds on pool timings:** the stored value is about 1,000 times smaller, so check every number you wrote on it.
- **Span searches:** prefer `db.query.text` and `db.system.name`, which Splunk showed for both agents.
- **The database node:** anything that names `postgresql:postgres` will see `postgresql:postgres|postgres` with the preview.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Code and messaging renames.** The [OpenTelemetry blog](https://opentelemetry.io/blog/2026/java-agent-3.0-preview/) lists them; this lab measured database pool metrics and database spans only.
- **Dual emission.** The blog says the preview turns off `/dup` for those domains; I didn't measure it.
- **The final 3.0.** 2.33.1's preview can still change before 3.0, as the project says.
- **Other databases, pools or agent versions.**

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/k8s/20-load.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/evidence): the raw output of my run

## Sources

- [The OpenTelemetry Java agent 3.0 is almost here: preview it today](https://opentelemetry.io/blog/2026/java-agent-3.0-preview/)
- [Release v2.33.1](https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/tag/v2.33.1)

Disclosure: I work at Splunk, part of Cisco, and contribute to OpenTelemetry. This is my personal writing; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The [lab code and raw output](https://github.com/Abhimanyu9988/showmerit/tree/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-poolth-one-flag) are public. Drafted with AI assistance and reviewed by me._
