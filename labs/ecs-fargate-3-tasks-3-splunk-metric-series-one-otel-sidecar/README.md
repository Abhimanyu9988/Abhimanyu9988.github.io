# Watch ECS Fargate task CPU and memory in Splunk with an OTel sidecar

This lab builds an ECS cluster with one Fargate service in public subnets. Then it adds a Splunk Distribution of the OpenTelemetry Collector sidecar to the task. The sidecar uses the `awsecscontainermetrics` receiver, which reads the ECS task metadata endpoint. Nothing is installed on a host, because Fargate has no host to install on. It sends per-task CPU, memory, network and storage metrics to Splunk Observability Cloud. Each series is tagged with `ecs.cluster.name`, `ecs.service.name` and `aws.ecs.task.id`.

The app container is a busybox web server. Each task picks a random load level at start: idle, light, or a full CPU burn. After the scale-out you can compare a hot task with its neighbours.

## Steps

| Script | What it does |
|---|---|
| `terraform apply` | VPC with 2 public subnets, ECS cluster (Container Insights disabled), task definition with the app only, service with 1 task. |
| `reproduce.sh` | BEFORE. Shows 1 running task, a task definition with no sidecar, and 0 series in Splunk for the cluster. Prints `HERO_RESULT: reproduced`. |
| `fix.sh` | BUILD. Registers a new task definition revision with the `otel-collector` sidecar, rolls the service, and waits for the task's series in Splunk. It scales 1 to 3 tasks, times it, and waits until every running task id appears in Splunk's metric time series API. It prints metric names, task ids and the latest CPU and memory values. All waits share one 13-minute deadline, so the script stays inside the 15-minute limit. Prints `HERO_RESULT: fixed`. |
| `cleanup.sh` | Stops tasks and deregisters the task definition revisions. Run it before `terraform destroy`. |

## Run it in your own account

Export these before running the scripts:

```bash
export AWS_REGION=eu-central-1
export LAB_NAME=ecs-otel-lab          # also pass it as -var lab_name=...
export SPLUNK_REALM=us1               # your Splunk Observability realm
export SPLUNK_ACCESS_TOKEN=...        # ingest token (send data only), used by the sidecar
export SPLUNK_API_TOKEN=...           # read-only API token, used to verify the data
```

The Terraform needs the variables `region`, `lab_name` and `permissions_boundary`. The lab runner declares them. In your own account, add a small `variables.tf` that declares them, and set `permissions_boundary` to null. The AWS provider also needs `region` and a backend of your choice.

```bash
terraform init && terraform apply -var lab_name=$LAB_NAME -var region=$AWS_REGION -var permissions_boundary=null
./reproduce.sh
./fix.sh
./cleanup.sh && terraform destroy -var lab_name=$LAB_NAME -var region=$AWS_REGION -var permissions_boundary=null
```

## Notes

- The collector config is passed in the `SPLUNK_CONFIG_YAML` environment variable of the sidecar, so no image build or config bucket is needed.
- The ingest token is a plain environment variable in the task definition revision. The lab has no Secrets Manager or SSM parameter to put it in. In production, use the `secrets` field of the container definition with a Secrets Manager secret or SSM parameter.
- The sidecar image is `quay.io/signalfx/splunk-otel-collector:0.163.1`, pinned in `fix.sh`. Fargate pulls it over the task's public IP, so there is no NAT gateway.
- Series from a stopped task stay in Splunk for a while. The proof compares the running task ids with the task ids in Splunk, so it is not thrown off by old tasks.
