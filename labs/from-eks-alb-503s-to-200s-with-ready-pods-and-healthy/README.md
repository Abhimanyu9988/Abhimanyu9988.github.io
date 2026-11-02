# Fix EKS ALB 503s caused by unready pods

Incident-style lab: an Ingress and an ALB that look correct, but users get `503 Service Temporarily Unavailable`.

## What it builds

* VPC with two public subnets, an EKS cluster (API auth mode) and **two plain `aws_instance` workers** (t3.large, AL2023 EKS AMI) joined with `nodeadm` and an `EC2_LINUX` access entry. No managed node groups, no NAT. The node role and instance profile are both named `hero-lab-<lab>-node`.
* EKS Pod Identity agent add-on + a `hero-lab-<lab>-lbc` role for the AWS Load Balancer Controller.
* `reproduce.sh` (BEFORE):
  * installs the Splunk Distribution of the OpenTelemetry Collector (chart `0.163.1`, `clusterName=$LAB_NAME`),
  * installs the AWS Load Balancer Controller (chart `1.13.4`, override with `LBC_CHART_VERSION`),
  * deploys nginx with two deliberate bugs: the readiness probe hits `/ready` (404) and the ALB health check hits `/healthz` (404),
  * prints pods Running but not Ready, the `Readiness probe failed ... 404` event, EndpointSlices with `ready=false`, **zero** registered ALB targets, and `curl` returning **503**.
* `fix.sh` (BUILD):
  * labels the namespace for pod readiness gate injection, fixes the probe path and the ALB health-check path, adds a `preStop` sleep,
  * waits for the rollout, shows the time until the first `200`, 20 requests all `200`,
  * prints `aws elbv2 describe-target-health` with all targets `healthy`,
  * queries the Splunk Observability Cloud API (retrying for up to 8 minutes, bounded by the script's own time budget) and prints the number and names of time series for `k8s.cluster.name=$LAB_NAME`, the app pods and the controller pod. All three must return data for the script to report `fixed`.
* `cleanup.sh` deletes the Ingress/namespace and waits for the ALB, target groups and security groups the controller created, then uninstalls both Helm releases.

Every script keeps all its waits bounded and finishes in under 15 minutes.

### Why 503?
With `target-type: ip` the controller only registers pods that are Ready. Unready pods mean an empty target group, and an ALB with no targets answers 503. The second bug (wrong health-check path) would only show up after fixing the first one alone: the targets would be `unhealthy` with `Target.ResponseCodeMismatch`. With the readiness gate enabled, a pod only becomes Ready after the ALB says its target is healthy, so a rollout never sends traffic to a pod the ALB doesn't trust.

## Run it in your own account

You need Terraform, aws CLI v2, kubectl, helm, jq and curl, plus AWS credentials for `eu-central-1`.

1. The runner normally supplies the backend, provider and variables. In your copy add a file like:
   ```hcl
   variable "region" { default = "eu-central-1" }
   variable "lab_name" { default = "alb503-lab" }
   variable "permissions_boundary" { default = null }
   provider "aws" { region = var.region }
   ```
2. Export the Splunk values (never commit them):
   ```bash
   export SPLUNK_REALM=us1                 # your realm
   export SPLUNK_ACCESS_TOKEN=...          # ingest token (send data)
   export SPLUNK_API_TOKEN=...             # read-only API token
   export AWS_REGION=eu-central-1
   export LAB_NAME=alb503-lab
   ```
3. `terraform init && terraform apply -var lab_name=$LAB_NAME`
4. `./reproduce.sh` -> ends with `HERO_RESULT: reproduced`
5. `./fix.sh` -> ends with `HERO_RESULT: fixed`
6. `./cleanup.sh && terraform destroy -var lab_name=$LAB_NAME`

The whole run takes about 40 minutes and costs well under 1 USD (EKS control plane, 2x t3.large, one ALB).

Note: the controller IAM policy in `main.tf` is lab-sized (broad `elasticloadbalancing:*`). For production use the official `iam_policy.json` from the AWS Load Balancer Controller docs.
