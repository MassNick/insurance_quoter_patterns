---
name: connect-foundry-iq
description: Create, refill or attach the Foundry IQ knowledge base `auto-quoting-kb` that the quote agent searches for coverage answers, with citations. Use it for "connect Foundry IQ", "load the knowledge base", "upload the coverage guide", "refresh the KB documents", "the agent should answer coverage questions", "citations are missing", "teach the fake model a new coverage answer". It covers the `kb-docs` blob container (formats, titles, upload), `deploy/create_knowledge_base.py`, how the agent reaches the tool `knowledge.knowledge_base_retrieve` (directly, not through the toolbox), citations on `text.done`, the prompt guidance on when to search, the fake knowledge base for offline tests, and the check after a deployment. It never writes insurance content itself, never uploads documents, creates the knowledge base or runs `deploy/smoke_test.py` unless the user asks for that step, and never puts the knowledge base behind the toolbox.
---

# Connect the Foundry IQ knowledge base

This fills the Blob container `kb-docs`, creates the knowledge source `kb-docs-source` and the knowledge base
`auto-quoting-kb` on Azure AI Search, and makes sure the hosted agent cites what it finds. The agent calls the base
directly over MCP (`FOUNDRY_IQ_MCP_URL`) as the narrate tool `knowledge.knowledge_base_retrieve`; the base returns
passages (`outputMode: extractiveData`) and the main agent writes the answer. Keep the call budget (one narrate round,
three model calls at most), keyless access, and the fake knowledge base for every offline suite.

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- The documents come from the insurer. Never write coverage, underwriting or discount text yourself; fake KB entries are test fixtures.
- No personal data in `kb-docs`. No Search or storage key anywhere: both have local auth off, tokens come from Entra.

## Read first

Paths are relative to the solution folder.

- `docs/toolbox.md` "Foundry IQ knowledge base": the flags, what the script sends, how the base is attached, what is unverified.
- `deploy/create_knowledge_base.py`: `build_source`, `build_knowledge_base`, `wait_for_ingestion`.
- `deploy/post_deploy.sh`: step `knowledge-base`, and which `terraform output` feeds each flag.
- `contracts/tools.json`: `knowledge.knowledge_base_retrieve` (server `foundry-iq`, `narrate`, `endpoint`).
- `agents/quote-agent/src/quote_agent/tools.py`: `build_tools` uses Foundry IQ when `FOUNDRY_IQ_MCP_URL` is set, else `_fake_kb`.
- `agents/quote-agent/src/quote_agent/turn.py`: `_citations`, `Turn._map_result`. `agent.py` puts `Turn.citations` on `text.done`.
- `agents/quote-agent/src/quote_agent/fake/rules.py`: `KB`, `kb_search`, `kb_match`, `QUESTION`.
- `contracts/prompts/main-agent.md` "Scope and guardrails": coverage answers come from the knowledge base, with a citation.
- `web/src/app/store.ts`, `web/src/app/components/Chat.tsx`: how citations are shown.

## Steps

1. **Collect the documents from the user.** The source is described as coverage definitions, underwriting rules,
   discounts and FAQ, in fr-CA (and en-CA). Ask for:
   - text files the blob source parses with `contentExtractionMode: minimal`: PDF with a text layer, DOCX, HTML,
     Markdown or plain text. Scanned images give no text. The exact supported list is **[UNVERIFIED]**: check the
     current Foundry IQ blob knowledge source docs.
   - one topic per file, a clear first heading, and a file name that reads as a title
     (`guide-protections-auto-section-3-2.pdf`). Citations show a title and a reference under the answer.
   - optional blob metadata (`language=fr-CA`, a document version). Whether Foundry IQ returns blob metadata is
     **[UNVERIFIED]** (spike S8); no code relies on it.
   - where the originals are kept: this repository does not version them; blob soft delete keeps a deleted one 7 days.
2. **Upload** (only when the user asks). Shared keys are off and public access is disabled, so upload from a machine
   in the VNet (or the private runner) as an identity with Storage Blob Data Contributor on `kb-docs` (Terraform grants
   it to `cicd_principal_id`). Show the command, then run it on the user's go:

   ```bash
   az storage blob upload-batch --auth-mode login --destination kb-docs --source <local-folder> \
     --account-name "$(terraform -chdir=infra/terraform output -raw kb_storage_id | awk -F/ '{print $NF}')"
   ```

3. **Create or refill the knowledge base.** Flags (environment variable, Terraform output): `--search-endpoint`
   (`SEARCH_ENDPOINT`, `search_endpoint`), `--storage-id` (`KB_STORAGE_ID`, `kb_storage_id`), `--container` (default
   `kb-docs`), `--openai-endpoint` (`OPENAI_ENDPOINT`, derived from the project endpoint by `post_deploy.sh`),
   `--embedding-deployment` / `--embedding-model` (role `embedding`), optional `--planning-deployment` /
   `--planning-model` (role `memory`), `--api-version` (`2026-10-01`), `--wait SECONDS`, `--dry-run`. The four model
   options default from `knowledge` and `deployments` in `settings.yaml` (flag or environment variable first); remove
   `knowledge.planning_deployment` there for a model-free base.
   - Show the requests offline: `PYTHON=deploy/.venv/bin/python deploy/post_deploy.sh --dry-run --tf-output
     <saved terraform output -json> knowledge-base` (the fixture `deploy/tests/fixtures/tf-output.json` shows the shape).
     It prints two `PUT` lines: `/knowledgesources/kb-docs-source` and `/knowledgebases/auto-quoting-kb`.
   - On the user's go: `deploy/post_deploy.sh knowledge-base` (runs `create_knowledge_base.py --wait 1800`). It needs the
     shared private links approved (`post_deploy.sh approve`) and refuses to start while `app_fqdns` is empty; before
     the apps exist, run `create_knowledge_base.py` directly with the flags.
   - Refill: there is no ingestion schedule (`ingestionSchedule: None`). Re-running the script replaces both
     definitions; whether that re-ingests changed blobs is not settled in the code (**[UNVERIFIED]**). Check the
     current docs, then propose a change to `build_source` with its test, and ask before making it.
4. **Attach it to the agent.** Not through the toolbox. `post_deploy.sh agents` passes output `kb_mcp_url` as
   `--foundry-iq-mcp-url` to `agents/quote-agent/deploy/deploy_quote_agent.py` (`FOUNDRY_IQ_MCP_URL`; scope
   `FOUNDRY_IQ_TOKEN_SCOPE`, default `https://search.azure.com/.default`). `build_tools` then opens
   `MCPStreamableHTTPTool("knowledge", ..., tool_name_prefix="knowledge", allowed_tools=["knowledge_base_retrieve"])`:
   the model sees `knowledge__knowledge_base_retrieve`. The agent identity needs Search Index Data Reader, from the
   second `terraform apply` with `quote_agent_principal_id` (skill `deploy-to-azure`). The Terraform connection
   `conn-foundry-iq` exists, but nothing uses it.
5. **Citations.** `Turn._map_result` runs `_citations` on each knowledge result: every object with a `title` and one of
   `url`, `ref_id`, `id`, at any depth, becomes `{id, ref_id, title, url}` (keys present only), and `agent.py` adds the
   list to `text.done` as `citations`. The real result shape is **[UNVERIFIED]** (S8): after the first deployment, read
   one result and adapt `_citations`, with a test, if titles or references sit under other keys. The web expects
   strings (`citations?: string[]` in `store.ts`, rendered as text in `Chat.tsx`) while the agent sends objects: fix
   the shape in `contracts/events.md` (`text.done`) first, then the agent or the web, and test both.
6. **When the agent searches.** The prompt line says coverage explanations must come from
   `knowledge.knowledge_base_retrieve`, with a citation, and to offer an advisor call when the base has no answer. The
   `contracts/tools.json` description says when to search ("before explaining any of them") and that the passages
   come back with sources to explain and cite; it is what the model sees locally (fake KB). In Azure the model sees
   the description Foundry IQ serves for `knowledge_base_retrieve`, which `tools.py` does not override. Change
   either through the skill `edit-agent-prompts` (copy the prompt, run the 30k test, deploy under a new
   `PROMPT_VERSION`).
7. **Teach the fake model an answer.** Append a tuple to `KB` in `fake/rules.py`: a regex on the lowered question, the
   fr-CA answer with `[1]` markers, and the list of titles. First match wins, so a specific pattern goes before a
   general one ("autres que la collision" before "collision"). `kb_search` builds ids `kb_<index>_<n>` and urls
   `kb://guide-protections-auto/<index>/<n>` from the position: append, never insert, or pinned ids shift. The fake
   calls the tool when `kb_match` is true and the topic is `kb` or `greet`, or the text is a question (`QUESTION`).

## Tests to add or update

- `agents/quote-agent/tests/unit/test_rules.py::test_knowledge_base`: the new pattern returns its passage; a near miss returns `[]`.
- `agents/quote-agent/tests/component/test_invocations.py`: like `test_knowledge_question_costs_three_calls_and_cites_sources`
  (3 model calls, `narrate`, the exact `citations`).
- `agents/quote-agent/tests/unit/test_policy.py`: a synthetic result in the real Foundry IQ shape gives the expected `t.citations`.
- `agents/quote-agent/tests/unit/test_wiring.py` if `build_tools` changes.
- `deploy/tests/test_create_knowledge_base.py` for any change to the bodies, the flags or the wait.
- `web/tests/unit/app-state.test.ts` and a component test in `web/tests/unit/` if the citation shape changes.

## Verify

- `cd agents/quote-agent && .venv/bin/python -m pytest`: pass.
- `cd deploy && .venv/bin/python -m pytest tests/test_create_knowledge_base.py tests/test_post_deploy.py`: pass.
- `./test_all.sh`: `all suites passed`.
- Only when the user asks: the real run ends without `failed items` or a timeout. `deploy/post_deploy.sh smoke`
  (`deploy/smoke_test.py`) checks health, the toolbox list and one "Bonjour" turn with 2 calls; it asks no knowledge
  question. To check the base, send one coverage question through the BFF the way the smoke `turn` check does, and
  expect `model_calls == 3` on `turn.completed` and a non-empty `citations` on `text.done`.

## Done when

- [ ] The documents came from the user, contain no personal data, and are in `kb-docs` (if the user asked).
- [ ] The knowledge base exists (if asked) and the first ingestion reported no failed items.
- [ ] The agent version has `FOUNDRY_IQ_MCP_URL`, and its identity has Search Index Data Reader.
- [ ] Citations reach the customer in one agreed shape: contract, agent, web and their tests.
- [ ] Fake KB entries exist for every answer the tests use; `./test_all.sh` passed or failures are reported.

## Pitfalls

- An empty `kb-docs` gives an empty base: the agent says it has no answer and offers an advisor.
- Private ingestion needs Search S2 or higher (`search_sku`) and approved shared private links.
- Two API versions: Search management `2026-10-01` (`--api-version`), the MCP endpoint `2026-11-01-preview` (`kb_mcp_url`).
- `--planning-deployment` without `--planning-model` is a usage error unless the deployment is a key of `deployments` in `settings.yaml`. `--dry-run` sends nothing and skips `--wait`.
- Never add the base to `create_toolbox.py`: `TOOLBOX_TOOLS` counts only `quoting` and `a2ui`.
- One narrate round per turn: a knowledge search and `create_quote` in two batches, and the second is refused.
- Without `FOUNDRY_IQ_MCP_URL` the agent silently uses the fake base. Check the deployed version's environment.
- `post_deploy.sh` without `--tf-output` runs `terraform output`, which reads the remote state in Azure.
