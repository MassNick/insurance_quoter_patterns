# bff/

Design-only code for the auto insurance quoting BFF. Nothing here is deployed. Python 3.10+, standard library only, no network.

## What is in this folder

| Path | What it is |
|---|---|
| `rules/end_of_form.py` | End-of-form rules: `Severity`, `Rule`, `RuleHit`, `RuleCheckResult`, the `EndOfFormRules` registry, five example rules on `DEFAULT_RULES`, and a short BFF `commit(batch)` integration example (`example_commit`). The module docstring explains the rules and has the step-by-step "How to add a rule" guide. |
| `rules/test_end_of_form.py` | `unittest` tests: each example rule, inferred confirmation, acknowledged fingerprints, STOP-before-FLAG ordering, a no-hit state, and the integration example. |
| `rules/__init__.py` | Re-exports the public names. |

## Run the tests

```
cd bff && python3 -m unittest -v
```

## When the check runs (recommendation)

The BFF evaluates the rules synchronously after every state commit, whatever the source (memory-agent deltas, form submits, document reviews, full-form saves, double confirmations), once per commit batch, before the main agent runs: `DEFAULT_RULES.check(state, session.acknowledged)`. A STOP hit ends the online quote and hands the customer to an advisor; a FLAG hit adds an advisor signal and the quote goes on. This is recommended over a callback on the memory agent's response because forms, documents and full-form edits never come back through the memory agent's output, the commit is the single choke point that sees every change, the check is deterministic and auditable (pure functions of the committed state), and it costs no model call. On the chat path the memory delta commits mid-turn, so a STOP there also drops the main agent's in-flight reply.
