---
title: "Count Every Order Once With Prometheus and OpenTelemetry Side by Side in Splunk"
description: "Duplicate Prometheus metrics on the Splunk OTel Collector DaemonSet: a cluster-wide scrape job read each pod from both nodes, 400 orders for 200. A node field selector or autodetect.prometheus gave exactly 200 over one series a pod, matching OpenTelemetry."
image: /assets/posts/count-every-order-once-with-prometheus-and-opentelemetry/cover.png
image_alt: "Cover: Count Every Order Once With Prometheus and OpenTelemetry Side by Side in Splunk"
tags: ["opentelemetry", "splunk", "kubernetes", "prometheus"]
channels: []
lab: labs/count-every-order-once-with-prometheus-and-opentelemetry
linkedin: "One field selector gave every pod one scraper: 200 orders, 200 counted in Splunk.\nWithout it, the Prometheus counter said 400: duplicate metrics.\n\nThe app was fine. The scrape job wasn't scoped.\n\n→ The Splunk OTel Collector agent runs on every node\n→ A scrape job with cluster-wide pod discovery runs in every agent\n→ So each pod was scraped by both nodes, and its counter reached Splunk twice\n\nScoping it takes one change:\n→ One field selector, spec.nodeName=${env:K8S_NODE_NAME}, on the pod discovery\n→ Or one chart value, autodetect.prometheus: true\n\nWhat I measured on EKS, 200 orders a run:\n→ Cluster-wide job: 400 over 4 series, 2 per pod\n→ Node selector and autodetect: 200 over 2 series, in all four runs\n→ OpenTelemetry: 200 in every run\n\nIf you run Prometheus next to OpenTelemetry during a migration, count a counter's series per pod in Splunk. In my lab, two series for one pod, differing only by host, meant two scrapers.\n\nI built and tested it in a disposable AWS lab and destroyed the resources afterwards. The post has every step:\n{LINKS}\n\nHow do you scope Prometheus scraping on your Collector DaemonSet?\n\nI work at Splunk, part of Cisco. Views are my own.\n\n#OpenTelemetry #Splunk #Kubernetes #Prometheus"
replies: [{"url": "https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/48546", "text": "I measured the DaemonSet case on EKS with two nodes and the Splunk OTel Collector chart. A pod-discovery scrape job with no node filter read each pod from both agents, so a counter summed to twice the real count over two series per pod. Adding the field selector spec.nodeName=${env:K8S_NODE_NAME} to kubernetes_sd_configs, or using the chart's node-scoped autodetection, gave one series per pod and the exact count in every run. I wrote up the full build here: {POST_URL}"}, {"url": "https://github.com/open-telemetry/opentelemetry-collector/discussions/9998", "text": "Not the same setup as yours (I tested a DaemonSet agent scraping app pods, not Prometheus scraping the Collector), but the symptom matched: identical series that differ only by the Collector instance that produced them. In my lab the extra series carried a different host.name and host.id per agent, and scoping discovery to each agent's node removed them. I wrote up the full build here: {POST_URL}"}]
---
![Cover: Count Every Order Once With Prometheus and OpenTelemetry Side by Side in Splunk](https://abhimanyu9988.github.io/assets/posts/count-every-order-once-with-prometheus-and-opentelemetry/cover.png)

One field selector, or one Splunk OTel Collector chart value (`autodetect.prometheus: true`), gave each of my `orders` pods **one scraper and one series in Splunk**. The Prometheus counter then matched OpenTelemetry's own count exactly: **200 orders, 200 counted**, in all four runs.

Before that, I had the scrape job many teams add first: Kubernetes pod discovery with no node filter. The agent runs as a DaemonSet, so the agent on each of my two nodes scraped both pods, and Splunk's Prometheus counter read 400 for the same 200 orders. Duplicate metrics, one copy per node.

Node-scoped scraping is in the OpenTelemetry Kubernetes blueprint. I wanted to see what Splunk holds in each case, which dimensions split a pod's counter, and whether the chart's autodetection really stays on its own node.

> **TL;DR**
> - A `spec.nodeName=${env:K8S_NODE_NAME}` field selector on pod discovery, or `autodetect.prometheus: true`, gave each pod one scraper and Splunk 200 over 2 series in all four runs.
> - A scrape job with cluster-wide pod discovery runs in every agent of the DaemonSet, so on two nodes each pod was scraped twice: 400 over 4 series, 2 a pod.
> - OpenTelemetry's pushed count of the same orders was 200 in every run.
> - To check your own: count the counter's series per pod in Splunk. In my lab, every extra scraper added one series per pod, told apart by `host.name` and `host.id`.

![Splunk Observability Cloud chart and data table: the Prometheus counter at 400 for the everywhere runs while OpenTelemetry's POST count is 200](/abhimanyu/articles/count-every-order-once-with-prometheus-and-opentelemetry/cover.png)

## Why a DaemonSet scrapes every pod twice

Many teams keep Prometheus endpoints next to OpenTelemetry while they move over, and a natural first step is a `prometheus` receiver with Kubernetes pod discovery on the agent. On a DaemonSet that config runs in every agent, and every agent discovers every matching pod. [collector-contrib issue 48546](https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/48546) describes the same DaemonSet case, with commenters weighing the Target Allocator, hashmod sharding and node-scoped discovery.

Each pod in my lab records who reads its `/metrics`. In the first run, both agents read both pods, and Splunk held four series:

```text
everywhere-1: orders sent 200, ok 200; the pods counted 200; scrapers per pod (IP:scrapes): orders-a-79b5f68684-9l254 [ 10.0.0.146(agent on ip-10-0-0-146.eu-central-1.compute.internal):9 10.0.1.6(agent on ip-10-0-1-6.eu-central-1.compute.internal):9 ] orders-b-d8f8bbcd5-dbd22 [ 10.0.0.146(agent on ip-10-0-0-146.eu-central-1.compute.internal):9 10.0.1.6(agent on ip-10-0-1-6.eu-central-1.compute.internal):9 ]
everywhere-1 in Splunk: orders_created_total (series by name: orders_created_total 4 ) 400 over 4 series, at most 2 a target (targets: 10.0.0.76:8080,10.0.1.5:8080); OpenTelemetry POST 200 count 200; the pods counted 200
```

The lab also prints what tells the series apart. Here is the first pod's pair; the same line goes on with the second pod's pair, in the lab's `evidence/04-reproduce.log`:

```text
everywhere-1 series told apart by AWSUniqueId,cloud.availability_zone,host.id,host.name: 10.0.0.76:8080 AWSUniqueId=i-046e48b462955f32c_eu-central-1_<account-id> cloud.availability_zone=eu-central-1a host.id=i-046e48b462955f32c host.name=ip-10-0-0-146.eu-central-1.compute.internal; 10.0.0.76:8080 AWSUniqueId=i-0788642e8a31ba1bb_eu-central-1_<account-id> cloud.availability_zone=eu-central-1b host.id=i-0788642e8a31ba1bb host.name=ip-10-0-1-6.eu-central-1.compute.internal
```

They differ only in where the agent ran: `host.name`, `host.id`, `cloud.availability_zone` and `AWSUniqueId`, stamped per node by the chart's resource detection. That is right for host metrics, and it means a `sum()` across series counts each pod once per agent.

![Splunk data table for everywhere-1: four series of orders_created_total summing to 400, two per pod, told apart by host.id, host.name and availability zone](/abhimanyu/articles/count-every-order-once-with-prometheus-and-opentelemetry/step-1.png)

## One scraper per pod, from the pod's own node

A pod lives on exactly one node, so if each agent only looks at its own node, each pod gets one scraper. I tried two ways, each a small change to the chart values.

The node field selector keeps your own scrape job. The chart already sets `K8S_NODE_NAME` on every agent pod from `spec.nodeName`:

```yaml
kubernetes_sd_configs:
  - role: pod
    namespaces:
      names: [demo]
    selectors:
      - role: pod
        field: spec.nodeName=${env:K8S_NODE_NAME}
```

Or drop the job and turn on autodetection. It scrapes each pod annotated `prometheus.io/scrape: "true"` once, by the agent on that pod's node, through the agent's `k8s_observer`:

```yaml
autodetect:
  prometheus: true
```

## Run it in your own account in 3 steps

### Step 1: Create the cluster (EKS took 7m8s in my run, on two t3.large nodes)

```bash
git clone https://github.com/Abhimanyu9988/Abhimanyu9988.github.io.git
cd Abhimanyu9988.github.io/labs/count-every-order-once-with-prometheus-and-opentelemetry
export AWS_REGION=eu-central-1 LAB_NAME=prom-lab TF_VAR_lab_name=prom-lab
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

You get EKS 1.34 with two t3.large nodes. The app is one Flask service with prometheus-client 0.26.0 and zero-code OpenTelemetry 0.66b2, as `orders-a` on one node and `orders-b` on the other.

### Step 2: The cluster-wide scrape job (333 s in my run)

Run `./reproduce.sh`. It installs the Splunk OTel Collector chart 0.163.1 with `values-everywhere.yaml`, deploys both pods, runs everywhere-1 and everywhere-2 (20 s at 10 orders a second each) and reads everywhere-1 back from Splunk.

### Step 3: The two node-scoped configs (601 s in my run)

Run `./fix.sh`. It switches the agents to `values-node.yaml`, then `values-autodetect.yaml`, two runs each, and reads all six runs back from Splunk. Before each switch it stops the last run's pods, so no run is scraped under two configs.

## The proof: 400 becomes 200, and Prometheus agrees with OpenTelemetry

Every run sent 200 orders, and the pods counted 200 themselves:

| Run | Scraper-pod pairs | Prometheus counter in Splunk | Series (most per pod) | OpenTelemetry count |
| --- | --- | --- | --- | --- |
| everywhere-1 | 4 | 400 | 4 (2) | 200 |
| everywhere-2 | 4 | 400 | 4 (2) | 200 |
| node-1 | 2 | 200 | 2 (1) | 200 |
| node-2 | 2 | 200 | 2 (1) | 200 |
| autodetect-1 | 2 | 200 | 2 (1) | 200 |
| autodetect-2 | 2 | 200 | 2 (1) | 200 |

With the field selector and with autodetection, each pod was read only by the agent on its own node:

```text
node-1: orders sent 200, ok 200; the pods counted 200; scrapers per pod (IP:scrapes): orders-a-9cb4df854-wb7fr [ 10.0.0.146(agent on ip-10-0-0-146.eu-central-1.compute.internal):9 ] orders-b-6df79594c-8xwz4 [ 10.0.1.6(agent on ip-10-0-1-6.eu-central-1.compute.internal):10 ]
node-1 in Splunk: orders_created_total (series by name: orders_created_total 2 ) 200 over 2 series, at most 1 a target (targets: 10.0.0.76:8080,10.0.1.160:8080); OpenTelemetry POST 200 count 200; the pods counted 200
autodetect-1: orders sent 200, ok 200; the pods counted 200; scrapers per pod (IP:scrapes): orders-a-74dbf6b7f4-4ss5t [ 10.0.0.146(agent on ip-10-0-0-146.eu-central-1.compute.internal):9 ] orders-b-6d488bfdcf-gmcxb [ 10.0.1.6(agent on ip-10-0-1-6.eu-central-1.compute.internal):9 ]
autodetect-1 in Splunk: orders_created_total (series by name: orders_created_total 2 ) 200 over 2 series, at most 1 a target (targets: 10.0.0.184:8080,10.0.1.52:8080); OpenTelemetry POST 200 count 200; the pods counted 200
```

![Splunk data table: the autodetect runs at 200 for both the Prometheus counter and OpenTelemetry's POST count](/abhimanyu/articles/count-every-order-once-with-prometheus-and-opentelemetry/step-2.png)

## Use it for your own scrape jobs

- **Keep your job, add the selector:** if you already relabel and filter in a `prometheus` receiver on the agent, the field selector is the smallest change.
- **Annotations instead of jobs:** autodetection needs no job, but it scrapes every pod on the node with `prometheus.io/scrape: "true"`, not only yours, so check which pods carry the annotation first.
- **Count series per pod:** in my lab, each extra scraper added one series per pod, told apart only by `host.name`, `host.id`, `cloud.availability_zone` and `AWSUniqueId`. Two series for one pod were the sign of two scrapers.
- **Or push it instead:** record `orders_created` as an OpenTelemetry counter and send it over OTLP, as these pods already did for request duration, and no scrape job is needed for it.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Cluster-level endpoints** such as kube-state-metrics, which live on one node and suit a single scraper or the Target Allocator.
- **Nodes without an agent:** node-scoped discovery doesn't scrape a pod there.
- **Scale:** one app, two nodes, one load level and a 10 s scrape interval.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/k8s/20-load.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/reproduce.sh)
- [`splunk.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/splunk.sh)
- [`values-autodetect.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/values-autodetect.yaml)
- [`values-everywhere.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/values-everywhere.yaml)
- [`values-node.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/values-node.yaml)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/count-every-order-once-with-prometheus-and-opentelemetry/evidence): the raw output of my run

## Sources

- [collector-contrib issue 48546: Prometheus receiver and leader election on a DaemonSet](https://github.com/open-telemetry/opentelemetry-collector-contrib/issues/48546)
- [Collector discussion 9998: multiple Collector pods give duplicate metrics](https://github.com/open-telemetry/opentelemetry-collector/discussions/9998)
- [Kubernetes Cluster Observability blueprint (OpenTelemetry)](https://opentelemetry.io/docs/guidance/blueprints/k8s-observability-blueprint/)
- [Kubernetes Prometheus Node source (Bindplane)](https://docs.bindplane.com/integrations/sources/kubernetes-prometheus-node)
- [Splunk OTel Collector chart values, autodetect.prometheus](https://github.com/signalfx/splunk-otel-collector-chart/blob/main/helm-charts/splunk-otel-collector/values.yaml)

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
