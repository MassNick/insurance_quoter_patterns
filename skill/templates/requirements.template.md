# Requirements: {{product}} quoting agent

<!-- Each requirement: ID, MUST/SHOULD, one-line statement, testable acceptance criterion. IDs are stable across rebuilds: retire IDs, don't reuse them. -->

## 1. Functional requirements

### FR-INT: Intent phase
| ID | Level | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-INT-1 | MUST | | |

### FR-GAT: Gather phase
### FR-ADV: Advise phase
### FR-MEM: Memory agent (state changes as structured output, signals, conflicts)
### FR-SES: Session persistence
### FR-A2UI: Agent-generated forms
<!-- Cover every field group; validation; hints and in-place info; prefill/autofill; atypical buttons and repeated fields; photo-to-field autofill; conditional fields; lookups. -->
### FR-CHAT: Chat experience (dots, streaming, clickable bullet suggestions)
### FR-DET: Deterministic fields (double confirmation)
### FR-DOC: Document agent
### FR-QUO: Quotes (current and past versions)
### FR-LOG: Logging
### FR-OBS: Observability
### FR-GRD: Guardrails
### FR-UI: Interface and animation (chat slides left, ball to form, reverse on submit; side panel tabs)

## 2. Non-functional requirements
| ID | Area | Requirement | Target | How measured |
|---|---|---|---|---|
<!-- performance (dots, TTFT p95), model calls per turn SLO, prompt+tools token cap (CI), cached-token ratio, availability, scalability (hosted session quotas), security, privacy (local law), data residency, retention/TTL, accessibility (WCAG 2.2 AA, reduced motion), i18n, auditability, cost per quote, maintainability (catalog-driven). -->

## 3. Traceability: brief → requirements → artifacts
| Brief item | FR/NFR | Shown in |
|---|---|---|

## 4. Open questions
