---
title: "Get Java HTTP client spans from EKS with one annotation"
description: "Add OpenTelemetry Java auto-instrumentation to a plain-JDK Deployment on EKS without changing source or rebuilding the image. The lab proves it with Collector debug logs showing Java HTTP client GET spans and service.name=orders-java."
image: /assets/posts/trace-a-java-service-on-eks-with-zero-co/cover.png
image_alt: "Terminal output showing the inject-java annotation, the injected javaagent option, and the Collector receiving Java HTTP client spans."
tags: ["aws", "eks", "opentelemetry", "java"]
channels: ["devto", "hashnode"]
lab: labs/trace-a-java-service-on-eks-with-zero-co
linkedin: "Get Java HTTP client spans from an EKS Deployment with one annotation and no code changes\n\nI built a lab where a plain-JDK Java Deployment starts emitting OpenTelemetry HTTP client spans after adding instrumentation.opentelemetry.io/inject-java: \"true\".\n\nIt took 23.6 minutes in my recorded run, including a retry. The key idea is the OpenTelemetry Operator: it injects the Java agent with an init container and JAVA_TOOL_OPTIONS, while the Collector receives the spans.\n\nI built it in a throwaway AWS lab, and the post has every step.\n\n{LINKS}\n\n#aws #eks #opentelemetry #java"
replies: []
---
![Terminal output showing the inject-java annotation, the injected javaagent option, and the Collector receiving Java HTTP client spans.](https://abhimanyu9988.github.io/assets/posts/trace-a-java-service-on-eks-with-zero-co/cover.png)

I added one annotation to a plain Java Deployment on EKS, and Java HTTP client spans with service.name=orders-java showed up in the OpenTelemetry Collector logs. I didn't change the source or rebuild the image, and the pod restarted once. Here's the Operator, Collector and Instrumentation setup, so you can try the same pattern on a Java workload you do not own.

The useful part at work is the handoff boundary: a platform or observability team can run the Operator, Collector and Instrumentation resource, while an app team accepts a pod template annotation.

**What you'll build**

An EKS cluster where a plain-JDK Java Deployment emits HTTP client trace spans after one annotation.

My recorded lab run took 23.6 minutes, including a failed first Terraform apply and retry.

The lab README says a run costs well under 1 USD.

## How it works

The OpenTelemetry Operator watches Kubernetes workloads and mutates pods that opt in to auto-instrumentation.

For Java, the opt-in is the pod template annotation `instrumentation.opentelemetry.io/inject-java: "true"`.

The Operator adds an init container with the Java agent and sets `JAVA_TOOL_OPTIONS` so the app starts with `-javaagent`.

An `Instrumentation` resource tells the agent where to send OTLP data, which propagators to use, and which sampler to use.

In this lab, the Collector uses the `debug` exporter, so the proof is in Collector logs instead of a tracing backend.

## Build it

1. Clone the lab, add the local Terraform variables/provider, and create the EKS cluster.

The lab Terraform uses `var.lab_name` and `var.permissions_boundary`. In the lab runner those are supplied for me; in your account, add a small local `.tf` file before `terraform init`. Keep `permissions_boundary = null` if you do not use one.

```hcl
variable "region" { type = string, default = "eu-central-1" }
variable "lab_name" { type = string, default = "otel-java" }
variable "permissions_boundary" { type = string, default = null }

provider "aws" {
  region = var.region
}
```

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/trace-a-java-service-on-eks-with-zero-co
export AWS_REGION=eu-central-1 LAB_NAME=otel-java
terraform init && terraform apply -auto-approve
```

Terraform builds a VPC with public subnets, an EKS control plane, and two self-managed `t3.medium` worker nodes joined with `nodeadm`. A fresh plan creates 18 resources. The node instance profile is named `hero-lab-<lab_name>-node`, like the roles, so restrictive IAM policies that allow `hero-lab-*` names can allow it too.

2. Deploy the uninstrumented Java service.

```bash
./reproduce.sh
```

The script writes kubeconfig, waits for the two nodes, deploys the sample app, and starts a `loadgen` Deployment. The app is small on purpose: it uses `com.sun.net.httpserver`, runs on `eclipse-temurin:17.0.13_11-jdk`, serves `/orders`, and calls `/inventory` on itself with the Java HTTP client.

You should see traffic before tracing exists:

```text
== Service is serving traffic (app log + load generator log)
orders-java listening on 8080
handled 5 requests, last=/inventory
handled 10 requests, last=/orders
handled 15 requests, last=/inventory
GET /orders -> 200
GET /orders -> 200
GET /orders -> 200
```

3. Install the Operator, Collector and Instrumentation, then annotate the Deployment.

```bash
./fix.sh
```

The script pins the OpenTelemetry Operator Helm chart to `0.71.2`, uses a self-generated webhook certificate, creates a Collector, creates an `Instrumentation` resource, and patches the Deployment.

The key application change is this patch from the script:

```bash
kubectl --request-timeout=30s -n demo patch deployment orders-java --type merge \
  -p '{"spec":{"template":{"metadata":{"annotations":{"instrumentation.opentelemetry.io/inject-java":"true"}}}}}'
```

The Collector manifest receives OTLP over gRPC and HTTP, batches spans, and prints them with the debug exporter. The Instrumentation resource points Java agents at `http://otel-collector.observability.svc.cluster.local:4318`, uses `tracecontext` and `baggage`, and disables metrics and logs export for this lab.

4. Check what changed on the pod.

The rollout took 85 seconds in my run. The image stayed the same, but the pod template gained the annotation and the new pod had an init container plus injected environment variables.

```text
rollout finished in 85s
image=eclipse-temurin:17.0.13_11-jdk
annotations={"instrumentation.opentelemetry.io/inject-java":"true"}

-- init containers:
opentelemetry-auto-instrumentation-java
-- agent image:
ghcr.io/open-telemetry/opentelemetry-operator/autoinstrumentation-java:1.33.5
```

The clearest signal is `JAVA_TOOL_OPTIONS`. That is how the Java process gets the agent without changing the app command or rebuilding the image.

```text
JAVA_TOOL_OPTIONS= -javaagent:/otel-auto-instrumentation-java/javaagent.jar
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_METRICS_EXPORTER=none
OTEL_LOGS_EXPORTER=none
OTEL_SERVICE_NAME=orders-java
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector.observability.svc.cluster.local:4318
OTEL_PROPAGATORS=tracecontext,baggage
OTEL_TRACES_SAMPLER=parentbased_always_on
```

## See it work

Before the annotation, the service handled requests, but the pod had no Java agent, no OpenTelemetry environment, and no Collector namespace in the cluster:

```text
-- Tracing-related env vars in the app container (JAVA_TOOL_OPTIONS / OTEL_*):
(none)
-- Init containers on the pod:
(none)
-- Java agent mentions in app logs:
count=0
-- OpenTelemetry CRDs / collector in the cluster:
opentelemetry CRDs installed: 0
Error from server (NotFound): namespaces "observability" not found
```

After the annotation, the Collector debug output had Java HTTP client spans with Kubernetes resource attributes and `service.name` set to the Deployment name:

```text
2026-10-19T18:15:40.063Z	info	ResourceSpans #0
     -> k8s.deployment.name: Str(orders-java)
     -> k8s.namespace.name: Str(demo)
     -> k8s.pod.name: Str(orders-java-578744fdb4-lxj4j)
     -> service.name: Str(orders-java)
InstrumentationScope io.opentelemetry.java-http-client 1.33.5-alpha
Span #0
    Name           : GET
    Kind           : Client
```

The script counted 4 spans received so far, all named `GET`, and saw `service.name: Str(orders-java)`. That is the payoff: one Deployment annotation, no source change, same image, one pod restart, and spans in the Collector.

## Make it yours

- Replace the debug exporter with your real exporter, then send traces to the backend your team uses.
- Put the `Instrumentation` resource in the service namespace and tune the endpoint, propagators, sampler, and metrics/logs export settings there.
- If the Deployment name is not the service name you want, set `resource.opentelemetry.io/service.name` on the pod template.

## Clean up

```bash
./cleanup.sh && terraform destroy -auto-approve
```

The cleanup script removes the Kubernetes namespaces and Helm release before Terraform destroys the AWS resources. My destroy completed with 18 resources destroyed.

## What I didn't test

- I did not send spans to a production tracing backend; this lab proves delivery with the Collector debug exporter.
- I did not test a private-subnet EKS layout or managed node groups.
- I did not test every Java framework; the lab uses a small plain-JDK HTTP service on `eclipse-temurin:17.0.13_11-jdk`.

## Sources

- [OpenTelemetry Operator for Kubernetes](https://opentelemetry.io/docs/platforms/kubernetes/operator/)
- [OpenTelemetry Operator automatic instrumentation](https://opentelemetry.io/docs/platforms/kubernetes/operator/automatic/)

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
