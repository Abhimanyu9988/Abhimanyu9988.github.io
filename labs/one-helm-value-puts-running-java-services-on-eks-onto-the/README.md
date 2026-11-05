# One Helm value puts running Java services on EKS onto the Splunk APM map

## What it shows

Two Spring Boot services on Java 21 run on EKS: `orders` calls `inventory` on another node, and `inventory` reads Postgres. A third node runs only the load. In the before state, neither service has any OpenTelemetry instrumentation or setting.

* `reproduce.sh` (BEFORE): installs the Splunk OTel Collector with OBI at its default, off, starts the services, warms them under a fixed load, and records each pod's UID, restart count and start time.
* `fix.sh` (BUILD): runs three load rounds with OBI off, sets `obi.enabled=true` on the Helm release, discards one round while OBI settles, runs three more on the same pods, and reads the service map and sampled traces through the Splunk APM API.
* `propagate.sh`, started by `fix.sh` with its own time limit: sets a second value, outgoing trace propagation, on the same pods and reads traces the same way: three rounds, the same sampling, one fixed wait and one pass.

The headline counts are services on the map and pod restarts added, reported for each of the three pods. Both come from the first value alone. A replaced pod counts as a restart, because a new pod starts at zero. Restarts after the second value are reported per pod as their own measure.

## What to know first

* OBI, the OpenTelemetry eBPF Instrumentation, is a beta feature in Splunk Observability Cloud. This lab uses chart 0.163.1, which ships OBI 0.15.2. Chart 0.164.0 ships the same OBI.
* The OBI DaemonSet runs privileged with host PID and host network, as root. Check this against your cluster's policy before you plan a rollout. The chart documents a reduced set of capabilities.
* You change nothing in the pod spec. OBI does load its own small Java agent into each running JVM, by default, for TLS tracing, virtual-thread correlation and runtime metrics. The lab reports this for each service, and `javaagent.enabled` controls it.
* OBI instruments every namespace by default, so the map also shows other workloads. The lab counts `orders` and `inventory` by name and prints the rest, including the load client, which OBI traces too.
* The services run on different nodes on purpose. OBI can match a client and server on one node without touching the network, so a same-node result wouldn't tell you what a real cluster does.
* One value puts the services on the map. Outgoing propagation is off by default in OBI, so joining one trace across nodes is a separate value, `obi.env.OTEL_EBPF_BPF_CONTEXT_PROPAGATION=headers`. The lab measures each on its own.
* Each trace case reports joined traces out of the known traces found in Splunk. `load.py` sets the trace IDs, so each case also prints one sample trace's spans, which shows whether the `orders` server span keeps that ID. The most services on one trace is a second line: 2 means joined and 1 means separate. Both are real results, and -1 means the stage did not run.

## Run it

Set `AWS_REGION`, `LAB_NAME`, `TF_VAR_lab_name` (the same value as `LAB_NAME`), `SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN` (ingest) and `SPLUNK_API_TOKEN` (read). Then run:

```bash
terraform init && terraform apply -auto-approve
./reproduce.sh
./fix.sh
./cleanup.sh
terraform destroy -auto-approve
```

The run uses three `t3.large` nodes in unlimited credit mode, Amazon Linux 2023 on Kubernetes 1.34, and an EKS control plane for about an hour. Splunk's chart documentation names Ubuntu as its tested node OS, and this lab adds a measured result on Amazon Linux 2023. The p95 figure covers OBI at its defaults, including the Java agent it loads, and comes from one cluster and one load rate. Each OFF and ON round prints its p95 next to CPU on the orders, inventory and load nodes, and each side prints its min, median and max, so you can see the spread. `p95_added_ms` is the median of three ON rounds minus the median of three OFF rounds. Measure your own service before you plan capacity.
