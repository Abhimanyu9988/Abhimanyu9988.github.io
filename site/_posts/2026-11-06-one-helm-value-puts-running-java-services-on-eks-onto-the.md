---
title: "One Helm Value Puts Running Java Services on EKS onto the Splunk APM Map"
description: "One Helm value put two running Spring Boot services on the Splunk APM map with 0 pod restarts. A second value joined their traces across two nodes, 30 of 30."
image: /assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/cover.png
image_alt: "Cover: One Helm Value Puts Running Java Services on EKS onto the Splunk APM Map"
tags: ["opentelemetry", "splunk", "kubernetes", "ebpf"]
channels: []
lab: labs/one-helm-value-puts-running-java-services-on-eks-onto-the
linkedin: "Getting Java services onto a service map usually means a rollout.\nMine took one Helm value, and not one pod restarted.\n\nI turned on OpenTelemetry eBPF Instrumentation (OBI) through the Splunk OTel Collector chart, for services already running on EKS.\n\n→ obi.enabled=true starts OBI on every node\n→ OBI finds the services that are already running\n→ Splunk APM draws the map\n\nWhat I measured on two Spring Boot services:\n2 of 2 services sent their own spans\n0 restarts across 3 pods, same pod UIDs\np95 latency change within the run's noise\n\nA second value carries trace context between nodes. Traces holding both services went from 0 of 30 to 30 of 30.\n\nSplunk flags orders as inferred on the map, and orders also sent its own server spans. The run doesn't show why, so the post gives both facts. OBI is in beta, and the post covers privileges and its Java agent too.\n\nI built and tested it on real EKS in a throwaway AWS account, and the post has every step:\nhttps://showmerit.com/abhimanyu/articles/one-helm-value-puts-running-java-services-on-eks-onto-the/\n\nWhat would you want on a first map before asking a team to redeploy?\n\n#OpenTelemetry #Splunk #Kubernetes #Observability"
replies: [{"url": "https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/issues/3895", "text": "For anyone comparing setups: I ran OBI 0.15.2 through the Splunk OTel Collector chart 0.163.1 against two Spring Boot services on EKS. Both JVMs went from 0 Java agents loaded to 4 agent library mappings. My OBI log excerpt was read early and holds no attach line either way, so I can't say the attach finished. Those services use plain HTTP, and the map and traces came through. I didn't test TLS or reduced capabilities. I wrote up the full build here: https://showmerit.com/abhimanyu/articles/one-helm-value-puts-running-java-services-on-eks-onto-the/"}]
---
![Cover: One Helm Value Puts Running Java Services on EKS onto the Splunk APM Map](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/cover.png)

You have Java services running on EKS, and you want them on a service map today. The usual route means a planned rollout.

With eBPF, one Helm value got me there without restarting anything. **`obi.enabled=true` on the Splunk OTel Collector chart put two running Spring Boot services on the Splunk APM service map, and all three pods in the lab stayed the same pods, with 0 restarts.** A second value then joined their traces across two nodes, 30 of 30.

I ran it on real EKS on Amazon Linux 2023, in a throwaway account, and checked it pod by pod.

> **TL;DR**
> - `obi.enabled=true` starts OpenTelemetry eBPF Instrumentation (OBI) on every node, with zero code change and no pod spec edit.
> - Both services sent their own spans and reached the map, and Splunk flags `orders` as `inferred=true`.
> - A second value joins traces across nodes, from 0 of 30 to 30 of 30.
> - p95 latency stayed within the run's noise. Allow about 25 minutes on three `t3.large` nodes.

## Why a first map usually waits for a rollout

Language agents give you the deepest detail, and they're where I'd go next. They join the JVM at startup, though, so the pods have to roll.

A first map shows who calls whom before any of that.

## Turning on OBI with one chart value

[OBI is a beta feature in Splunk Observability Cloud](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-an-application-using-obi). It runs as a DaemonSet and captures traces and request metrics from outside the process. The [chart's OBI guide](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md) covers the setting, and chart 0.163.1 brought OBI 0.15.2:

```bash
helm upgrade splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --reuse-values --set obi.enabled=true
```

Nothing in the pod spec changes. For the JVMs it finds, OBI adds a small Java agent of its own, on by default, [for TLS tracing, virtual-thread correlation and runtime metrics](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.15.2/devdocs/config/CONFIG.md). `javaagent.enabled` turns it off.

## Build it yourself in 3 steps

### Step 1: Create the cluster (about 8 minutes)

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/one-helm-value-puts-running-java-services-on-eks-onto-the
export AWS_REGION=eu-central-1 LAB_NAME=obi-demo TF_VAR_lab_name=obi-demo
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Start the services with OBI off (about 7 minutes)

Run `./reproduce.sh`. It installs the Collector, starts `orders` and `inventory` on separate nodes and records each pod. It starts with nothing attached:

```text
OBI pods running: 0
orders JAVA_TOOL_OPTIONS or OTEL settings: 0
orders Java agents loaded: 0
inventory JAVA_TOOL_OPTIONS or OTEL settings: 0
inventory Java agents loaded: 0
```

### Step 3: Turn on OBI and read the results (about 11 minutes)

Run `./fix.sh`. It sets the first value, runs the timed load rounds and reads the map, then calls `propagate.sh` for the second value.

## The same pods, now on the map

This is what Splunk APM returned with only the first value on:

![Service map of load, orders flagged inferred, and inventory, above line charts of requests and latency for orders and inventory](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/step-2.png)

```text
-- service map from Splunk APM
  inventory inferred=false
  orders inferred=true
  load inferred=false
  load -> orders
  orders -> inventory
```

`load` is my test client. OBI picked it up too, and [service discovery settings](https://opentelemetry.io/docs/zero-code/obi/configure/service-discovery/) can narrow what it instruments, including by namespace.

Before OBI, after the first value and after the second, each pod kept its UID, start time and restart count. A replaced pod starts again at 0, so the UIDs matter:

![Table of the orders, inventory and postgres pods with unchanged UIDs, start times and 0 restarts](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/step-4.png)

Splunk returned server spans for both services, 5 in 5 sampled traces each, and both had a `service.request.count` series, so 2 of 2 services sent their own data.

Splunk marked `orders` as `inferred=true` in both map windows, and [it describes an inferred service](https://help.splunk.com/en/splunk-observability-cloud/monitor-application-performance/manage-services-spans-and-traces-in-splunk-apm/inferred-services-in-splunk-apm) as one it deduces from a caller's span. Those server spans show `orders` reporting for itself as well. This run doesn't show why the flag is set, so I report both facts and no cause.

## A second value joins the trace across nodes

The map drew `orders -> inventory` from the first value alone. A single trace across both takes one more step, because OBI's [outgoing propagation is off by default](https://opentelemetry.io/docs/zero-code/obi/distributed-traces/):

```bash
helm upgrade splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --reuse-values \
  --set-string obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers
```

The same pods stayed, again with 0 restarts:

```text
default case, as OBI exported it: orders traces 398, inventory traces 326, in both 0
propagation case, as OBI exported it: orders traces 331, inventory traces 314, in both 314
traces joined default 0 found 30 of 30, joined propagation 30 found 30 of 30
```

Each case read 30 traces back from Splunk 200 seconds after its round. The first value's sample stops at `orders`:

```text
sample trace from the default case: 011a317b50f3e77b5d452e479336ffe5, 2 spans in 1 segments
  orders client GET /stock/{id} span=4402cfde8c77194f parent=6877d60d833aa8ca
  orders server GET /orders/{id} span=6877d60d833aa8ca parent=none
```

With both values on, `inventory` hangs off that client call, on the other node:

![Three spans of one trace crossing from orders to inventory, above the results 0 of 30 and 30 of 30](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/step-3.png)

```text
sample trace from the propagation case: 002e06932894812bc84b7af32de7b100, 3 spans in 1 segments
  inventory server GET /stock/{id} span=aad6f72a2e2688d5 parent=de19790582fb2b09
  orders server GET /orders/{id} span=b7800369ed7c30c4 parent=e76355175e7d3f75
  orders client GET /stock/{id} span=de19790582fb2b09 parent=b7800369ed7c30c4
```

The `orders` server span names a parent outside these three spans, and this run doesn't show whose it is.

My load client sends a random `traceparent` with every request, and the first sample still shows `parent=none`, so I didn't rely on those IDs. The IDs I read back came from a Collector tap, a second pipeline that logs each exported span. It was only for measuring and went in after the timed rounds, so no latency figure includes it. Helm still showed `{"enabled":true}` under `obi`. Leave it out of your own cluster.

## What to plan for before a rollout

**Privileges.** My run printed `hostPID=true hostNetwork=true privileged=true`, the [chart's default](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md). Check that against your cluster policy. The guide also covers reducing these permissions.

**The Java agent.** Both JVMs went from 0 Java agents loaded to 4 agent library mappings, which shows the library is there and no more. The OBI log excerpt I kept was read right after OBI reported both JVMs, and it holds no attach line either way, so this run doesn't show whether the attach finished. If your services use TLS, search OBI's log for `unable to attach java agent`. If you reduce OBI's capabilities, read [this OBI issue on non-root JVMs](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/issues/3895).

**Latency.** I ran five rounds on each side, at about 20 requests a second:

```text
p95 with OBI off: min 8.43, median 12.22, max 16.01 ms
p95 with OBI on: min 9.81, median 14.80, max 15.04 ms
```

The medians differ by 2.58 ms, and the five rounds with OBI off spread across 7.58 ms on their own, so the change sits within noise. Every round finished with `errors=0`. CPU on the `orders` node read 8 to 11 percent with OBI off and 10 to 14 with it on. That's one cluster at one rate, so measure your own service.

## Use it for your own services

- **Use the first map to choose where a language agent goes first.** [Splunk recommends combining them](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-an-application-using-obi).
- **Add the second value when you need one trace across nodes.** Try it on your own network path, because [proxies and load balancers can affect propagation](https://opentelemetry.io/docs/zero-code/obi/distributed-traces/), and check async frameworks [on their own](https://opentelemetry.io/docs/zero-code/obi/context-propagation/).

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Other stacks.** This is two Spring Boot services on Java 21 over plain HTTP, on Amazon Linux 2023 with kernel 6.12. The [chart guide](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md) names Ubuntu as its tested OS, so this adds one result on Amazon Linux 2023.
- **Database spans.** Postgres wasn't on this map, and I didn't measure database spans.
- **TLS traffic, reduced capabilities or higher load.**

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/00-build.yaml)
- [`k8s/05-prepull.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/05-prepull.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/20-load.yaml)
- [`k8s/collector-tap.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/collector-tap.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/main.tf)
- [`propagate.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/propagate.sh)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/evidence): the raw output of my run

## Sources

- [Instrument with OBI (Splunk)](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-an-application-using-obi)
- [Zero-code eBPF instrumentation (Splunk chart guide)](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md)
- [Inferred services (Splunk)](https://help.splunk.com/en/splunk-observability-cloud/monitor-application-performance/manage-services-spans-and-traces-in-splunk-apm/inferred-services-in-splunk-apm)
- [OBI distributed traces](https://opentelemetry.io/docs/zero-code/obi/distributed-traces/)
- [OBI context propagation](https://opentelemetry.io/docs/zero-code/obi/context-propagation/)
- [OBI service discovery](https://opentelemetry.io/docs/zero-code/obi/configure/service-discovery/)
- [OBI 0.15.2 configuration](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/blob/v0.15.2/devdocs/config/CONFIG.md)
- [OBI issue on non-root JVMs](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/issues/3895)

Disclosure: I work at Splunk, part of Cisco. This is my personal writing; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The [lab code and raw output](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the) are public. Drafted with AI assistance and reviewed by me._
