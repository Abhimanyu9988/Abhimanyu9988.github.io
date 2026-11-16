# Retry storm after a slowdown, seen in Splunk APM

Two Python services on EKS: `orders` takes an order and calls `inventory` over HTTP with a 500 ms timeout. Both run with
zero-code OpenTelemetry (`opentelemetry-instrument`) and send traces to the Splunk OTel Collector. `inventory` has 4 worker
threads and takes 40 ms a request, so it can finish about 100 a second.

An open-loop load sends 40 orders a second for 110 s. At second 20 it asks inventory for a 20 s slowdown: every request takes
2 s. After that inventory is healthy again. Each run gets fresh pods and its own order numbers. Runs alternate, three of each
mode: naive-1, budget-1 and naive-2 in `reproduce.sh`, budget-2, naive-3 and budget-3 in `fix.sh`.

| Run | Retry setting in `orders` |
| --- | --- |
| `naive` | retry a failed call at once, up to 3 more times |
| `budget` | retry up to 3 more times with exponential backoff and full jitter (base 100 ms, cap 2 s), only while a retry budget allows: each order adds 0.1 of a retry |

`analyze.sh` reads the load output, the orders log and the inventory log for each run and prints, for the 70 s after the
slowdown ended: failed orders, whether and when success got back to 95% for 5 s in a row, inventory calls per order,
retries the budget skipped, latency of successful orders, and how many requests inventory started after the caller's timeout
had already passed (orders sends its deadline in an `X-Caller-Deadline` header), with each container's CPU from its cgroup (average and busiest second), the node's busiest second from /proc/stat,
inventory's queue of accepted requests waiting for a thread, failed calls by kind (timeout, refused or reset, HTTP), and the
server settings inventory actually started with.
`fix.sh` then reads the naive-2 and budget-2 windows back from Splunk Observability Cloud: request counts and 503s from
the stable HTTP server metric the services send (`http.server.request.duration_count`, filtered to each route), until two reads agree, and checks Splunk's orders count against the load's count for the same window (orders arrive at a
steady rate, so bucket edges can't move it). Inventory's counts are compared and printed too, but its rate changes fast after
the slowdown, so they aren't the check.
The `budget` mode changes three things at once (backoff, jitter, budget), so the post credits the combination, not one part.

- `check.sh`: the free check, the same two runs in Docker with an OTLP sink instead of Splunk.
- `lib.sh`: the clock, starting a run, the load and the report. `splunk.sh`: the Splunk reads.

The slowdown is simulated, and this is one load level, one timeout and one dependency. The backlog of requests clients
already gave up on still has to drain; server-side load shedding isn't tested here.
