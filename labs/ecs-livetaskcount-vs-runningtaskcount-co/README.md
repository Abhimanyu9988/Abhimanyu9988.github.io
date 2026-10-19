# ECS LiveTaskCount vs RunningTaskCount during scale-in

What it shows: when an ECS service behind an ALB scales in, tasks being deregistered sit in the
`DEACTIVATING` state until connection draining ends. `LiveTaskCount` includes those tasks. The Container
Insights `RunningTaskCount` does not. A dashboard or alarm built on `RunningTaskCount` therefore reports
fewer tasks than are actually alive (and billed) during scale-in.

## What it builds
- VPC with 2 public subnets, an ALB, and a target group with a 120 s deregistration delay. The ALB and target
  group use a short hashed name because AWS limits them to 32 characters.
- ECS cluster with Container Insights enabled, and a Fargate service (nginx, 0.25 vCPU) running 4 tasks.

No IAM roles are created.

## Run
1. `terraform init && terraform apply -var lab_name=<name> -var region=eu-central-1 -var permissions_boundary=null`
2. `./reproduce.sh` scales the service from 4 to 1. It prints task states from the ECS API every 10 s, then the
   per-minute LiveTaskCount and RunningTaskCount from CloudWatch. It ends with `HERO_RESULT: reproduced` when
   it sees minutes where Live > Running. The script discovers the LiveTaskCount namespace and dimensions
   with `list-metrics`. Metrics can take a few minutes to appear.
3. `./fix.sh` creates a dashboard and a metric-math alarm on `live - running` (draining tasks). It checks them
   against the scale-in data and ends with `HERO_RESULT: fixed`. It needs only the aws CLI and jq.
4. `./cleanup.sh`, then `terraform destroy`.

Cost: a few cents. Four Fargate tasks run for roughly 30 minutes, plus one ALB.
