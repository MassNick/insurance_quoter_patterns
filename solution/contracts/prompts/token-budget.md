# Token budget

**Requirement:** the agent prompt plus the tool descriptions must stay under 30,000 tokens.

**Method.** The figures below are estimates at about 4 characters per token (the framework's `CharacterEstimatorTokenizer`), measured on 2026-11-25 with `prompts.static_prefix_tokens()` of `agents/quote-agent`. `test_token_budget_under_30k_and_counts_tools` runs it with the quote-agent tests in CI and fails the build above 30k. The design's `skill/scripts/check_artifacts.py --tokens` counts less (3,059 for the same files: it does not inline the FormSpec schema into `a2ui.render_form` and writes the key list differently), so use the figures here. Before go-live, re-measure them with the deployed model's tokenizer (`o200k`-family for GPT-5.x).

## Static prefix of the main agent (cached)

| Part | Source | Est. tokens |
|---|---|---|
| System prompt, including the D13–D15 rules (document offer once, text first with small subject forms, answer-mode preference, generic suggestions) and the PREFERENCES and RULES context lines | `prompts/main-agent.md` (comment stripped) | ~1,220 |
| 13 tools, each description saying what the tool does, when to call it and what comes back; `a2ui.render_form` with the FormSpec schema inlined (about 1,360 for that tool, 1,260 of it the schema and its `$defs`), `ui.suggest_replies` about 130 (its D15 wording rule lives in the prompt only) | `contracts/tools.json`, `contracts/form-spec.schema.json` | ~2,490 |
| Compact state key list (`entity.key:type[codes]`; labels dropped) | generated from `state-catalog.json` by `prompts.key_list` (68 fields, including `handoff_summary` and `handoff_consent`) | ~740 |
| **Static total** | measured by `prompts.static_prefix_tokens()` (prompt, key list and tools; 4,464) | **4,464** |

The earlier figure (about 4,160, measured on 2026-11-24 with the design's helper) is superseded. The tool-description rewrite of 2026-11-25 (usage guidance per tool, a few property descriptions, developer notes dropped from the FormSpec descriptions) took the total from 4,492 to 4,464. The key list counts keys, types and enum codes only, so `label_fr` and `help_fr` in the catalog do not change it.

Headroom against 30k: about 25.5k. This leaves room for refined business rules, more tools and per-province variants. The CI check is what keeps future edits honest.

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
| Output schema (`memory-output.schema.json`, as `response_format`, compact JSON), including `session_preferences` (about 130) | ~940 |
| Compact key list: the same labels-free list as the main agent (`prompts.key_list`, no labels) | ~740 |
| Dynamic: last assistant message, user message, STATE | 500–3,000 |
| Output | 50–600 |

## Cache layout (both agents)

1. Static prefix first: system prompt, then tools, then the key list. It is byte-identical across turns and sessions, and changes only when `prompt_version` changes.
2. Conversation history next. It is append-only, so each turn's prefix is the previous turn's prefix plus the new turn's items. The BFF's system notes are appended at the end of the history, before the new input, so they never break the prefix.
3. Volatile context last: STATE, signals, input. It is never inserted before the history.
4. `prompt_cache_key = "quote-{agent}:{prompt_version}:{session_id}"` is set on every call. Foundry's prompt-caching guidance says to set this key for gpt-5.6 models. Per-session keys also keep each key under the ~15 requests/min level above which hits degrade.
5. `usage.input_tokens_details.cached_tokens` is recorded per call in `turn.completed` and App Insights.

The target is a cached-token ratio of at least 70% from turn 3 onward.
