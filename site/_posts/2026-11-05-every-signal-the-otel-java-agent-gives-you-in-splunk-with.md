---
title: "Every Signal the OTel Java Agent Gives You in Splunk, With Zero Code"
description: "One environment variable, same jar, same image. Here's what the OpenTelemetry Java agent put in Splunk Observability Cloud, and the measured cost in startup, memory and p95."
image: /assets/posts/every-signal-the-otel-java-agent-gives-you-in-splunk-with/cover.png
image_alt: "Terminal output showing zero agent metric names in Splunk before the change, then 3 services, 19 metric names and 4 spans per request, with the jar unchanged."
tags: ["opentelemetry", "java", "kubernetes", "splunk"]
channels: ["devto", "hashnode"]
lab: labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with
linkedin: "Adding tracing to a Java service usually starts with a code change.\nMine took one environment variable. Same jar, same image.\n\nI wanted real answers to the two questions every Java team asks: what do I get, and what will it cost?\n\n→ The JVM reads JAVA_TOOL_OPTIONS at startup\n→ That one variable attached the OpenTelemetry Java agent\n→ Splunk Observability Cloud drew the rest\n\nWhat appeared:\n3 services on the map, including Postgres\n4 spans in one order request\n19 metric names from the JVM and connection pool\n\nWhat it cost:\nAbout 18 seconds of extra startup\n92 to 94 MiB of extra memory\np95 of 18.80 ms before, 18.64 ms after\n\nNow a platform team can show a service team its traces first, and answer the cost question with numbers.\n\nI built and tested this in a throwaway AWS account, and the post has every step:\n{LINKS}\n\nWhat do you measure before you roll an agent out to every service?\n\n#OpenTelemetry #Java #Kubernetes #Observability"
replies: []
---
![Terminal output showing zero agent metric names in Splunk before the change, then 3 services, 19 metric names and 4 spans per request, with the jar unchanged.](https://abhimanyu9988.github.io/assets/posts/every-signal-the-otel-java-agent-gives-you-in-splunk-with/cover.png)

Every Java team asks two questions before an agent goes near their service: what do I get, and what will it cost me?

I attached the OpenTelemetry Java agent to two Spring Boot services with **one environment variable per service**. Same jar, same image. Splunk Observability Cloud then showed **3 services on the map, 4 spans per request and 19 metric names**, and p95 latency stayed level: 18.80 ms before, 18.64 ms after.

I work a lot with Java instrumentation inside Kubernetes clusters, so I wanted real numbers for both questions, not a feature list.

Here's exactly what appeared, what it cost, and how to build it yourself.

> **TL;DR**
> - **What you get:** a Splunk APM service map, full request traces, and JVM and connection pool metrics from a Spring Boot service with no code change.
> - **The trick:** the JVM reads `JAVA_TOOL_OPTIONS` at startup, so one variable can attach the agent and configure it.
> - **The proof:** 3 services on the map, 4 spans in one order request, 19 metric names, and the jar's SHA-256 unchanged.
> - **Time and cost:** about 12 minutes for the cluster, then about 11 for the two scripts. The agent added 18.17 and 18.32 seconds of startup, and 94.4 and 91.8 MiB of memory.

## Why a healthy service can still be invisible

Before the change, both services answered every request. The Collector was already running, and Splunk was receiving the cluster's own data. The services themselves sent nothing:

```text
orders JAVA_TOOL_OPTIONS or OTEL settings: 0
inventory JAVA_TOOL_OPTIONS or OTEL settings: 0
cluster metric time series in Splunk: 801
agent metric names in Splunk before the change: 0
```

That 801 matters. It shows the path to Splunk works, so the zero next to it means something: no agent, no application telemetry.

![Before the change: cluster data flows, services send nothing](https://abhimanyu9988.github.io/assets/posts/every-signal-the-otel-java-agent-gives-you-in-splunk-with/step-1.png)

## The trick: one variable the JVM already reads

The JVM picks up `JAVA_TOOL_OPTIONS` by itself when it starts. That means you can attach an agent without touching the image, the entrypoint or the code:

```bash
for s in orders inventory; do
  kubectl -n demo set env "deployment/$s" "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$s -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$LAB_NAME,deployment.environment.name=$LAB_NAME -Dotel.logs.exporter=none" >/dev/null
done
```

The value does five jobs. It attaches the agent, names the service, points it at the Collector on the same node, sets the environment you'll filter by in APM, and turns log export off for this lab. `HOST_IP` comes from the pod's own spec.

After the rollout, the script compares the jar with the one from the before run:

```text
orders image: maven:3.9.9-eclipse-temurin-21
orders jar: c0e52e094a8b1e0129eb679a22dc9c84c848346d35d4b5e4cfd61fd00c7cdb97  /lab/app.jar
jar unchanged from the before run
```

## Build it yourself in 3 steps

The app is small on purpose: `orders` calls `inventory`, and `inventory` reads Postgres.

### Step 1: Create the cluster (about 12 minutes)

You need your Splunk realm, an ingest token and a read token.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with
export AWS_REGION=eu-central-1 LAB_NAME=otel-java TF_VAR_lab_name=otel-java
export SPLUNK_REALM=<realm> SPLUNK_ACCESS_TOKEN=<ingest-token> SPLUNK_API_TOKEN=<read-token>
terraform init && terraform apply
```

### Step 2: Measure the service with no agent (about 5 minutes)

```bash
./reproduce.sh
```

This builds the jar once inside the cluster, runs both services, restarts them three times to time startup, and sends one order request every 50 ms. The first 30 seconds count as warm-up.

### Step 3: Install the Collector and attach the agent (about 6 minutes)

```bash
./fix.sh
```

The script installs the Collector with this chart, sets the variable, repeats the same measurements, and reads the results back from the Splunk API:

```bash
helm upgrade --install splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --create-namespace \
  --set splunkObservability.realm="$SPLUNK_REALM" \
  --set splunkObservability.accessToken="$SPLUNK_ACCESS_TOKEN" \
  --set clusterName="$LAB_NAME" \
  --set cloudProvider=aws \
  --set distribution=eks
```

## The proof: what appeared, and what it cost

Splunk APM drew the map by itself, including Postgres, which runs no agent:

```text
postgresql:postgres inferred=true
orders inferred=false
inventory inferred=false
inventory -> postgresql:postgres
orders -> inventory
```

The load generator chose its own trace ID, so I could fetch one exact request from Splunk. It held 4 spans across both services:

```text
inventory null SELECT postgres.stock
inventory null GET /stock/{id}
orders null GET
orders null GET /orders/{id}
```

The `null` is the kind field my script printed, which this response didn't carry. The operation names are the useful part: route templates and the database table, written by no one on the app team.

The 19 metric names were 12 from the JVM, such as `jvm.memory.used`, `jvm.gc.duration` and `jvm.thread.count`, and 7 from the connection pool, such as `db.client.connections.usage`. The map was there at the first check, 107 seconds after traffic started, and the trace was readable by 206 seconds.

Here's the cost, on the same jar, image, nodes and load:

| Measure | Without agent | With agent | Added |
| --- | --- | --- | --- |
| `orders` startup | 11.534 s | 29.701 s | 18.17 s |
| `inventory` startup | 11.739 s | 30.055 s | 18.32 s |
| `orders` resident memory | 250296 kB | 346944 kB | 94.4 MiB |
| `inventory` resident memory | 247548 kB | 341500 kB | 91.8 MiB |
| Order request p95 | 18.80 ms | 18.64 ms | -0.16 ms |

The p95 difference is too small to read anything into at this rate. The runs served 1,192 and 1,196 requests with 0 errors.

![Splunk APM service map and one 4-span trace](https://abhimanyu9988.github.io/assets/posts/every-signal-the-otel-java-agent-gives-you-in-splunk-with/step-2.png)

![Startup, memory and p95 cost of the agent](https://abhimanyu9988.github.io/assets/posts/every-signal-the-otel-java-agent-gives-you-in-splunk-with/step-3.png)

## Use it for your own services

- **Plan for startup first.** Startup was the biggest cost here. Both services started together on one `t3.large`, so treat 18 seconds as this lab's figure, and give your startup probes room before you roll out.
- **Raise memory limits on purpose.** The heap was fixed at 256 MiB, so the added 92 to 94 MiB sits outside your `-Xmx` setting. Measure your own service, then set limits from that number.
- **Start with the agent, then add custom spans where they earn it.** The agent gives a team a map and traces on day one. Hand-written spans can follow for business steps it can't know about. The [OpenTelemetry performance guide](https://opentelemetry.io/docs/zero-code/java/agent/performance/) covers how to reduce overhead.

## Clean up (the cluster and nodes cost money while they run)

```bash
./cleanup.sh
terraform destroy
```

## What I didn't test

- **Logs.** I turned log export off, because this lab sends to Observability Cloud only, so "every signal" here means traces, the service map and metrics.
- **HTTP duration metrics.** No `http.*` names were in the list when the script stopped checking at 206 seconds, so I haven't counted them.
- **Heavier load or tuned settings.** This was one run at about 20 requests a second on burstable nodes, with the agent's default settings.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/k8s/20-load.yaml)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/every-signal-the-otel-java-agent-gives-you-in-splunk-with/evidence): the raw output of my run

## Sources

- [Metrics and attributes collected by the Splunk OTel Java agent](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-a-java-application/metrics-and-attributes)
- [OpenTelemetry Java agent performance guide](https://opentelemetry.io/docs/zero-code/java/agent/performance/)

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
