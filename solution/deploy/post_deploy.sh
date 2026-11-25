#!/usr/bin/env bash
# Foundry data-plane deployment, run after `terraform apply` and the image rollout (README of infra/, "Apply order").
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$ROOT/infra/terraform"
if [ -n "${PYTHON:-}" ]; then PY=("$PYTHON"); else PY=(uv run --project "$ROOT/deploy" --extra azure python); fi
ALL_STEPS=(approve knowledge-base toolbox agents identity smoke)
DRY_RUN=0
TF_JSON=""
STEPS=()

# Print the usage text (the caller sends it to stdout for -h and to stderr after a usage error).
usage() {
  cat <<USAGE
Usage: post_deploy.sh [--dry-run] [--tf-output FILE] [STEP...]

Reads the Terraform outputs, then runs the Foundry data-plane steps in order. With no STEP, runs all of them.

Steps:
  approve         approve the pending private endpoint connections (Search shared private links, Front Door)
  knowledge-base  deploy/create_knowledge_base.py: knowledge source over kb-docs + knowledge base auto-quoting-kb
  toolbox         deploy/create_toolbox.py: toolbox version over conn-mcp-quoting and conn-mcp-a2ui, promoted to default
  agents          agents/quote-agent/deploy/deploy_quote_agent.py and agents/document-agent/deploy_document_agent.py (run with uv);
                  the deployments come from the Terraform outputs, thinking levels, limits and schemas from settings.yaml
  identity        print the quote-agent identity id (stdout only) for the second terraform apply (quote_agent_principal_id)
  smoke           deploy/smoke_test.py through Front Door, then the toolbox tool list

Options:
  --dry-run          print what would run. deploy/*.py run with --dry-run (they print their requests); everything else is only printed.
  --tf-output FILE   a saved \`terraform output -json\` (default: run it in infra/terraform)
  -h, --help         this text

Environment: PYTHON (interpreter for deploy/*.py, default: uv run in deploy/), PROMPT_VERSION (default main:<git sha>); az must be signed in for a real run, uv for the agents.
The runner needs a network path to the private Foundry, Search and registry endpoints (self-hosted runner in the VNet).
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --tf-output) TF_JSON="${2:?--tf-output needs a file}"; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
    *) STEPS+=("$1") ;;
  esac
  shift
done
[ ${#STEPS[@]} -eq 0 ] && STEPS=("${ALL_STEPS[@]}")
for s in "${STEPS[@]}"; do
  [[ " ${ALL_STEPS[*]} " == *" $s "* ]] || { echo "unknown step: $s" >&2; usage >&2; exit 2; }
done
command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

if [ -z "$TF_JSON" ]; then
  TF_JSON="$(mktemp)"
  trap 'rm -f "$TF_JSON"' EXIT
  terraform -chdir="$TF_DIR" output -json > "$TF_JSON"
fi

out() { jq -er "$1" "$TF_JSON"; }       # one Terraform output value
show() { printf '+ %s\n' "$*"; }        # dry-run echo of a command that is not run
run() { if [ "$DRY_RUN" = 1 ]; then show "$@"; else "$@"; fi; }  # run a command, or only show it with --dry-run
DRY_ARGS=()
[ "$DRY_RUN" = 1 ] && DRY_ARGS=(--dry-run)

# deploy/*.py read these from the environment; the agent deploy scripts get them as flags in step_agents.
out '.app_fqdns.value | length > 0' > /dev/null || { echo "the container apps are not deployed (deploy_apps = false): apply them first" >&2; exit 1; }
export FOUNDRY_PROJECT_ENDPOINT SEARCH_ENDPOINT KB_STORAGE_ID OPENAI_ENDPOINT MCP_QUOTING_URL MCP_A2UI_URL
export EMBEDDING_DEPLOYMENT EMBEDDING_MODEL PLANNING_DEPLOYMENT PLANNING_MODEL
export MAIN_MODEL_DEPLOYMENT MEMORY_MODEL_DEPLOYMENT DOCUMENT_MODEL_DEPLOYMENT
export TOOLBOX_MCP_URL KB_MCP_URL COSMOS_ENDPOINT COSMOS_DATABASE RAI_POLICY_ID_QUOTING RAI_POLICY_ID_DOCUMENT PROMPT_VERSION BFF_URL
FOUNDRY_PROJECT_ENDPOINT="$(out '.foundry_project_endpoint.value')"
SEARCH_ENDPOINT="$(out '.search_endpoint.value')"
KB_STORAGE_ID="$(out '.kb_storage_id.value')"
OPENAI_ENDPOINT="$(printf '%s' "$FOUNDRY_PROJECT_ENDPOINT" | sed -E 's#\.services\.ai\.azure\.com/.*#.openai.azure.com#')"
MCP_QUOTING_URL="https://$(out '.app_fqdns.value["mcp-quoting"]')/mcp"
MCP_A2UI_URL="https://$(out '.app_fqdns.value["mcp-a2ui"]')/mcp"
# Terraform builds the model deployments from settings.yaml (a tfvars override wins); its output model_deployments maps each role
# (main, memory, document: agents.<role>.deployment; embedding: knowledge.embedding_deployment) to { deployment, model }, so the
# values below follow the file or the override. Only the planning model of the knowledge base is not in that map: it is the memory
# role, as knowledge.planning_deployment says (deploy/tests/test_post_deploy.py checks the file agrees). The scripts take everything
# else (thinking levels, limits, schemas) from settings.yaml themselves: nothing more is passed.
EMBEDDING_DEPLOYMENT="$(out '.model_deployments.value.embedding.deployment')"
EMBEDDING_MODEL="$(out '.model_deployments.value.embedding.model')"
PLANNING_DEPLOYMENT="$(out '.model_deployments.value.memory.deployment')" # the small, fast model plans knowledge-base queries
PLANNING_MODEL="$(out '.model_deployments.value.memory.model')"
MAIN_MODEL_DEPLOYMENT="$(out '.model_deployments.value.main.deployment')"
MEMORY_MODEL_DEPLOYMENT="$(out '.model_deployments.value.memory.deployment')"
DOCUMENT_MODEL_DEPLOYMENT="$(out '.model_deployments.value.document.deployment')"
TOOLBOX_MCP_URL="$(out '.toolbox_mcp_url.value')"
KB_MCP_URL="$(out '.kb_mcp_url.value')"
COSMOS_ENDPOINT="$(out '.cosmos_endpoint.value')"
COSMOS_DATABASE="$(out '.cosmos_database.value')"
RAI_POLICY_ID_QUOTING="$(out '.guardrail_ids.value["quoting-guardrail"]')"
RAI_POLICY_ID_DOCUMENT="$(out '.guardrail_ids.value["document-guardrail"]')"
BFF_URL="https://$(out '.frontdoor_hostname.value')"
PROMPT_VERSION="${PROMPT_VERSION:-main:$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo dev)}"

# Step approve: approve the pending private endpoint connections of the Search shared private links and Front Door.
step_approve() {
  # Pending connections come from Search shared private links and from Front Door. Approving is idempotent.
  local target conn
  while IFS= read -r target; do
    if [ "$DRY_RUN" = 1 ]; then
      show az network private-endpoint-connection list --id "$target" "(approve each Pending one)"
      continue
    fi
    while IFS= read -r conn; do
      [ -n "$conn" ] && az network private-endpoint-connection approve --id "$conn" --description "approved by post_deploy.sh"
    done < <(az network private-endpoint-connection list --id "$target" --query "[?properties.privateLinkServiceConnectionState.status=='Pending'].id" -o tsv)
  done < <(out '.private_link_approval_targets.value[]')
}

# Step knowledge-base: create the knowledge source and the knowledge base, and wait for the first ingestion.
step_knowledge_base() { "${PY[@]}" "$ROOT/deploy/create_knowledge_base.py" --wait 1800 ${DRY_ARGS[@]+"${DRY_ARGS[@]}"}; }
# Step toolbox: create a toolbox version and promote it to the default.
step_toolbox() { "${PY[@]}" "$ROOT/deploy/create_toolbox.py" --promote ${DRY_ARGS[@]+"${DRY_ARGS[@]}"}; }

# Deploy options are flags. --rai-policy-id (it attaches the guardrail to the agent version) is passed once a script supports it.
# ARM ids hold no spaces, so the unquoted expansion below is a safe word split.
# rai_args SCRIPT ID: print "--rai-policy-id ID" when SCRIPT has that flag, nothing otherwise.
rai_args() { if grep -q -- '--rai-policy-id' "$1"; then echo "--rai-policy-id $2"; fi; }

# Step agents: deploy the quote-agent (hosted agent) and the document-agent (prompt agent).
step_agents() {
  # Owned by the agent components. The first quote-agent deploy creates its Entra agent identity.
  local quote="$ROOT/agents/quote-agent/deploy/deploy_quote_agent.py" doc="$ROOT/agents/document-agent/deploy_document_agent.py"
  # shellcheck disable=SC2046
  run uv run --project "$ROOT/agents/quote-agent" --extra deploy python "$quote" \
    --project-endpoint "$FOUNDRY_PROJECT_ENDPOINT" --main-model-deployment "$MAIN_MODEL_DEPLOYMENT" \
    --memory-model-deployment "$MEMORY_MODEL_DEPLOYMENT" --toolbox-mcp-url "$TOOLBOX_MCP_URL" --foundry-iq-mcp-url "$KB_MCP_URL" \
    --cosmos-endpoint "$COSMOS_ENDPOINT" --cosmos-database "$COSMOS_DATABASE" --prompt-version "$PROMPT_VERSION" --wait \
    $(rai_args "$quote" "$RAI_POLICY_ID_QUOTING")
  # shellcheck disable=SC2046
  run uv run --project "$ROOT/agents/document-agent" --extra deploy python "$doc" \
    --project-endpoint "$FOUNDRY_PROJECT_ENDPOINT" --model "$DOCUMENT_MODEL_DEPLOYMENT" $(rai_args "$doc" "$RAI_POLICY_ID_DOCUMENT")
}

# Step identity: print the quote-agent's identity (principal id) on stdout, for the second terraform apply.
step_identity() {
  local url="$FOUNDRY_PROJECT_ENDPOINT/agents/quote-agent?api-version=v1"
  if [ "$DRY_RUN" = 1 ]; then
    show "GET $url | jq -r .instance_identity.principal_id"
    return
  fi
  local token principal
  token="$(az account get-access-token --resource https://ai.azure.com --query accessToken -o tsv)"
  principal="$(curl -fsS -H "Authorization: Bearer $token" "$url" | jq -er '.instance_identity.principal_id')"
  echo "Apply again with: -var quote_agent_principal_id=$principal (Cosmos agent_history, Search read, toolbox)" >&2
  echo "$principal"
}

# Step smoke: the smoke tests through Front Door, then the toolbox tool list.
step_smoke() {
  "${PY[@]}" "$ROOT/deploy/smoke_test.py" --checks bff,turn ${DRY_ARGS[@]+"${DRY_ARGS[@]}"}
  "${PY[@]}" "$ROOT/deploy/smoke_test.py" --checks toolbox ${DRY_ARGS[@]+"${DRY_ARGS[@]}"}
}

for s in "${STEPS[@]}"; do
  echo "== $s" >&2
  "step_${s//-/_}"
done
