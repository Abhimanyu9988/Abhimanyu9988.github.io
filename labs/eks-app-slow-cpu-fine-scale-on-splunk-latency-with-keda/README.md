# Scale an EKS app on request latency from Splunk, not CPU, with KEDA

## What it shows

A small I/O-bound Python HTTP service runs on EKS. Each pod has 4 "backend slots" and every request holds a slot for 200 ms (a sleep), so when traffic grows requests queue, latency climbs and CPU stays almost idle. CPU-based autoscaling would never react.

* `reproduce.sh` (BEFORE): deploys the service with a fixed replica count and no autoscaler, pushes 60 concurrent clients at it and prints latency (seconds), CPU (a few percent of one core) and replicas (still 1). Stats are read with `kubectl exec` inside the pod.
* `fix.sh` (BUILD):
  1. installs the Splunk Distribution of the OpenTelemetry Collector (chart `splunk-otel-collector` 0.163.1, `clusterName=$LAB_NAME`, Prometheus autodetect) and KEDA (chart `keda` 2.21.0), then prints `helm list`;
  2. proves through the Splunk API that the cluster's metrics and the app's `app_request_latency_ms` gauge arrive;
  3. creates a Secret with the read-only API token, a `TriggerAuthentication`, and a `ScaledObject` with a `splunk-observability` trigger (SignalFlow query on the latency metric, `targetValue: 400`, `activationTargetValue: 0`, 1..5 replicas, `metricType: Value`);
  4. starts the load generator and prints replicas (1 -> 5) with the Splunk-sourced metric value and timings, then stops the load and prints replicas returning to 1.
* `cleanup.sh` uninstalls the Helm releases and removes the namespaces.

The cluster is a 2-node EKS cluster (t3.large, plain `aws_instance` nodes joined with nodeadm, public subnets, no NAT, no managed node groups). The node instance profile is named `hero-lab-<lab_name>-node` like the roles. The app and load generator use the pinned image `python:3.12.11-slim` from the ECR Public mirror.

## Run it in your own account

Export before running:

* AWS credentials for the target account, `AWS_REGION` (lab uses `eu-central-1`) and `LAB_NAME` (same value as the Terraform `lab_name`).
* `SPLUNK_REALM` (e.g. `eu0`), `SPLUNK_ACCESS_TOKEN` (ingest token, used by the collector) and `SPLUNK_API_TOKEN` (read-only API token, used by KEDA and by the proof queries). Tokens are never printed or written to files.

The Terraform needs an AWS provider block and the variables `region`, `lab_name` and `permissions_boundary` (set it to `null` if you have no boundary); the lab runner supplies these in `_runner.tf`, so create an equivalent file locally. Then:

```bash
terraform init && terraform apply -var lab_name=$LAB_NAME ...
./reproduce.sh   # before: latency climbs, CPU low, replicas stay 1
./fix.sh         # build + proof: replicas 1 -> 5 -> 1 driven by Splunk latency
./cleanup.sh && terraform destroy
```

The whole run takes about 40 minutes and costs well under 1 USD. Each script has a wall-clock budget below 15 minutes and always ends with exactly one `HERO_RESULT` line.

## Notes

* The KEDA Splunk Observability scaler requires `query`, `duration`, `targetValue`, `queryAggregator` and `activationTargetValue`, plus `accessToken` and `realm` from the TriggerAuthentication. The SignalFlow query must publish a single aggregated stream (here `.max()` over pods).
* `metricType: Value` makes the HPA compute `ceil(replicas * latency / target)`, so replicas keep growing while the latency stays above target; with 60 clients, 5 pods still sit above 400 ms, so the workload reaches the maximum of 5.
