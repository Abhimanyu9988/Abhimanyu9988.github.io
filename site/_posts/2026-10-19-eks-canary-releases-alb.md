---
title: "Roll back an EKS release in 16 seconds without restarting a pod"
description: "Build an EKS lab where the AWS Load Balancer Controller sends 10% of ALB traffic to v2, shifts to 50/50, then rolls back to 100/0 by changing an Ingress annotation."
image: /assets/posts/eks-canary-releases-alb/cover.png
image_alt: "Chart of curl tallies per ALB weight stage: v2 gets 21 of 200 at 90/10, 108 at 50/50, and 0 after rollback to 100/0."
tags: ["aws", "eks", "kubernetes", "devops"]
channels: ["devto", "hashnode"]
lab: labs/eks-canary-releases-alb
linkedin: "A Kubernetes rollback usually means a redeploy.\nMine took 16 seconds and didn't restart a single pod.\n\nHere's what I built on EKS:\n\n→ v1 and v2 of the app run side by side, each behind its own Service\n→ The AWS Load Balancer Controller turns one Ingress into an ALB with weighted target groups\n→ The traffic split lives in a single Ingress annotation\n\nThen I moved the dial with kubectl:\n90/10: 21 of 200 requests hit v2\n50/50: 108 of 200\n100/0: 0. Fully back on v1 in 16 seconds.\n\nNo rollout. No new pods. Restarts: 0.\n\nWhy it matters: a plain Kubernetes Service splits traffic by pod count, so a real 10% canary would need nine v1 pods for every v2 pod. The ALB gives you an actual dial.\n\nI built and tested it in a throwaway AWS account. Every step, the Terraform and the raw output are in the post:\n{LINKS}\n\nHow do you roll back today: redeploy, or shift traffic?\n\n#aws #eks #kubernetes #devops"
replies: []
---
![Chart of curl tallies per ALB weight stage: v2 gets 21 of 200 at 90/10, 108 at 50/50, and 0 after rollback to 100/0.](https://abhimanyu9988.github.io/assets/posts/eks-canary-releases-alb/cover.png)

One kubectl annotate command moves an ALB from 90/10 to 50/50 to 100/0 between two versions of an app on EKS, and the pods never restart. Without it, a plain Service splits traffic by pod count, so a 10% canary would need nine v1 pods for every v2 pod. I rebuilt this in a throwaway AWS account with the AWS Load Balancer Controller and Pod Identity, and the post shows the curl tallies and the ALB listener-rule weights at each step.

The useful part is the rollback path: v2 can stay deployed and healthy, but the ALB stops sending it user traffic. No Deployment rollout is needed.

**What you'll build**

An internet-facing ALB in front of two EKS app versions, with weighted target groups managed from Kubernetes.

My run took 25.4 minutes; the README says to expect about 45 minutes end to end.

The README also says to expect under 1 USD in a throwaway account.

## How it works

A normal Kubernetes Service load-balances across the pods it selects. If one Service selects both versions, the split is tied to the current endpoints, not to a release weight you can set to 10%.

The AWS Load Balancer Controller watches an Ingress and creates an AWS Application Load Balancer. In this lab, the Ingress has a weighted `forward` action that points at two Services: `app-v1` and `app-v2`. The weights live in one Ingress annotation. EKS Pod Identity gives the controller AWS credentials without adding load balancer permissions to the node role.

The pods stay at three v1 replicas and one v2 replica. The ALB listener rule decides whether v2 gets 10%, 50%, or 0%.

## Build it

Prerequisites: Terraform, AWS CLI v2, kubectl, helm, jq, and curl.

1. Clone the lab, set one lab name, and create the AWS resources. `LAB_NAME` is for the scripts and `TF_VAR_lab_name` gives Terraform the same value. The apply creates the EKS 1.31 cluster, two `t3.medium` workers, the Pod Identity agent addon, and the Pod Identity association. The README says this apply is about 12-15 minutes.

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/eks-canary-releases-alb
export AWS_REGION=eu-central-1
export LAB_NAME=canary
export TF_VAR_lab_name="$LAB_NAME"
terraform init && terraform apply
```

A fresh apply adds the 23 Terraform resources from the plan. Names, IDs, and ARNs will differ in your account; this is the trimmed output from my run:

```text
Plan: 23 to add, 0 to change, 0 to destroy.
...
aws_eks_addon.pod_identity_agent: Creation complete after 26s [id=shift-traffic-gradually-713185-eks:eks-pod-identity-agent]
aws_eks_pod_identity_association.lbc: Creation complete after 1s [id=a-tdp6e0ovxnxudtscn]
Outputs:
cluster_name = "shift-traffic-gradually-713185-eks"
lbc_role_arn = "arn:aws:iam::<account-id>:role/hero-lab-shift-traffic-gradually-713185-lbc"
vpc_id = "vpc-03c2a0425ca6709c8"
```

2. Run the before-state script. It creates the demo namespace, two Deployments, three v1 pods, one v2 pod, three Services, and a tester pod. Then it curls the combined Service from inside the cluster.

```bash
bash reproduce.sh
```

This is the baseline: there is no Ingress, no external ALB, and no release weight to change. The sample is random, but it shows the important limit: a plain Service does not give you a 10% dial.

3. Install the controller, create the weighted Ingress, and run the traffic stages.

```bash
bash fix.sh
```

`fix.sh` installs AWS Load Balancer Controller Helm chart `1.11.0` with controller `v2.11.0`, using Pod Identity. It applies `k8s/ingress.yaml`, waits for the ALB, then walks the weights through 90/10, 50/50, and 100/0. The tallies in the next section come from this run.

The key config is the weighted forward action on the Ingress:

```yaml
alb.ingress.kubernetes.io/actions.weighted: >-
  {"type":"forward","forwardConfig":{"targetGroups":[{"serviceName":"app-v1","servicePort":80,"weight":90},{"serviceName":"app-v2","servicePort":80,"weight":10}]}}
```

4. Re-run the same dial manually if you want to test the change yourself. The script already ran these stages; these are the commands behind the 50/50 step and the rollback step.

```bash
kubectl -n demo annotate ingress app --overwrite 'alb.ingress.kubernetes.io/actions.weighted={"type":"forward","forwardConfig":{"targetGroups":[{"serviceName":"app-v1","servicePort":80,"weight":50},{"serviceName":"app-v2","servicePort":80,"weight":50}]}}'
kubectl -n demo annotate ingress app --overwrite 'alb.ingress.kubernetes.io/actions.weighted={"type":"forward","forwardConfig":{"targetGroups":[{"serviceName":"app-v1","servicePort":80,"weight":100},{"serviceName":"app-v2","servicePort":80,"weight":0}]}}'
```

Both commands update one Kubernetes object. The Deployments do not change.

## See it work

Before the ALB exists, the combined Service sends traffic to both versions with no weight setting:

```text
== Before: how is traffic split between v1 and v2? (40 in-cluster requests to Service 'app')
     34 version-v1
      6 version-v2
kubectl get ingress -A -> No resources found
Services of type LoadBalancer: 0
```

After `fix.sh`, the ALB listener rule and the curl tallies moved with the annotation:

| Stage | ALB listener-rule weights | Curl tally |
| --- | --- | --- |
| 90/10 | `app-v2=10`, `app-v1=90` | `v1=179 v2=21 other=0 of 200` => v2 10% |
| 50/50 | `app-v2=50`, `app-v1=50` | `v1=92 v2=108 other=0 of 200` => v2 54% |
| 100/0 | `app-v2=0`, `app-v1=100` | `v1=200 v2=0 other=0 of 200` => v2 0% |

For rollback, the ALB rule updated 3 seconds after `kubectl annotate`, and the sampled traffic was fully back on v1 after 16 seconds.

The last check is that the pods were not redeployed. The same pods were still running with zero restarts:

```text
pod app-v1-5b8cd854d6-2k7kl Running restarts=0 age=4m13s
pod app-v1-5b8cd854d6-6m5vm Running restarts=0 age=4m13s
pod app-v1-5b8cd854d6-krvvr Running restarts=0 age=4m13s
pod app-v2-649d75bcd8-dd555 Running restarts=0 age=4m13s
pod tester Running restarts=0 age=4m11s
```

That is the release control this lab proves: the app versions can stay deployed, while the ALB controls how much traffic each one receives.

## Make it yours

- Keep separate Services per version, like `app-v1` and `app-v2`. The weighted forward action points at Services, not directly at Deployments.
- Put the annotation update behind your normal release process. A 10% canary, a 50% step, and a 0% rollback are the same object update with different weights.
- For production IAM, use the official controller policy from the AWS Load Balancer Controller release instead of the simplified lab policy.

## Clean up

Delete the Ingress first so the controller removes the ALB, target groups, and security groups. Then destroy the Terraform resources. Keep the same lab name for destroy.

```bash
export AWS_REGION=eu-central-1
export LAB_NAME=canary
export TF_VAR_lab_name="$LAB_NAME"
bash cleanup.sh
terraform destroy
```

My cleanup removed the Ingress and namespace, then Terraform destroyed all 23 resources.

## What I didn't test

- TLS certificates, host-based routing, or multiple paths on the same ALB.
- Automatic metric-based promotion or rollback.
- Long-running connection behavior during a weight change.

## Sources

- [AWS Load Balancer Controller documentation](https://kubernetes-sigs.github.io/aws-load-balancer-controller/latest/)
- [Amazon EKS Pod Identities](https://docs.aws.amazon.com/eks/latest/userguide/pod-identities.html)

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
