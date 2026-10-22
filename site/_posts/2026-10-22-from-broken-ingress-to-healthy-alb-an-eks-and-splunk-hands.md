---
title: "From Broken Ingress to Healthy ALB: An EKS and Splunk Hands-On Lab"
description: "Build a small EKS lab where an Ingress starts with an empty ADDRESS, then install the AWS Load Balancer Controller with Pod Identity. Prove the ALB works with curl, target health, controller logs, and Splunk metrics."
image: /assets/posts/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/cover.png
image_alt: "Terminal output showing an EKS Ingress with an empty hostname and no controller, then an ALB address, 2/2 healthy targets and HTTP status 200 after the fix."
tags: ["aws", "eks", "kubernetes", "splunk"]
channels: ["devto", "hashnode"]
lab: labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands
linkedin: "A valid Kubernetes Ingress can sit there with an empty ADDRESS forever.\nMine went from that to an active ALB serving HTTP 200 in 148 seconds.\n\nThe YAML was never the problem. Nothing was watching ingressClassName: alb.\n\n→ I deployed nginx and the Ingress before any controller existed, so ADDRESS stayed empty\n→ Terraform had already made the IAM role, the Pod Identity association and the public subnet tags\n→ Then I installed the AWS Load Balancer Controller, which built the ALB from the Ingress\n→ Splunk Observability Cloud collected cluster, controller and app metrics\n\nThe proof from my run:\nHTTP 200 through the ALB\n2/2 healthy targets\n1 successful reconciliation, 0 reconciler errors\n1,968 Splunk cluster time series on the first attempt\n\nWhy it matters at work: when ADDRESS is empty, check the IngressClass, controller, Pod Identity and subnet tags before you touch the app.\n\nI built and tested this in a throwaway AWS account. The post has every step, including cleanup, since the ALB costs money while it runs:\n\n{LINKS}\n\nWhen an Ingress has no ADDRESS, what do you check first?\n\n#EKS #Kubernetes #AWS #Observability"
replies: []
---
![Terminal output showing an EKS Ingress with an empty hostname and no controller, then an ALB address, 2/2 healthy targets and HTTP status 200 after the fix.](https://abhimanyu9988.github.io/assets/posts/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/cover.png)

You’ll take an EKS Ingress with an empty ADDRESS and turn it into a working internet-facing ALB, then prove it with curl, healthy targets, controller reconciliation logs, and Splunk metrics. This is the kind of failure I see in real clusters: the Kubernetes YAML can look right while IAM, Pod Identity, subnet tags, and load balancer reconciliation decide whether traffic ever reaches a pod.

In this lab, the before-state is one specific case: the `alb` Ingress exists before the AWS Load Balancer Controller exists. The lab still creates the IAM role, Pod Identity association, and public subnet tags that the controller needs after it is installed.

In my run, the proof ended with **HTTP 200, 2/2 healthy ALB targets, 1 successful reconciliation, 0 reconciler errors, and 1,968 Splunk cluster time series**. Here’s how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** EKS 1.32, two `t3.large` worker nodes, nginx, an `alb` Ingress, an internet-facing ALB, and Splunk Observability Cloud data.
> - **The trick:** the Ingress only becomes an ALB after the AWS Load Balancer Controller is installed with AWS permissions through EKS Pod Identity.
> - **The proof:** curl returned HTTP 200, ALB target health was 2/2, the controller logged 1 successful reconciliation and 0 errors, and Splunk returned cluster/controller/app series.
> - **Time and cost:** the reproduced-and-fixed run took 28.3 minutes. The lab uses one EKS control plane, two `t3.large` instances, and one ALB for about an hour, estimated as well under 1 USD.

## Why a valid Ingress can still have no ALB

A Kubernetes Ingress is a request. It is not the load balancer by itself.

Terraform creates the VPC, two public subnets tagged `kubernetes.io/role/elb=1`, the EKS cluster, two EC2 worker nodes, the Pod Identity agent, and the IAM role for the controller. Then `reproduce.sh` deploys the app and Ingress before any controller is installed.

The Deployment is ready and the Service exists, but nothing is watching `ingressClassName: alb` yet:

```text
== Ingress status (ADDRESS should be empty: nothing reconciles it)
NAME   CLASS   HOSTS   ADDRESS   PORTS   AGE
web    alb     *                 80      34s
Ingress hostname: ''

== Ingress events
Events:       <none>

== IngressClass objects and controller pods
No resources found
aws-load-balancer-controller deployments: 0
```

That is the useful gap: no controller, no `IngressClass`, no ALB, no target group, and no Splunk data for this cluster yet.

![reproduce.sh: Ingress with empty ADDRESS](https://abhimanyu9988.github.io/assets/posts/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/step-1.png)

## The trick: give the `alb` Ingress a controller with AWS permissions

The AWS Load Balancer Controller watches Kubernetes resources and creates AWS load balancer resources from them. In this lab, Terraform binds the controller IAM role to the service account `kube-system/aws-load-balancer-controller` with EKS Pod Identity.

The Ingress stays small:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/healthcheck-path: /healthz
spec:
  ingressClassName: alb
...
```

For your service, change the namespace, service name, port, health check path, and whether the ALB should be internet-facing.

## Build it yourself in 4 steps

You need Terraform, aws CLI v2, kubectl, helm 3.16 or newer, jq, and curl, plus a Splunk Observability Cloud ingest token and API token. The access token sends data; the API token is used to query for proof.

### Step 1: Clone the lab and set the names once

This keeps Terraform, Kubernetes labels, and Splunk queries on the same lab name.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands

export AWS_REGION=eu-central-1
export LAB_NAME=alb-ingress-lab
export TF_VAR_lab_name=$LAB_NAME
export SPLUNK_REALM=us1
export SPLUNK_ACCESS_TOKEN=...
export SPLUNK_API_TOKEN=...
```

Use your own Splunk realm and tokens from your Splunk Observability Cloud org.

### Step 2: Create the EKS foundation (609 seconds in my run)

The repo includes the Terraform. The provider setup supplies the region and permissions boundary, so do not pass those as Terraform variables here.

```bash
terraform init && terraform apply
```

My apply created 23 resources and printed the cluster name, VPC ID, region, and controller role ARN.

### Step 3: Reproduce the empty ADDRESS state (48 seconds in my run)

This deploys nginx and the Ingress before the controller is installed.

```bash
./reproduce.sh
```

You should see an empty Ingress hostname, no `IngressClass`, 0 controller deployments, and 0 ALBs tagged for the cluster.

### Step 4: Install the controller and prove the ALB (148 seconds in my run)

The script installs the AWS Load Balancer Controller chart `1.13.0` for app `v2.13.0`, installs the Splunk OpenTelemetry Collector chart `0.116.0`, waits for the ALB, checks targets, curls the ALB, checks logs, and queries Splunk.

```bash
./fix.sh
```

One lab detail: recent Helm versions reject that Splunk chart schema because one `deprecated` field is a string where the metaschema wants a boolean, so the script uses `--skip-schema-validation` when Helm supports it.

![fix.sh: Ingress gets an ALB address and active ALB](https://abhimanyu9988.github.io/assets/posts/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/step-2.png)

![Targets healthy, curl returns 200](https://abhimanyu9988.github.io/assets/posts/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/step-3.png)

## The proof: HTTP 200, 2 healthy targets, and Splunk series

| Check | Result |
|---|---:|
| AWS Load Balancer Controller deployment | 1/1 available |
| Ingress ADDRESS | `k8s-demo-web-70bb130243-28212986.eu-central-1.elb.amazonaws.com` |
| ALB state | active |
| ALB target health | 2/2 healthy |
| HTTP status through ALB | 200 |
| Controller reconciliations | 1 successful, 0 errors |
| Splunk cluster time series | 1,968 |
| Splunk controller pod time series | 1,621 |
| Splunk app pod time series | 34 |
| Controller Prometheus metric series | 12 |

The target health and curl output prove AWS is sending traffic to pod IPs:

```text
== 4. ALB target health (target-type ip, one target per pod)
10.20.0.106	80	healthy
10.20.0.53	80	healthy
healthy targets: 2/2

== 5. curl through the load balancer: http://k8s-demo-web-70bb130243-28212986.eu-central-1.elb.amazonaws.com/
HTTP status: 200
```

The controller logged the reconciliation path:

```text
{"level":"info","ts":"2026-10-22T09:51:01Z","logger":"controllers.ingress","msg":"creating targetGroup","stackID":"demo/web","resourceID":"demo/web-web:80"}
{"level":"info","ts":"2026-10-22T09:51:02Z","logger":"controllers.ingress","msg":"created loadBalancer","stackID":"demo/web","resourceID":"LoadBalancer","arn":"arn:aws:elasticloadbalancing:eu-central-1:<account-id>:loadbalancer/ap
{"level":"info","ts":"2026-10-22T09:51:02Z","logger":"controllers.ingress","msg":"successfully deployed model","ingressGroup":"demo/web"}
{"level":"info","ts":"2026-10-22T09:51:03Z","msg":"registering targets","arn":"arn:aws:elasticloadbalancing:eu-central-1:<account-id>:targetgroup/k8s-demo-web-f26d64a1b9/dc37538e55c037db","targets":[{"Id":"10.20.0.106","Availabili
successful reconciliations: 1, reconciler errors: 0
```

Splunk had data for the same lab name on the first attempt:

```text
attempt 1: cluster=1968 controller-pods=1621 app-pods=34
-- controller Prometheus metrics (best effort):
controller_runtime_reconcile_total series: 12

Proof: curl 200 via k8s-demo-web-70bb130243-28212986.eu-central-1.elb.amazonaws.com, 2/2 targets healthy, controller reconciled, Splunk has cluster/controller/app series.
```

## Use it for real ingress checks

- **Check reconciliation before changing the app.** If `ADDRESS` is empty, confirm the `IngressClass`, controller deployment, Pod Identity association, subnet tags, and controller logs.
- **Keep health checks boring.** This lab uses `/healthz`, a 10-second interval, and a healthy threshold of 2.
- **Watch Kubernetes and AWS.** Pod readiness tells you the app is ready. ALB target health tells you AWS can reach it. Splunk gives you the cluster, controller, and app view together.

## Clean up (the ALB costs money while it runs)

Delete Kubernetes-created load balancer resources before destroying Terraform. The cleanup script waits for the controller-managed ALB, target groups, Helm releases, and security groups to go away.

```bash
./cleanup.sh
terraform destroy
```

My cleanup script finished after 37 seconds, and Terraform destroyed 23 resources.

## What I didn't test

- HTTPS listeners, ACM certificates, or redirects from HTTP to HTTPS.
- Private/internal ALBs.
- Production sizing, autoscaling, or multi-AZ pod spread beyond this two-node lab.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/fix.sh)
- [`k8s/app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/k8s/app.yaml)
- [`k8s/ingress.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/k8s/ingress.yaml)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/from-broken-ingress-to-healthy-alb-an-eks-and-splunk-hands/evidence): the raw output of my run

## Sources

- [AWS Load Balancer Controller documentation](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/)
- [Amazon EKS Pod Identities](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [AWS Load Balancer Controller v3.6.1 release](https://github.com/kubernetes-sigs/aws-load-balancer-controller/releases/tag/v3.6.1) — background source; this lab pins chart `1.13.0` and app `v2.13.0` so Terraform downloads the matching IAM policy.

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.
---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
