---
title: "Mask Tokens and Drop Health Check Spans With OTTL Before They Reach Splunk"
description: "On EKS, one OTTL statement in a gateway Collector masked a customer token in all 60 spans of 20 sample traces in Splunk APM, and a filter took health-check spans out of APM while metrics kept counting them. Naming url.query and url.full alone left it in http.target and http.url."
image: /assets/posts/mask-tokens-and-drop-health-check-spans-with-ottl-before/cover.png
image_alt: "Cover: Mask Tokens and Drop Health Check Spans With OTTL Before They Reach Splunk"
tags: ["opentelemetry", "splunk", "kubernetes", "aws"]
channels: []
lab: labs/mask-tokens-and-drop-health-check-spans-with-ottl-before
linkedin: "Customer tokens in URLs end up in your traces. So do health checks.\nOTTL in the OpenTelemetry Collector can fix both before Splunk sees them.\n\nI tested it on EKS, reading 20 sample traces per run back from Splunk APM:\n→ Batch only: the token in 60 of 60 spans, under url.query, url.full, http.target and http.url\n→ Two named statements (url.query, url.full): 60 spans masked, but http.target and http.url still held it (the services emitted old and new HTTP names, as during a migration)\n→ One replace_all_patterns over every attribute: 0 spans with the token, 60 masked, in both runs\n→ A filter on url.path: health-check spans gone from APM, still counted in the services' own metrics\n→ Every sample trace kept its 3 spans and both services\n\nThe three configs, every step and the raw output:\n{LINKS}\n\nWhich HTTP attribute names do your services still send?\n\nI work at Splunk, part of Cisco. Views are my own.\n\n#OpenTelemetry #Splunk #Kubernetes #Observability"
replies: []
---
![Cover: Mask Tokens and Drop Health Check Spans With OTTL Before They Reach Splunk](https://abhimanyu9988.github.io/assets/posts/mask-tokens-and-drop-health-check-spans-with-ottl-before/cover.png)

Every order in my lab carried a customer token in its query string. In Splunk APM, that token sat in **60 spans of every 20 sample traces**, under four attributes: `url.query`, `url.full`, `http.target` and `http.url`. The kubelet's health checks were in the traces too, about 40 a minute per service.

With **OTTL in a gateway Collector** (contrib 0.165.0), one `replace_all_patterns` statement masked the token in **all 60 spans, in both runs**, and one filter condition took every health-check span out of APM. Every sample trace still had its 3 spans and both services, and the gateway sent on **1,350 spans a run: 450 orders, 3 spans each**.

The quick start, naming `url.query` and `url.full`, masked those two. The same spans still held the raw token in `http.target` and `http.url`, because the services also emitted the older HTTP attribute names, as they do during a semantic-convention migration.

> **TL;DR**
> - Batch only: the token in 60 of 60 spans of 20 sample traces, in four attributes.
> - Two named statements (`url.query`, `url.full`): 60 spans masked, 60 still holding the token in `http.target` and `http.url`.
> - One statement over every attribute value: 0 spans with the token, 60 masked, in both runs.
> - Health checks: 0 in APM with the filter, while the services' own HTTP metric kept counting them.

![Spans holding the customer token in Splunk APM per run, by attribute, next to spans with token=REDACTED](/abhimanyu/articles/mask-tokens-and-drop-health-check-spans-with-ottl-before/cover.png)

## Why the token and the health checks end up in traces

Zero-code instrumentation records the URL of every HTTP request on its span, query string included. Splunk's [guide to sensitive data](https://help.splunk.com/en/splunk-observability-cloud/manage-data/manage-sensitive-data) says instrumentation that knows a query parameter is sensitive should scrub it before setting `url.query`, `url.path` and `url.full`, and it can only know the names it was built for. How much of the URL to collect by default is still an [open semantic-conventions discussion](https://github.com/open-telemetry/semantic-conventions/issues/860). Health checks are requests too, so the instrumentation traces them like any other.

Here is what Splunk APM held for the first run, read back trace by trace:

```text
before-1: 20 of 20 sample traces in Splunk APM; fewest spans 3; both services in 20;
  raw token in 60 spans (attributes: http.target(40) http.url(20) url.full(20) url.query(40) ); token=REDACTED in 0 spans
```

## The setup

- **EKS**, two t3.large nodes. Two Flask services, `orders` and `inventory`, with zero-code OpenTelemetry 0.66b2 and `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup`, which the [HTTP migration guide](https://opentelemetry.io/docs/specs/semconv/non-normative/http-migration/) defines as emitting both the old and the stable conventions.
- `POST /orders/<n>?token=...` on orders, which calls `GET /reserve/<n>?token=...` on inventory. Each order has its own token and a trace ID the load picks.
- The kubelet checks `/healthz` on each service every 2 s (readiness) and every 10 s (liveness).
- Both services send to a **gateway Collector, contrib 0.165.0**, which forwards to the **Splunk OTel Collector chart 0.163.1** agent on its node and on to Splunk Observability Cloud.
- Each run: fresh pods, 45 s at 10 orders a second, 20 sample traces read back from Splunk APM by trace ID. Two runs per gateway config.

## The OTTL

The three configs differ only in the traces pipeline. Base is `batch` alone. Both OTTL configs add the same filter; they differ in the transform:

```yaml
filter/healthchecks:
  error_mode: ignore
  trace_conditions:
    - span.attributes["url.path"] == "/healthz"
transform/redact:            # named.yaml
  error_mode: ignore
  trace_statements:
    - replace_pattern(span.attributes["url.query"], "token=[^&]*", "token=REDACTED")
    - replace_pattern(span.attributes["url.full"], "token=[^&]*", "token=REDACTED")
transform/redact:            # all.yaml
  error_mode: ignore
  trace_statements:
    - replace_all_patterns(span.attributes, "value", "token=[^&]*", "token=REDACTED")
```

## What reached Splunk APM

| Run | Gateway config | Traces found | Spans each | Spans with the raw token | Where | Spans with `token=REDACTED` |
| --- | --- | --- | --- | --- | --- | --- |
| before-1, before-2 | batch only | 20 of 20 | 3 | 60 | `url.query` 40, `http.target` 40, `url.full` 20, `http.url` 20 | 0 |
| named-1, named-2 | `url.query` + `url.full` | 20 of 20 | 3 | 60 | `http.target` 40, `http.url` 20 | 60 |
| all-1, all-2 | every attribute | 20 of 20 | 3 | **0** | none | 60 |

Both runs of each config gave the same counts. The 40s are the two server spans of each trace, and the 20s the client span.

```text
named-1: ... raw token in 60 spans (attributes: http.target(40) http.url(20) ); token=REDACTED in 60 spans
all-1: 20 of 20 sample traces in Splunk APM; fewest spans 3; both services in 20; raw token in 0 spans (attributes: none); token=REDACTED in 60 spans
```

So the named statements did exactly what they say. During a migration the same value also lives under the older names, and `replace_all_patterns` reaches every attribute value, whatever it's called. Adding statements for `http.target` and `http.url` would be the narrower alternative.

## Health checks: out of traces, still in metrics

![Health checks per minute for each config and service: Splunk APM's GET /healthz spans next to the services' HTTP metric and the 36 a minute the probes give](/abhimanyu/articles/mask-tokens-and-drop-health-check-spans-with-ottl-before/step-1.png)

Over the same 10 s points in each config's period, per service:

| Config | APM, `GET /healthz` spans | Services' HTTP metric, `/healthz` | The probes give about |
| --- | --- | --- | --- |
| batch only (250 s) | 166 and 166 | 168 and 163 | 150 |
| named (140 s) | 0 and 0 | 90 and 90 | 84 |
| every attribute (130 s) | 0 and 0 | 89 and 85 | 78 |

The metric comes from the app itself, so the trace filter doesn't touch it: health-check rates stay visible for alerting while the traces get cleaner. The counts run a little above the probe figure, likely from new pods' first checks after each restart; I didn't measure that. In the gateway's own counters for the load window, all-1 received 1,445 spans, dropped 95 and sent 1,350; all-2 received 1,443, dropped 93 and sent 1,350.

## What to plan for

- **Maturity:** OTTL itself is stable for traces, metrics and logs, while in contrib 0.165.0 the [transform processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor) is beta and the [filter processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/filterprocessor) alpha for traces.
- **Cost:** `replace_all_patterns` runs the pattern on every attribute value of every span. I didn't measure CPU here, so try it at your own volume.
- **The filter's attribute:** it matches `url.path`. Services that only emit the older names need `http.target` instead.
- **A slow health check** won't show up in a trace any more. Its count and duration stay in the services' HTTP metric.

## Run it in your own account in 3 steps

### Step 1: Create the cluster (the EKS cluster took 6m58s in my run)

```bash
git clone https://github.com/Abhimanyu9988/showmerit.git
cd showmerit/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before-with-ottl-before
export AWS_REGION=eu-central-1 TF_VAR_region=eu-central-1 LAB_NAME=ottl-lab TF_VAR_lab_name=ottl-lab
export SPLUNK_REALM=us1 SPLUNK_ACCESS_TOKEN=your-ingest-token SPLUNK_API_TOKEN=your-api-token
terraform init && terraform apply -auto-approve
```

### Step 2: Batch only (about 7 minutes)

Run `./reproduce.sh`. It installs the Splunk OTel Collector chart, the gateway and both services, runs before-1 and before-2, and reads before-1's sample traces back from Splunk APM.

### Step 3: The two OTTL configs (about 14 minutes)

Run `./fix.sh`. It runs named-1, named-2, all-1 and all-2, then reads every run's sample traces and the health checks back from Splunk.

## Clean up (the cluster costs money while it runs)

```bash
./cleanup.sh
terraform destroy -auto-approve
```

## What I didn't test

- **Tokens elsewhere:** in headers, request bodies, span events or logs. Each needs its own statement.
- **CPU and latency** of the processors, and more than one load level or gateway.
- **Redacting in the instrumentation** instead of the Collector, and services that only emit the older names.

## Every file in this lab

Everything above runs from these files. Open any of them to copy it, or clone the repo as shown:

- [`README.md`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/README.md)
- [`check.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/check.sh)
- [`cleanup.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/cleanup.sh)
- [`fix.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/fix.sh)
- [`k8s/00-build.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/k8s/00-build.yaml)
- [`k8s/10-app.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/k8s/10-app.yaml)
- [`k8s/20-load.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/k8s/20-load.yaml)
- [`k8s/30-gateway.yaml`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/k8s/30-gateway.yaml)
- [`lib.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/lib.sh)
- [`main.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/main.tf)
- [`providers.tf`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/providers.tf)
- [`reproduce.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/reproduce.sh)
- [`splunk.sh`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/blob/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/splunk.sh)
- [`evidence/`](https://github.com/Abhimanyu9988/Abhimanyu9988.github.io/tree/main/labs/mask-tokens-and-drop-health-check-spans-with-ottl-before/evidence): the raw output of my run

## Sources

- [Manage sensitive data in Splunk Observability Cloud](https://help.splunk.com/en/splunk-observability-cloud/manage-data/manage-sensitive-data)
- [Semantic conventions issue 860: default collection of url.full and url.query](https://github.com/open-telemetry/semantic-conventions/issues/860)
- [HTTP semantic convention stability migration](https://opentelemetry.io/docs/specs/semconv/non-normative/http-migration/)
- [OpenTelemetry Transformation Language (OTTL)](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/pkg/ottl)
- [Transform processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor)
- [Filter processor](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/filterprocessor)
- [OTTL reaches v1 (The New Stack)](https://thenewstack.io/kubernetes-workload-conformance-ai/)

Disclosure: I work at Splunk, part of Cisco. This is my personal blog; the post isn't sponsored or reviewed by my employer.

---
_I reproduced this in a disposable AWS lab. The lab code and raw output are in the repo. Drafted with AI assistance and reviewed by me._
