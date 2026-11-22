# Prometheus and OpenTelemetry side by side, each order counted once in Splunk

One Python service, `orders`, runs as two pods on EKS, one on each of two nodes, behind one Service. Each pod counts
every order two ways, as teams do while they move to OpenTelemetry:

- a Prometheus counter, `orders_created_total{run}` (prometheus-client 0.26.0), on `GET /metrics`, for a scrape job;
- zero-code OpenTelemetry (`opentelemetry-instrument`, 0.66b2), which records `http.server.request.duration` for every
  `POST /orders/<n>` and pushes it over OTLP to the Splunk OTel Collector agent on the pod's own node.

The agent is the Splunk OTel Collector chart 0.163.1, a DaemonSet with one agent per node, sending to Splunk
Observability Cloud. Only the agent's config differs between runs:

| Config | Runs | How the agent finds the orders pods |
| --- | --- | --- |
| `values-everywhere.yaml` | everywhere-1, everywhere-2 (`reproduce.sh`) | a `prometheus/orders` scrape job with Kubernetes pod discovery for `app=orders` |
| `values-node.yaml` | node-1, node-2 (`fix.sh`) | the same job plus one field selector: `spec.nodeName=${env:K8S_NODE_NAME}` |
| `values-autodetect.yaml` | autodetect-1, autodetect-2 (`fix.sh`) | no job of our own: `autodetect.prometheus: true`, which scrapes pods with `prometheus.io/scrape: "true"` on the agent's node |

Both orders pods carry the `prometheus.io/scrape`, `prometheus.io/port` and `prometheus.io/path` annotations in every run.
Each run restarts both pods with a new `run` label (the lab's name plus the run), so counters start at 0 and every run's
series in Splunk are its own. Every scrape job runs every 10 s. Each run waits 25 s at 0, sends 20 s of open-loop load at
10 orders a second through the Service, then waits 22 s for two more scrapes and OTLP exports.

What is measured, per run:
- `orders_ok`: orders that succeeded, counted by the load.
- `pod_orders`: the orders the two pods counted themselves (their own counters, read in the pods).
- `pod_scrapers`: scraper-and-pod pairs, from each pod's own record of who read its `/metrics` (an IP with 2 or more
  scrapes counts). One scraper per pod gives 2.
- `splunk_prometheus_orders`: the Prometheus counter in Splunk for the run, the newest value of each series summed.
- `splunk_prometheus_series`: how many metric time series in Splunk hold that counter for the run.
- `splunk_series_per_pod_max`: the most series any one scrape target (a pod's IP and port, `service.instance.id`) has in
  Splunk (1 is one per pod).
- `splunk_dims_splitting_a_pod`: how many dimensions have different values between series of the same target (0 when
  each pod has one series). Their names and each series' values are printed with it.
- `splunk_otel_orders`: OpenTelemetry's own count of the same orders in Splunk (`http.server.request.duration_count` for
  `http.request.method` POST and `http.response.status_code` 200), pushed once over OTLP.

Every config change saves the agent's whole rendered ConfigMap to `config-<config>.yaml`, fails if it isn't the config
the run needs, and prints the scrape job, discovery and autodetect lines and each agent pod's node, and every
run prints how many scrapers each pod had before the load and, after it, each scraper IP with the node of the agent that
owns it.

`reproduce.sh` passes when both everywhere runs show 4 scraper-and-pod pairs (every agent scraped every pod) and
OpenTelemetry's count in Splunk matches the pods. What Splunk's Prometheus counter shows for them is measured, not gated:
the chart's `resource_detection` (override, eks detector) sets `host.name` and `host.id` per node, so series from two agents
are expected to differ in those dimensions and add up to about twice. `fix.sh` passes when, in all four node and autodetect runs, Splunk's Prometheus counter
equals what the pods counted, over 2 series with 1 a pod, with 2 scraper-and-pod pairs; OpenTelemetry's count matches in
all six runs; and the everywhere runs keep 4 scraper-and-pod pairs, which ties any extra count to its cause.

`check.sh` is the free check on a GitHub runner with Docker: the same app, packages and load, with two Collector
containers (contrib 0.165.0) that each scrape both pods, writing to a file. It shows the doubling and OpenTelemetry's
single count before AWS. Node scoping needs Kubernetes, so the rehearsal and the paid lab show that.

Run it yourself: two t3.large EKS nodes (Kubernetes 1.34), `helm`, `kubectl`, `jq`, and a Splunk Observability Cloud org
(`SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN`, `SPLUNK_API_TOKEN` with API read). Apply `main.tf`, then `bash reproduce.sh` and
`bash fix.sh`, then `bash cleanup.sh`. The lab is published with its article on showmerit.com.
