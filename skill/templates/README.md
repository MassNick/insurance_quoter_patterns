# Templates

| Path | Use |
|---|---|
| `README.template.md`, `architecture.template.md`, `requirements.template.md`, `implementation-plan.template.md` | Section skeletons with guidance comments. The filled reference is the design folder this skill lives in. |
| `contracts/` | Seed copy of the contracts from the auto insurance reference (2026-11-24). Edit `state-catalog.json` and `tools.json` first. |
| `prompts/` | Seed copy of the main, memory and document prompts and the token budget. |
| `bff/` | The end-of-form rules module (`rules/end_of_form.py`, class `EndOfFormRules`) with its unit tests and README. Copy it to `<design folder>/bff/`, edit the example rules, and run `cd bff && python3 -m unittest`. |
| `demo/` | The demo engine: `src/` (styles, A2UI compiler and renderer, forms, journey, gallery, page templates) and `build.py`. Copy it to `<design folder>/demo/` and run `python3 demo/build.py`. |

`process-flow.html` is not copied here. To regenerate the BPMN view, edit the `/* <flow-data> */` block of the existing page; the appendix of `process-flow.md` holds the generator for the Mermaid chart and the table.
These are copies. When the reference design changes, refresh them by copying `contracts/`, `prompts/`, `bff/`, `demo/src/` and `demo/build.py` from the design folder again (2026-11-24: refreshed for Variation C).
