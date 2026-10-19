---
title: "Roll back an EKS release in 16 seconds without restarting a pod"
description: "A real traffic dial for EKS releases: send 10% to v2 with one Ingress annotation, and roll back in 16 seconds without restarting a pod. Built and tested in a real AWS lab, every step included."
image: /assets/posts/eks-canary-releases-alb/cover.png
image_alt: "Chart of curl tallies per ALB weight stage: v2 gets 21 of 200 at 90/10, 108 at 50/50, and 0 after rollback to 100/0."
tags: ["aws", "eks", "kubernetes", "devops"]
channels: ["devto", "hashnode"]
lab: labs/eks-canary-releases-alb
linkedin: "A Kubernetes rollback usually means a redeploy.\nMine took 16 seconds and didn't restart a single pod.\n\nHere's what I built on EKS:\n\n→ v1 and v2 of the app run side by side, each behind its own Service\n→ The AWS Load Balancer Controller turns one Ingress into an ALB with weighted target groups\n→ The traffic split lives in a single Ingress annotation\n\nThen I moved the dial with kubectl:\n90/10: 21 of 200 requests hit v2\n50/50: 108 of 200\n100/0: 0. Fully back on v1 in 16 seconds.\n\nNo rollout. No new pods. Restarts: 0.\n\nWhy it matters: a plain Kubernetes Service splits traffic by pod count, so a real 10% canary would need nine v1 pods for every v2 pod. The ALB gives you an actual dial.\n\nI built and tested it in a throwaway AWS account. Every step, the Terraform and the raw output are in the post:\n{LINKS}\n\nHow do you roll back today: redeploy, or shift traffic?\n\n#aws #eks #kubernetes #devops"
replies: []
---
![Chart of curl tallies per ALB weight stage: v2 gets 21 of 200 at 90/10, 108 at 50/50, and 0 after rollback to 100/0.](https://abhimanyu9988.github.io/assets/posts/eks-canary-releases-alb/cover.png)

Every team running on Kubernetes knows this moment. A release goes out, something looks off, and the only "undo" button is another deploy: roll back the image, wait for new pods, hope nothing else breaks on the way.

I wanted a rollback that is just a dial. So I built one on EKS. The new version gets 10% of real traffic first, and if it misbehaves, one `kubectl` command sends everyone back to the old one. In my lab, traffic was fully back on v1 **16 seconds** later, and not a single pod restarted.

Here's how it works, and how to build it yourself.

> **TL;DR**
> - **What you get:** an Application Load Balancer in front of two versions of your app, with a traffic dial you turn from Kubernetes.
> - **The trick:** the AWS Load Balancer Controller reads the traffic weights from one Ingress annotation.
> - **The proof:** 90/10, then 50/50, then a rollback to 100/0 in 16 seconds, with 0 pod restarts.
> - **Time and cost:** my run took 25 minutes end to end in a throwaway AWS account; the lab budgets about 45 minutes and under 1 USD.

## Why a normal Kubernetes Service can't give you a 10% canary

A Service spreads requests across whatever pods match its selector. Put 3 v1 pods and 1 v2 pod behind one Service, and v2 gets "about a quarter, depending on luck". That's what my lab showed before the build:

```text
== Before: how is traffic split between v1 and v2? (40 in-cluster requests to Service 'app')
     34 version-v1
      6 version-v2
v1=34 v2=6  (ratio follows the pod count 3:1; to get 10% you would need 9 v1 pods per v2 pod)
```

So a real 10% canary means running nine v1 pods for every v2 pod. And rolling back means scaling or redeploying. There is no weight you can set to zero.

## The trick: one Ingress, two target groups, one dial

The AWS Load Balancer Controller turns a Kubernetes Ingress into an ALB. Give the Ingress a weighted `forward` action, and the ALB splits traffic between two target groups, one per Service, by weight. The number of pods behind each Service no longer matters.

The whole dial is one annotation:

```yaml
alb.ingress.kubernetes.io/actions.weighted: >-
  {"type":"forward","forwardConfig":{"targetGroups":[{"serviceName":"app-v1","servicePort":80,"weight":90},{"serviceName":"app-v2","servicePort":80,"weight":10}]}}
```

Change the numbers, and the controller updates the ALB listener rule. Your Deployments never change. The controller gets its AWS permissions through EKS Pod Identity, so there's no IRSA setup and nothing extra on the node role.

## Build it yourself in 4 steps

You need Terraform, the AWS CLI v2, kubectl, helm, jq and curl, plus an AWS account you can experiment in.

### Step 1: Create the cluster (about 12 to 15 minutes)

This creates an EKS 1.31 cluster with two `t3.medium` workers, the Pod Identity agent and the IAM role for the controller.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/eks-canary-releases-alb
export AWS_REGION=eu-central-1
export LAB_NAME=canary
export TF_VAR_lab_name="$LAB_NAME"
terraform init && terraform apply
```

You should see `Plan: 23 to add, 0 to change, 0 to destroy.` before you confirm.

### Step 2: Deploy two versions of the app

```bash
bash reproduce.sh
```

This creates `app-v1` (3 pods) and `app-v2` (1 pod) in the `demo` namespace, one Service for each, and shows the "before" split above.

### Step 3: Add the load balancer and the dial

```bash
bash fix.sh
```

This installs the AWS Load Balancer Controller (Helm chart `1.11.0`, controller `v2.11.0`) with Pod Identity, applies `k8s/ingress.yaml` with the 90/10 weights, waits for the ALB, and then walks through 90/10, 50/50 and 100/0, counting 200 requests at each stage.

### Step 4: Turn the dial yourself

Send half the traffic to v2:

```bash
kubectl -n demo annotate ingress app --overwrite 'alb.ingress.kubernetes.io/actions.weighted={"type":"forward","forwardConfig":{"targetGroups":[{"serviceName":"app-v1","servicePort":80,"weight":50},{"serviceName":"app-v2","servicePort":80,"weight":50}]}}'
```

Something wrong with v2? Roll back:

```bash
kubectl -n demo annotate ingress app --overwrite 'alb.ingress.kubernetes.io/actions.weighted={"type":"forward","forwardConfig":{"targetGroups":[{"serviceName":"app-v1","servicePort":80,"weight":100},{"serviceName":"app-v2","servicePort":80,"weight":0}]}}'
```

## The proof: 90/10, 50/50, and a 16-second rollback

Each stage sent 200 requests through the ALB:

| Weights | ALB listener rule | What 200 requests hit |
| --- | --- | --- |
| 90/10 | app-v1=90, app-v2=10 | v1=179, v2=21 |
| 50/50 | app-v1=50, app-v2=50 | v1=92, v2=108 |
| 100/0 (rollback) | app-v1=100, app-v2=0 | v1=200, v2=0 |

The rollback timing, straight from the run:

```text
ALB rule updated 3s after kubectl annotate; 100% of sampled traffic on v1 after 16s
```

And the pods? The same ones, still running, never restarted:

```text
pod app-v1-5b8cd854d6-2k7kl Running restarts=0 age=4m13s
pod app-v1-5b8cd854d6-6m5vm Running restarts=0 age=4m13s
pod app-v1-5b8cd854d6-krvvr Running restarts=0 age=4m13s
pod app-v2-649d75bcd8-dd555 Running restarts=0 age=4m13s
```

That's the whole point: v2 can stay deployed and healthy while the load balancer decides how much traffic it gets.

## Use it for your own releases

- **Give each version its own Service**, like `app-v1` and `app-v2`. The weighted action points at Services, not Deployments.
- **Make the annotation your release step.** 10%, 50%, 100% and a rollback to 0% are the same one-line change, so they fit any pipeline.
- **Use the official IAM policy in production.** The lab's controller policy is simplified for a throwaway account; take the official one from the controller release.

## Clean up (the ALB costs money while it runs)

Delete the Ingress first, so the controller removes the ALB, target groups and security groups, then destroy everything else. Use the same lab name.

```bash
export AWS_REGION=eu-central-1
export LAB_NAME=canary
export TF_VAR_lab_name="$LAB_NAME"
bash cleanup.sh
terraform destroy
```

## What I didn't test

- TLS certificates, host-based routing, or several paths on one ALB.
- Automatic promotion or rollback driven by metrics.
- Long-running connections while the weights change.

## Sources

- [AWS Load Balancer Controller documentation](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/)
- [Amazon EKS Pod Identities](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
