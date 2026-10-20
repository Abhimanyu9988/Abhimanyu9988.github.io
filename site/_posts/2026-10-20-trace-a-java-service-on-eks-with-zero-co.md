---
title: "Trace a Java service on EKS with one annotation, zero code changes"
description: "Build an EKS lab where the OpenTelemetry Operator injects the Java agent into a running service. The proof is Java HTTP client spans reaching the Collector debug exporter without changing source code or rebuilding the image."
image: /assets/posts/trace-a-java-service-on-eks-with-zero-co/cover.png
image_alt: "Terminal output showing zero OpenTelemetry CRDs before, then after one annotation an 85-second rollout, the injected Java agent, and 4 spans received."
tags: ["aws", "eks", "opentelemetry", "kubernetes"]
channels: ["devto", "hashnode"]
lab: labs/trace-a-java-service-on-eks-with-zero-co
linkedin: "Java tracing often starts with a request to the app team:\n\nAdd dependencies.\nChange startup flags.\nRebuild the image.\nShip a new version.\n\nI built a small EKS lab to test the other path: OpenTelemetry Java auto-instrumentation by annotation.\n\nNo source edit.\nNo image rebuild.\nNo tracing dependency added to the app.\n\nWhat happens:\n→ EKS runs a plain Java service called orders-java.\n→ The OpenTelemetry Operator watches for one pod annotation.\n→ The next pod gets the Java agent and OTEL env vars injected.\n→ The Collector receives Java HTTP client spans over OTLP and prints them with the debug exporter.\n\nProof from the run:\nBefore: 0 OpenTelemetry CRDs, no init container, no JAVA_TOOL_OPTIONS.\nAfter: agent image autoinstrumentation-java:1.33.5 injected.\nRollout after annotation: 85 seconds.\nCollector summary: 4 GET client spans with service.name=orders-java.\n\nThis is not proof of a full multi-service trace. It is proof that the Java agent was injected and spans reached the Collector without touching the app image or source.\n\nI wrote up the full build here:\n{LINKS}\n\nWould you let platform teams add auto-instrumentation by annotation, or do you require app teams to own every tracing change?\n\n#aws #eks #opentelemetry #kubernetes"
replies: []
---
![Terminal output showing zero OpenTelemetry CRDs before, then after one annotation an 85-second rollout, the injected Java agent, and 4 spans received.](https://abhimanyu9988.github.io/assets/posts/trace-a-java-service-on-eks-with-zero-co/cover.png)

You can get trace spans out of a Java service on EKS without asking its team to add tracing code, rebuild the image, or change a line of source.

In my throwaway-account run, one annotation on the Deployment made the pod restart once, and spans showed up in the OpenTelemetry Collector's debug output with `service.name=orders-java`. The rollout after the annotation finished in **85 seconds**, and the Collector had **4 Java HTTP client spans** when the script checked.

Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** an EKS cluster with a plain Java HTTP service emitting OpenTelemetry spans through a Collector.
> - **The trick:** install the OpenTelemetry Operator, create an `Instrumentation` resource, then add `instrumentation.opentelemetry.io/inject-java: "true"` to the Deployment pod template.
> - **The proof:** before: no `JAVA_TOOL_OPTIONS`, no init container, Java agent count `0`, OpenTelemetry CRDs `0`. After: rollout finished in `85s`, Java agent `1.33.5` injected, and `4` `GET` client spans arrived with `service.name=orders-java`.
> - **Time and cost:** the README says the EKS control plane takes about 10 minutes and the lab costs well under 1 USD. In my run, `reproduce.sh` took `70s` and `fix.sh` took `194s` after the cluster existed.

## Why a healthy Java pod can still produce no spans

The usual tracing rollout starts with a request to the app team: add dependencies, set startup flags, rebuild the image, and ship a new version.

This lab starts before that point. The `orders-java` service is healthy. It runs on `eclipse-temurin:17.0.13_11-jdk`, serves `/orders`, and a `loadgen` pod curls it every 2 seconds. But there is no Java agent in the pod and no Collector in the cluster.

```text
-- Tracing-related env vars in the app container (JAVA_TOOL_OPTIONS / OTEL_*):
(none)
-- Init containers on the pod:
(none)
-- Java agent mentions in app logs:
count=0
-- OpenTelemetry CRDs / collector in the cluster:
opentelemetry CRDs installed: 0
...
```

That is the gap this build closes: not app health, but trace visibility.

## The trick: let the Operator mutate the next pod

The OpenTelemetry Operator uses a Kubernetes admission webhook. When a new pod matches the injection annotation, the webhook changes the pod spec before the container starts.

For Java, it adds an init container that places the agent in the pod, then adds `JAVA_TOOL_OPTIONS` and `OTEL_*` environment variables to the app container. The image stays the same. The source stays the same. The Deployment changes only through a pod template annotation.

The `Instrumentation` resource points the injected agent at the Collector. It also uses `tracecontext` and `baggage`, `parentbased_always_on`, OTLP HTTP/protobuf, and disables metrics and logs export.

```yaml
apiVersion: opentelemetry.io/v1alpha1
kind: Instrumentation
metadata:
  name: java-auto
  namespace: demo
spec:
  exporter:
    endpoint: http://otel-collector.observability.svc.cluster.local:4318
  sampler:
    type: parentbased_always_on
```

And this is the one app-side change the script applies:

```bash
kubectl -n demo patch deployment orders-java --type merge \
  -p '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-java":"true"}}}}}'
```

Because it changes the pod template, Kubernetes rolls the pod. That restart is expected.

## Build it yourself in 3 steps

### Step 1: Create the EKS cluster and two nodeadm workers (about 10 minutes for the control plane)

You need Terraform, the AWS CLI, `kubectl`, and Helm available locally. Start from the lab directory and export one lab name. Your cluster name will be that `LAB_NAME` value.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/trace-a-java-service-on-eks-with-zero-co
export LAB_NAME=otel-java
export TF_VAR_lab_name="$LAB_NAME"
terraform init && terraform apply -auto-approve
```

Terraform creates a VPC with public subnets, an EKS cluster, and two self-managed `t3.medium` worker nodes joined with nodeadm.

### Step 2: Deploy the Java service and prove tracing is absent (70s in my run)

This script writes kubeconfig, waits for the nodes, deploys the app and load generator, and prints the before-state evidence.

```bash
bash reproduce.sh
```

You should see requests succeed, but no tracing env vars, no init containers, no Java agent log lines, and no OpenTelemetry CRDs.

### Step 3: Install the Operator, Collector, Instrumentation, and annotation (194s in my run)

The build script installs the pinned Operator Helm chart, creates a debug-exporter Collector, creates the `Instrumentation` resource, and patches the Deployment.

```bash
bash fix.sh
```

In my run the Helm chart was `opentelemetry-operator-0.71.2`, app version `0.110.0`. The Collector service exposed OTLP on `4317` and `4318` inside the cluster.

## The proof: same image, injected agent, Java spans in Collector logs

The important part is not that a pod restarted. It is what changed and what did not change.

| Stage | Evidence |
| --- | --- |
| Before instrumentation | No `JAVA_TOOL_OPTIONS`, no init containers, Java agent count `0`, OpenTelemetry CRDs installed `0` |
| Operator installed | Helm chart `opentelemetry-operator-0.71.2`, app version `0.110.0`, pod `2/2 Running` |
| Deployment annotated | Same image, annotation added, rollout finished in `85s` |
| Pod mutated | Init container `opentelemetry-auto-instrumentation-java`, agent image `autoinstrumentation-java:1.33.5` |
| Spans received | `4` spans so far, all shown as `GET`; `service.name` seen as `orders-java` |

This is the injected pod evidence:

```text
== Injected pod: orders-java-578744fdb4-lxj4j
-- init containers:
opentelemetry-auto-instrumentation-java
-- agent image:
ghcr.io/open-telemetry/opentelemetry-operator/autoinstrumentation-java:1.33.5
-- injected env:
OTEL_NODE_IP=
OTEL_POD_IP=
JAVA_TOOL_OPTIONS= -javaagent:/otel-auto-instrumentation-java/javaagent.jar
...
```

And this is the Collector seeing Java HTTP client spans. Notice the empty `Parent ID`: this proves agent spans arrived, not a full multi-service parent/child trace.

```text
     -> service.name: Str(orders-java)
     -> telemetry.sdk.language: Str(java)
     -> telemetry.sdk.name: Str(opentelemetry)
     -> telemetry.sdk.version: Str(1.40.0)
InstrumentationScope io.opentelemetry.java-http-client 1.33.5-alpha
Span #0
    Parent ID      : 
    Name           : GET
    Kind           : Client
```

The summary line is the result I cared about:

```text
== Summary
spans received so far: 4
span names / counts:
      4 Name           : GET
service.name values seen:
      2 -> service.name: Str(orders-java)
```

## Use it for services where the platform owns the tracing path

- **Start with one namespace and one runtime.** This lab uses Java auto-instrumentation only. Create the `Instrumentation` resource in the service namespace, point it at your Collector, and annotate one Deployment first.

- **Make service naming explicit.** The lab got `OTEL_SERVICE_NAME=orders-java` from the workload. For real services, set a naming standard before spans reach your backend.

- **Treat the annotation as a rollout.** The image does not change, but the pod template does. Use normal rollout checks and capacity planning.

## Clean up (remove Kubernetes objects before AWS resources)

There is no LoadBalancer service or persistent volume in this lab, but I still remove the Kubernetes objects before destroying AWS resources.

```bash
./cleanup.sh && terraform destroy -auto-approve
```

In my run, cleanup completed and Terraform destroyed `18` resources.

## What I didn't test

- I did not run this against a Spring Boot image. The sample is a plain JDK HTTP service so the lab stays self-contained.
- I did not export traces to a vendor backend. The Collector uses the debug exporter so the proof is visible in logs.
- I did not test Node.js or .NET auto-instrumentation in this run.

## Sources

- [OpenTelemetry automatic instrumentation on Kubernetes](https://opentelemetry.io/docs/platforms/kubernetes/operator/automatic/)
- [OpenTelemetry Operator for Kubernetes](https://opentelemetry.io/docs/platforms/kubernetes/operator/)

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
