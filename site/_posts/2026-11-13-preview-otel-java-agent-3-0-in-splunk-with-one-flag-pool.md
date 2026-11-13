---
title: "Preview OTel Java Agent 3.0 in Splunk With One Flag: Pool Metrics and DB Spans"
description: "A preview copy with one environment variable showed OTel Java agent 3.0's database names in Splunk on EKS: pool metrics under db.client.connection, timings stored 1,000x smaller, and db.query.text already on 2.x spans."
image: /assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/cover.png
image_alt: "Terminal output showing the 3.0 preview's pool metrics under new names with none under the old, and stored pool use time about 1,000 times smaller in all three rounds."
tags: ["opentelemetry", "splunk", "java", "kubernetes"]
channels: []
lab: labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool
linkedin: "Agent upgrades usually mean finding out what moved after the rollout.\nFor OpenTelemetry Java agent 3.0, I looked first, with one flag.\n\nOTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true on agent 2.33.1 shows 3.0's database names in Splunk today.\n\n→ A copy of each service runs with the flag, next to the original\n→ Same jar, same image, same load, same minutes\n→ Splunk Observability Cloud shows both side by side\n\nWhat I measured on real EKS:\nAll 19 pool metric names moved to db.client.connection\nThe same pool timing stored about 1,000 times smaller (989.4, 1009.7 and 992.5 over three rounds)\ndb.namespace changed from postgres to postgres|postgres\ndb.client.operation.duration from 0 series to 112\n\nHandy for today: 2.x spans in Splunk already carry db.query.text and db.system.name, with the same values the preview sends.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\n{LINKS}\n\nWhich dashboard would you check first?\n\n#OpenTelemetry #Splunk #Java #Kubernetes"
replies: []
---
![Terminal output showing the 3.0 preview's pool metrics under new names with none under the old, and stored pool use time about 1,000 times smaller in all three rounds.](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/cover.png)

I put a preview copy of two Java services next to the originals on EKS, flipped one flag on the copy, and opened Splunk Observability Cloud to see what my dashboards will meet when the OpenTelemetry Java agent 3.0 becomes the default.

The release notes list the renames. Splunk showed me two things they can't: **`db.namespace` changes value, from `postgres` to `postgres|postgres`, and spans from the default 2.33.1 agent already arrive in Splunk with `db.query.text` and `db.system.name` next to the old keys, carrying the same values the preview sends.**

The flag is `OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true`, on the same jar, image and load. You can run the same check on your own services before any upgrade.

> **TL;DR**
> - One environment variable on a copy of each service shows 3.0's database names in Splunk, while the originals keep feeding your charts.
> - Pool metrics moved to `db.client.connection.*` (65 series per service, 0 under the old names), and the same pool timing was stored about 1,000 times smaller in all three rounds.
> - `db.client.operation.duration` appeared with the preview: 0 series before, 112 after.
> - `terraform apply` took 7 minutes in my run, then about 7 and 12 minutes for the two scripts, on two `t3.large` nodes.

## Why I looked before upgrading

The OpenTelemetry project shipped 2.32.0 as the release candidate for 3.0 and [asks users to preview it now](https://opentelemetry.io/blog/2026/java-agent-3.0-preview/), with a list of renamed fields. I wanted the same list as Splunk shows it, on a running system. With the default agent, Splunk held this for two Spring Boot services on Postgres:

```text
default run, orders: pool series under db.client.connections.* 65, under db.client.connection.* 0
default run, inventory: pool series under db.client.connections.* 65, under db.client.connection.* 0
default run: db.client.operation.duration series 0
```

![Default agent: pool series under old names only, no operation.duration](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-1.png)

## One flag, on a copy rather than the live service

The preview turns off dual emission for these names, as the blog explains, so switching a live service in place would move its charts at once. `fix.sh` instead copies the running pair to `orders-v3` and `inventory-v3` on the same node, with two changes to the environment:

```yaml
env:
  - name: OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW
    value: "true"
  - name: JAVA_TOOL_OPTIONS   # same options, with a -v3 environment label
    value: "-javaagent:/lab/opentelemetry-javaagent.jar ... -Dotel.resource.attributes=deployment.environment=YOUR_ENV-v3 ..."
```

For your own service, copy its Deployment under a new name with the same two changes.

## Run it in your own account in 3 steps

### Step 1: Create the cluster (7 minutes in my run)

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool
export AWS_REGION=eu-central-1 TF_VAR_region=eu-central-1 LAB_NAME=agent3-preview TF_VAR_lab_name=agent3-preview
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Record what the default agent sends (about 7 minutes)

Run `./reproduce.sh`. It installs the Splunk OTel Collector (chart 0.163.1), builds one jar, starts `orders`, `inventory` and Postgres with agent 2.33.1 on its defaults, runs a load and reads Splunk back.

### Step 3: Start the preview copy and compare (about 12 minutes)

Run `./fix.sh`. It starts the copy, warms it up, then runs three 90-second rounds against both pairs at once. Both reached 19.89 requests a second with 0 failed requests.

## Four things that changed in Splunk

**1. Pool metric names.** `05-fix.log` lists 19 pool metric names for each mode, counting histogram parts (`_bucket`, `_count`, `_max`, `_min`, `_sum`) separately: lines 32 to 50 for the default pair and 52 to 70 for the preview pair. Most keep their suffix under the new prefix, two get new words: `db.client.connections.usage` becomes `db.client.connection.count`, and `db.client.connections.max` becomes `db.client.connection.limit`. Each service had 65 series under the new names and 0 under the old.

**2. Stored pool timings.** Both pairs held connections for the same work in the same minutes. Splunk stored the values as the agent sent them, so the default pair's mean is in milliseconds and the preview pair's in seconds:

| Round | Default `use_time` mean | Preview `use_time` mean | Ratio |
| --- | --- | --- | --- |
| 1 | 3.073044 | 0.003106 | 989.4 |
| 2 | 2.829065 | 0.002802 | 1009.7 |
| 3 | 2.837665 | 0.002859 | 992.5 |

**3. Span names, keys and the database node.** One span from each pair's trace, as Splunk APM returned them (`05-fix.log` lines 164 and 168):

```text
inventory UPDATE postgres.stock: db.collection.name=stock db.connection_string=postgresql://postgres:5432 db.name=postgres db.namespace=postgres db.operation=UPDATE db.operation.name=UPDATE db.query.text=update stock set qty = qty where id = ? db.sql.table=stock db.statement=update stock set qty = qty where id = ? db.system=postgresql db.system.name=postgresql db.user=postgres
inventory update stock: db.namespace=postgres|postgres db.query.summary=update stock db.query.text=update stock set qty = qty where id = ? db.system.name=postgresql
```

In the Splunk APM screenshots from the first EKS run of this same lab (it stopped on one Splunk read, and its traces showed the same keys), the database node also read `postgresql:postgres|postgres` instead of `postgresql:postgres`:

![Splunk APM trace waterfalls from the lab: the default agent's spans named SELECT postgres.audit and UPDATE postgres.stock on postgresql:postgres, and the preview's spans named select audit and update stock on postgresql:postgres|postgres](/abhimanyu/articles/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-1.png)

**4. A new metric.** `db.client.operation.duration` went from 0 series to 112 with the preview.

![Preview pair: new pool names, 0 under old names, operation.duration appears](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-2.png)

![Pool use_time stored per round: default in ms vs preview in s](https://abhimanyu9988.github.io/assets/posts/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-3.png)

## What 2.x spans already carry in Splunk

The agent itself sends only the old keys by default. The lab's free check prints the agent's own console output, before any Collector or Splunk is involved:

```text
-- default run: database span keys: db.connection_string db.name db.operation db.sql.table db.statement db.system db.user
-- preview run: database span keys: db.namespace db.query.summary db.query.text db.system.name
use_time unit: default ms, preview s
```

In Splunk, the same default spans also carried `db.query.text`, `db.system.name`, `db.namespace`, `db.operation.name` and `db.collection.name`. So those keys are added after the agent, by the Collector or by Splunk APM; the lab doesn't separate the two. `db.query.text` and `db.system.name` held the same values as on the preview spans, while `db.namespace` didn't. The check's last line also shows the agent declaring the pool timing unit as `ms` by default and `s` with the preview.

![Splunk APM span tags from the lab: the default span with db.statement, db.sql.table and db.name next to db.query.text and db.namespace, and the preview span with db.namespace postgres|postgres, db.query.summary, db.query.text and db.system.name](/abhimanyu/articles/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool/step-2.png)

## Before you upgrade: a short checklist

- **Pool charts during the move:** keep both names on the chart, the old `db.client.connections.*` plot scaled by 0.001 so both read in seconds (I didn't build this chart in the lab), and switch fully once every service runs the new defaults. Remember `usage` becomes `count` and `max` becomes `limit`.
- **Pool thresholds:** the stored value is about 1,000 times smaller, so check every number written on it.
- **Span searches:** `db.query.text` and `db.system.name` carried the same values on both agents' spans in Splunk, so they're the safest keys for new searches; check them on your own data first.
- **Filters on `db.namespace`:** expect `postgres|postgres` instead of `postgres`, and check anything that names the database node.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Code and messaging renames.** The [OpenTelemetry blog](https://opentelemetry.io/blog/2026/java-agent-3.0-preview/) lists them; this lab measured database pool metrics and spans only.
- **Dual emission**, which the blog says the preview turns off. I didn't measure it.
- **The final 3.0.** The preview can still change before 3.0, as the project says.
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
_I built this in a disposable AWS lab. The [lab code and raw output](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/preview-otel-java-agent-3-0-in-splunk-with-one-flag-pool) are public. Drafted with AI assistance and reviewed by me._
