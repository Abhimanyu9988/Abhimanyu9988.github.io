# Put an EKS app behind an ALB and watch ingress health

This lab builds a small EKS cluster and exposes a web service through an Application Load Balancer. The AWS Load Balancer Controller creates the ALB. The lab then shows ingress health with `curl`, ALB target health, controller logs and Splunk Observability Cloud metrics.

## What gets built

**Terraform (`main.tf`)**
- A VPC with two public subnets in two AZs, tagged `kubernetes.io/role/elb=1`.
- An EKS cluster (1.32, API auth mode).
- Two plain `aws_instance` workers (t3.large, AL2023 EKS AMI) that join the cluster with `nodeadm`. They use an `EC2_LINUX` access entry.
- The `eks-pod-identity-agent` add-on.
- A role named `hero-lab-<lab>-lbc` for the controller. It carries the official controller IAM policy, and a Pod Identity association binds it to `kube-system/aws-load-balancer-controller`.
- Every role uses the permissions boundary variable.

**BEFORE (`reproduce.sh`)**
- Deploys the nginx sample app (2 replicas, `/healthz`) and an Ingress with `ingressClassName: alb`.
- Nothing reconciles the Ingress. There is no ADDRESS, no ALB, no IngressClass and no Splunk data for the cluster.
- Prints that evidence and ends with `HERO_RESULT: reproduced`.

**BUILD (`fix.sh`)**
1. Installs the `aws-load-balancer-controller` Helm chart (pinned to 1.13.0, app v2.13.0) with Pod Identity. No IRSA annotation is needed. Prometheus scrape annotations are set on the controller pod.
2. Installs `splunk-otel-collector` (pinned to 0.116.0) with `clusterName=$LAB_NAME`. It is installed without `--wait`, so it starts sending data while the ALB is being built. The chart's `values.schema.json` for 0.116.0 contains `"deprecated": "<string>"` where JSON Schema wants a boolean. Recent Helm versions reject that, so the install passes `--skip-schema-validation` (Helm 3.16 or newer; the script detects the flag).
3. Applies the Ingress and waits for the ALB hostname and for the ALB to become active.
4. Prints the ALB target health. It waits for every pod IP to be `healthy`.
5. Runs `curl http://<alb-dns>/`, with six requests that show different pod names.
6. Prints controller log lines such as `created targetGroup`, `registering targets` and `successfully deployed model`.
7. Queries the Splunk API, retrying while data arrives (up to 8 minutes, within the script deadline). It checks time series for `k8s.cluster.name=$LAB_NAME`, for the controller pod and for the `web-*` pods. It prints the metric names.

The script ends with `HERO_RESULT: fixed` only when all of these checks pass.

**Time limits.** Each script finishes within 15 minutes. `reproduce.sh` caps its waits (300s nodes, 240s rollout). `fix.sh` runs every wait under one shared deadline of about 12.5 minutes, so a broken step ends with `HERO_RESULT: not_fixed` instead of hanging.

`cleanup.sh` deletes the Ingress and waits for the ALB, target groups and controller security groups to be removed. It uninstalls both Helm releases. Its AWS CLI fallback force-deletes tagged ALBs and waits until they are gone. It then retries target group deletion. For each controller security group it first revokes rules in other groups that reference it, then retries the deletion. All of this runs under one deadline and never fails hard.

## Run it in your own account

You need Terraform, the aws CLI v2, kubectl, helm (3.16 or newer), jq and curl.

```bash
export AWS_REGION=eu-central-1
export LAB_NAME=alb-ingress-lab            # also passed as -var lab_name
export SPLUNK_REALM=us1                    # your realm
export SPLUNK_ACCESS_TOKEN=...             # ingest token (send data only)
export SPLUNK_API_TOKEN=...                # read-only API token

# Declare the variables the lab runner normally provides, with a backend of your choice:
#   region, lab_name, permissions_boundary (set to null if you have no boundary),
#   and a default provider "aws" { region = var.region }
terraform init && terraform apply -var lab_name=$LAB_NAME -var region=$AWS_REGION

./reproduce.sh   # BEFORE: Ingress without a controller
./fix.sh         # BUILD + proof
./cleanup.sh     # then: terraform destroy
```

Tokens are only passed to `helm` and the Splunk API. They are never printed or written to files.

## Notes
- The chart version (`LBC_CHART_VERSION`, default 1.13.0) and Terraform's `lbc_app_version` (default v2.13.0) must match. Terraform downloads the IAM policy of that release tag. To use another release, change both.
- Terraform adds one security group rule that lets the VPC reach the pods on port 80. The controller manages the ALB's own security groups.
- Cost: one EKS control plane, two t3.large instances and one ALB for about an hour, which is well under 1 USD.
