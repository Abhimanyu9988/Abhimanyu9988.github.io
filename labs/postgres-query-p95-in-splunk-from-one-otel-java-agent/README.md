# Postgres Query p95 in Splunk From One OTel Java Agent Setting, No Code Changes (db.client.operation.duration)

## The setting, and the check behind it

`OTEL_SEMCONV_STABILITY_OPT_IN=database` turns on `db.client.operation.duration`. Splunk holds up to 36 dimensions per series, and on EKS the Collector adds cloud and host attributes, so the run measures whether the series from that one setting is stored.

* If it is stored, one setting is the result, and the run prints how close the series sits to 36.
* If it isn't, the run adds a second agent change, `-Dotel.java.disabled.resource.providers=io.opentelemetry.instrumentation.resources.ProcessResourceProvider,io.opentelemetry.instrumentation.resources.ProcessRuntimeResourceProvider`, which leaves out the agent's process attributes, and measures again. Then the title becomes "Get Postgres Query p95 Into Splunk on EKS: Two OTel Java Agent Changes, No Code Changes".

Either way, no application code changes. `title_changes` in the output says which one happened.

## What it shows

A small Spring Boot system runs on EKS: `orders` calls `inventory`, and both talk to Postgres. Both run the OpenTelemetry Java agent 2.33.1 and send to the Splunk OTel Collector. Each run prints the agent's version line.

The app runs four statements: an audit count in `orders`, and an update, a lookup and a known-latency statement in `inventory`. Every fourth request runs `select pg_sleep(0.03), qty from reserved where id = ?`, so one reading has a known answer. `inventory` also carries a small timer around that one call, which is reference code for this lab only, so your own services need no code change.

* `reproduce.sh` (BEFORE): installs the Collector, builds the jar once in the cluster, attaches the agent with no opt-in, runs a fixed load, and lists the database metrics Splunk Observability Cloud holds for the environment. It then puts the opt-in on alone and reads whether Splunk stores the series, which sets `title_changes`. That step is in this script so the trim branch still fits the 14 minutes `fix.sh` has.
* `fix.sh` (BUILD): adds the trim only if `title_changes` is 2, runs the load, and reads `db.client.operation.duration` back from Splunk: its time series through `/v2/metrictimeseries`, and its readings in milliseconds through the SignalFlow stream.

The opt-in is the only change between the runs. The jar's SHA-256, `JAVA_TOOL_OPTIONS` and the environment variable names are compared across the two runs. The Collector chart keeps its default values.

## What the setting does

The agent records `db.client.operation.duration`, a histogram in seconds, once a service opts in to the stable database conventions. The agent reads this setting from the environment variable `OTEL_SEMCONV_STABILITY_OPT_IN` or the system property `otel.semconv-stability.opt-in`, and `fix.sh` runs both routes.

The stable conventions bring new names for database span attributes and connection pool metrics. `database/dup` emits the old and new names together, which suits a team moving dashboards across. `fix.sh` runs that route too, and prints the pool metric names from both runs side by side. `check.sh` repeats these routes on a GitHub runner with no secrets.

## How the run decides

`reproduce.sh` sends test points with 30, 36, 37 and 40 dimensions and prints which ones Splunk holds. With the opt-in alone it then runs a 75-second load and reads Splunk for about two minutes. `db.client.connection.count` is the control, because it exists only with the opt-in and sits well under the limit. If the `select audit` bucket series is stored, `title_changes` is 1. If the control is there and that series isn't, `title_changes` is 2. If neither is there, the run fails, because it can't tell.

`fix.sh` prints the dimensions on this metric's series and how far they sit under 36, leaving out `sf_metric`, which Splunk's reply lists as a key. In its last short load `orders` runs on the other side of the trim, so the script can count what the trim would remove. HTTP metrics are printed separately: they carry more attributes, which is why an earlier run saw none of them on EKS. A rehearsal on kind has no EC2 metadata, so it sets the same cloud and host attributes itself to carry the count EKS has.

## How the readings work

With the Collector chart's default values, this histogram reaches Splunk as `db.client.operation.duration_bucket` with an `le` dimension, plus `_count`, `_sum`, `_min` and `_max`. The scripts print the exporter's histogram setting and the raw metric names, and `lib.sh` and `tools.sh` hold the Splunk reads. This SignalFlow program gives one statement's p95 in milliseconds:

```python
data('db.client.operation.duration_bucket', filter=filter('deployment.environment', 'YOUR_ENV') and filter('db.query.summary', 'select reserved'), rollup='delta').sum(by=['le']).histogram_percentile(pct=95, bucket_name='le').scale(1000).publish('p95_ms')
```

The agent's default export interval is 60 seconds, so `fix.sh` reads one point per minute, from the start time the load prints, and reports the median, minimum and maximum across at least six minutes. The read window comes from the load alone. The load prints what it sent each minute, and only those lines decide which minutes count, so a short or empty minute in Splunk stays in the comparison. The first program it prints shows the filter it used, built from the dimensions Splunk holds on the series: `db.query.summary`, or `db.operation.name` with `db.collection.name`.

A percentile from a histogram is an estimate inside one bucket, so the known statement shows how far to trust it. Its real time is 30 ms plus a little overhead, which sits between the stored `le` edges 0.01 and 0.05, so 10 to 50 ms. The run prints the edges Splunk stores, and those are the ones to quote. `fix.sh` compares the Splunk p95 with a timer around that JDBC call in `inventory`, logged once a minute, and prints the bucket edges as Splunk stores them. `fixed` needs the p95 to come from the SignalFlow program printed in the run and to fall inside the stored bucket that holds the timer's p95. The p95 from the raw bucket counts is printed under its own name, as a cross-check only. It then compares Splunk's mean from `_sum` and `_count` with the mean from `pg_stat_statements`, and prints that view's maximum and standard deviation. `fixed` needs those two means within 10%.

The lookups Splunk counted are compared minute by minute with the requests the load sent in those minutes, and the total has to land within 10%. The rate the load reached is printed too. The script prints the series for each statement and service, so a reader can work out a rollout by multiplying by their own statements and services. It also prints the CPU steal and t3 credit balance on the app node. A run with any load errors stops before a reading counts.

## Timing

The BEFORE load runs for 120 seconds to fit the 14-minute limit on a script, because it only has to show which metrics arrive. The BUILD load runs for 540 seconds so at least six full minutes can be read, starting 150 seconds in so every export a reading holds sits inside the load. Both aim for one request every 50 ms, and `fix.sh` reports the rate reached. The nodes stay `t3.large`, so each number comes from one cluster on burstable instances.

## Run it

Set `AWS_REGION`, `LAB_NAME`, `TF_VAR_lab_name` (the same value as `LAB_NAME`), `SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN` (ingest) and `SPLUNK_API_TOKEN` (read). Then run:

```bash
terraform init && terraform apply -auto-approve
./reproduce.sh
./fix.sh
./cleanup.sh
terraform destroy -auto-approve
```

The run uses two `t3.large` nodes and an EKS control plane for about an hour. Read your own service's numbers before you set a target.
