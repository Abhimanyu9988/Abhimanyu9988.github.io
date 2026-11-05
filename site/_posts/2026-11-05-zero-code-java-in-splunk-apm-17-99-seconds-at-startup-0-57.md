---
title: "Zero-Code Java in Splunk APM: 17.99 Seconds at Startup, 0.57 ms at p95"
description: "One environment variable put two Spring Boot services on a Splunk APM service map. The agent's cost landed at startup and memory, while p95 moved 0.57 ms."
image: /assets/posts/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/cover.png
image_alt: "Terminal output showing the Java agent adding about 18 seconds to startup for both services while p95 request latency rises only 0.57 ms."
tags: ["opentelemetry", "java", "kubernetes", "splunk"]
channels: ["devto", "hashnode"]
lab: labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57
linkedin: "Zero-code Java tracing in Splunk APM cost me 17.99 seconds at startup.\nIt cost 0.57 ms at p95.\n\nI attached the OpenTelemetry Java agent to two Spring Boot services and measured both sides, with the same jar, image and Collector.\n\n→ The JVM reads JAVA_TOOL_OPTIONS when it starts\n→ That one variable attached the agent\n→ Splunk APM drew the service map, including Postgres\n\nWhat appeared:\n3 services on the map\n4 spans in one order request\n19 metric names from the JVM and connection pool\n\nWhat it cost:\n17.32 to 17.99 seconds of startup\n101.6 to 112.5 MiB of memory\np95 of 17.91 ms before, 18.48 ms after\n\nSo the planning work is mostly startup probes and memory limits, not request latency.\n\nI built and tested this in a throwaway AWS account, and the post has every step:\n{LINKS}\n\nWhat do you measure before you roll an agent out to every service?\n\n#OpenTelemetry #Java #Kubernetes #Observability"
replies: []
---
![Terminal output showing the Java agent adding about 18 seconds to startup for both services while p95 request latency rises only 0.57 ms.](https://abhimanyu9988.github.io/assets/posts/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/cover.png)

One environment variable put two Spring Boot services on a Splunk APM service map, with no code change. In my run it added **17.99 seconds to startup**, **101.6 MiB of memory** and just **0.57 ms to p95**.

That split is the useful part. The agent's cost landed at startup, while each request stayed close to where it was: 17.91 ms without the agent, 18.48 ms with it.

Java teams keep asking about this. One [report in the OpenTelemetry Java repo](https://github.com/open-telemetry/opentelemetry-java-instrumentation/issues/8629) describes Spring startup going from 61.4 to 84.9 seconds on older agent versions, so I measured it on the current one, 2.33.1.

I work a lot with Java instrumentation inside Kubernetes clusters, and I wanted numbers I could hand to a service team. Here's what Splunk showed, what to plan for, and how to repeat it on your own account.

> **TL;DR**
> - **What you get:** a Splunk APM service map, request traces, and JVM and connection pool metrics from a Spring Boot service, with no code change.
> - **How:** the JVM reads `JAVA_TOOL_OPTIONS`, so one variable attaches and configures the agent.
> - **What it cost:** in one run, with two JVMs starting together on a `t3.large` at 100m CPU requests: 17.32 to 17.99 seconds of startup, 101.6 to 112.5 MiB of memory, and 0.57 ms of p95.
> - **Time:** about 8 minutes for the cluster, then about 5 and 12 for the two scripts.

## The Collector was on for both runs, so the agent is the only difference

A cost number only means something if one thing changes. I installed the Splunk OTel Collector first, measured the services with no agent, then attached it. The image and the jar stayed the same:

```text
orders JAVA_TOOL_OPTIONS or OTEL settings: 0
inventory JAVA_TOOL_OPTIONS or OTEL settings: 0
cluster metric time series in Splunk: 2098
agent metric names in Splunk before the change: 0
```

The 2098 shows the path to Splunk was working, so the zero next to it is real: the cluster was reporting, and the services weren't.

![Before: no agent settings, jar recorded, startup baseline](https://abhimanyu9988.github.io/assets/posts/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/step-1.png)

## The whole change is one environment variable

The JVM [picks up `JAVA_TOOL_OPTIONS` when it starts](https://docs.oracle.com/javase/8/docs/technotes/guides/troubleshoot/envvars002.html), so you can attach an agent without touching the image, the entrypoint or the code:

```bash
for s in orders inventory; do
  kubectl -n demo set env "deployment/$s" "JAVA_TOOL_OPTIONS=-javaagent:/lab/opentelemetry-javaagent.jar -Dotel.service.name=$s -Dotel.exporter.otlp.endpoint=http://\$(HOST_IP):4318 -Dotel.resource.attributes=deployment.environment=$LAB_NAME,deployment.environment.name=$LAB_NAME -Dotel.logs.exporter=none" >/dev/null
done
```

That value attaches the agent, names the service, points it at the Collector on the same node, and sets the environment you filter by in APM. It also turns log export off for this lab. `HOST_IP` comes from the pod's own spec.

After the rollout, the jar's hash still matched:

```text
orders jar: e9c6db24a1e342f6bae103fb67b936724fa187e3aa40aa7a04e5b61514780d01  /lab/app.jar
jar unchanged from the before run
```

## What Splunk showed with no code change

Splunk APM drew the map by itself, and that includes Postgres, which runs no agent:

```text
postgresql:postgres inferred=true
inventory inferred=false
orders inferred=false
inventory -> postgresql:postgres
orders -> inventory
```

The load generator chose its own trace ID, so I could fetch one exact request from Splunk. It held 4 spans across both services:

```text
orders null GET /orders/{id}
orders null GET
inventory null SELECT postgres.stock
inventory null GET /stock/{id}
```

The `null` is a field my script printed that this response didn't carry. The names are the useful part: route templates and the table, written by no one on the app team.

The 19 metric names were 12 from the JVM, such as `jvm.memory.used` and `jvm.gc.duration`, and 7 from the connection pool, such as `db.client.connections.usage`. The map was there at my first check, 109 seconds after traffic started, and the trace was readable by 208 seconds.

![Splunk APM service map and the 4-span trace](https://abhimanyu9988.github.io/assets/posts/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/step-2.png)

## What the agent cost, side by side

Same jar, image, nodes, Collector and load:

| Measure | Without agent | With agent | Added |
| --- | --- | --- | --- |
| `orders` startup, median | 11.107 s | 29.095 s | 17.99 s |
| `inventory` startup, median | 11.065 s | 28.382 s | 17.32 s |
| `orders` resident memory | 245812 kB | 349812 kB | 101.6 MiB |
| `inventory` resident memory | 240756 kB | 355916 kB | 112.5 MiB |
| Order request p95 | 17.91 ms | 18.48 ms | 0.57 ms |

The three startup samples ran from 10.77 to 11.521 seconds without the agent, and from 28.027 to 30.273 seconds with it. The runs served 1179 and 1196 requests with 0 errors.

![The cost computed from both runs](https://abhimanyu9988.github.io/assets/posts/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/step-3.png)

## What to plan for before you roll it out

- **Give startup room.** Check startup probes first, because that's where the time went. Both services started together on one `t3.large`, so measure your own service before you pick a value.
- **Set memory limits from a measurement.** The heap was fixed at 256 MiB, so the extra 101.6 to 112.5 MiB sits outside your `-Xmx` setting.
- **Start with the agent, add custom spans later.** It gives a team a map and traces on day one, and hand-written spans can follow for business steps. The [OpenTelemetry performance guide](https://opentelemetry.io/docs/zero-code/java/agent/performance/) covers tuning, and maintainers have a [startup PR in review](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/21033) reporting a 5.19% cut in agent-added startup on their benchmark.

## Build it yourself in 3 steps

The app is small on purpose: `orders` calls `inventory`, and `inventory` reads Postgres.

### Step 1: Create the cluster (about 8 minutes)

You need your Splunk realm, an ingest token and a read token.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57
export AWS_REGION=eu-central-1 LAB_NAME=otel-java TF_VAR_lab_name=otel-java
export SPLUNK_REALM=<realm> SPLUNK_ACCESS_TOKEN=<ingest-token> SPLUNK_API_TOKEN=<read-token>
terraform init && terraform apply
```

### Step 2: Measure with no agent (about 5 minutes)

```bash
./reproduce.sh
```

This installs the Collector, builds the jar once in the cluster, restarts each service three times to time startup, and sends one order request every 50 ms. The first 30 seconds count as warm-up.

### Step 3: Attach the agent and compare (about 12 minutes)

```bash
./fix.sh
```

This sets the variable, repeats the measurements, and reads the results back from the Splunk API.

## Clean up (the cluster and nodes cost money while they run)

```bash
./cleanup.sh
terraform destroy
```

## What I didn't test

- **Logs.** I turned log export off, because this lab sends to Observability Cloud only.
- **HTTP duration metrics.** No `http.*` names appeared in the metric list during the 565 seconds my script checked, so I haven't counted them here.
- **Other shapes.** This was one run at about 20 requests a second on burstable nodes, with the agent's default settings. Treat the numbers as this lab's result, and expect your service to differ.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/k8s/20-load.yaml)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/zero-code-java-in-splunk-apm-17-99-seconds-at-startup-0-57/evidence): the raw output of my run

## Sources

- [Metrics and attributes collected by the Splunk OTel Java agent](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-a-java-application/metrics-and-attributes)
- [OpenTelemetry Java agent performance guide](https://opentelemetry.io/docs/zero-code/java/agent/performance/)
- [Startup report in the OpenTelemetry Java repo](https://github.com/open-telemetry/opentelemetry-java-instrumentation/issues/8629)
- [Startup matching PR in the OpenTelemetry Java repo](https://github.com/open-telemetry/opentelemetry-java-instrumentation/pull/21033)
- [JAVA_TOOL_OPTIONS in the Java troubleshooting guide](https://docs.oracle.com/javase/8/docs/technotes/guides/troubleshoot/envvars002.html)

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
