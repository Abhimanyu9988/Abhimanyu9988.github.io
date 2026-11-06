# One Helm value puts running Java services on EKS onto the Splunk APM map

## What it shows

Two Spring Boot services on Java 21 run on EKS: `orders` calls `inventory` on another node, and `inventory` reads Postgres. A third node runs only the load. In the before state, neither service has any OpenTelemetry instrumentation or setting.

* `reproduce.sh` (BEFORE): installs the Splunk OTel Collector with OBI at its default, off, starts the services, warms them, runs five load rounds with OBI off, and records each pod's UID, restart count and start time.
* `fix.sh` (BUILD): sets `obi.enabled=true` on the Helm release, discards one round while OBI settles, runs five more on the same pods, then adds the Collector tap, runs one more round and reads the service map and 30 traces through the Splunk APM API.
* `propagate.sh`, started by `fix.sh` with its own time limit: sets a second value, outgoing trace propagation, on the same pods and runs one round. Its `read` mode reads each case the same way: 200 seconds after that case's round, 30 traces, then each miss once more.

The headline counts are `services_with_own_spans`, the services that sent spans with the first value, shown by a server span Splunk returned plus a `service.request.count` series created by then, and pod restarts added. `services_on_map` counts nodes on the Splunk APM topology, and the lab prints the map's inferred flag for each service separately. Restarts are reported for each of the three pods. Both come from the first value alone. A replaced pod counts as a restart, because a new pod starts at zero. Restarts after the second value are reported per pod as their own measure.

## What to know first

* OBI, the OpenTelemetry eBPF Instrumentation, is a beta feature in Splunk Observability Cloud. This lab uses chart 0.163.1, which ships OBI 0.15.2. Chart 0.164.0 ships the same OBI.
* The OBI DaemonSet runs privileged with host PID and host network, as root. Check this against your cluster's policy before you plan a rollout. The chart documents a reduced set of capabilities.
* You change nothing in the pod spec. OBI does load its own small Java agent into each running JVM, by default, for TLS tracing, virtual-thread correlation and runtime metrics. The lab reports this for each service, and `javaagent.enabled` controls it.
* OBI instruments every namespace by default, so the map also shows other workloads. The lab counts `orders` and `inventory` by name and prints the rest, including the load client, which OBI traces too.
* The services run on different nodes on purpose. OBI can match a client and server on one node without touching the network, so a same-node result wouldn't tell you what a real cluster does.
* One value puts the services on the map. Outgoing propagation is off by default in OBI, so joining one trace across nodes is a separate value, `obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers`. The lab measures each on its own.
* OBI assigns its own trace IDs in these runs, so IDs set by a caller can't identify traces. `k8s/collector-tap.yaml` adds a second Collector pipeline that logs the spans OBI exported, and both trace cases take 30 evenly spaced `orders` trace IDs from it. It goes in after the timed rounds, so no latency figure includes it, and OBI's settings and the export to Splunk don't change. Leave the tap out of your own cluster.
* Each trace case reports joined traces out of the traces found in Splunk, prints what OBI exported, and prints one sample trace's spans. The most services on one trace is a second line: 2 means joined and 1 means separate. Both are real results, and -1 means the case was not measured.

## Run it

`reproduce.sh` also pulls the OBI image onto each node first, so a slow pull can't use up the time of a measured stage.

Set `AWS_REGION`, `LAB_NAME`, `TF_VAR_lab_name` (the same value as `LAB_NAME`), `SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN` (ingest) and `SPLUNK_API_TOKEN` (read). Then run:

```bash
terraform init && terraform apply -auto-approve
./reproduce.sh
./fix.sh
./cleanup.sh
terraform destroy -auto-approve
```

The run uses three `t3.large` nodes in unlimited credit mode, Amazon Linux 2023 on Kubernetes 1.34, and an EKS control plane for about an hour. Splunk's chart documentation names Ubuntu as its tested node OS, and this lab adds a measured result on Amazon Linux 2023. The p95 figure covers OBI at its defaults, including the Java agent it loads, and comes from one cluster and one load rate. Each OFF and ON round prints its p95 next to CPU on the orders, inventory and load nodes, and each side prints its min, median and max. `p95_added_ms` is the median of five ON rounds minus the median of five OFF rounds, at about 20 requests a second, and `p95_noise_ms` is the spread of the OFF rounds. A figure inside that spread is noise, not a cost. Measure your own service before you plan capacity.
