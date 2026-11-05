---
title: "One Helm Value Puts Running Java Services on EKS onto the Splunk APM Map"
description: "One Helm value put two running Spring Boot services on the Splunk APM map with 0 pod restarts. With a second value, traces held both services across two nodes."
image: /assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/cover.png
image_alt: "Terminal output showing zero services on the Splunk APM map before the change, two after one Helm value, zero pod restarts, and 39 of 39 traces joined with the second value."
tags: ["opentelemetry", "splunk", "kubernetes", "ebpf"]
channels: []
lab: labs/one-helm-value-puts-running-java-services-on-eks-onto-the
linkedin: "Getting Java services onto a service map usually means a rollout.\nMine took one Helm value, and not one pod restarted.\n\nI turned on OpenTelemetry eBPF Instrumentation (OBI) through the Splunk OTel Collector chart, for services already running on EKS.\n\n→ obi.enabled=true starts OBI on every node\n→ OBI finds the services that are already running\n→ Splunk APM draws the map\n\nWhat I measured on two Spring Boot services:\n2 of 2 services on the map\n0 restarts across 3 pods, same pod UIDs\norders to inventory drawn on the map\n\nA second value turned on trace propagation. 39 of 39 traces found in Splunk then held both services, across two nodes.\n\nSo a platform team can show a first map before asking anyone to redeploy. OBI is in beta, and the post covers what to plan for, including privileges, its Java helper and what I didn't test.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\nhttps://showmerit.com/abhimanyu/articles/one-helm-value-puts-running-java-services-on-eks-onto-the/\n\nWhat would you want on a first map before asking a team to redeploy?\n\n#OpenTelemetry #Splunk #Kubernetes #Observability"
replies: [{"url": "https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/issues/3895", "text": "For anyone comparing setups: I ran OBI 0.15.2 through the Splunk OTel Collector chart 0.163.1, which runs it privileged by default, against two Spring Boot services on EKS. Both JVMs showed the Java agent library mapped afterwards, and OBI logged one attach timeout, java attach timed out. Those services use plain HTTP, so map and traces came through. I didn't test TLS or reduced capabilities. I wrote up the full build here: https://showmerit.com/abhimanyu/articles/one-helm-value-puts-running-java-services-on-eks-onto-the/"}]
---
![Terminal output showing zero services on the Splunk APM map before the change, two after one Helm value, zero pod restarts, and 39 of 39 traces joined with the second value.](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/cover.png)

You have Java services already running on EKS, and you want them on a service map today. That usually starts with a conversation: a team adds instrumentation, plans a rollout, and you wait.

One value on the Splunk OTel Collector chart let me skip that step. **Two running Spring Boot services appeared on the Splunk APM map, and all three pods in the lab stayed the same pods, with 0 restarts.** Splunk drew the line from `orders` to `inventory` too.

I work a lot with the OpenTelemetry Collector and Java instrumentation, and I reproduce things in a throwaway AWS account before I write about them. Here's what one value gives you, what a second value adds, and how to build it yourself.

> **TL;DR**
> - **What you get:** running Java services on the Splunk APM map, with no change to the pod spec.
> - **The trick:** `obi.enabled=true` starts OpenTelemetry eBPF Instrumentation (OBI) on every node.
> - **The proof:** 2 of 2 services on the map and 0 restarts across 3 pods. With a second value, 39 of 39 traces found in Splunk held both services, across two nodes.
> - **Time:** about 28 minutes for the whole run.

## Why a first map usually waits for a rollout

A language agent gives you the deepest detail, and it's where I'd go next. It joins the JVM at startup, though, so the pods have to roll, and that needs a team's time.

So I started from services with nothing attached, and OBI off:

```text
OBI pods running: 0
orders JAVA_TOOL_OPTIONS or OTEL settings: 0
orders Java agents loaded: 0
inventory JAVA_TOOL_OPTIONS or OTEL settings: 0
inventory Java agents loaded: 0
```

The Collector was already sending cluster metrics, and the map had neither service on it.

![The JVMs before OBI: nothing attached](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/step-1.png)

## The trick: one value, and OBI finds what's already running

[OBI is a beta feature in Splunk Observability Cloud](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-an-application-using-obi). It runs as a DaemonSet and captures traces and request metrics from running workloads, with no code change or restart. The [chart turns it on with one value](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md):

```bash
helm upgrade splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --reuse-values --set obi.enabled=true
```

That's the whole change. Nothing in the `demo` namespace was edited, and chart 0.163.1 brought OBI 0.15.2.

## The proof: 2 services, 0 restarts, the same pods

These are the pods before OBI. After the first value and again after the second, the UIDs, start times and restart counts were identical:

```text
inventory  ca653889-b1a6-4447-936a-93d5e12206a2  0  2026-11-05T22:26:50Z
orders     f0c67713-a216-4a19-a520-9d124ff13fd2  0  2026-11-05T22:26:49Z
postgres   d2e39469-51ee-4d5a-ad27-b8f3ed5abfc2  0  2026-11-05T22:26:55Z
```

I compared UIDs as well as restart counts, because a replaced pod starts again at 0.

And this is the map Splunk APM returned with only the first value on:

```text
-- service map from Splunk APM
  inventory inferred=false
  orders inferred=false
  load inferred=false
  load -> orders
  orders -> inventory
```

`load` is my test client. OBI covers every namespace by default, so it appeared as well.

![Service map from Splunk APM, and restarts added by the first value](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/step-2.png)

## The second value: one trace across two nodes

`orders` and `inventory` ran on different nodes on purpose, so a joined trace had to cross the network. OBI's [outgoing propagation is off by default](https://opentelemetry.io/docs/zero-code/obi/distributed-traces/), and this value turns it on:

```bash
helm upgrade splunk-otel-collector splunk-otel-collector-chart/splunk-otel-collector \
  --version 0.163.1 --namespace splunk --reuse-values \
  --set-string obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers
```

The same pods carried on, again with 0 restarts. Of 48 sampled trace IDs, Splunk returned 39, and **all 39 held both services**. Here's one:

```text
sample trace from the propagation case: 07515449a2bc2d3973c9a54e05c5a3c3
  orders null GET /orders/{id} span=6d8b9deb02b22f67 parent=27bbfc90c12b0320
  orders null GET /stock/{id} span=abb1276d3baad92f parent=6d8b9deb02b22f67
  load null GET /orders/* span=27bbfc90c12b0320 parent=none
  inventory null GET /stock/{id} span=967bac47e7062f91 parent=abb1276d3baad92f
```

Follow the parents and you get `load`, `orders`, then `inventory`, one chain across two nodes.

With the first value alone, none of the 56 sampled IDs came back inside the same 90-second wait. That's unmeasured. It doesn't tell you those traces were separate, so I can show you what both values give you, but not a before and after.

![One joined trace: load, orders, inventory](https://abhimanyu9988.github.io/assets/posts/one-helm-value-puts-running-java-services-on-eks-onto-the/step-3.png)

## What to plan for before you roll it out

**Privileges.** The chart runs OBI [privileged, with host PID and host network](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md). Check that against your cluster policy. The chart guide lists a reduced set of capabilities too.

**OBI's Java helper.** You add nothing to the pod spec, and OBI handles its own helper for the JVMs it finds. Both JVMs went from 0 Java agents loaded to 4 agent library mappings. OBI's log ties this helper to Java TLS telemetry, and it logged at least one attach that didn't finish:

```text
level=ERROR msg="couldn't attach OpenTelemetry eBPF Java Agent" ...
level=WARN msg="unable to attach java agent to process, Java TLS telemetry will not work" ... error="java attach timed out"
```

These services speak plain HTTP, and the map and the joined traces above came through anyway. If yours use TLS, look for this line. If you reduce OBI's capabilities, read [this OBI issue on non-root JVMs](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/issues/3895).

**Latency.** I ran three load rounds on each side, at one request every 50 ms:

```text
p95 with OBI off: min 11.66, median 13.93, max 15.52 ms
p95 with OBI on: min 11.21, median 12.87, max 15.82 ms
```

The median moved by -1.06 ms and the ranges overlap, so I read that as no visible change at this load, not a speed-up.

## Build it yourself in 3 steps

Set your own realm and tokens. The ingest token sends data, and the API token reads it back.

### Step 1: Create the cluster (about 8 minutes)

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/one-helm-value-puts-running-java-services-on-eks-onto-the
export AWS_REGION=eu-central-1 LAB_NAME=obi-demo TF_VAR_lab_name=obi-demo
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Start the services with OBI off (about 4 minutes)

Run `./reproduce.sh`. It installs the Collector, builds the jar, starts the services and records each pod.

### Step 3: Turn on OBI and read the results (about 12 minutes)

Run `./fix.sh`. It measures latency with OBI off, sets the first value and reads the map. It then runs `propagate.sh` for the second value, and prints everything above.

## Use it for your own services

- **Start with one cluster and a clear question.** A first map shows who calls whom. That's often enough to choose which teams to talk to next.
- **Add the second value when you want joined traces.** Test it on your own network path, because [proxies and load balancers can affect propagation](https://opentelemetry.io/docs/zero-code/obi/distributed-traces/). Async frameworks need [their own check](https://opentelemetry.io/docs/zero-code/obi/context-propagation/) too.
- **Keep language agents for depth.** [Splunk recommends combining them](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-an-application-using-obi), and OBI skips processes that already have OpenTelemetry instrumentation.

## Clean up (the cluster and nodes cost money while they run)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Other stacks.** This is two Spring Boot services on Java 21 over plain HTTP, on three `t3.large` nodes running Amazon Linux 2023. The [chart guide](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md) names Ubuntu as its tested OS, so this adds one measured result on Amazon Linux 2023.
- **Database spans.** I didn't measure them, and Postgres wasn't on this map.
- **Traces with the first value alone**, as explained above, or anything at higher load.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/k8s/20-load.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/main.tf)
- [`propagate.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/propagate.sh)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the/evidence): the raw output of my run

## Sources

- [Instrument an application using OBI (Splunk)](https://help.splunk.com/en/splunk-observability-cloud/manage-data/instrument-back-end-services/instrument-back-end-applications-to-send-spans-to-splunk-apm/instrument-an-application-using-obi)
- [Zero-Code Instrumentation with eBPF (Splunk OTel Collector chart)](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/docs/zero-code-ebpf-instrumentation.md)
- [Distributed traces with OBI (OpenTelemetry)](https://opentelemetry.io/docs/zero-code/obi/distributed-traces/)
- [Trace context association in OBI (OpenTelemetry)](https://opentelemetry.io/docs/zero-code/obi/context-propagation/)
- [Add the OBI DaemonSet (Splunk workshop)](https://splunk.github.io/observability-workshop/en/ninja-workshops/instrumentation/16-obi-ebpf/5-kubernetes/3-add-obi-daemonset/)
- [Java agent injection into non-root JVMs (OBI issue)](https://github.com/open-telemetry/opentelemetry-ebpf-instrumentation/issues/3895)

Disclosure: I work at Splunk, part of Cisco. This is my personal writing; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The [lab code and raw output](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/one-helm-value-puts-running-java-services-on-eks-onto-the) are public. Drafted with AI assistance and reviewed by me._
