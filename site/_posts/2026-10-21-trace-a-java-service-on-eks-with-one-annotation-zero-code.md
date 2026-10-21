---
title: "Trace a Java service on EKS with one annotation, zero code changes"
description: "Build an EKS lab where a plain Java service starts emitting OpenTelemetry traces after one Deployment annotation. No source edits, no image rebuild, and the Collector logs prove service.name=orders-java."
image: /assets/posts/trace-a-java-service-on-eks-with-one-annotation-zero-code/cover.png
image_alt: "Terminal output showing zero OpenTelemetry CRDs before, then a single inject-java annotation, the Java agent picked up, and spans arriving with service.name orders-java."
tags: ["aws", "eks", "opentelemetry", "java"]
channels: ["devto", "hashnode"]
lab: labs/trace-a-java-service-on-eks-with-one-annotation-zero-code
linkedin: "Getting traces out of a Java service usually means a ticket to the app team.\nI did it with one annotation. No code change, no image rebuild.\n\nI built a small EKS lab for Java auto-instrumentation with OpenTelemetry.\n\nThe app started as a plain Java HTTP service.\nIt served traffic, but had no JAVA_TOOL_OPTIONS, no OTEL env vars, no init container, and no Collector.\n\n→ Install the OpenTelemetry Operator.\n→ Create an Instrumentation resource in the app namespace.\n→ Run a Collector with an OTLP receiver.\n→ Add one pod-template annotation: inject-java=true.\n\nThe proof from the run:\nSame image before and after: eclipse-temurin:17.0.13_11-jdk.\nThe instrumented rollout finished in 85 seconds.\nThe injected agent image was autoinstrumentation-java:1.33.5.\nThe Collector received 4 Java HTTP client spans with service.name=orders-java.\n\nOne honest detail: this run captured the outbound /orders → /inventory client spans, not inbound server spans for /orders.\n\nThis matters when the platform team can add tracing without asking every app team to change code or rebuild images.\n\nI built and tested it in a throwaway AWS account, and the post has every step:\n{LINKS}\n\nHow are you handling zero-code tracing for services your team does not own?\n\n#aws #eks #opentelemetry #java"
replies: []
---
![Terminal output showing zero OpenTelemetry CRDs before, then a single inject-java annotation, the Java agent picked up, and spans arriving with service.name orders-java.](https://abhimanyu9988.github.io/assets/posts/trace-a-java-service-on-eks-with-one-annotation-zero-code/cover.png)

Getting traces out of a Java service usually means a ticket to the app team; in this build, one EKS deployment annotation injects the OpenTelemetry Java agent with no code change and no image rebuild. I’ll start with a plain Java service that has no tracing, then add the Operator, an Instrumentation resource, and a Collector until spans show up with service.name=orders-java.

The same image stays in place: `eclipse-temurin:17.0.13_11-jdk`. In my run, the instrumented rollout finished in **85 seconds**.

I work a lot with observability agents inside Kubernetes clusters, so I want this kind of change proven from cluster output, not only from a diagram.

Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** an EKS cluster where a plain Java service emits OpenTelemetry traces without source edits or an image rebuild.
> - **The trick:** the OpenTelemetry Operator watches one pod-template annotation and injects the Java agent plus OTEL environment variables.
> - **The proof:** same image before and after, rollout finished in 85s, Collector received 4 Java client spans with `service.name: Str(orders-java)`.
> - **Time and cost:** my reproduced run took 23.6 minutes total including cluster creation; the README says the EKS control plane takes about 10 minutes and the run costs well under 1 USD.

## Why a healthy Java pod can still produce no traces

A service can serve traffic and still be invisible to tracing. In the baseline run, `/orders` returned HTTP 200s, but there was no Java agent, no OTEL environment, no init container, and no Collector.

```text
== Evidence of the gap
-- Tracing-related env vars in the app container (JAVA_TOOL_OPTIONS / OTEL_*):
(none)
-- Init containers on the pod:
(none)
-- Java agent mentions in app logs:
count=0
-- OpenTelemetry CRDs / collector in the cluster:
opentelemetry CRDs installed: 0
```

That is the platform-team problem: the app is up, but nothing is creating spans and nothing is receiving them.

## The trick: mutate the pod template, not the Java code

The OpenTelemetry Operator adds a Kubernetes `Instrumentation` resource. That resource tells the injected agent where to export traces and which defaults to use.

Then this annotation on the Deployment pod template turns on Java auto-instrumentation:

`instrumentation.opentelemetry.io/inject-java: "true"`

On rollout, the Operator adds an init container, copies the Java agent into the pod, sets `JAVA_TOOL_OPTIONS`, sets the OTLP endpoint, and gives the service a name. In this lab, that name is `orders-java`.

## Build it yourself in 5 steps

### Step 1: Create the EKS lab cluster (about 10 minutes for the control plane)

This creates a VPC, an EKS cluster, and two self-managed `t3.medium` worker nodes joined with `nodeadm`.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code
export AWS_REGION=eu-central-1 LAB_NAME=otel-java TF_VAR_lab_name=otel-java
terraform init && terraform apply -auto-approve
./reproduce.sh
```

`reproduce.sh` waits for nodes, applies `k8s/app.yaml`, starts the load generator, and prints the before-state evidence.

If your clone only has `main.tf`, add a small Terraform file with the variable declarations and provider wiring the README calls out. Set `permissions_boundary` to `null` if you do not use one.

```hcl
variable "region" {
  type    = string
  default = "eu-central-1"
}

variable "lab_name" {
  type = string
}

variable "permissions_boundary" {
  type    = string
  default = null
}

provider "aws" {
  region = var.region
}
```

### Step 2: Keep the app Deployment plain on purpose

The sample app lives in `k8s/app.yaml`. It mounts Java source from a ConfigMap and runs it with the JDK image. For your own service, the annotation still belongs under `spec.template.metadata.annotations`.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: orders-java
  namespace: demo
spec:
  replicas: 1
  selector:
    matchLabels:
      app: orders-java
  template:
    metadata:
      labels:
        app: orders-java
    spec:
      containers:
        - name: app
          image: eclipse-temurin:17.0.13_11-jdk
          command: ["java", "-Xmx256m", "/app/Main.java"]
          ports:
            - containerPort: 8080
          readinessProbe:
            tcpSocket:
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 5
          resources:
            requests:
              cpu: 200m
              memory: 384Mi
            limits:
              memory: 768Mi
          volumeMounts:
            - name: src
              mountPath: /app
      volumes:
        - name: src
          configMap:
            name: orders-java-src
```

Change the image, command, ports, and probes for your service. Keep the pod-template location for the annotation.

### Step 3: Add a Collector that proves spans in logs

The Collector manifest is `k8s/collector.yaml`. It uses the debug exporter so the proof stays inside Kubernetes logs instead of depending on a tracing backend.

```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: observability
---
apiVersion: opentelemetry.io/v1beta1
kind: OpenTelemetryCollector
metadata:
  name: otel
  namespace: observability
spec:
  mode: deployment
  replicas: 1
  config:
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318
    processors:
      batch: {}
    exporters:
      debug:
        verbosity: detailed
    service:
      pipelines:
        traces:
          receivers: [otlp]
          processors: [batch]
          exporters: [debug]
```

In a real cluster, keep the OTLP receiver and replace the debug exporter with your tracing backend exporter.

### Step 4: Point Java auto-instrumentation at the Collector

The Instrumentation resource is `k8s/instrumentation.yaml`. It points Java agents in the `demo` namespace at the in-cluster Collector service and turns off metrics and logs for this tracing-only lab.

```yaml
apiVersion: opentelemetry.io/v1alpha1
kind: Instrumentation
metadata:
  name: java-auto
  namespace: demo
spec:
  exporter:
    endpoint: http://otel-collector.observability.svc.cluster.local:4318
  propagators:
    - tracecontext
    - baggage
  sampler:
    type: parentbased_always_on
  env:
    - name: OTEL_EXPORTER_OTLP_PROTOCOL
      value: http/protobuf
    - name: OTEL_METRICS_EXPORTER
      value: none
    - name: OTEL_LOGS_EXPORTER
      value: none
```

Change the endpoint if your Collector runs in a different namespace or you send directly to an external endpoint.

### Step 5: Install the Operator and add the one annotation

`fix.sh` installs the pinned Operator Helm chart `0.71.2`, applies `k8s/collector.yaml` and `k8s/instrumentation.yaml`, patches the Deployment, waits for rollout, and prints Collector logs.

```bash
./fix.sh
kubectl -n observability logs deploy/otel-collector --tail=-1
```

The patch it applies is the only app-side change: `instrumentation.opentelemetry.io/inject-java: "true"`.

## The proof: same image, injected agent, Collector received spans

| Stage | What changed | Evidence |
|---|---|---|
| Before | App deployed with no tracing | no `JAVA_TOOL_OPTIONS`, no init containers, OpenTelemetry CRDs installed: 0 |
| Operator | OpenTelemetry Operator installed | chart `opentelemetry-operator-0.71.2`, app version `0.110.0` |
| Collector | OTLP receiver created | service exposed `4317/TCP,4318/TCP` inside the cluster |
| Annotation | Deployment patched | rollout finished in 85s, image still `eclipse-temurin:17.0.13_11-jdk` |
| Trace proof | Java agent exported spans | 4 spans received, `service.name: Str(orders-java)` |

After rollout, the new pod showed the auto-instrumentation init container and Java agent startup line:

```text
== Injected pod: orders-java-578744fdb4-lxj4j
-- init containers:
opentelemetry-auto-instrumentation-java
-- agent image:
ghcr.io/open-telemetry/opentelemetry-operator/autoinstrumentation-java:1.33.5
-- app log head:
Picked up JAVA_TOOL_OPTIONS:  -javaagent:/otel-auto-instrumentation-java/javaagent.jar
[otel.javaagent 2026-10-19 18:15:24:512 +0000] [main] INFO io.opentelemetry.javaagent.tooling.VersionLogger - opentelemetry-javaagent - version: 1.33.5
orders-java listening on 8080
```

The Collector excerpt is cut, but it shows the important path: Java agent to OTLP HTTP to Collector debug output.

```text
2026-10-19T18:15:40.063Z	info	ResourceSpans #0
...
     -> service.name: Str(orders-java)
     -> telemetry.sdk.language: Str(java)
InstrumentationScope io.opentelemetry.java-http-client 1.33.5-alpha
Span #0
    Name           : GET
    Kind           : Client
...
```

The 4 spans in this run are outbound Java HTTP client spans from the `/orders` to `/inventory` call. The build proves the Java auto-instrumentation and Collector path; it did not capture inbound server spans for `/orders` in this run.

![Annotation patch and rollout, same image](https://abhimanyu9988.github.io/assets/posts/trace-a-java-service-on-eks-with-one-annotation-zero-code/step-1.png)

![Env vars injected by the Operator](https://abhimanyu9988.github.io/assets/posts/trace-a-java-service-on-eks-with-one-annotation-zero-code/step-2.png)

![Collector summary: spans received from orders-java](https://abhimanyu9988.github.io/assets/posts/trace-a-java-service-on-eks-with-one-annotation-zero-code/step-3.png)

## Use it for services you do not want to rebuild

- **Platform-owned rollout:** create the Operator, Collector, and `Instrumentation` resource once per namespace, then let teams opt in with the pod-template annotation.
- **Service naming:** accept the deployment-derived `OTEL_SERVICE_NAME`, or set `resource.opentelemetry.io/service.name` on the pod template if your naming standard is different.
- **Backend swap:** keep the same app annotation and Instrumentation shape, but replace the debug exporter with the exporter your tracing platform needs.

## Clean up (the EKS cluster and EC2 nodes cost money while they run)

Remove the Kubernetes objects first, then destroy the AWS resources.

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- I did not send these spans to a production tracing backend; I used the Collector debug exporter only.
- I did not capture inbound server spans or parent/child links in this run.
- I did not test private subnets, managed node groups, Fargate, IRSA, Node.js, .NET, or Python auto-instrumentation.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/fix.sh)
- [`k8s/app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/k8s/app.yaml)
- [`k8s/collector.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/k8s/collector.yaml)
- [`k8s/instrumentation.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/k8s/instrumentation.yaml)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/trace-a-java-service-on-eks-with-one-annotation-zero-code/evidence): the raw output of my run

## Sources

- [OpenTelemetry Operator for Kubernetes](https://opentelemetry.io/docs/platforms/kubernetes/operator/)
- [OpenTelemetry automatic instrumentation with the Operator](https://opentelemetry.io/docs/platforms/kubernetes/operator/automatic/)

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
