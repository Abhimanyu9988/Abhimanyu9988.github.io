# Every signal the OTel Java agent gives you in Splunk, with zero code

## What it shows

A small Spring Boot system runs on EKS: `orders` calls `inventory`, and `inventory` reads Postgres. Both use the same jar on the same image.

* `reproduce.sh` (BEFORE): installs the Splunk OTel Collector, builds the jar once in the cluster, runs it with no agent, and records startup, resident memory and p95 latency under a fixed load.
* `fix.sh` (BUILD): sets one environment variable on each service, repeats the same measurements, and counts what Splunk Observability Cloud shows through its API.

The Collector runs in both measurements, so the agent is the only difference between them.

The only change is `JAVA_TOOL_OPTIONS`. The jar's SHA-256 and the image are printed in both runs. Application log export is off here because this lab sends to Observability Cloud only; traces and metrics use the agent's defaults.

## Run it

Set `AWS_REGION`, `LAB_NAME`, `TF_VAR_lab_name` (the same value as `LAB_NAME`), `SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN` (ingest) and `SPLUNK_API_TOKEN` (read). Then run:

```bash
terraform init && terraform apply -auto-approve
./reproduce.sh
./fix.sh
./cleanup.sh
terraform destroy -auto-approve
```

The run uses two `t3.large` nodes and an EKS control plane for about an hour. Each overhead figure comes from one cluster, one load rate and burstable instances, so measure your own service before you plan capacity.
