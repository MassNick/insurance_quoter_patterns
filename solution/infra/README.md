# Infrastructure, deployment and CI/CD

Status: **code and tests; nothing deployed.** No file here calls Azure unless you run it with credentials. Everything was checked offline: `terraform validate`, `tflint`, `checkov`, `actionlint`, `shellcheck`, `pytest`.

The repository root holds the design folder and `solution/`; the workflows are in the root's `.github/` and every job runs in `solution/`, so the paths below are relative to `solution/`.

## Layout

| Path | What it is |
|---|---|
| `infra/terraform/` | The Azure platform. Root module: `core.tf` (resource group, Log Analytics, App Insights, identities, Key Vault and its secrets, registry, Search), `data.tf` (two Cosmos accounts and the app database, three storage accounts, lifecycle, Defender), `private_endpoints.tf` (one resource, every private endpoint), `apps.tf` (Container Apps environment and the four apps), `rbac.tf` (the RBAC matrix as data), `diagnostics.tf`, `main.tf` (module calls and the RemoteTool connections), `locals.tf` (names, roles, app definitions), `variables.tf`, `outputs.tf`. |
| `infra/terraform/modules/` | `network` (VNet, subnets, NSG, private DNS zones, optional NAT gateway), `foundry` (account, project, deployments, guardrails, connections, standard agent setup), `frontdoor` (Premium, WAF, private link origins). |
| `infra/terraform/entra/` | Separate stack: the two MCP app registrations, their app roles and the grants. It needs Microsoft Graph permissions the platform pipeline should not hold. Its state is remote (`entra/backend.tf`), key `aiq-<env>-entra.tfstate`. |
| `infra/terraform/environments/` | `dev.tfvars.example`, `prod.tfvars.example`. |
| `deploy/` | Foundry data-plane scripts and their tests (see "Scripts"). |
| `.github/workflows/` | `ci.yml`, `cd.yml`, `deploy-env.yml` (reusable, one environment end to end), `visual-review.yml`, `codeql.yml`. `.github/actions/` holds two composite actions (`azure-env`, `terraform`). |

Consolidated on purpose (the brief listed twelve modules): monitoring, identity, Key Vault, registry, Search, Cosmos, storage, Container Apps and RBAC are flat root files driven by `for_each` maps; every private endpoint is one resource over one map; every container app is one resource over `local.app_defs`; the RBAC matrix is two tables and two resources. Only `network`, `foundry` and `frontdoor` are modules.

## Run the checks

```bash
cd infra/terraform
terraform fmt -check -recursive
terraform init -backend=false && terraform validate
(cd entra && terraform init -backend=false && terraform validate)
tflint --init && tflint --recursive --config "$PWD/.tflint.hcl"      # --config: each module dir would not find it
checkov --config-file .checkov.yaml

cd ../../deploy
uv sync --extra test --extra azure
.venv/bin/python -m pytest && .venv/bin/ruff check . && .venv/bin/python check_contracts.py
shellcheck *.sh
actionlint          # from the repository root; it follows `./.github/...` paths from the git root
```

`deploy/post_deploy.sh --dry-run --tf-output <saved terraform output -json>` prints every request and command without Azure.

## Variables

All in `infra/terraform/variables.tf`. Values go in `environments/<env>.tfvars` (git-ignored; in CI the GitHub environment variable `TFVARS` holds the file).

| Variable | Default | Meaning |
|---|---|---|
| `subscription_id`, `environment` | required | One subscription per environment. `environment` is `dev`, `test` or `prod`; prod turns on zone redundancy, purge protection, 2 BFF replicas and 90-day logs. |
| `workload`, `location`, `tags` | `aiq`, `canadacentral`, `{}` | Names are `<type>-aiq-<env>-<cc|ce>`; globally unique ones add 4 hex characters derived from the subscription. |
| `vnet_cidr`, `enable_nat_gateway` | `10.40.0.0/16`, `false` | Subnets are carved from the range: `aca-infra` /23, `private-endpoints` /24, `agent-egress` /24 (both delegated ones to `Microsoft.App/environments`). NAT for a fixed outbound IP. |
| `model_deployments` | `null` = `settings.yaml` | Overrides `deployments` in `settings.yaml` (solution root): map of deployment name to `{model_name, model_version, model_format = "OpenAI", sku_name, capacity}`. |
| `model_roles` | `null` = `settings.yaml` | Overrides which deployment is `main`, `memory`, `document`, `embedding` (`agents.*.deployment`, `knowledge.embedding_deployment`). Chat models get `quoting-guardrail`, the document model `document-guardrail`, embeddings none. |
| `cosmos_location` | `null` = `location` | Region of the two Cosmos accounts when it must differ (dev uses `canadaeast`: Canada Central refused new Cosmos accounts for the subscription, pending a region access request). |
| `operator_ips` | `[]` | Dev only: the deployer's public IPs reach the registry, the Foundry account, Search and kb-docs (firewall rules, default deny), so a workstation can deploy without a runner in the VNet. Refused in prod. |
| `agent_egress_mode` | `Audit` | Hosted-agent egress allow-list (Foundry, Search, Cosmos, Application Insights). Switch to `Enforced` after a clean audit period. |
| `cosmos_failover_location` | `null` | Read region of the app Cosmos account (prod: `canadaeast`, service-managed failover). |
| `cosmos_containers` | the table of architecture 6.3 | Seven containers on `/session_id`: TTL in seconds (proposals, pending compliance review) and autoscale maximum. |
| `search_sku`, `search_replicas` | `standard2`, `1` | See "Decisions": private ingestion needs S2 or higher. |
| `deploy_apps`, `deploy_web`, `app_images` | `false`, `false`, `{}` | `deploy_apps` is false on the first apply (the registry is empty). `deploy_web` adds the web app as a container app (the design used Static Web Apps). Images are used only when an app is created; CD rolls revisions afterwards. |
| `mcp_client_ids` | `{}` | Output `client_ids` of the Entra stack, by app. Set after it is applied; `ENTRA_AUDIENCE` then also holds the client id. |
| `quoting` | `{}` | mcp-quoting: `backends` (`rating`, `catalog`, `address`, `vin` each `{base_url, scope}`; an adapter left out runs on its stub), `http_timeout_seconds` (5), `http_retries` (2). |
| `cicd_principal_id`, `operators_group_id`, `quote_agent_principal_id` | `null` | Optional principals. The agent identity exists only after the first agent deploy, so it is set on the second apply; the `terraform` action then reuses it from the state output `quote_agent_principal_id` on later passes. |

### Models and regions

Models are chosen in `settings.yaml` (GPT-5.6 family: `gpt-5.6-terra` main, `gpt-5.6-luna` memory and documents,
`text-embedding-3-large`), all `GlobalStandard`: in `canadacentral` the current models are offered only as Global
Standard, so prompts may be processed outside Canada. Fine for the demo's fictional data; a Law 25 review comes first for
real customers.

- **Region.** The Foundry account, its agent subnet and the VNet must share one region, so the account region is `location`. There is no per-deployment region.
- **Processing location** follows each deployment's `sku_name`: `Standard` stays in the region; `DataZoneStandard` and `GlobalStandard` are wider. The architecture assumed Data Zone Standard; data zones are US and EU **[UNVERIFIED for Canada]**, so check availability and the Law 25 assessment (NFR-PRV-2) before choosing.
- If a needed model is not offered in `canadacentral`, the choices are another SKU, another region for the whole stack, or a second Foundry account. Not built: it is the team's decision.

## Apply order

New environment, once (needs an admin):

1. Providers registered: `Microsoft.KeyVault, CognitiveServices, Storage, MachineLearningServices, Search, Network, App, ContainerService, ContainerRegistry, DocumentDB, OperationalInsights, Insights, ManagedIdentity, Cdn, Security`.
2. State storage account (instructions in `backend.tf`), a Terraform identity with Contributor and Role Based Access Control Administrator on the subscription, a GitHub OIDC identity per environment (federated credential `repo:<org>/<repo>:environment:<env>`), the GitHub environments below, and a **runner with a network path to the private endpoints** (self-hosted in the VNet or peered, with `docker`, `az` and `jq` installed; hosted runners cannot reach the registry, Foundry or Search).
3. `terraform apply -var-file=environments/<env>.tfvars` once from a workstation with `deploy_apps=false`; this creates the identities.
4. Entra stack by someone with Graph permissions (and Storage Blob Data Contributor on the state account): `terraform -chdir=entra init -backend-config="resource_group_name=<state resource group>" -backend-config="storage_account_name=<state storage account>" -backend-config="key=aiq-<env>-entra.tfstate"`, then `terraform -chdir=entra apply -var environment=<env> -var bff_principal_id=$(terraform output -raw bff_principal_id) -var project_principal_id=$(terraform output -raw project_principal_id)`.

Every release (what `deploy-env.yml` does, in this order):

| Step | What | Where |
|---|---|---|
| 1. Terraform | Platform. Apps stay as they are (or are not created yet). | `terraform` action |
| 2. Images | `docker build` for bff, mcp-quoting, mcp-a2ui (and web), tagged with the commit, pushed to the private registry. | private runner |
| 3. Container apps | Terraform again with `deploy_apps=true` and the image map. | |
| 4. Revisions | `deploy/aca_rollout.sh release`: new revision at 0%, healthy, then 10% (mcp-quoting 100%). | |
| 5. Foundry data plane | `deploy/post_deploy.sh approve knowledge-base toolbox agents`: approve private links, knowledge source and base, toolbox version (promoted), `quote-agent` and `document-agent`. | private runner |
| 6. Agent identity | `post_deploy.sh identity` prints the agent identity; Terraform again with `quote_agent_principal_id` (Cosmos, Search, project roles). Later passes take the id from the state, so steps 1 and 3 of the next release keep those roles. | |
| 7. Smoke | `deploy/smoke_test.py` through Front Door (health, SSE, one real turn with `model_calls == 2`), the toolbox tool list, then 10 more fresh sessions so both revisions answer. | |
| 8. Promote or roll back | Pass: `aca_rollout.sh finish` for every app. Fail: `rollback` (previous revision 100%, new one deactivated). | |

### Scripts

| Script | Purpose |
|---|---|
| `deploy/common.py` | The one HTTP client (Entra bearer, JSON, SSE decoding) and `--dry-run` (one JSON line per request) every script shares. |
| `deploy/create_toolbox.py` | Toolbox `auto-quoting-toolbox` version over `conn-mcp-quoting` and `conn-mcp-a2ui` (`require_approval=never`); `--promote`, `--pin N`, `--list`. |
| `deploy/create_knowledge_base.py` | Blob knowledge source over `kb-docs` (private ingestion, keyless) and `auto-quoting-kb` (query planning, no answer synthesis); `--wait`. Search REST `2026-10-01`. |
| `deploy/smoke_test.py` | `bff`, `toolbox`, `turn` checks. |
| `deploy/post_deploy.sh` | Reads `terraform output -json`, runs the steps above. The agent scripts are called with their real flags (`deploy_quote_agent.py`, `deploy_document_agent.py`). |
| `deploy/aca_rollout.sh` | `release`, `finish`, `rollback` of one container app. |
| `deploy/visual_review.py` | AI visual review (below). |
| `deploy/check_contracts.py` | Schemas are valid JSON Schema, `tools.json` is consistent (6 quoting and 4 a2ui tools). |

## What Terraform creates

| Area | Resources |
|---|---|
| Network | VNet, 3 subnets with one default-rules NSG, 8 private DNS zones plus the Container Apps zone, optional NAT gateway. |
| Foundry | AIServices account (public access off, local auth off, project management on, **hosted-agent network injection into `agent-egress`**), project `auto-quoting`, deployments (serialised with a lock), guardrails `quoting-guardrail` and `document-guardrail` (content filters, prompt shields, egress rules), connections (App Insights, BYO Cosmos, Storage, Search), account and project capability hosts, the three RemoteTool connections. |
| Data | App Cosmos (database `quoting`, 7 containers, continuous backup, local auth off); a second, empty Cosmos for the Agent Service's own state; storage `kb-docs`, `uploads` (30-day lifecycle, Defender malware scanning on upload with blob index tags) and one for the Agent Service; Search (private, RBAC only) with two shared private links; Key Vault (RBAC, no public access) with `session-ref-key` and `session-cookie-key`; registry (Premium, private). |
| Apps | Internal VNet-integrated environment; `bff` (8000, session affinity, 2 to 20 replicas in prod), `mcp-quoting` (8101, **1 replica**), `mcp-a2ui` (8102), optional `web` (8080); one identity each; Key Vault secret references; liveness and readiness probes; revision mode Multiple. |
| Edge | Front Door Premium, WAF (default and bot rule sets, 60 requests per minute per IP on `/api/`), one private link origin per app, 240 s origin timeout, no caching or compression on `/api/*`. |
| Operations | Resource logs of every service to Log Analytics (`diagnostics.tf`). |

Outputs (`outputs.tf`) are endpoints, names and ids; no secret is an output, and the Key Vault secrets are written through ARM with `sensitive_body`.

## Identity and RBAC

No keys anywhere (Cosmos, Storage, Search and the Foundry account have local auth off). Data in `rbac.tf`, `modules/foundry/main.tf` and `entra/main.tf`.

| Principal | Target | Role | Scope |
|---|---|---|---|
| each app identity | Registry | AcrPull | registry |
| BFF | Foundry agents `quote-agent`, `document-agent` | Foundry Agent Consumer | project |
| BFF | Cosmos `quoting` | Built-in Data Contributor | containers `sessions`, `events`, `state_changes`, `quotes`, `confirmations`, `handoffs` |
| BFF | Blob `uploads` | **Storage Blob Data Owner** | container `uploads` (Contributor has no `blobs/tags/read`, which the BFF needs to read the Defender scan tag) |
| BFF | Key Vault | Key Vault Secrets User | secrets `session-ref-key`, `session-cookie-key` |
| mcp-quoting | Cosmos | Built-in Data Reader | container `sessions` |
| mcp-quoting | Key Vault | Key Vault Secrets User | secret `session-ref-key` (the same as the BFF's) |
| mcp-a2ui | none | none | pure compiler |
| Project identity | `mcp-quoting`, `mcp-a2ui` | app role `Tools.Invoke` | `entra/` |
| BFF | `mcp-quoting` (lookups), `mcp-a2ui` (BFF-side renders) | app roles `Lookup.Read`, `Tools.Invoke` | `entra/` |
| Project identity | BYO Cosmos, Storage, Search; Foundry account | Cosmos DB Operator and Built-in Data Contributor; Storage Account Contributor and Blob Data Owner; Search Index Data Contributor and Search Service Contributor; Foundry User | the dedicated BYO accounts, the search service, the account (standard agent setup; the BYO accounts hold nothing else) |
| Search identity | Foundry account | Cognitive Services User and Cognitive Services OpenAI User | account (query planning, embeddings; the docs name both) |
| Search identity | Blob `kb-docs` | Storage Blob Data Reader | container `kb-docs` |
| quote-agent identity (second apply) | Cosmos | Built-in Data Contributor | database `quoting` (see Decisions) |
| quote-agent identity | Search; Foundry project | Search Index Data Reader; Foundry User | search service; project **[UNVERIFIED that Foundry User covers toolbox calls]** |
| CI/CD principal (optional) | Registry, apps, project, Search, `kb-docs` | AcrPush, Container Apps Contributor, Foundry Project Manager, Search Service Contributor and Index Data Contributor, Blob Data Contributor | each resource |
| Operators group (optional) | Log Analytics | Log Analytics Reader | workspace |

The MCP servers' tokens: audience `api://<tenant-id>/aiq-<env>-mcp-quoting` and `api://<tenant-id>/aiq-<env>-mcp-a2ui` (the design said `api://mcp-quoting`; the environment suffix keeps dev and prod apart in one tenant).

## Environment contract of the apps

Set by `locals.tf` (`app_defs`); every app also gets `AZURE_CLIENT_ID` (its identity), `ENVIRONMENT` and `APPLICATIONINSIGHTS_CONNECTION_STRING`. Names follow each component's README.

| App | Variables |
|---|---|
| `bff` | `FOUNDRY_PROJECT_ENDPOINT`, `HOSTED_AGENT_NAME`, `DOCUMENT_AGENT_NAME`, `A2UI_MCP_URL`, `A2UI_MCP_SCOPE`, `QUOTING_MCP_URL`, `QUOTING_MCP_SCOPE`, `COSMOS_ENDPOINT`, `COSMOS_DATABASE`, `BLOB_ACCOUNT_URL`, `BLOB_CONTAINER`, `CORS_ORIGINS` (closed: same origin through Front Door), `OTEL_ENABLED`; secrets (Key Vault references) `SESSION_REF_KEY`, `SESSION_COOKIE_KEY`. |
| `mcp-quoting` | `STATE_READER=cosmos`, `COSMOS_ENDPOINT`, `COSMOS_DATABASE`, `COSMOS_CONTAINER`, `RATING_ENGINE`, `VEHICLE_CATALOG`, `ADDRESS_SERVICE`, `VIN_DECODER` (`stub` or `http`), `<PREFIX>_BASE_URL` and `<PREFIX>_SCOPE` for each real back end, `HTTP_TIMEOUT_SECONDS`, `HTTP_RETRIES`; secret `SESSION_REF_KEY`. **Max replicas 1** until a persistent `QuoteStore` exists: its quotes are in memory, so a second replica would answer `unknown_quote`. The rollout therefore cuts it over at 100% and open quotes are lost at each release. |
| `mcp-quoting`, `mcp-a2ui` | `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` (the `api://` URI, then `,<client id>` once `mcp_client_ids` is set, because v1 tokens carry the client id as `aud`), `ENTRA_ALLOWED_PRINCIPAL_IDS` (BFF and project identities). Read by the inbound Entra check `quote_common.auth.wrap_from_env`, which both servers apply in `create_app()` (NFR-SEC-3): every request except `GET /healthz` needs a token of the tenant for that audience, from an allowed caller, with an app role (mcp-quoting: `Tools.Invoke` for `create_quote` and `modify_quote`, `Tools.Invoke` or `Lookup.Read` otherwise; mcp-a2ui: `Tools.Invoke`). See `docs/toolbox.md`. |
| `web` | none. |

The hosted agent's variables are not set here: `deploy_quote_agent.py` receives them as flags from `post_deploy.sh`.

## CI/CD

| Workflow | Runs | Does |
|---|---|---|
| `ci.yml` | pull requests, main, and as the first job of `cd.yml` | One matrix job for the 7 Python components (quote_common, bff, quote-agent, document-agent, mcp-quoting, mcp-a2ui, deploy: install, `ruff`, `pytest`); web (typecheck, biome, vitest, Playwright visual and e2e, artifact upload of diffs); `contracts`; `terraform` (fmt, validate both stacks, tflint, checkov); `workflows` (actionlint, shellcheck); `api-e2e` (the four services as local processes: `bff/.venv/bin/python -m pytest -c e2e/pytest.ini e2e`); `e2e` (`docker compose -f e2e/docker-compose.yml up --build --exit-code-from e2e`: the service images with the fake model, and the Playwright suite `web/e2e-stack` in its own container). |
| `cd.yml` | push to main, manual (`plan-only`) | `ci` then `dev` then `prod`, both through `deploy-env.yml`. The `prod` GitHub environment has required reviewers. |
| `deploy-env.yml` | called by `cd.yml` | The release steps above, in one job so reviewers approve once. |
| `visual-review.yml` | pull requests that touch `web/` | Below. |
| `codeql.yml` | optional | Python and JavaScript. |

Actions are pinned to commit SHAs (the comment gives the release). No secret is written in any workflow: Azure access is OIDC.

**GitHub setup.** Environments `dev` and `prod` (reviewers on `prod`), each with variables `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`, `TFVARS`, `TF_STATE_RESOURCE_GROUP`, `TF_STATE_STORAGE_ACCOUNT`. Repository variable `PRIVATE_RUNNER` (JSON runner labels such as `["self-hosted","aiq-vnet"]`; default `ubuntu-24.04`).

**Rollback.**

- A failed smoke test or an unhealthy revision rolls back by itself: the previous revision takes 100% and the new one is deactivated.
- After a finished release: re-run `cd.yml` on the previous commit (images are tagged by commit), or `deploy/aca_rollout.sh rollback -g <rg> <app> <new> <previous>`; the previous revision stays available until it is deleted.
- Toolbox: `deploy/create_toolbox.py --pin <n>` points the consumer endpoint at an earlier version at once.
- Agents: versions are immutable. Re-deploy the previous commit, or pin the version selector (plan 6.7).
- Terraform: revert the commit and re-apply. Cosmos and Key Vault have soft delete or backups; `prevent_destroy` is not set so dev stays disposable.

### Visual review job

`visual-review.yml` runs on pull requests that touch `web/`. `screenshots` runs the Playwright visual suite (a moved baseline does not fail it) and uploads `test-results/` (`*-actual`, `*-expected`, `*-diff`) and the baselines. `review` downloads them and asks an AI agent to check them against `docs/style-guide.md`: brand tokens, 12-column grid alignment, fr-CA copy and typography, AA contrast, 390 px phone layout. It posts one sticky pull request comment (marker `<!-- visual-review -->`).

It is advisory: both jobs have `continue-on-error`, the comment says it never blocks a merge, and forks (no OIDC, no secrets) are skipped. Two interchangeable reviewers, chosen by the repository variable `VISUAL_REVIEW_AGENT`:

- **azure-openai** (default): `deploy/visual_review.py` sends the screenshots (data URLs) and the style guide to a vision deployment through the Responses API with a strict JSON schema, and renders the findings as a table. Variables `VISUAL_REVIEW_ENDPOINT`, `VISUAL_REVIEW_MODEL`, `VISUAL_REVIEW_CLIENT_ID` (federated credential for `repo:<org>/<repo>:pull_request`, role Cognitive Services OpenAI User), `AZURE_TENANT_ID`, `AZURE_SUBSCRIPTION_ID`. The endpoint must be reachable from hosted runners, so it is not the private quoting account.
- **claude**: the Claude Code GitHub Action with the same prompt (`visual_review.py --print-prompt` is the single source). Secret `ANTHROPIC_API_KEY`.

## Decisions and deviations

1. **Search tier.** The design said S1 with 2 replicas. The knowledge-source docs (2026-11) require S2 or higher for private ingestion (shared private links to `kb-docs` and the embedding model). `search_sku` defaults to `standard2`; dev costs more than the design assumed. With S1 or Basic the source would have to ingest over a public path, which the storage account does not offer.
2. **Toolbox authentication.** Project identity with an app role (architecture 11, plan 2.4). `contracts/tools.json` says "agent identity". To switch: `authType` of the connections in `main.tf`, and the agent principal as the grantee in `entra/`.
3. **Standard agent setup** uses the documented capability host flow (`2025-06-01`). The newer "capability settings" (`2026-07-15-preview`) page could not be read, so its schema is not used.
4. **Agent history scope.** The quote-agent identity gets Data Contributor on the whole `quoting` database, as requested, because the framework's `CosmosHistoryProvider` calls `create_container_if_not_exists`. The design and the agent README say `agent_history` only; narrow it (one value in `rbac.tf`) once spike S5 shows that is enough.
5. **Guardrails** are attached to the model deployments, because the SDK's `HostedAgentDefinition` has no `rai_config`. The agent-level filters in the policy (`PreRun`, `PostRun`, `PostToolCall`, the indirect-attack check on tool results) only act once the policy is attached to the agent. Until then the tool-response check of architecture 14 is not active.
6. **BFF roles.** Foundry Agent Consumer (least privilege; the message asked for "Azure AI User"), and Storage Blob Data Owner on `uploads` (see the table).
7. **Defender for Storage** is on with scanning on upload; results are blob index tags. No Event Grid topic is provisioned: the BFF polls the tag.
8. **Front Door and the web app.** The web app can run as a container app behind the same Front Door (`deploy_web`); Static Web Apps is not built. Custom domains and certificates are not built.
9. **Hand-offs** stay unconnected: only the Cosmos `handoffs` container (decision of 2026-11-25).
10. **Egress policy.** The allow-list is an inventory (Foundry, Search, Cosmos, Application Insights). Platform domains are allowed automatically. Start in `Audit`.
11. **Application Insights** ingestion is public (no Azure Monitor private link scope).
12. **Secret rotation.** `session-ref-key` and `session-cookie-key` are generated once. Rotation with an overlap window needs the services to accept two keys; not built.

## Verify before build

**[UNVERIFIED]** items, none confirmed against a live subscription:

- `Microsoft.CognitiveServices/accounts/projects/connections` with `authType = ProjectManagedIdentity` and an `audience`: absent from the published ARM schema (through `2026-09-15-preview`), although `azd ai connection create --kind remote-tool --auth-type project-managed-identity --audience ...` exists. If ARM rejects it, create the three connections with that command and remove `azapi_resource.remote_tool`.
- `raiPolicies@2026-09-01`: the agent sources and the filter name `Indirect Attack`; egress rule host patterns.
- `capabilityHosts@2025-06-01`: azapi's embedded schema lacks `capabilityHostKind`, so validation is off for those two resources (the properties are as documented). `deployments` and `connections` use the same API version.
- Front Door private link to a **VNet-integrated internal** Container Apps environment with public network access disabled (documented for workload-profile environments; the combination is not).
- Container Apps resolving Key Vault references through the Key Vault private endpoint; the app FQDN shape `<app>.<environment domain>` (a `postcondition` fails the apply if it is wrong).
- Search shared private link group ids (`blob`, `openai_account`) and that the pending connections are approved by `post_deploy.sh approve`.
- WAF rule set versions (`Microsoft_DefaultRuleSet 2.1`, `Microsoft_BotManagerRuleSet 1.1`).
- Role that lets the agent identity call the toolbox; that Cosmos container scope is not enough for the history provider.
- Toolbox wire names (dot or double underscore): `smoke_test.py` prints what the endpoint serves; record it for R11.
- `GET {project}/agents/quote-agent` returning `instance_identity.principal_id` (the docs name `instance_identity`).
- `visual_review.py`: the token scope `https://ai.azure.com/.default` on an Azure OpenAI `/openai/v1/responses` endpoint (the docs also show `https://cognitiveservices.azure.com/.default`), and a strict `json_schema` answer from the vision deployment.
- `terraform plan` was never run (no subscription): the dependency graph was checked offline (`terraform graph`), and `validate` covers schemas of azurerm and azapi resources whose types are embedded.

## Requests to other components

- **web:** `web/Dockerfile`, build context `solution/`, non-root, listening on **8080**, `GET /` returns 200 (the probe), single page app fallback, `/api` is not served (Front Door routes it to the BFF).
- **mcp-quoting:** a persistent `QuoteStore`, then max replicas can go up (`app_defs` in `locals.tf`). (The inbound Entra validation asked of both MCP servers, NFR-SEC-3, is built: `quote_common.auth.wrap_from_env`.)
- **quote-agent, document-agent:** add `--rai-policy-id` (full ARM id, output `guardrail_ids`) when the SDK exposes `rai_config`; `post_deploy.sh` passes it as soon as the script has the flag.
- **bff:** the Defender scan result is read from the blob index tag with the identity above; nothing else to provision. `CORS_ORIGINS` is closed because the SPA and `/api` share one origin.
- **e2e:** `e2e/docker-compose.yml`; the CI job runs `docker compose -f e2e/docker-compose.yml up --build --exit-code-from e2e` and uploads `e2e/test-results` and `e2e/playwright-report`.
- **contracts:** `tools.json` toolbox auth says agent identity, the architecture says project identity (decision 2).

## Accepted findings

`tflint` runs with the `azurerm` ruleset; `prevent_destroy` and module version pins are switched off in `.tflint.hcl` with a reason. `checkov` passes 59 checks; the 15 skips in `.checkov.yaml` are azurerm 5 renames the checks do not read yet (`public_network_access`, `local_authentication_enabled`), the for_each private endpoints, and decisions (ZRS only, Microsoft-managed keys, one replica in dev).
