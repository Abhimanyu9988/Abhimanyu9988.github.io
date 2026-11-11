# Where Fluent Bit logs go when the destination stops accepting them (ECS Fargate + FireLens)

This lab measures, on the real FireLens path on Fargate, how many log records are lost when an HTTP destination
stops for 3 minutes, and what changes that.

```
app container (python, 200 lines/s on stdout)
   -> awsfirelens log driver (fluentd forward over /var/run/fluent.sock)
   -> log_router container (AWS for Fluent Bit 3.0.0, Fluent Bit 4.1.1) -> http output
   -> sink on one t3.medium EC2 instance (port 8080)     <- the "endpoint the lab can stop"
```

The sink is a small Python HTTP server (embedded in `embedded_scripts.tf`). Its ingest listener on port 8080 is
**really closed** during the outage (connection refused), then reopened. A second, token-protected admin port (8081)
serves counts. The sink counts records per **sequence number**, so loss is computed exactly from what the generator
emitted versus what arrived.

The generator (also embedded) writes `seq=N run=... ts=... <padding>` lines (~170 bytes) at 200 lines/s, times each
stdout write, and posts progress and the router container's memory (read from the ECS task metadata endpoint
`/task/stats`) straight to the sink admin port, bypassing Fluent Bit. It stops generating when the sink says the
endpoint is back, so after the restart the destination count only rises because of backlog being drained.

## The three configurations (same rate, same 3-minute outage)

| id | config | how it is set |
|----|--------|---------------|
| `default` | **(a)** FireLens-generated config, nothing tuned | only the `logConfiguration.options` (`Name http`, `Host`, `Port`, `URI`, `Format json_lines`) |
| `mem` | **(b)** memory buffering, raised `Retry_Limit 60`, `Mem_Buf_Limit 64MB` | custom Fluent Bit config in the task definition |
| `fs` | **(c)** filesystem buffering (`storage.type filesystem`, `storage.path`, `storage.total_limit_size 512M`), `Retry_Limit 60` | custom Fluent Bit config in the task definition |

Both hardened configs also set `scheduler.base 2` / `scheduler.cap 20`. Without a cap, the exponential retry backoff
(default cap 2000 s) leaves stale chunks waiting long after the destination is back, which makes drain time unbounded.
That is one of the trade-offs the lab shows, and it is why the cap is part of the hardened configs.

**Honest caveat about how (b) and (c) are applied.** FireLens' generated inputs cannot be tuned from an included file
and the lab sandbox has no Docker to build a custom image or S3 for a config file. So for `mem` and `fs` the
router container's `entryPoint`/`command` write the config above from an environment variable and start Fluent Bit
with it. The input is still the forward socket `/var/run/fluent.sock` that the `awsfirelens` driver writes to, but
the configs are not FireLens-generated ones. In a real service you would bake the same file into a custom
image (or deliver it from S3) and use `config-file-type`. The generated default config is what AWS writes, so its
exact input limits are whatever your agent version uses.

## Files

- `main.tf`: VPC with one public subnet, sink EC2 instance, ECS cluster, three task definitions, exec role `hero-lab-<lab>-exec`, log group for router logs.
- `embedded_scripts.tf`: the sink and the generator as Terraform locals (no separate .py files).
- `lib.sh`: shared functions for the scripts.
- `reproduce.sh`: BEFORE. Runs the default config through the outage and prints loss, drain, memory and stdout latency.
- `fix.sh`: BUILD. Runs `mem` and `fs` side by side through one shared outage, then compares with the default result.
- `cleanup.sh`: stops tasks started by the scripts (Terraform does not know them) and waits until all are STOPPED so the ENIs are released.

## How to run it in your own account

You need Terraform, AWS CLI v2, jq, curl, coreutils `timeout` and AWS credentials with permission to create VPC, EC2,
ECS, IAM roles and CloudWatch log groups in `eu-central-1`. Splunk is **not** used (the destination is the sink; the
result applies to any HTTP output such as Splunk HEC), so no Splunk realm or token is needed.

```bash
terraform init
terraform apply -var region=eu-central-1 -var lab_name=fb-outage -var permissions_boundary=null
export LAB_NAME=fb-outage AWS_REGION=eu-central-1
./reproduce.sh   # default config; writes hero_* files into this directory
./fix.sh         # hardened configs; reads hero_lost_pct_default.txt from this directory
./cleanup.sh && terraform destroy -var region=eu-central-1 -var lab_name=fb-outage -var permissions_boundary=null
```

(The variables `region`, `lab_name`, `permissions_boundary` are supplied by the runner in the hosted lab; for your
own run, declare them in a small `vars.tf`.) The sink admin port is open to the internet but needs a random token
that Terraform generates; destroy the lab when done.

## What you get

One `HERO_MEASURE` line per config for: `records_lost_pct`, `records_delivered_after_recovery`, `drain_time_s`,
`fluentbit_peak_memory_mib` (Fluent Bit container working set during the outage, baseline printed beside it),
`app_stdout_max_write_ms`, and `loss_by_config_comparison` (default loss minus each hardened loss, in percentage
points). The unedited jq/cat output the value came from, including the config name, is printed on the line directly
above each `HERO_MEASURE` line (the full raw sink report is printed once before each group). Fluent Bit's own
retry/drop log lines are pulled from CloudWatch Logs as supporting evidence.

How to read it:
- Records are counted by sequence number. "Lost" means never arrived by the time the count was unchanged for 60 s.
  If the count is still changing when the time limit hits, the lab prints `null` for loss and drain and `not_fixed`; it never
  counts records still in flight as lost.
- Window boundaries (outage start/end) come from the generator's 1 s progress reports, so "generated during outage"
  is accurate to about 200 records.
- Each configuration is measured once; treat the numbers as one sample at this log rate, not as a benchmark. Scale
  the buffer from the memory growth you see: roughly (peak - baseline) MiB per 3 minutes at 200 lines/s of ~170-byte lines.
- Filesystem buffering uses the task's ephemeral storage (20 GiB default). It survives an output outage but not a task
  stop. The router container here has a 256 MiB hard memory limit so you can see how close each config gets to an OOM kill.
- Whether the app's stdout writes stall depends on the log driver mode and buffer; the lab reports the maximum write
  time seen, and does not change the driver mode (default blocking).

## Time and cost

Each script finishes in under 14 minutes, with every wait time-bounded by one experiment clock: sink readiness <=75 s,
tasks RUNNING <=180 s (shared), steady state <=75 s, outage about 190 s, and the drain loop ends at 680 s after the
experiment started (but never less than 90 s after recovery), followed by about a minute of evidence queries. Typical
runs finish the drain much earlier (a config counts as drained after 60 s without new records). If a backlog is still
arriving at the drain deadline, loss and drain are reported as null and fix.sh prints `not_fixed`.
Cost is well under 1 USD: one t3.medium for about an hour and a few Fargate task-minutes (0.5 vCPU / 1 GiB).
