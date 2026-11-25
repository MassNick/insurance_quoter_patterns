# document-agent (Foundry prompt agent)

Reads one uploaded document (driver's licence, VIN plate, registration certificate, current policy page) and returns its fields as strict JSON (`../../contracts/document-output.schema.json`). It runs as a Foundry **prompt agent**, outside the hosted container, and only the BFF calls it (Responses protocol with the agent reference, `interfaces.md` §3.3). The main agent never sees a document: extracted values reach state only as schema-validated fields (D7, architecture §4.3).

## Files

| Path | What |
|---|---|
| `document_agent.py` | `build_definition(model, reasoning_effort, output_schema)`: the `PromptAgentDefinition` payload (kind `prompt`, model, instructions, optional `reasoning`, strict `text.format`); `strict_schema(schema_file)`; `strip_nulls()`. |
| `prompts/document-agent.md` | Copy of `../../contracts/prompts/document-agent.md` (the HTML comment is stripped when building the definition). |
| `deploy_document_agent.py` | Reads the `agents.document` entry of `../../settings.yaml` for the defaults of its options, then creates a new agent version with `azure-ai-projects` (`AIProjectClient.agents.create_version`). Not run here. |
| `tests/test_definition.py` | Unit tests for the definition, the schemas and the `settings.yaml` defaults of the deploy script. |

## Strict output schema

Strict structured output needs every property listed in `required`. `strict_schema()` derives that form from the contract: optional properties (the 17 extractable fields, `reason`, `page`, `bbox`) become required **and nullable**, where `null` means "not read"; `$schema`, `$id` and the BFF-only `field_map` are removed. `strip_nulls(output)` turns a model output back into the contract shape, which then validates against `document-output.schema.json` (tested). The BFF should apply it before validating (see Requests).

The schema is the contract file named by `agents.document.output_schema` in `settings.yaml` (`document-output.schema.json`). The BFF still reads `document-output.schema.json` (its validator and `field_map`), so another file must keep that shape or the BFF changes with it.

Per-purpose schemas (implementation plan 7.2: `vin`, `licence`, `registration`, `declaration`) can be passed by the BFF per call as `text.format`; the definition carries the full schema as the default.

## Run and test

```bash
cd agents/document-agent
uv venv && uv pip install -e ../../packages/quote_common -e ".[test,deploy]"
.venv/bin/python -m pytest
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/python deploy_document_agent.py --project-endpoint https://dev-only.example --dry-run   # prints the definition
```

Locally and in the BFF's tests a `FakeDocumentExtractor` (BFF) stands in for this agent; there is no local runtime here.

## Deploy (not run here)

The deployment, thinking level and output schema default from the `agents.document` entry of `settings.yaml`; a flag wins (precedence: flag, then `settings.yaml`, then the default below). The deployment named there (`document`) runs `gpt-5.6-luna`; `deployments.document` in the same file is what Terraform creates.

| Option | Default | Meaning |
|---|---|---|
| `--settings` | `../../settings.yaml` (the solution root) | Settings file read for the defaults below. |
| `--project-endpoint` | required | Foundry project endpoint. |
| `--model` | `agents.document.deployment` (required if the file has none) | Vision model **deployment name** (not the model name). `deploy/post_deploy.sh` passes the `document` role's deployment from the Terraform output. |
| `--reasoning-effort` | `agents.document.reasoning_effort`, else empty | Thinking level (`reasoning.effort`); empty = the model's default. |
| `--output-schema` | `agents.document.output_schema`, else `document-output.schema.json` | Structured output: a file in `contracts/`. |
| `--agent-name` | `document-agent` | Foundry agent name. |
| `--description` | "Reads one insurance document and returns strict JSON fields" | Version description. |
| `--dry-run` | off | Print the definition, the settings file used and the output schema name; no Azure call. |

Authentication is `DefaultAzureCredential` (your developer identity or a pipeline identity); no secret. `azure-ai-projects` 2.11 `PromptAgentDefinition` has no guardrail field, so the `document-guardrail` (indirect-attack detection) must be attached to the model deployment.

## Change things

- **Prompt**: edit `../../contracts/prompts/document-agent.md`, copy it to `prompts/`, redeploy.
- **Fields**: change `document-output.schema.json` (and its `field_map` for the BFF); the strict schema follows automatically.
- **Model, thinking level, output schema**: edit `agents.document` and `deployments.document` in `../../settings.yaml`, then redeploy (`deploy/post_deploy.sh agents`); or pass `--model`, `--reasoning-effort`, `--output-schema` once.

## Unverified

- That the service accepts the derived strict schema (nullable `$ref` via `anyOf`, `minimum`/`maximum`, `minItems`/`maxItems`).
- That `gpt-5.6-luna` as a Global Standard deployment is offered in Canada Central (S4), and that the service accepts `reasoning` on a prompt agent for it (the SDK model has the field).
- The deploy script was only run with `--dry-run`.

## Requests to other components

- **BFF**: call `strip_nulls` (or the same logic) on the agent output before validating it against `document-output.schema.json`; `null` means "not read".
