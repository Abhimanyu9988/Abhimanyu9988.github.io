# Preview OTel Java agent 3.0 on Amazon SQS

Two Spring Boot services on EKS exchange orders over a real Amazon SQS queue: `orders` sends each order with the AWS SDK and
`fulfilment` handles it with `@SqsListener` (single-message mode, up to 10 messages per poll). Nothing in the code knows about tracing.

The lab runs the same services four times, with fresh pods each time:

| Run | Agent setting | Orders |
| --- | --- | --- |
| `default` | OTel Java agent 2.33.1 on its defaults | 400 |
| `receive_telemetry` | defaults, plus receive telemetry on `fulfilment` | 100 |
| `preview` | `OTEL_INSTRUMENTATION_COMMON_V3_PREVIEW=true` on both services | 400 |
| `receive_preview` | the preview, plus receive telemetry on `fulfilment` | 100 |

Before every run the queue must be empty (visible and in-flight messages both 0), and each run uses its own order numbers
(1-400, 1001-1100, 2001-2400, 3001-3100), so a message from another run can't be counted. After every rollout the lab reads the
agent version and the `OTEL_` variables from inside each pod and stops if the preview flag isn't what the run needs.

For every run it joins each message's producer span and process span on the SQS message ID and counts how many share one trace,
how many process spans carry a link to their producer's trace, and what the agent named the spans. Then it reads 10 sampled preview
traces and 5 default traces back from Splunk Observability Cloud's trace API, and lists the operation names Splunk APM holds for each
mode: the names a filter or chart in Splunk would match on. Every count is reported out of the joined messages for its run.

- `reproduce.sh`: builds the jar in the cluster, deploys both services, and runs `default` and `receive_telemetry`.
- `fix.sh`: runs `preview` and `receive_preview`, then the Splunk read-back.
- `lib.sh`: the load, the queue drain, attaching the agent and the per-run report. `spans.sh` reads the spans from pod logs;
  `base.sh` holds the clock and small helpers.
- `check.sh`: the free check. The same four runs against ElasticMQ in Docker, with no AWS and no Splunk.

The preview runs report under `deployment.environment=<lab name>-v3`, so Splunk keeps the two modes apart. That label and the one
preview variable are the only differences between the modes.

The 2.33.1 preview can still change before 3.0. This lab measures messaging spans only; the flag also changes database and code
conventions, which it doesn't measure.
