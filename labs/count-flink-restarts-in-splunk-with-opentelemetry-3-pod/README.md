# Flink job restarts in Splunk Observability Cloud, with the OpenTelemetry Flink receiver

A Flink 1.20 streaming job (the StateMachine example, checkpoints every 5 s) runs on EKS with a fixed-delay restart
strategy, so a lost TaskManager brings it back to RUNNING by itself. The Splunk OTel Collector chart runs on the cluster.

- `reproduce.sh`: Flink, a watcher that reads Flink's REST API once a second, and two TaskManager pod deletions. It
  records, per deletion, how long the job was not RUNNING and how long it said RUNNING before every task ran and a new
  checkpoint completed, the restarts Flink counted, and what the Collector's Kubernetes metrics in Splunk show for the
  TaskManager over the same window (container restarts, available replicas, pod series).
- `fix.sh`: the upstream OpenTelemetry Collector (contrib 0.165.0) with the Flink metrics receiver, sending OTLP to the
  Splunk agent on its node. Three more deletions. It reads Splunk's restart counter and Flink's REST API at the same two
  instants and compares them, polls Splunk every 3 s for arrival lag and reports point lag too, the checkpoint counters, the flink.*
  metric names, and the JVM GC series for the JobManager and the TaskManager.
- `analyze.sh`: turns the watcher's lines into counts (code only). `check.sh`: the free check, the same Flink settings,
  job, watcher and receiver config in Docker. `lib.sh`: shared steps and the Splunk reads (every SignalFlow program is printed).

Deleting the TaskManager pod stands in for a node drain, an eviction or an out-of-memory kill. One job, one TaskManager,
one restart strategy; the receiver reads every 10 s.
