# Preview OTel Java agent 3.0 in Splunk with one flag

The OpenTelemetry Java agent 3.0 is close, and the project asks users to preview it with one environment variable,
`OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true`. This lab turns it on for a running Java system on EKS and shows, in
Splunk Observability Cloud, what changes: the connection pool metric names, the unit of the pool timings, and the
database keys on real traces.

## What runs

- EKS with two t3.large nodes (`main.tf`), the Splunk OTel Collector (Helm chart 0.163.1).
- Two Spring Boot services, `orders` and `inventory`, on Postgres. The jar is built once in the cluster.
- The OTel Java agent 2.33.1, attached through `JAVA_TOOL_OPTIONS`.

`reproduce.sh` runs the agent with its defaults and a fixed load of one order every 50 ms for 120 seconds, then reads
from Splunk: the database metric names, the pool series under `db.client.connections.*` for each service, the
`db.client.operation.duration` series, and every database key=value pair on one trace through Splunk's trace API.

`fix.sh` starts a preview pair, `orders-v3` and `inventory-v3`, next to the default pair on the same node. It uses the same
jar, image, Collector and agent options, with `OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true` added. The only other
difference is `deployment.environment`, which gets a `-v3` suffix so Splunk keeps the pairs apart. Three rounds of the
same load (90 seconds, the first 30 as warm-up) hit both pairs at the same time. Each round gives one stored mean of the
connection use time from each pair over the same minutes, and the script requires every round's ratio to fall between
500 and 2000, since the same timing stored in milliseconds and in seconds should differ by about 1000.

`check.sh` is a free check with Docker and console exporters: it proves the same name and unit change before any cloud run.

## Run it yourself

You need an AWS account, Terraform, kubectl, Helm, jq and a Splunk Observability Cloud org with an ingest token and an API token.

```bash
export AWS_REGION=eu-central-1 LAB_NAME=agent3-preview-$RANDOM
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=... SPLUNK_API_TOKEN=...
terraform init && terraform apply -var region=$AWS_REGION -var lab_name=$LAB_NAME
./reproduce.sh && ./fix.sh
./cleanup.sh && terraform destroy -var region=$AWS_REGION -var lab_name=$LAB_NAME
```

Each script finishes within 14 minutes, waits included. The EKS control plane takes about 10 minutes to create.

## What the run shows

The names and keys each mode sends are printed in the run's log, read back from Splunk: the pool metric names, the
stored use time per round, and every database key on one trace from each pair. Use those lists, not this README, as the
record of what changed.
