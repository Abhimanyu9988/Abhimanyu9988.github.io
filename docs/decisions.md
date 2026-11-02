# Decisions

Newest first. Each entry says what was decided and why, so a future agent does not undo it by accident.

## 2026-11-02: Milestone 1 builds gates before the Editor

The Topic Investment Gate and Evidence Contract come first, because code can check them. The Editor's scorecard is
built but stays off until those two have been evaluated. A scorecard from another AI is not proof of quality.

## 2026-11-02: Shadow mode must not change production behaviour

In shadow mode, briefs go in issue comments, which labs do not read. Review records go to the analytics branch.

## 2026-11-02: Low views do not prove low quality

Three separate questions: are posts worth reading, are the right people finding them, and do readers act afterward?
The Editor addresses only the first. With so little traffic, milestone success uses human ratings and saved cases.

## 2026-11-02: Cost per accepted worthwhile article is the main cost measure

Model fees per article hide the cost of rejected topics and failed labs. Stopping a weak topic early can save more
than using a cheaper model.

## 2026-11-02: Splunk is used wherever it fits

Splunk is no longer required in every topic. The aim is about half of posts over time, and it is never forced.

## 2026-11-02: Reviews, goals and costs live on the analytics branch

Git keeps history and needs no new service. The existing bot rule already allows `analytics/*`.

## 2026-11-02: Fixed workflow, no LLM orchestrator

A fixed order is testable, costs nothing per decision, and cannot delegate recursively.
