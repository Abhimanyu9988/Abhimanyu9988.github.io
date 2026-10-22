---
title: "Expose an EKS app through an ALB and prove ingress health"
description: "Build a small EKS lab where an Ingress starts with an empty ADDRESS, then install the AWS Load Balancer Controller. Prove the ALB path with curl and target health, and confirm controller, cluster and app telemetry in Splunk Observability Cloud."
image: /assets/posts/expose-an-eks-app-through-an-alb-and-prove-ingress-health/cover.png
image_alt: "Terminal output showing an Ingress with an empty ADDRESS and zero controller deployments, then after the fix 2/2 healthy targets and HTTP status 200 through the ALB."
tags: ["aws", "eks", "kubernetes", "devops"]
channels: ["devto", "hashnode"]
lab: labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health
linkedin: "Usually, a valid Kubernetes Ingress means your app is reachable.\nMine had an empty address because no controller was watching it.\n\n-> The Ingress object only declares your intent to route traffic.\n-> The AWS Load Balancer Controller actually reads that intent.\n-> It spins up the internet-facing ALB and registers pod IPs directly.\n\nMy build script closed the gap in 148 seconds.\nIt proved the path with HTTP 200 and 2/2 healthy targets.\nController logs confirmed 1 successful reconciliation and 0 errors.\nSplunk caught the telemetry with 1968 cluster time series.\n\nWhen your ALB exists but traffic fails, checking target health and controller logs saves you from guessing.\n\nI built and tested this in a throwaway AWS account and the post has every step:\n\n{LINKS}\n\nHow do you verify your ingress paths actually work after a deployment?\n\n#kubernetes #aws #eks #observability"
replies: []
---
![Terminal output showing an Ingress with an empty ADDRESS and zero controller deployments, then after the fix 2/2 healthy targets and HTTP status 200 through the ALB.](https://abhimanyu9988.github.io/assets/posts/expose-an-eks-app-through-an-alb-and-prove-ingress-health/cover.png)

By the end, you’ll have an EKS web service reachable through an internet-facing ALB, with curl and healthy targets proving the ALB path, and controller logs plus Splunk Observability metrics showing reconciliation and telemetry. I start with the broken state first: an Ingress that looks fine in Kubernetes but has an empty ADDRESS because nothing is reconciling it.

The payoff from my run was concrete: **HTTP 200 through the ALB**, **2/2 healthy targets**, **1 successful reconciliation**, **0 reconciler errors**, and Splunk time series for the cluster, controller pod and app pods.

Most of my week goes on one question: why does something that looks correctly configured not work in a real cluster? This lab is that question in miniature.

Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** a small EKS 1.32 cluster, nginx behind an internet-facing ALB, and evidence from Kubernetes, AWS, curl, controller logs and Splunk Observability Cloud.
> - **The trick:** an Ingress is only intent; the AWS Load Balancer Controller turns it into an ALB, target group, listener rules and pod IP registrations.
> - **The proof:** `HTTP status: 200`, `healthy targets: 2/2`, `successful reconciliations: 1, reconciler errors: 0`, plus Splunk counts of `cluster=1968 controller-pods=1621 app-pods=34`.
> - **Time and cost:** Terraform apply took 609 seconds, the build script took 148 seconds, cleanup took 37 seconds, and the README estimates well under 1 USD for about an hour.

## Why a valid Ingress can still have no ADDRESS

Kubernetes accepts the Ingress object even when no controller is installed. That is the part that catches people.

In this lab, the app is running. The Service exists. The Ingress has `ingressClassName: alb`. But there is no AWS Load Balancer Controller yet, so nothing is watching that Ingress and creating AWS resources.

This is the before state from `reproduce.sh`:

```text
== Ingress status (ADDRESS should be empty: nothing reconciles it)
NAME   CLASS   HOSTS   ADDRESS   PORTS   AGE
web    alb     *                 80      34s
Ingress hostname: ''
...
aws-load-balancer-controller deployments: 0
ALBs created for the cluster: 0
cluster MTS count: 0
```

That is the lesson: a valid Ingress is not the same thing as a reachable application.

![reproduce.sh: Ingress exists but ADDRESS is empty](https://abhimanyu9988.github.io/assets/posts/expose-an-eks-app-through-an-alb-and-prove-ingress-health/step-1.png)

## The trick: let the ALB controller close the loop

The controller is the bridge between Kubernetes intent and AWS load balancer reality.

The Ingress says: create an internet-facing ALB, register pod IPs directly, and health check `/healthz`. The controller reads that and creates the ALB, target group, target group binding and registrations.

These are the key Ingress lines used in the lab:

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
    ...
```

```yaml
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
...
```

For your own service, change the namespace, service name, service port and health check path. Keep `target-type: ip` if you want the ALB target group to register pod IPs directly.

## Build it yourself in 3 steps

You need AWS credentials configured, Terraform, aws CLI v2, kubectl, Helm 3.16 or newer, jq and curl. For Splunk, `SPLUNK_ACCESS_TOKEN` is an ingest token and `SPLUNK_API_TOKEN` is a read-only API token.

### Step 1: Create the EKS lab cluster and Pod Identity role (about 10 minutes)

Terraform creates the VPC, two public subnets, an EKS 1.32 cluster, two `t3.large` nodes, the EKS Pod Identity agent and the IAM role used by the controller.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health
export AWS_REGION=eu-central-1
export LAB_NAME=alb-ingress-lab
export TF_VAR_lab_name=$LAB_NAME
export SPLUNK_REALM=eu1
export SPLUNK_ACCESS_TOKEN=...
export SPLUNK_API_TOKEN=...
terraform init && terraform apply
```

In my run, Terraform added 23 resources. The apply completed after 609 seconds.

### Step 2: Show the empty ADDRESS before the controller exists (about 48 seconds)

This deploys the nginx app and the Ingress first, on purpose. It proves the gap before we close it.

Run `bash reproduce.sh`.

The script applies `k8s/app.yaml` and `k8s/ingress.yaml`, then checks the Ingress address, IngressClass objects, controller deployment count, tagged ALBs and Splunk time series for the cluster name. Look for the empty ADDRESS and `aws-load-balancer-controller deployments: 0`.

### Step 3: Install the controller, collector, ALB and proof

The lab uses AWS Load Balancer Controller chart `1.13.0`, which installs app `v2.13.0`. Terraform already created the Pod Identity association for `kube-system/aws-load-balancer-controller`, so no IRSA annotation is needed.

```bash
bash fix.sh
```

`fix.sh` writes the controller Helm values from Terraform outputs: cluster name, region and VPC ID. It also installs `splunk-otel-collector` chart `0.116.0`, applies the Ingress, waits for the ALB hostname, checks target health, curls the ALB, prints controller logs and queries the Splunk API.

One practical note: recent Helm versions can reject the pinned Splunk chart schema because a `deprecated` field is a string where the JSON Schema metaschema expects a boolean. The script detects Helm support and uses `--skip-schema-validation`.

## The proof: curl proves the path, Splunk shows telemetry arrived

Here is the successful run in stages:

| Check | Result from my lab |
|---|---:|
| Build script runtime | 148 seconds |
| ALB state | active |
| HTTP status through ALB | 200 |
| Healthy targets | 2/2 |
| Successful reconciliations | 1 |
| Reconciler errors | 0 |
| Splunk cluster time series | 1968 |
| Splunk controller pod time series | 1621 |
| Splunk app pod time series | 34 |

The target health and curl output are the hard proof that the ALB path works:

```text
== 4. ALB target health (target-type ip, one target per pod)
10.20.0.106  80  healthy
10.20.0.53   80  healthy
healthy targets: 2/2

== 5. curl through the load balancer: http://k8s-demo-web-70bb130243-28212986.eu-central-1.elb.amazonaws.com/
HTTP status: 200
      3 hello from pod web-7ccf95689c-2g7ww behind the ALB
      3 hello from pod web-7ccf95689c-kxmzr behind the ALB
```

The controller logs show reconciliation:

```text
{"level":"info",...,"msg":"creating targetGroup","stackID":"demo/web",...}
{"level":"info",...,"msg":"created loadBalancer","stackID":"demo/web",...}
{"level":"info",...,"msg":"successfully deployed model","ingressGroup":"demo/web"}
{"level":"info",...,"msg":"registering targets",...}
successful reconciliations: 1, reconciler errors: 0
```

And Splunk confirms that observability data arrived for the cluster, controller and app pods:

```text
== 7. Splunk Observability Cloud (realm eu1), retrying until data arrives (max 8 min, bounded by the script deadline)
attempt 1: cluster=1968 controller-pods=1621 app-pods=34
-- controller Prometheus metrics (best effort):
controller_runtime_reconcile_total series: 12

Proof: curl 200 via k8s-demo-web-70bb130243-28212986.eu-central-1.elb.amazonaws.com, 2/2 targets healthy, controller reconciled, Splunk has cluster/controller/app series.
```

![fix.sh: healthy targets and curl through the ALB](https://abhimanyu9988.github.io/assets/posts/expose-an-eks-app-through-an-alb-and-prove-ingress-health/step-2.png)

![Controller reconciliation and Splunk series counts](https://abhimanyu9988.github.io/assets/posts/expose-an-eks-app-through-an-alb-and-prove-ingress-health/step-3.png)

## Use it for the EKS ingress failures you see at work

- **When ADDRESS stays empty:** check whether the controller exists, whether the IngressClass exists and whether the controller is watching that class.
- **When the ALB exists but traffic fails:** look at target health first. In this lab, pod IP targets on port 80 had to become `healthy` before curl proved the path.
- **When you need evidence, not guesses:** keep one command that prints Kubernetes status, AWS target health, controller logs and telemetry counts together.

## Clean up (the ALB costs money while it runs)

Delete the Kubernetes-created AWS resources first, then destroy the Terraform stack.

```bash
bash cleanup.sh
terraform destroy
```

In my successful run, cleanup took 37 seconds and Terraform destroyed 23 resources.

## What I didn't test

- TLS certificates, HTTPS listeners or host-based routing.
- Private ALBs or internal-only subnet layouts.
- Production scaling settings for the controller or the collector.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/fix.sh)
- [`k8s/app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/k8s/app.yaml)
- [`k8s/ingress.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/k8s/ingress.yaml)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/expose-an-eks-app-through-an-alb-and-prove-ingress-health/evidence): the raw output of my run

## Sources

- [AWS Load Balancer Controller documentation](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/)
- [Amazon EKS Pod Identity documentation](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)
- [AWS Load Balancer Controller v3.6.1 release page](https://github.com/kubernetes-sigs/aws-load-balancer-controller/releases/tag/v3.6.1) — background reading for controller releases; this lab pins chart `1.13.0` and app `v2.13.0`.

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.
---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
