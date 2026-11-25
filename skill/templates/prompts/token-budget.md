# Token budget

**Requirement:** the agent prompt plus the tool descriptions must stay under 30,000 tokens.

**Method.** The figures below are estimates at about 4 characters per token, measured on the files in this folder on 2026-11-24 with `skill/scripts/check_artifacts.py --tokens`, after the D12–D20 prompt and contract changes. Before go-live, re-measure them with the deployed model's tokenizer (`o200k`-family for GPT-5.x). Add the measurement as a CI check (implementation plan, step 4.6) that fails the build above 30k.

## Static prefix of the main agent (cached)

| Part | Source | Est. tokens |
|---|---|---|
| System prompt, including the D13–D15 rules (document offer once, text first with small subject forms, answer-mode preference, generic suggestions) and the PREFERENCES and RULES context lines | `prompts/main-agent.md` (comment stripped) | ~1,220 |
| 13 tools, `a2ui.render_form` with the FormSpec schema inlined (about 1,220 of that), `ui.suggest_replies` with the D15 description (about 140) | `contracts/tools.json`, `contracts/form-spec.schema.json` | ~2,340 |
| Compact state key list (key, type and enum codes; labels dropped) | generated from `state-catalog.json` (68 fields, including `handoff_summary` and `handoff_consent`) | ~600 |
| **Static total** | measured by `python3 skill/scripts/check_artifacts.py --tokens` (4,155) | **~4,160** |

The D12–D20 changes added about 540 tokens (3,615 before): about 260 in the main prompt; about 270 in the tool definitions (the new `ui.suggest_replies` wording, and the FormSpec schema with the `textarea`, `document_drop` and `assignment_board` widgets); and about 10 for the two hand-off fields in the key list. The key list counts keys, types and enum codes only, so `label_fr` and `help_fr` in the catalog do not change it.

Headroom against 30k: about 25.8k. This leaves room for refined business rules, more tools and per-province variants. The CI check is what keeps future edits honest.

## Per-turn dynamic part of the main agent (not counted in the 30k rule)

| Part | Est. tokens |
|---|---|
| Conversation history (Agent Framework `CosmosHistoryProvider`; tool results replaced by stubs) | 1,500–8,000, compacted at 12k |
| STATE (only non-empty fields, compact form `path=value~source/status`) | 300–2,500 |
| SIGNALS, CONFLICTS, OPEN_SURFACES, PREFERENCES, RULES, INPUT | 100–700 |
| System notes appended to the history (reopened-form and full-form saves, rule hits, hand-offs) | 0–150 per turn, then part of the cached history |

## Memory agent (separate call, same turn)

| Part | Est. tokens |
|---|---|
| System prompt (`prompts/memory-agent.md`, comment stripped), including the `session_preferences` rule | ~690 |
| Output schema (`memory-output.schema.json`, as `response_format`, compact JSON), including `session_preferences` (about 150) | ~950 |
| Compact key list (keys, types, labels and enum codes) | ~960 |
| Dynamic: last assistant message, user message, STATE | 500–3,000 |
| Output | 50–600 |

## Cache layout (both agents)

1. Static prefix first: system prompt, then tools, then the key list. It is byte-identical across turns and sessions, and changes only when `prompt_version` changes.
2. Conversation history next. It is append-only, so each turn's prefix is the previous turn's prefix plus the new turn's items. The BFF's system notes are appended at the end of the history, before the new input, so they never break the prefix.
3. Volatile context last: STATE, signals, input. It is never inserted before the history.
4. `prompt_cache_key = "quote-{agent}:{prompt_version}:{session_id}"` is set on every call. Foundry's prompt-caching guidance says to set this key for gpt-5.6 models. Per-session keys also keep each key under the ~15 requests/min level above which hits degrade.
5. `usage.input_tokens_details.cached_tokens` is recorded per call in `turn.completed` and App Insights.

The target is a cached-token ratio of at least 70% from turn 3 onward.
