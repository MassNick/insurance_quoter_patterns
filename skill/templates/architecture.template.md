# Architecture: {{product}} quoting agent on Azure AI Foundry

<!-- Facts about Azure services must come from docs read in this rebuild (or within 30 days). Mark the rest [UNVERIFIED]. Link to contracts/ instead of restating them. -->

## 1. Context and scope
{{What the system does, for whom, what is out of scope.}}

### 1.1 Constraints from the brief
| Constraint | How the design meets it | Section |
|---|---|---|

## 2. Components
| Component | Azure service and SKU | Region | Purpose | Identity |
|---|---|---|---|---|
<!-- Foundry resource and project; hosted agent (sandbox size); document prompt agent; model deployments; toolbox; Foundry IQ KB + AI Search + Blob; Container Apps (MCP servers, BFF); Cosmos DB with each container, partition key and TTL; Blob uploads + Defender; Key Vault; App Insights / Log Analytics; Front Door + WAF; frontend hosting; Entra External ID; ACR; VNet/private endpoints. -->

## 3. Architecture diagram
```mermaid
flowchart LR
  %% zones: Customer | Edge | Container Apps | Foundry | Data | Observability
```

## 4. Agents
### 4.1 Hosted agent (the one container)
### 4.2 Main agent and memory agent responsibilities
### 4.3 Document agent
### 4.4 Toolbox and knowledge

## 5. Sequence diagrams
### (a) Chat turn: memory and main concurrently, streaming, state merge
### (b) A2UI form: open, submit, close
### (c) Document upload → document agent → autofill
### (d) Deterministic field: double confirmation
### (e) Button action: the tool runs before the model

## 6. State model and merge rules
### 6.1 Shape
### 6.2 Merge rules (summary of `contracts/events.md`)
### 6.3 Cosmos DB containers

## 7. A2UI design
### 7.1 Pipeline (FormSpec → compiler → messages → renderer)
### 7.2 Custom catalog components
### 7.3 Data binding and live autofill

## 8. Model-call budget per turn
### 8.1 Tool result modes (from `contracts/tools.json`)
### 8.2 Mechanics (middleware, pre-executed actions)
### 8.3 Why the agents run concurrently

## 9. Streaming and time-to-first-token path
## 10. Caching (prompt cache layout, BFF session cache)
## 11. Identity and RBAC
| Identity | Role | Scope | Why |
|---|---|---|---|
## 12. Networking
## 13. Observability pipeline
## 14. Guardrails
## 15. Logging and audit
## 16. Failure modes and fallbacks
| Failure | Detection | Fallback | Customer sees |
|---|---|---|---|
## 17. Cost levers
## 18. Verify before build
| # | Item | Why it matters | Source |
|---|---|---|---|
