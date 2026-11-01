---
title: "October: Kubernetes memory semantics and Collector edge fixes"
description: "In October I had 10 PRs merged across four projects, mostly around Kubernetes memory semantics, Helm chart behavior, Collector receiver correctness, and active component ownership."
image: /assets/posts/open-source-summary-2026-10/cover.png
image_alt: "List of the open-source PRs merged in October 2026"
tags: ["opensource", "opentelemetry", "kubernetes", "observability"]
channels: ["devto", "hashnode"]
linkedin: "Observability work often fails at the edges.\nThis month mine was 10 small fixes across 4 projects.\n\nWhat I shipped in open source this month:\n\nKubernetes memory metrics got clearer.\nOperator Helm chart values and docs got easier to reason about.\nCollector contrib tests, receivers, and ownership moved forward.\nA Machine Agent monitoring extension starts again on Java 11+.\n\n→ Memory utilization now uses working set, not total usage.\n→ The Operator chart documents when auto-generated certs can drift under Argo CD.\n→ Flink receiver matching now uses exact suffixes instead of broad substring matches.\n→ Azure Data Explorer Exporter has active ownership again.\n\nThe proof:\n10 merged PRs.\n4 projects.\n3 Kubernetes semantic convention PRs.\n1 Helm chart docs fix shipped in opentelemetry-operator-0.127.1.\n\nWhy it matters at work: these are the edges that decide whether dashboards, configs, alerts, and upgrades are understandable during an incident.\n\nI reproduced the problem cases and tested the build paths; the post has the PR and release links:\n{LINKS}\n\nWhat observability edge case has cost your team the most time recently?\n\n#opensource #opentelemetry #kubernetes #observability"
replies: []
---
![List of the open-source PRs merged in October 2026](https://abhimanyu9988.github.io/assets/posts/open-source-summary-2026-10/cover.png)

In October I had 10 PRs merged across four projects. The common thread was practical observability work: clearer Kubernetes memory conventions, Helm chart behavior that is easier to reason about, less flaky Collector tests, and maintainership for components that need active review.

Most of my week goes into one question: why does something that looks correctly configured not work in a real cluster? These PRs came from the same place.

> **TL;DR**
> - Fixed flaky Kubernetes inventory cache-sync tests.
> - Removed a Java 11+ startup failure in a DataPower monitoring extension.
> - Let Operator chart users override only the Target Allocator repository.
> - Added Kubernetes memory utilization conventions.
> - Clarified Kubernetes working set descriptions.
> - Added Pod-level memory limit and request conventions.
> - Joined Flink Metrics Receiver code ownership.
> - Fixed broad Flink operator metric matching.
> - Documented Operator chart certificate drift with Argo CD.
> - Restored Azure Data Explorer Exporter active ownership and beta status.

## Kubernetes memory conventions became more precise

### Pod memory limits and requests now have desired and current forms

The Kubernetes semantic conventions had Pod-level CPU limit and request metrics, but not the same shape for memory. I added `k8s.pod.memory.limit.{desired,current}` and `k8s.pod.memory.request.{desired,current}` so the model can represent values from `spec.resources` and `status.resources`.

This shows up in OpenTelemetry semantic conventions, which define standards for consistent telemetry. It closes issue #4133, and there is no release listed for this PR yet.

Once implemented downstream, dashboards and alerts can distinguish desired Pod memory settings from current Pod memory status.

[open-telemetry/semantic-conventions PR 4158](https://github.com/open-telemetry/semantic-conventions/pull/4158)

### Working set descriptions now say what Linux includes

The working set descriptions for `k8s.pod.memory.working_set`, `k8s.node.memory.working_set`, and `container.memory.working_set` were clarified. On Linux, working set excludes inactive file-backed memory, while active file-backed memory remains included.

The generated semantic convention documentation now also says that kubelet uses Pod working set when ranking Pods for eviction, and node working set when determining memory availability for the `memory.available` eviction signal. There is no release listed for this PR yet.

The next effect is clearer documentation for comparing usage, working set, and eviction behavior.

[open-telemetry/semantic-conventions PR 4184](https://github.com/open-telemetry/semantic-conventions/pull/4184)

### Memory utilization now uses working set as the numerator

I added four Kubernetes memory utilization metrics: Pod and container limit utilization, and Pod and container request utilization. These use memory working set as the numerator, not total memory usage.

The PR explains the reason: total usage includes reclaimable page cache, which can make a workload look close to its limit even when part of that memory is reclaimable. I validated the behavior against a kubelet Summary API using kind with Kubernetes v1.37.0. There is no release listed for this PR yet.

Once implemented downstream, memory utilization can be shown against limits and requests with the numerator semantics documented in the convention and migration guide.

[open-telemetry/semantic-conventions PR 4219](https://github.com/open-telemetry/semantic-conventions/pull/4219)

## Operator Helm chart configuration and docs got clearer

### Private registry users can override only the Target Allocator repository

The OpenTelemetry Operator Helm chart did not let users override only `manager.targetAllocatorImage.repository` while keeping the chart’s default tag. I added explicit defaults for the Target Allocator repository and tag, plus validation that repository and tag are either both set or both empty.

This is for the OpenTelemetry Helm Charts project. It closes issue #1866, where 2 people asked for it, and the chart version was bumped to `0.125.0`. There is no release listed for this PR yet.

The practical effect is simpler values for private registry or air-gapped installs that redirect the Target Allocator image. The upgrade guide also documents the configuration precedence change.

[open-telemetry/opentelemetry-helm-charts PR 2456](https://github.com/open-telemetry/opentelemetry-helm-charts/pull/2456)

### Argo CD users get documentation for auto-generated webhook certs

When `admissionWebhooks.autoGenerateCert` is used, the Operator chart can generate a new CA on each render. With Argo CD, or other tools that use `helm template`, Helm cannot use `lookup` during rendering to reuse the existing Secret, so the webhook `caBundle`, certificate Secret, and running operator may become inconsistent.

I documented this in the README’s TLS certificate section, including the possible `x509: certificate signed by unknown authority` failure. I reproduced the behavior with Argo CD v3.5.5, chart 0.126.0, and Kubernetes v1.37.0. This shipped in `opentelemetry-operator-0.127.1`.

The change is documentation: Argo CD users have a clearer reason to use cert-manager or supply certificate material independently of render-time generation.

[open-telemetry/opentelemetry-helm-charts PR 2473](https://github.com/open-telemetry/opentelemetry-helm-charts/pull/2473)

## Collector contrib tests, receivers, and ownership moved forward

### Kubernetes inventory cache-sync tests are deterministic now

Two `internal/k8sinventory/informer` tests were flaky because they depended on timing around fake client cache sync. I changed the tests to block the initial Pod `List` call explicitly, so the failure paths are deterministic without changing production behavior.

This is internal test code in the OpenTelemetry Collector contrib repository. It closes issue #50433, which had 24 comments about failed tests on main. There is no release listed for this PR yet.

The effect is less noise from racey tests when contributors work on Kubernetes inventory code.

[open-telemetry/opentelemetry-collector-contrib PR 51657](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/51657)

### The Flink Metrics Receiver has another code owner

The Flink Metrics Receiver needed additional maintainers. I added myself as a code owner after reviewing receiver changes, running unit tests, and validating changes against a local Apache Flink 2.3.0 deployment in Docker.

This shows up in CODEOWNERS and generated README metadata in the OpenTelemetry Collector contrib repository. There is no release listed for this PR yet, and it does not change runtime behavior.

The practical change is maintenance coverage for reviews, triage, bug fixes, and receiver upkeep.

[open-telemetry/opentelemetry-collector-contrib PR 52019](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52019)

### Flink operator metrics now match exact suffixes

The Flink Metrics Receiver used `strings.Contains` for operator-level metric names. That could also match longer names like `numRecordsInPerSecond` when looking for `numRecordsIn`.

I changed the four affected operator metric cases to exact suffix matching: `numRecordsIn`, `numRecordsOut`, `numLateRecordsDropped`, and `currentOutputWatermark`. Existing metric names, attributes, and configuration are preserved. There is no release listed for this PR yet.

The result is more precise matching for those receiver metrics.

[open-telemetry/opentelemetry-collector-contrib PR 52406](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52406)

### Azure Data Explorer Exporter has active ownership again

The Azure Data Explorer Exporter had been marked unmaintained after its previous active code owner moved to emeritus. I added myself as an active code owner and restored the previous beta stability designation for traces, metrics, and logs.

This is visible in generated README, CODEOWNERS, component labels, status metadata, and the changelog. It closes issue #52282, and there is no release listed for this PR yet.

The practical effect is that the component again has an active maintainer path for review and triage.

[open-telemetry/opentelemetry-collector-contrib PR 52500](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52500)

## A Machine Agent monitoring extension starts again on Java 11+

### The DataPower extension no longer depends on JDK SAAJ

On Machine Agents running a Java 11+ JRE, the DataPower monitoring extension could fail to instantiate because the JDK no longer ships the SAAJ implementation removed by JEP 320. The extension only used SAAJ to build a fixed SOAP request envelope.

I removed the `javax.xml.soap` dependency and now build the envelope directly as a string, with XML attribute escaping for domain and status-class values. The PR bumps the extension version to 2.0.2, and there is no release link listed for this PR yet.

The practical change is that Machine Agent users on Java 8 through 21 have a startup path that does not depend on a removed JDK implementation.

[Appdynamics/datapower-monitoring-extension PR 13](https://github.com/Appdynamics/datapower-monitoring-extension/pull/13)

## Still in review

- [open-telemetry/opentelemetry-operator: Generate a list of collector components with specific operator support](https://github.com/open-telemetry/opentelemetry-operator/pull/5850)
- [Appdynamics/process-monitoring-extension: Update dependencies to remediate reported vulnerabilities](https://github.com/Appdynamics/process-monitoring-extension/pull/25)
- [open-telemetry/opentelemetry-collector-contrib: update tested Flink versions and correct README configuration](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52485)
- [open-telemetry/opentelemetry-collector-contrib: Record JVM GC metrics for both JobManager and TaskManager](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52450)
- [open-telemetry/opentelemetry-collector-contrib: Add opt-in PVC-level volume metrics](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52279)
- [open-telemetry/opentelemetry-collector-contrib: Add Warnings section to probabilistic sampler README](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/51952)
- [CiscoDevNet/appdynamics-charts: ENV variables fix in InfraViz](https://github.com/CiscoDevNet/appdynamics-charts/pull/36)

## Sources

- [Fix flaky Kubernetes inventory cache-sync tests](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/51657)
- [Fix DataPower monitoring extension startup on Java 11+](https://github.com/Appdynamics/datapower-monitoring-extension/pull/13)
- [Allow overriding only the Target Allocator image repository](https://github.com/open-telemetry/opentelemetry-helm-charts/pull/2456)
- [Add Pod and container memory limit and request utilization metrics](https://github.com/open-telemetry/semantic-conventions/pull/4219)
- [Clarify memory working set metric descriptions](https://github.com/open-telemetry/semantic-conventions/pull/4184)
- [Add Pod-level memory limit and request metrics](https://github.com/open-telemetry/semantic-conventions/pull/4158)
- [Add a Flink Metrics Receiver code owner](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52019)
- [Match Flink operator metrics on exact suffix](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52406)
- [Explain autoGenerateCert behavior with Argo CD](https://github.com/open-telemetry/opentelemetry-helm-charts/pull/2473)
- [opentelemetry-operator-0.127.1 release](https://github.com/open-telemetry/opentelemetry-helm-charts/releases/tag/opentelemetry-operator-0.127.1)
- [Restore Azure Data Explorer Exporter active ownership and beta stability](https://github.com/open-telemetry/opentelemetry-collector-contrib/pull/52500)

---
_Drafted with AI assistance from the PRs themselves and reviewed by me._
