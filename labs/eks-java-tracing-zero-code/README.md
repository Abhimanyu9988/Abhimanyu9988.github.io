# Trace a Java service on EKS with zero code changes

This lab shows how to get distributed traces out of a Java service without touching its source or image. You add one annotation and the OpenTelemetry Operator does the rest.

## What it builds

- **Terraform:** a VPC with public subnets only, an EKS cluster, and two plain `aws_instance` t3.medium workers joined with nodeadm. Each worker has an `EC2_LINUX` access entry. The node instance profile is named `hero-lab-<lab_name>-node`, like the roles, so restrictive pipeline IAM policies that only allow `hero-lab-*` names permit it.
- **Sample app (`k8s/app.yaml`):** a small Java HTTP service called `orders-java`.
  - It is deliberately a tiny plain-JDK service (`com.sun.net.httpserver`), not a Spring Boot app. The lab sandbox has no docker or Maven build, so the source is mounted from a ConfigMap and run with `java /app/Main.java`. The Java agent attaches the same way to a Spring Boot service, and the same annotation applies to it.
  - It runs on the pinned `eclipse-temurin:17.0.13_11-jdk` image.
  - `/orders` calls `/inventory` on itself over HTTP.
  - A `loadgen` pod curls it every 2 seconds.

## The flow

1. `reproduce.sh` (BEFORE):
   - Waits for the nodes and deploys the app.
   - Shows that requests are served but the pod has no `JAVA_TOOL_OPTIONS`, no `OTEL_*` variables, no init container and no OpenTelemetry CRDs.
   - Prints `HERO_RESULT: reproduced`.
2. `fix.sh` (BUILD):
   - Installs the OpenTelemetry Operator with Helm. The chart is pinned to `0.71.2` (override with `OTEL_OPERATOR_CHART_VERSION`). The webhook certificate is self-generated, so cert-manager is not needed.
   - Creates an `OpenTelemetryCollector` that prints spans with the `debug` exporter (`verbosity: detailed`).
   - Creates an `Instrumentation` resource.
   - Patches the Deployment with `instrumentation.opentelemetry.io/inject-java: "true"`.
   - Reads the Collector logs and prints the spans. The resource has `service.name: Str(orders-java)`, taken from the Deployment name, plus `k8s.*` attributes.
   - Prints `HERO_RESULT: fixed` only when those spans arrived.
3. `cleanup.sh` removes the namespaces and the Helm release before `terraform destroy`.

Both scripts run under a 14-minute overall deadline.

## Run it in your own account

```bash
export AWS_REGION=eu-central-1 LAB_NAME=otel-java
# Declare the region, lab_name and permissions_boundary variables plus an aws provider (the runner does this for you).
# Set permissions_boundary = null if you have no boundary.
terraform init && terraform apply -auto-approve
./reproduce.sh
./fix.sh
./cleanup.sh && terraform destroy -auto-approve
```

The EKS control plane takes about 10 minutes to create. A run costs well under 1 USD.

## Applying it to other services

1. Create an `Instrumentation` resource in the service's namespace.
2. Add `instrumentation.opentelemetry.io/inject-java: "true"` to the pod template.
3. Optionally set `resource.opentelemetry.io/service.name` on the pod template to override the service name.

The pod restarts once, and the team's image and code stay untouched.
