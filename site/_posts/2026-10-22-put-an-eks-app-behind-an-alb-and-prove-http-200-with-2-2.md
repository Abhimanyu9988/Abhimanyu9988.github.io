---
title: "Put an EKS app behind an ALB and prove HTTP 200 with 2/2 targets"
description: "Build the EKS ingress path end to end with the AWS Load Balancer Controller, EKS Pod Identity, an internet-facing ALB, target health, curl proof, controller logs, and Splunk metrics."
image: /assets/posts/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/cover.png
image_alt: "Terminal output showing an Ingress with an empty hostname and no controller, followed by HTTP 200, 2/2 healthy targets and one successful reconciliation after the controller is installed."
tags: ["aws", "eks", "kubernetes", "observability"]
channels: ["devto", "hashnode"]
lab: labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2
linkedin: "An accepted Kubernetes Ingress doesn't mean your app is on the internet.\nMine wired pod IPs to a real AWS load balancer in 148 seconds.\n\n-> The Ingress requests the alb class and sets the target type to ip.\n-> EKS Pod Identity grants the controller exact AWS permissions without complex annotations.\n-> The controller provisions the ALB and registers pod IPs directly as targets.\n\nCurl returned an HTTP 200 status straight through the load balancer.\nAWS target health verified 2/2 healthy targets.\nThe controller recorded 1 successful reconciliation and 0 errors.\n\nConnecting Kubernetes objects to AWS target health removes the guessing game when routing fails.\n\nI built and tested this in a throwaway AWS account, and the post has every step:\n\n{LINKS}\n\nDo you point your AWS target groups at node ports or directly at pod IPs?\n\n#kubernetes #aws #eks #platformengineering"
replies: []
---
![Terminal output showing an Ingress with an empty hostname and no controller, followed by HTTP 200, 2/2 healthy targets and one successful reconciliation after the controller is installed.](https://abhimanyu9988.github.io/assets/posts/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/cover.png)

By the end, you have an EKS service reachable through a real AWS Application Load Balancer, with proof from curl, target health, controller logs, and Splunk metrics. The failure mode is the common one: an Ingress that looks right in Kubernetes but never gets an address because nothing is reconciling it.

I reproduced that gap in a throwaway AWS account, then added the AWS Load Balancer Controller and EKS Pod Identity. The final proof was **HTTP 200 through the ALB with 2/2 targets healthy**.

Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** an nginx app in EKS exposed through an internet-facing AWS Application Load Balancer.
> - **The trick:** an Ingress with `ingressClassName: alb`, reconciled by the AWS Load Balancer Controller, using IAM through EKS Pod Identity.
> - **The proof:** `HTTP status: 200`, `2/2` healthy targets, `1` successful reconciliation, `0` reconciler errors, and Splunk cluster/controller/app series.
> - **Time and cost:** Terraform apply took 609s, reproduce took 48s, build-and-proof took 148s, cleanup took 37s, and the full reproduced-and-fixed run was 28.3 minutes. The lab README estimates one EKS control plane, two `t3.large` instances, and one ALB for about an hour is well under 1 USD.

## Why an accepted Ingress can still have an empty ADDRESS

A Kubernetes Ingress is a request. It is not the load balancer by itself.

In the before-state, I applied a working app, a `ClusterIP` Service, and an Ingress that asked for the `alb` class. Kubernetes accepted all of it. The pods were ready. The Ingress existed.

But there was no controller watching that Ingress and creating AWS resources.

```text
NAME   CLASS   HOSTS   ADDRESS   PORTS   AGE
web    alb     *                 80      34s
Ingress hostname: ''
No resources found
aws-load-balancer-controller deployments: 0
ALBs created for the cluster: 0
cluster MTS count: 0
```

That is the gap to make visible: Kubernetes can store the object, but AWS will not create an ALB until the AWS Load Balancer Controller reconciles it.

![Before state: Ingress exists, no address, no controller, no ALB](https://abhimanyu9988.github.io/assets/posts/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/step-1.png)

## The trick: one Ingress class, pod IP targets, and IAM through Pod Identity

The key config is small. The Ingress asks for the `alb` class and tells the controller to register pod IPs directly as ALB targets.

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: web
  namespace: demo
  annotations:
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
    alb.ingress.kubernetes.io/healthcheck-path: /healthz
    alb.ingress.kubernetes.io/healthcheck-interval-seconds: "10"
    alb.ingress.kubernetes.io/healthy-threshold-count: "2"
spec:
  ingressClassName: alb
  rules:
    - http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: web
                port:
                  number: 80
```

For your own service, change the namespace, service name, port, and health check path. Keep `target-type: ip` if you want the ALB target group to point at pod IPs instead of node ports.

The controller needs AWS permissions to create load balancers, listeners, target groups, security groups, and target registrations. In this lab, Terraform creates the IAM role and binds it to the controller service account with EKS Pod Identity. No IRSA annotation is needed.

## Build it yourself in 4 steps

You need Terraform, aws CLI v2, kubectl, Helm 3.16 or newer, jq, and curl. You also need your own Splunk Observability realm, an ingest token to send data, and a read-only API token for the proof queries.

### Step 1: Create the EKS lab cluster (about 10 minutes)

This creates the VPC, two public subnets tagged for ELB discovery, an EKS 1.32 cluster, two plain EC2 worker nodes, the Pod Identity agent, and the IAM role for the controller.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2

export AWS_REGION=eu-central-1
export LAB_NAME=alb-ingress-lab
export TF_VAR_lab_name=$LAB_NAME

export SPLUNK_REALM=eu1              # use your own realm
export SPLUNK_ACCESS_TOKEN=...       # ingest token
export SPLUNK_API_TOKEN=...          # read-only API token

terraform init && terraform apply
```

In my run, Terraform created 23 resources. The first apply took 609s.

### Step 2: Prove the empty Ingress address first (about 48 seconds)

The first script deploys the demo app and Ingress before the controller exists. The second script installs the controller and collector, then waits for the ALB path to work.

```bash
./reproduce.sh  # BEFORE: Ingress without a controller
./fix.sh        # BUILD + proof
```

The reproduce step should show ready pods, an empty Ingress `ADDRESS`, no `IngressClass`, no controller deployment, no ALB tagged for the cluster, and no Splunk cluster time series yet.

### Step 3: Install the controller and collector (about 148 seconds)

The build script installs AWS Load Balancer Controller chart `1.13.0` and Splunk OpenTelemetry Collector chart `0.116.0`.

The controller values file is generated from Terraform outputs. The important shape is this:

```yaml
clusterName: alb-ingress-lab
region: eu-central-1
vpcId: vpc-068f0387f9fbee968
replicaCount: 1
serviceAccount:
  create: true
  name: aws-load-balancer-controller
podAnnotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "8080"
  prometheus.io/path: "/metrics"
```

Use your own cluster name, region, and VPC ID if you lift this into another setup. One practical note: the lab pins the Splunk chart to `0.116.0`, and the script adds `--skip-schema-validation` when Helm supports it.

### Step 4: Inspect the Kubernetes-to-AWS path

After the script applies the Ingress, it waits for an ALB hostname, waits for the load balancer state to become `active`, checks target health, curls the ALB, reads controller logs, and queries Splunk Observability Cloud.

These are the commands worth knowing when you debug the same path yourself:

```bash
kubectl -n demo get ingress web
kubectl -n demo get targetgroupbinding
kubectl -n kube-system logs deploy/aws-load-balancer-controller --tail=500
```

The `TargetGroupBinding` is useful because it is the Kubernetes object that connects your Service to the AWS target group.

![Ingress gets an ALB hostname and both targets turn healthy](https://abhimanyu9988.github.io/assets/posts/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/step-2.png)

## The proof: HTTP 200, 2/2 targets, and controller reconciliation

| Check | Result from my run |
|---|---:|
| Ingress hostname assigned | yes |
| ALB state | `active` |
| curl through ALB | `HTTP status: 200` |
| Healthy targets | `2/2` |
| Controller reconciliations | `1` |
| Reconciler errors | `0` |
| Splunk cluster MTS | `1968` |
| Splunk controller pod MTS | `1621` |
| Splunk app pod MTS | `34` |
| Controller Prometheus reconcile series | `12` |

The traffic proof showed both pods behind the same ALB, and AWS target health matched the Kubernetes replica count:

```text
HTTP status: 200
      3 hello from pod web-7ccf95689c-2g7ww behind the ALB
      3 hello from pod web-7ccf95689c-kxmzr behind the ALB
10.20.0.106	80	healthy
10.20.0.53	80	healthy
healthy targets: 2/2
```

The controller log block below is abridged from the script output; long ARNs are shortened with `...`.

```text
{"level":"info","ts":"2026-10-22T09:51:01Z","logger":"controllers.ingress","msg":"creating targetGroup","stackID":"demo/web","resourceID":"demo/web-web:80"}
{"level":"info","ts":"2026-10-22T09:51:01Z","logger":"controllers.ingress","msg":"created targetGroup","stackID":"demo/web","resourceID":"demo/web-web:80","arn":"arn:aws:elasticloadbalancing:eu-central-1:<account-id>:targetgroup/k..."}
{"level":"info","ts":"2026-10-22T09:51:01Z","logger":"controllers.ingress","msg":"creating loadBalancer","stackID":"demo/web","resourceID":"LoadBalancer"}
{"level":"info","ts":"2026-10-22T09:51:02Z","logger":"controllers.ingress","msg":"created loadBalancer","stackID":"demo/web","resourceID":"LoadBalancer","arn":"arn:aws:elasticloadbalancing:eu-central-1:<account-id>:loadbalancer/ap..."}
{"level":"info","ts":"2026-10-22T09:51:02Z","logger":"controllers.ingress","msg":"created targetGroupBinding","stackID":"demo/web","resourceID":"demo/web-web:80","targetGroupBinding":{"name":"k8s-demo-web-f26d64a1b9","namespace":"..."}}
{"level":"info","ts":"2026-10-22T09:51:02Z","logger":"controllers.ingress","msg":"successfully deployed model","ingressGroup":"demo/web"}
{"level":"info","ts":"2026-10-22T09:51:03Z","msg":"registering targets","arn":"arn:aws:elasticloadbalancing:eu-central-1:<account-id>:targetgroup/k8s-demo-web-f26d64a1b9/dc37538e55c037db","targets":[{"Id":"10.20.0.106",...}]}
successful reconciliations: 1, reconciler errors: 0
```

That combination is what I want for ingress proof: Kubernetes state, AWS target health, real HTTP traffic, controller logs, and metrics.

![curl through the ALB and controller reconciliation count](https://abhimanyu9988.github.io/assets/posts/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/step-3.png)

## Use it for your own EKS services

- **Start with health checks.** Make the ALB health check path something your app owns, like `/healthz`, and make sure it returns 200 before you blame the controller.
- **Keep IAM boring.** Bind the controller service account to an AWS role with Pod Identity and verify the association before installing the chart.
- **Debug in layers.** Check Ingress address, `TargetGroupBinding`, ALB target health, curl, controller logs, then telemetry. Each layer removes one guess.

## Clean up so the ALB and EKS control plane stop costing money

Delete Kubernetes-created AWS resources first, then destroy the Terraform stack.

```bash
./cleanup.sh
terraform destroy
```

In my final run, cleanup took 37s and Terraform destroyed 23 resources.

## What I didn't test

- TLS termination with ACM certificates.
- Private/internal ALBs.
- A production multi-namespace ingress policy model.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/fix.sh)
- [`k8s/app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/k8s/app.yaml)
- [`k8s/ingress.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/k8s/ingress.yaml)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/put-an-eks-app-behind-an-alb-and-prove-http-200-with-2-2/evidence): the raw output of my run

## Sources

- [AWS Load Balancer Controller documentation](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/)
- [Amazon EKS Pod Identities](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [AWS Load Balancer Controller v3.6.1 release](https://github.com/kubernetes-sigs/aws-load-balancer-controller/releases/tag/v3.6.1) — reference only; this lab pins chart `1.13.0` / app `v2.13.0`.

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.
---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
