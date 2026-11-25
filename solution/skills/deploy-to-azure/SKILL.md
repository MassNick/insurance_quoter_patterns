---
name: deploy-to-azure
description: Deploy the auto-quote solution to Azure in the documented order, one gated step at a time - prerequisites and open decisions, the models in `settings.yaml`, the platform Terraform stack, the Entra stack, container images and revisions, the Foundry data plane through `deploy/post_deploy.sh` (approvals, knowledge base, toolbox, quote-agent, document-agent, agent identity), smoke tests, then promote or roll back. Use it for "deploy to dev", "deploy to prod", "run terraform", "first deployment", "release a new version", "push the images", "redeploy the agent", "roll back", "what do I need before deploying". Each step that changes Azure is shown first, with its exact command and its plan or dry-run output, and runs only after the user's explicit go for that step. It never applies, pushes, deploys or rolls back on its own initiative, never fills an open decision (regions, Search tier, `deploy_web`) or changes a model, SKU or thinking level in `settings.yaml` without the user, and never commits tfvars, state, plan files or secrets.
---

# Deploy to Azure

The order of `docs/deployment.md` and `.github/workflows/deploy-env.yml`, run by hand with a gate before each change:
two Terraform stacks (`infra/terraform/`, `infra/terraform/entra/`), images for the BFF and the MCP servers, then the
Foundry data plane. Keep the patterns: no keys (Entra everywhere, local auth off), secrets only in Key Vault, OIDC in
CI/CD, and the solution's own scripts rather than ad-hoc `az` calls.

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- **The gate**, for every step that changes Azure: show the exact command, show its plan or dry-run output, ask "Run
  step N on `<env>`?", wait for an explicit yes for that step only, run it, report the output as it is. On a failure,
  stop and offer the matching rollback row. A yes never carries over to the next step.
- `terraform plan`, `terraform output` on a remote state, `identity` and the smoke tests read Azure: run them only within a step the user asked for.
- Never commit `*.tfvars`, state, plan files or saved outputs. Run from a machine with a path into the VNet.

## Read first

Paths are relative to the solution folder.

- `docs/deployment.md`: the order, the variables of both stacks, the data-plane scripts, rollback, secrets.
- `docs/cicd.md`: `deploy-env.yml`, OIDC identities, GitHub environments and variables, the private runner.
- `infra/README.md` "Verify before build": the **[UNVERIFIED]** Foundry items that may fail an apply.
- `settings.yaml` (root) and `docs/hosted-agent.md` "Settings file": the model deployments Terraform creates, each agent's deployment, thinking level, limits and output schema, and the flags of `agents/quote-agent/deploy/deploy_quote_agent.py`, model roles, guardrails.
- `docs/toolbox.md`: `create_toolbox.py`, `create_knowledge_base.py`, RemoteTool connections, troubleshooting.
- `infra/terraform/variables.tf` and `infra/terraform/environments/<env>.tfvars.example`: what the user must fill.
- `deploy/post_deploy.sh --help`, `deploy/aca_rollout.sh --help`: steps and arguments.

## Steps

1. **Prerequisites, offline.** Tools: `az`, `jq`, `docker`, `uv`, `terraform` 1.9 or later (CI uses 1.17.2). Once per
   environment, an admin prepares the providers, the state storage (comment in `infra/terraform/backend.tf`), the
   Terraform and OIDC identities, the GitHub environments and a private runner (`docs/deployment.md` "Once per
   environment" 1 and 2). Then run `./test_all.sh`, `uv run --project deploy --extra test python
   deploy/check_contracts.py`, and in both stacks `terraform fmt -check -recursive`, `init -backend=false`, `validate`.
2. **Models, then open decisions.** The models are in the committed `settings.yaml`, not in the tfvars (the variables
   `model_deployments` and `model_roles` stay unset: they only override the file for one environment): the GPT-5.6
   family (main `gpt-5.6-terra`, memory and document `gpt-5.6-luna`, `text-embedding-3-large`), all `GlobalStandard`
   in Canada Central. `GlobalStandard` can be processed outside the region: before real customer data, the user
   confirms the Law 25 assessment (`docs/hosted-agent.md` "Model deployments"), and a model, version, SKU, capacity or
   thinking level changes only on the user's word. Then ask for the open decisions and write
   `infra/terraform/environments/<env>.tfvars` from the example (git-ignored):

   | Variable | Default | The user decides |
   |---|---|---|
   | `location` | `canadacentral` | One region for everything, the Foundry account included |
   | `search_sku`, `search_replicas` | `standard2`, `1` | S2 is needed for private ingestion of `kb-docs`; a unit costs several times S1, in dev too. Quote today's price from the Azure pricing page, never from memory |
   | `deploy_web` | `false` | `false`: nothing serves the web app in Azure. `true` adds the `web` app and its Front Door route |
   | `agent_egress_mode` | `Audit` | `Enforced` after a clean audit period |
   | `quoting` | `{}` | Real back ends (skill `connect-quoting-systems`); empty means stubs |
   | `cicd_principal_id`, `operators_group_id`, `quote_agent_principal_id` | `null` | Object ids; the last exists only after the first agent deploy, then stays in the tfvars |

3. **Platform stack, first apply** (gate), with `deploy_apps = false`:

   ```bash
   cd infra/terraform && terraform init -backend-config="key=aiq-<env>.tfstate"
   terraform plan -var-file=environments/<env>.tfvars -var deploy_apps=false -out=<env>.tfplan
   terraform apply <env>.tfplan
   ```

4. **Entra stack** (gate), after step 3 because it needs two principal ids. Applied by someone with the Graph
   permissions `Application.ReadWrite.OwnedBy` and `AppRoleAssignment.ReadWrite.All`; `workload` equals the platform's.

   ```bash
   terraform -chdir=entra init
   terraform -chdir=entra plan -var environment=<env> -var bff_principal_id="$(terraform output -raw bff_principal_id)" \
     -var project_principal_id="$(terraform output -raw project_principal_id)" -out=entra-<env>.tfplan
   terraform -chdir=entra apply entra-<env>.tfplan
   ```

5. **Knowledge documents** into `kb-docs` (gate): skill `connect-foundry-iq`, step 2.
6. **Release, platform pass** (gate): plan and apply as in step 3, with `-var deploy_apps=true` and placeholder
   `app_images` once the apps exist (existing apps ignore it).
7. **Images.** From the solution folder, for `bff`, `mcp-quoting`, `mcp-a2ui` (and `web` with `deploy_web`): `docker
   build --platform linux/amd64 -f <bff|mcp/quoting|mcp/a2ui|web>/Dockerfile -t <acr_login_server>/<app>:<git-sha> .`
   Building is local; the gate is on `az acr login --name <acr_name>` and `docker push`.
8. **Platform with the apps** (gate): plan with `-var deploy_apps=true -var 'app_images={bff = "<acr>/bff:<sha>",
   "mcp-quoting" = "...", "mcp-a2ui" = "..."}'`, then apply the plan file.
9. **Revisions** (gate per app): show `deploy/aca_rollout.sh --dry-run release -g <resource_group> <app_names[app]>
   <image> 10`, then run it without `--dry-run`. Use `100` for mcp-quoting. Keep the printed `<previous> <new>`.
10. **Foundry data plane.** Save `terraform output -json` outside the repository, then show all of it:
    `PYTHON=deploy/.venv/bin/python deploy/post_deploy.sh --dry-run --tf-output <file> approve knowledge-base toolbox agents`.
    Run each step on its own go, as `deploy/post_deploy.sh --tf-output <file> <step>`:
    - `approve`: pending private endpoint connections (Search shared private links, Front Door).
    - `knowledge-base`: `create_knowledge_base.py --wait 1800`; needs the documents of step 5.
    - `toolbox`: `create_toolbox.py --promote`; needs the Entra stack and the `conn-mcp-*` connections.
    - `agents`: quote-agent (`deploy_quote_agent.py ... --wait`) and document-agent (`deploy_document_agent.py --model
      <document deployment>`). The deployments come from the Terraform output; the thinking levels, token limits,
      timeout and output schemas come from `settings.yaml`. To show the definitions, run each script with `--dry-run`
      through `uv run --project agents/<agent> --extra deploy python ...` (it prints the resolved values); both
      still need `--project-endpoint`, and the deployments and options default from `settings.yaml`.
11. **Agent identity** (gate): `post_deploy.sh --tf-output <file> identity` prints the principal id. Put it in the tfvars
    as `quote_agent_principal_id`, then plan and apply as in step 6 (Cosmos `quoting`, Search read, Foundry User).
12. **Smoke** (gate, it spends model calls): `post_deploy.sh --tf-output <file> smoke`. CD also runs `smoke_test.py
    --checks turn` 10 more times so both revisions answer. The toolbox serves `<server>___<tool>` names (verified on the first dev deploy).
13. **Promote or roll back** (gate): `deploy/aca_rollout.sh finish -g <rg> <app> <new> <previous>` for each app on
    success, `rollback` with the same arguments on any failure.

**Rollback** (`docs/deployment.md` "Rollback"; each one is a gated step):

| What | How |
|---|---|
| A revision, during or after a release | `deploy/aca_rollout.sh rollback -g <rg> <app> <new> <previous>`, or `az containerapp ingress traffic set -n <app> -g <rg> --revision-weight <previous>=100` |
| Toolbox | `create_toolbox.py --list`, then `--pin <version>` (creates nothing) |
| quote-agent, document-agent | Versions are immutable: deploy again from the previous commit |
| Knowledge base | Re-run `create_knowledge_base.py` from the previous commit; deleted blobs stay 7 days |
| Platform or Entra stack | Revert the commit, plan, apply |

## Tests to add or update

A deployment adds no product code. If you change a `deploy/` script, update `deploy/tests/`; if you change
`deploy_quote_agent.py`, `agents/quote-agent/tests/unit/test_deploy.py`. After the first real deployment, record in
`infra/README.md` "Verify before build" what each **[UNVERIFIED]** item turned out to be (toolbox wire names included).

## Verify

- Offline, always: `./test_all.sh`; `cd deploy && .venv/bin/python -m pytest`; `fmt`, `validate` of both stacks;
  `PYTHON=deploy/.venv/bin/python deploy/post_deploy.sh --dry-run --tf-output deploy/tests/fixtures/tf-output.json` exits 0.
- After a real release the user asked for: `PASS bff`, `PASS turn: ... model_calls=2`, `PASS toolbox: 10 tools: ...`
  (6 `quoting`, 4 `a2ui`); exit code 0. `create_toolbox.py --list` shows the promoted version.

## Done when

- [ ] Every open decision came from the user and sits in the git-ignored `<env>.tfvars`; the models are the ones in the committed `settings.yaml`, unchanged without the user's word.
- [ ] Each Azure-changing step was shown (command and plan or dry run) and ran only after its own yes.
- [ ] Both stacks applied, images tagged by commit, revisions finished (or rolled back), second apply with the agent id.
- [ ] `post_deploy.sh` steps `approve`, `knowledge-base`, `toolbox`, `agents`, `identity` and `smoke` passed.
- [ ] No secret, tfvars, state, plan or output file in the repository; nothing committed or pushed unless asked.

## Pitfalls

- Order matters: platform before Entra (principal ids), Entra before `toolbox` (app roles), apps before `post_deploy.sh`
  (it exits while `app_fqdns` is empty, even for `knowledge-base`).
- `post_deploy.sh` with no step runs all six. Always name the step that got the yes.
- An apply without `quote_agent_principal_id` in the tfvars removes the agent's three role assignments until step 11.
- mcp-quoting releases at 100 %, runs one replica, and loses open quotes at each release or rollback.
- The CD rollback never touches the agents or the toolbox: a version created or promoted in step 10 stays.
- `PROMPT_VERSION` is a runtime setting, set at each deploy by `--prompt-version`; `post_deploy.sh` defaults it to `main:<short sha>`, so every release starts new prompt-cache keys.
- A change to `settings.yaml` reaches Azure in two steps: Terraform for `deployments`, then `post_deploy.sh agents` (or `knowledge-base`) for the agents' options. `post_deploy.sh` reads the deployments by role name from the Terraform output.
- If ARM rejects `azapi_resource.remote_tool`, `infra/README.md` names the `azd ai connection create` fallback. Ask first.
- `session-ref-key` and `session-cookie-key` are generated into Key Vault and never output. To rotate: taint, apply,
  then restart the BFF and mcp-quoting together.
- A model not offered in `location` with its SKU means another SKU, another region for the whole stack, or a second
  Foundry account (not built). The user decides.
