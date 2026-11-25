# Skills

Skills for the agent (or person) who refines this solution. Each folder holds a `SKILL.md`: when to use it, what to
read, the steps, the tests to add and the checks to run. They keep the patterns in `docs/patterns.md`.

| Skill | Use it to |
|---|---|
| [`quote-solution`](quote-solution/SKILL.md) | Find your way: the map, the invariants, which skill to use, what is still stubbed. Read it first. |
| [`connect-quoting-systems`](connect-quoting-systems/SKILL.md) | Replace the stub quoting engine and lookups in mcp-quoting with the insurer's systems, and expose them through the Foundry toolbox. |
| [`connect-foundry-iq`](connect-foundry-iq/SKILL.md) | Create or refill the Foundry IQ knowledge base and attach it to the agent, with citations. |
| [`add-end-of-form-rule`](add-end-of-form-rule/SKILL.md) | Add or change a rule that stops the online quote or flags it for an advisor. |
| [`add-guardrail`](add-guardrail/SKILL.md) | Add a guardrail at the right layer: model deployment content filters, prompt, BFF, tool policy. |
| [`edit-agent-prompts`](edit-agent-prompts/SKILL.md) | Change the main, memory or document agent instructions without breaking caching, tools or tests. |
| [`add-field-or-form`](add-field-or-form/SKILL.md) | Add a state field (and its flags: deterministic, personal data, required to price) or a new form. |
| [`run-regression`](run-regression/SKILL.md) | Run the suites, update screenshot baselines on purpose, and run the visual review by an agent. |
| [`deploy-to-azure`](deploy-to-azure/SKILL.md) | Deploy with Terraform and the Foundry data-plane scripts, in order, only when the user asks. |

## Using them

- **Claude Code:** copy or link the folders into `.claude/skills/` of the repository that holds this solution (or
  `~/.claude/skills/`). The `name` and `description` in each front matter decide when the skill loads.
- **Other agents:** point the agent at `AGENTS.md`; it routes to these files.

Every skill shares the same hard rules (`AGENTS.md`): no secrets, no commit unless asked, no push, no Azure call or
deployment unless the user asks for that step, light code, contracts first, fr-CA customer text, `./test_all.sh` before
done.
