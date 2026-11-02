---
title: "From EKS ALB 503s to 200s with Ready Pods and Healthy Targets"
description: "Reproduce an EKS Ingress that returns 503 from an AWS ALB, then correct readiness, ALB health checks, and readiness gates. The lab proves the change with curl, target health, and Splunk Observability Cloud time series."
image: /assets/posts/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/cover.png
image_alt: "Terminal output showing the ALB with 0 registered targets and a 503, then a first 200 after 35 seconds and 2/2 healthy targets."
tags: ["aws", "eks", "kubernetes", "opentelemetry"]
channels: ["devto", "hashnode"]
lab: labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy
linkedin: ""
replies: []
---
![Terminal output showing the ALB with 0 registered targets and a 503, then a first 200 after 35 seconds and 2/2 healthy targets.](https://abhimanyu9988.github.io/assets/posts/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/cover.png)

You’ll take an EKS Ingress that returns 503 from an AWS ALB and turn it into repeated 200s with healthy targets. The lab uses EKS, Helm, the AWS Load Balancer Controller, readiness probes, readiness gates, the Splunk OpenTelemetry Collector, and Splunk Observability Cloud so you can prove the change instead of trusting YAML.

That moment is familiar: the Ingress exists, the Service exists, the pods are `Running`, and the browser still gets `503 Service Temporarily Unavailable`. In my run, the ALB had **0 registered targets** before the change. After the correction, the first 200 came back **35 seconds** from `kubectl apply`, with **2/2 healthy targets**.

Most of my week goes on one question: why does something that looks correctly configured not work in a real cluster? Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** a disposable EKS lab where an ALB returns 503, then returns 200 after the readiness and health-check paths are corrected.
> - **The trick:** make Kubernetes readiness and ALB target health agree, then enable the AWS Load Balancer Controller pod readiness gate.
> - **The proof:** before: `registered targets: 0` and `curl http status: 503`; after: first 200 in 35s, 19 of the next 20 requests returned 200, and ALB target health was 2/2 healthy.
> - **Time and cost:** my reproduced-and-changed run took 27.4 minutes; the lab README estimates the whole run at about 40 minutes and well under 1 USD.

## Why a Running pod can still leave the ALB with nowhere to send traffic

The trap is treating `Running` as “ready for traffic”. Kubernetes does not.

In this lab, nginx is alive, but the readiness probe checks `/ready`. nginx does not serve that path, so the probe gets 404. The Service has no ready endpoints. Because the Ingress uses ALB `target-type: ip`, the AWS Load Balancer Controller has no ready pod IPs to register as usable targets.

The important part of `reproduce.sh` looked like this:

```text
web-59c4d9997-lcxh2   0/1   Running   0   30s
web-59c4d9997-nhr8d   0/1   Running   0   30s
Readiness probe failed: HTTP probe failed with statuscode: 404
pod web-59c4d9997-lcxh2  ip 10.42.1.120  ready=false
pod web-59c4d9997-nhr8d  ip 10.42.0.26  ready=false
registered targets: 0
[]
curl http status: 503
```

The ALB is not confused. It has a real DNS name, but its target group is empty.

## The trick: make Kubernetes and the ALB agree on health

The correction has three parts.

First, probe a path the container really serves. Second, point the ALB health check at that same working path. Third, label the namespace so the AWS Load Balancer Controller injects a readiness gate.

A readiness gate is an extra pod condition. In plain English: the pod does not become Ready until the ALB target health also says it is healthy.

The lab applies the namespace label, then applies `k8s/app-fixed.yaml`:

```bash
kubectl label namespace lab-web elbv2.k8s.aws/pod-readiness-gate-inject=enabled --overwrite
kubectl apply -f k8s/app-fixed.yaml
```

Excerpt from `k8s/app-fixed.yaml`:

```yaml
readinessProbe:
  httpGet:
    path: /
    port: http
  initialDelaySeconds: 2
  periodSeconds: 5
  failureThreshold: 2
lifecycle:
  preStop:
    exec:
      command: ["sh", "-c", "sleep 15"]
```

For your own service, change `/` to the path that proves your app is actually ready. Do not use a port-open check if the app can accept TCP while still failing requests.

## Build it yourself in 3 steps

You need Terraform, aws CLI v2, kubectl, helm, jq, curl, AWS credentials for `eu-central-1`, and a Splunk Observability Cloud account. `SPLUNK_ACCESS_TOKEN` is the ingest token; `SPLUNK_API_TOKEN` is the read token used for the final proof. Without those Splunk values, the Splunk check will not pass.

### Step 1: Create the EKS cluster (about 8 minutes)

Terraform creates the VPC, two public subnets, an EKS cluster, two plain EC2 worker nodes, the EKS Pod Identity agent, and the IAM role used by the AWS Load Balancer Controller.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy
export AWS_REGION=eu-central-1
export LAB_NAME=alb503-lab
export TF_VAR_lab_name=$LAB_NAME
export SPLUNK_REALM=us1
export SPLUNK_ACCESS_TOKEN=...
export SPLUNK_API_TOKEN=...
terraform init && terraform apply
```

Use your own Splunk realm and tokens. The lab uses `LAB_NAME` as the EKS cluster name and as the Splunk `k8s.cluster.name` value.

### Step 2: Reproduce the 503 (about 3 minutes)

`reproduce.sh` writes kubeconfig, waits for the two nodes, installs the Splunk OpenTelemetry Collector chart `0.163.1`, installs the AWS Load Balancer Controller chart `1.13.4` with app version `v2.13.4`, then deploys the misconfigured nginx app and Ingress.

```bash
bash reproduce.sh
```

You should see Helm report the collector and controller, then the app ends with zero registered targets and a 503:

```text
aws-load-balancer-controller  kube-system  deployed  aws-load-balancer-controller-1.13.4  v2.13.4
splunk-otel-collector         splunk       deployed  splunk-otel-collector-0.163.1        0.163.1
ALB: k8s-labweb-web-bd7d33bdf0-1112945333.eu-central-1.elb.amazonaws.com
registered targets: 0
curl http status: 503
```

![reproduce.sh: pods Running but never Ready, 0 targets, 503](https://abhimanyu9988.github.io/assets/posts/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/step-1.png)

### Step 3: Apply the readiness and health-check change (about 1 minute)

`fix.sh` labels the namespace for readiness gate injection, applies `k8s/app-fixed.yaml`, waits for rollout, curls the ALB, checks target health through AWS, and queries Splunk Observability Cloud.

```bash
bash fix.sh
```

The reassuring part is the readiness gate line. The pods are not just passing their own probe; they are also tied to ALB target health.

```text
deployment "web" successfully rolled out
rollout finished in 33s
web-7f4756dc78-c68x2   1/1   Running   0   34s   10.42.1.152   ...   1/1
web-7f4756dc78-n5fcj   1/1   Running   0   34s   10.42.0.107   ...   1/1
web-7f4756dc78-c68x2 readinessGates=target-health.elbv2.k8s.aws/k8s-labweb-web-b1f7fa280a Ready=True
web-7f4756dc78-n5fcj readinessGates=target-health.elbv2.k8s.aws/k8s-labweb-web-b1f7fa280a Ready=True
```

![fix.sh: rollout done, pods Ready with ALB readiness gate](https://abhimanyu9988.github.io/assets/posts/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/step-2.png)

## The proof: 503 to 200, empty target group to 2 healthy targets

| Check | Before | After |
|---|---:|---:|
| ALB curl | 503 | first 200 after 35s |
| Registered targets | 0 | 2 |
| Healthy targets | 0 | 2/2 |
| Repeated requests | not applicable | 19× 200, 1× 000 |
| Splunk cluster time series | not checked in reproduce step | 1000 |
| Splunk app pod time series | not checked in reproduce step | 150 |
| Splunk controller pod time series | not checked in reproduce step | 25 |

The post-change curl and target health output looked like this:

```text
first 200 after 35s from kubectl apply (status now: 200)
HTTP/1.1 200 OK
<title>Welcome to nginx!</title>
20 requests ->       1 000;     19 200;
target 10.42.0.107:80  state=healthy
target 10.42.1.152:80  state=healthy
healthy targets: 2/2
```

The Splunk Observability Cloud API also returned time series for the lab cluster, the app pods, and the AWS Load Balancer Controller pod. That matters because curl proves the symptom, AWS proves the load balancer state, and telemetry proves the collector saw the cluster components.

One note: the 20-request check had one `000`, which means curl did not get an HTTP response for that attempt. I am not rounding that up to “perfect”. The important result is that the ALB moved from no targets and 503 to repeated 200s with both targets healthy.

![After the fix: 200s from the ALB and 2/2 healthy targets](https://abhimanyu9988.github.io/assets/posts/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/step-3.png)

## Use it for your own EKS services

- **Make readiness match real user success.** If your app needs config, a database, a cache warmup, or a downstream dependency before serving, make that an intentional readiness choice.
- **Keep ALB health checks and pod readiness aligned.** If Kubernetes says Ready but the ALB health check fails, you will chase two different truths during an incident.
- **Use readiness gates for ALB IP targets.** They help stop a rollout from marking pods Ready before the load balancer trusts them.

## Clean up before the ALB and EKS control plane keep billing

Run the Kubernetes cleanup first so the controller can delete the ALB, target groups, and security groups. Then destroy the Terraform resources.

```bash
bash cleanup.sh
terraform destroy
```

In my run, `cleanup.sh` finished in 64 seconds and Terraform destroyed 23 resources.

## What I didn't test

- HTTPS listeners, ACM certificates, WAF, Shield, or WAFv2. The lab uses HTTP only.
- Private subnets or NAT gateways. The worker nodes are plain EC2 instances in public subnets.
- A production IAM policy for the controller. The lab uses a broad `elasticloadbalancing:*` policy; use the official controller policy for real clusters.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/README.md)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/fix.sh)
- [`k8s/app-broken.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/k8s/app-broken.yaml)
- [`k8s/app-fixed.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/k8s/app-fixed.yaml)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/from-eks-alb-503s-to-200s-with-ready-pods-and-healthy/evidence): the raw output of my run

## Sources

- [Helm quickstart](https://helm.sh/docs/intro/quickstart/)
- [AWS Load Balancer Controller documentation](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/)

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.
---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
