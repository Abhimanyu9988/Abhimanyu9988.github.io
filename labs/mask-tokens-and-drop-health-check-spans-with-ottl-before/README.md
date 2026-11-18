# Tokens and health checks in traces, handled in the Collector with OTTL

Two Python services on EKS: `orders` takes an order and reserves stock from `inventory`. The caller sends a customer token
in the query string (`POST /orders/<n>?token=...`), and orders passes it on the same way (`GET /reserve/<n>?token=...`).
Both run with zero-code OpenTelemetry (`opentelemetry-instrument`, 0.66b2) with `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup`,
so every HTTP span carries the current attribute names and the older ones, as during a semantic-convention migration:
the token is in `url.query` and `http.target` on both server spans, and in `url.full` and `http.url` on orders' client span.
The kubelet checks `/healthz` on each service every 2 s (readiness) and every 10 s (liveness), and those requests are traced.

Both services send to a gateway Collector, the upstream OpenTelemetry Collector contrib 0.165.0, where OTTL is stable for
traces, metrics and logs. The gateway forwards over OTLP to the Splunk OTel Collector agent (chart 0.163.1) on its node,
which sends to Splunk Observability Cloud. `k8s/30-gateway.yaml` holds three configs that differ only in the processors on
the traces pipeline; the metrics pipeline is the same in all three.

| Config | Runs | Traces pipeline |
| --- | --- | --- |
| `base.yaml` | before-1, before-2 (`reproduce.sh`) | `batch` |
| `named.yaml` | named-1, named-2 (`fix.sh`) | `filter/healthchecks`, `transform/redact` naming `url.query` and `url.full`, `batch` |
| `all.yaml` | all-1, all-2 (`fix.sh`) | `filter/healthchecks`, `transform/redact` on every attribute value, `batch` |

```yaml
filter/healthchecks:                # named.yaml and all.yaml
  error_mode: ignore
  trace_conditions:
    - span.attributes["url.path"] == "/healthz"
transform/redact:                   # named.yaml
  error_mode: ignore
  trace_statements:
    - replace_pattern(span.attributes["url.query"], "token=[^&]*", "token=REDACTED")
    - replace_pattern(span.attributes["url.full"], "token=[^&]*", "token=REDACTED")
transform/redact:                   # all.yaml
  error_mode: ignore
  trace_statements:
    - replace_all_patterns(span.attributes, "value", "token=[^&]*", "token=REDACTED")
```

Each run gets a fresh gateway and fresh service pods, and 45 s of open-loop load at 10 orders a second. Every order has its
own token and a trace ID the load picks; every 2 s from second 5 one order is a sample (20 a run) whose trace ID and token
are printed.

What is measured:
- Per run, from the gateway's own metrics (port 8888), read right before the load and again after the flush, so only the
  load window counts: spans received, spans exported, spans the filter dropped, failed exports.
- Per run, from Splunk APM, each sample trace read back by its trace ID: traces found, fewest spans in a trace, whether both
  services are in it, spans holding the run's raw token anywhere, which attributes hold it, and spans holding
  `token=REDACTED`.
- Per config and service, over the same whole 10 s points inside that config's period (start and end times in `periods.txt`):
  APM's request count for the `GET /healthz` operation (built from spans that reached Splunk), the services' own HTTP
  server metric for the `/healthz` route (built in the app, so the trace filter doesn't touch it), and the count the probe
  periods give, about 36 a minute per service.

`fix.sh` passes only if, in both all runs, at least 90% of the sample traces reached Splunk, none holds the raw token, each
holds 3 masked spans and both services, every order succeeded and the filter dropped health-check spans, while a before
run shows the raw token in Splunk. What named.yaml leaves behind is measured, not a condition.

- `check.sh`: the free check, the same code and all three gateway configs in Docker, with the gateway writing to a file
  instead of the Splunk agent and a pinger in place of the kubelet.
- `lib.sh`: runs, the gateway counts, the trace and health-check counts. `splunk.sh`: the low-level Splunk API calls.

To run it yourself: `terraform init && terraform apply`, export `SPLUNK_REALM`, `SPLUNK_ACCESS_TOKEN` (ingest),
`SPLUNK_API_TOKEN` (read), `AWS_REGION` and `LAB_NAME`, then `bash reproduce.sh && bash fix.sh`, then `bash cleanup.sh`
and `terraform destroy`.

Limits: one token name in the query string, one load level, one gateway. A token in a header, a body or a span event needs
its own statement. Dropping health-check spans removes them from traces only; their counts stay in the services' HTTP
metrics, and a slow health check no longer shows in a trace.
