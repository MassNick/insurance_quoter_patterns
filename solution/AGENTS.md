# AGENTS.md

Guidance for AI coding agents taking over this solution: a chat-first auto insurance quote on Azure AI Foundry
(hosted agent, MCP servers on Azure Container Apps, A2UI forms, fr-CA customer text). Read `README.md` for the layout.

## Start here

1. Load the skill for your task from `skills/` (index: `skills/README.md`). Each skill lists the files to read, the
   steps, the tests to add and the checks to run. `skills/quote-solution/SKILL.md` is the map and the invariants.
2. The contracts in `contracts/` are the single source of truth. Change them first, then code, then tests, then docs.
3. Run `./test_all.sh` before you say a change is done (`--quick` while iterating, `--full` before a hand-off).

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...`
  test values.
- Do not commit unless asked. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step.
  `terraform validate` and the offline test suites are always fine.
- Keep the code light: reuse `quote_common`, the BFF's one commit path (`Bff.commit`), the renderer's one field shell,
  and the existing helpers. No speculative abstractions.
- Keep the patterns (`docs/patterns.md`): the model never sees injected tool arguments; no personal data is committed
  before contact consent; every deterministic field is confirmed by the customer twice; end-of-form rules ask before
  they stop the quote; customer text comes from the catalog or a string table, in fr-CA.
- The Desjardins mark in `web/` is a placeholder. Replace it with the official asset before any external showing.

## Where things are

| Task | Start in | Doc |
|---|---|---|
| Connect the insurer's quoting systems | `mcp/quoting/src/mcp_quoting/adapters/` | `docs/toolbox.md` |
| Connect or refill the Foundry IQ knowledge base | `deploy/create_knowledge_base.py` | `docs/toolbox.md`, `docs/hosted-agent.md` |
| Add an end-of-form rule | `bff/src/quote_bff/rules/end_of_form.py` | `docs/patterns.md` |
| Add a guardrail | model deployments (Terraform), prompts, BFF | `docs/hosted-agent.md`, `docs/patterns.md` |
| Change the agent's instructions | `contracts/prompts/` | `docs/hosted-agent.md` |
| Change a model, thinking level or structured output | `settings.yaml` | `docs/hosted-agent.md` |
| Add a field or a form | `contracts/state-catalog.json` | `docs/patterns.md` |
| Deploy | `infra/terraform/`, `deploy/` | `docs/deployment.md`, `docs/cicd.md` |
| Tests and regression | `test_all.sh`, `e2e/` | `docs/testing.md` |
