---
title: "Count Flink Restarts in Splunk With OpenTelemetry: 3 Pod Deletions, 7 Restarts"
description: "I deleted a Flink TaskManager pod 5 times on EKS. Job state blinked for 5 s and Splunk's Kubernetes metrics stayed flat. Flink counted 7 restarts from 3 deletions, and the OpenTelemetry Flink receiver put exactly 7 in Splunk."
image: /assets/posts/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/cover.png
image_alt: "Cover: Count Flink Restarts in Splunk With OpenTelemetry: 3 Pod Deletions, 7 Restarts"
tags: ["kubernetes", "opentelemetry", "splunk", "aws"]
channels: []
lab: labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod
linkedin: "Your Flink job says RUNNING. Your pods look healthy.\nIt may still be restarting.\n\nI deleted a TaskManager pod 5 times on EKS:\n→ Job state: RESTARTING for 5 s, then RUNNING again\n→ Kubernetes metrics in Splunk: available 1, container restarts 0, every time\n\nFlink's own counter: 3 deletions, 7 restarts.\nWith the OpenTelemetry Flink receiver, Splunk counted 7 too, at the same two instants.\nEach restart was in a Splunk point 17 to 21 s later.\n\nThe receiver isn't in the Splunk distribution, so I ran the contrib Collector next to it. The config is 20 lines.\n\nEvery step and the raw output:\n{LINKS}\n\nHow do you alert on Flink restarts today?\n\n#ApacheFlink #OpenTelemetry #Splunk #Kubernetes"
replies: []
---
![Cover: Count Flink Restarts in Splunk With OpenTelemetry: 3 Pod Deletions, 7 Restarts](https://abhimanyu9988.github.io/assets/posts/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/cover.png)

I deleted a Flink TaskManager pod five times on EKS. Each time Flink restarted the job and reported RUNNING again about 5 seconds later, Kubernetes had a new pod Ready in 2 seconds, and **the Kubernetes metrics in Splunk Observability Cloud stayed flat**: available replicas 1, container restarts 0.

Flink's own restart counter told a different story. In the second phase, **3 pod deletions added 7 to Flink's `numRestarts`**, and with the **OpenTelemetry Flink receiver** sending to Splunk, Splunk's `flink.job.restart.count` rose by **exactly 7** over the same window. Each restart was in a Splunk data point 17 to 21 seconds after Flink's REST API showed it.

> **TL;DR**
> - Job state: RESTARTING for 5.0 to 5.1 s after each deletion, then RUNNING. A dashboard that polls state every 10 s or more can miss that entirely.
> - Kubernetes view in Splunk: `k8s.deployment.available` stayed at 1 in every 10 s point (rollup min) while `kubectl` saw 0, and `k8s.container.restarts` stayed at 0, because a deleted pod is replaced, not restarted.
> - Flink's restart counter: +1 per deletion in the first phase, but +3, +2 and +2 in the second. Splunk's counter matched Flink's REST API: +7 and +7.
> - The receiver also sent checkpoint counters and JVM GC for both the JobManager and the TaskManagers: 29 `flink.*` metric names in all.

![Flink's numRestarts read every second against Splunk's flink.job.restart.count in 10 second points, both rising from 2 to 9 over three pod deletions](/abhimanyu/articles/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/cover.png)

## Why restarts, not job state

A Flink job with a restart strategy heals itself: lose a TaskManager and it goes RESTARTING, then RUNNING again. Flink's [monitoring guide](https://flink.apache.org/2019/02/21/monitoring-apache-flink-applications-101/) lists restarts and checkpoints among the first metrics to watch, and the [AWS Managed Flink lifecycle post](https://aws.amazon.com/blogs/big-data/part-2-deep-dive-into-the-amazon-managed-service-for-apache-fink-application-lifecycle) describes an application that reports RUNNING while its job keeps failing and restarting. Flink's own test suite detects restarts with `numRestarts` ([FLINK-23807](https://issues.apache.org/jira/browse/FLINK-23807)), and [FLINK-34907](https://issues.apache.org/jira/browse/FLINK-34907) notes that a job is marked RUNNING when scheduling starts, before every task runs.

I'm a code owner of the OpenTelemetry Flink metrics receiver, so I wanted numbers: what job state, Kubernetes and Splunk each show when a TaskManager disappears, and whether the receiver's restart count in Splunk matches Flink's own.

## The setup

- **EKS**, two nodes. Flink 1.20.5 (`flink:1.20-java17`) as a session cluster: one JobManager, one TaskManager with 2 slots.
- **The job:** Flink's StateMachine example at parallelism 2, checkpoints every 5 s, fixed-delay restarts with a 5 s delay.
- **Splunk OTel Collector chart 0.163.1** on the cluster, which sends the Kubernetes metrics.
- **A watcher** on the other node read Flink's REST API once a second: job state, tasks running, `numRestarts`, checkpoint counts, plus Flink's own state-change timestamps.
- **Deleting the TaskManager pod** stands in for a node drain, an eviction or an out-of-memory kill.

Phase 1 had no Flink receiver. Phase 2 added the receiver, the only change.

## Phase 1: what job state and Kubernetes showed

Two deletions, 45 seconds apart:

| Deletion | Flink RESTARTING after | Not RUNNING | New pod Ready after | `kubectl` available min | Splunk available min | Flink restarts |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 1.2 s | 5.1 s | 2.0 s | 0 | 1 | +1 |
| 2 | 1.1 s | 5.0 s | 2.0 s | 0 | 1 | +1 |

"Not RUNNING" runs from Flink's RESTARTING timestamp to its next RUNNING timestamp. After RUNNING, every task was running and a new checkpoint had completed by the next one-second sample, in all five deletions, so the FLINK-34907 gap didn't show up at this scale.

Kubernetes saw the gap: `kubectl` read 0 available TaskManagers for about 2 seconds each time. Splunk didn't. I read `k8s.deployment.available` with `rollup='min'`, so a dip inside a 10 s point would survive, and every point was 1. The chart sets no `collection_interval` for its `k8s_cluster` receiver, which reports every 10 s by default, so a 2-second gap usually falls between two reports.

`k8s.container.restarts` stayed at 0 too. Splunk held a series for 3 TaskManager pods: the original and two replacements, each with 0 restarts. A deleted pod is replaced, so the restart count of a container never moves.

![Splunk chart of k8s.deployment.available for flink-taskmanager with rollup min, flat at 1 through the first phase, with one dip to 0 in the second](/abhimanyu/articles/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/step-1.png)

In five deletions, this chart caught one dip, in the second phase.

## Phase 2: the receiver in Splunk

The Flink receiver isn't in the Splunk distribution of the Collector, so I ran the upstream contrib Collector (0.165.0) next to it, sending OTLP to the Splunk agent on its node. This is the whole config:

```yaml
receivers:
  flink_metrics:
    endpoint: http://flink-jobmanager:8081
    collection_interval: 10s
processors:
  resource:
    attributes:
      - {key: deployment.environment, value: flink-lab, action: upsert}
  batch:
    timeout: 5s
exporters:
  otlphttp:
    endpoint: http://${env:HOST_IP}:4318
service:
  pipelines:
    metrics:
      receivers: [flink_metrics]
      processors: [resource, batch]
      exporters: [otlphttp]
```

`HOST_IP` comes from the pod's `status.hostIP`. The receiver type is `flink_metrics`; the older `flinkmetrics` name still works.

Then three more deletions. Flink's `numRestarts` rose by 3 for the first, then 2 and 2: 7 restarts from 3 deletions. Each job-state gap was still 5.0 s.

To compare like with like, the lab read both counts at the same two instants, 11:24:50 and 11:27:20 UTC, with no restart within 15 seconds of either:

| | At 11:24:50 | At 11:27:20 | Increase |
| --- | --- | --- | --- |
| Flink REST API `numRestarts` | 2 | 9 | +7 |
| Splunk `flink.job.restart.count` | 2 | 9 | +7 |

The receiver sends these as cumulative counters (Splunk lists `flink.job.checkpoint.count` as `CUMULATIVE_COUNTER`), and I read them with `rollup='latest'`, so each point holds the running total, not a per-point delta. Here's the chart in Splunk, with the same SignalFlow:

```text
data('flink.job.restart.count', filter=filter('deployment.environment', 'flink-lab'), rollup='latest').max().publish()
```

![Splunk chart of flink.job.restart.count rising in steps from 2 to 9 between 11:25 and 11:27 UTC](/abhimanyu/articles/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/step-2.png)

## How soon Splunk showed each restart

For each of the 7 restarts, from the second Flink's REST API first showed it:

- **To the end of the first 10 s Splunk point holding it:** 17 to 21 s.
- **To a Splunk query that returned it**, polling every 3 s during the deletions: 20 to 25 s.

The receiver reads every 10 s and the batch processor waits up to 5 s, so most of that delay is the path I chose. A 5 s `collection_interval` should shorten it; I didn't test that.

## Checkpoints and JVM GC

Between the same two instants, failed checkpoints matched: +2 in Flink, +2 in Splunk. Completed checkpoints were +53 in Flink's REST API and +58 in Splunk. With a checkpoint every 5 s, a 10 s Splunk point holds a value scraped somewhere inside it, not at its edge, so the two reads aren't at the same moment. The restart count could be lined up because it was still at both instants; the checkpoint count never is. Treat completed checkpoints in Splunk as a rate, not an exact tally.

`flink.jvm.gc.collections.count` arrived for the JobManager (G1 young and old generation, 2 series) and for the TaskManagers (8 series across the pods).

## Run it in your own account in 3 steps

### Step 1: Create the cluster (the EKS cluster took 7m28s in my run)

```bash
git clone https://github.com/Abhimanyu9988/showmerit.git
cd showmerit/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod
export AWS_REGION=eu-central-1 TF_VAR_region=eu-central-1 LAB_NAME=flink-lab TF_VAR_lab_name=flink-lab
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Without the receiver (about 6 minutes)

Run `./reproduce.sh`. It installs the Splunk OTel Collector chart, starts Flink and the job, deletes the TaskManager twice, and reads the Kubernetes metrics back from Splunk next to `kubectl`.

### Step 3: With the receiver (about 4 minutes)

Run `./fix.sh`. It starts the contrib Collector with the Flink receiver, deletes the TaskManager three more times, and compares Splunk's counters with Flink's REST API at the same instants.

To alert on it, a detector on the increase of `flink.job.restart.count` per job over a few minutes catches restarts that job state and pod health don't show.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Why some deletions counted more than one restart.** The first phase counted one per deletion and the second phase two or three; I recorded the counts, not the cause.
- **Other failures:** a crashing job, a lost JobManager, or a node failure. Only TaskManager pod deletions.
- **Scale.** One job, one TaskManager with 2 slots, one restart strategy, and a 10 s collection interval.
- **The receiver's maturity.** It's alpha, so metric names can still change.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/README.md)
- [`analyze.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/analyze.sh)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/fix.sh)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/k8s/20-load.yaml)
- [`k8s/30-collector.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/k8s/30-collector.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/reproduce.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod/evidence): the raw output of my run

## Sources

- [Monitoring Apache Flink Applications 101](https://flink.apache.org/2019/02/21/monitoring-apache-flink-applications-101/)
- [Deep dive into the Amazon Managed Service for Apache Flink application lifecycle (part 2)](https://aws.amazon.com/blogs/big-data/part-2-deep-dive-into-the-amazon-managed-service-for-apache-fink-application-lifecycle)
- [FLINK-34907: jobRunningTs should be the timestamp that all tasks are running](https://issues.apache.org/jira/browse/FLINK-34907)
- [FLINK-23807: use numRestarts to detect restarts in tests](https://issues.apache.org/jira/browse/FLINK-23807)
- [Flink metrics receiver (OpenTelemetry Collector contrib)](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/flinkmetricsreceiver)

Disclosure: I work at Splunk, part of Cisco, and I'm a code owner of the OpenTelemetry Flink metrics receiver. This is my personal writing; the post isn't sponsored or reviewed by my employer.

---
_I built this in a disposable AWS lab. The [lab code and raw output](https://github.com/Abhimanyu9988/showmerit/tree/main/labs/count-flink-restarts-in-splunk-with-opentelemetry-3-pod) are public. Drafted with AI assistance and reviewed by me._
