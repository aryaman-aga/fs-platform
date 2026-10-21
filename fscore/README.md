# fscore — the FS-Platform engine

Pure-Python, offline, deterministic engine for Ind AS financial statements:

* **Module R (review)** — casting on printed values, cross-casting, typed rounding
  plugs, hidden contributions, roll-forwards, cross-references and mandatory
  tie-outs, supporting schedules, ratios, signatories / DIN / CIN, period labels,
  narrative and Going Concern figures, related-party three-way reconciliation,
  multi-period / multi-entity rules, and PDF-versus-workbook reconciliation.
* **Fix engine** — totals-only rewrite to a fixed point, plug removal, joint
  balancing of rounding-sized gaps through the designated lines, typed-text
  narrative corrections, safe hiding of out-of-scope columns/rows — written by
  surgical OOXML patching into a *copy*, then verified (XML, independent re-open,
  cell diff, evaluator self-test, full re-check, optional LibreOffice recalc).
* **Module P (preparation)** — trial balance validation gate, mapping library,
  template library, statements and notes builder in house style, disclosure
  checklist, year-end rollover.
* **PDF** — Excel (COM) / LibreOffice / built-in renderers with mandatory
  post-conversion verification, draft watermarks, bookmarks.
* **AI gate** (optional extra `[ai]`) — pseudonymised, approval-gated reviews with
  the official `anthropic` SDK (default model `claude-opus-5-5`).

```python
from fscore import api
from fscore.config import load_config

cfg = load_config("config.yaml")             # or EngagementConfig()
result = api.check_workbook("FS FY26.xlsx", cfg)
plan = api.propose_fixes("FS FY26.xlsx", cfg, check_result=result)
applied = api.apply_fixes("FS FY26.xlsx", plan, "FS FY26 - casting corrected.xlsx", config=cfg)
api.render_pdf(applied.out_path, "out/", mode="draft", config=cfg)
```

CLI: `fscheck check|fix|pdf|pdf-export|xref|review-ai|report|prepare|validate-tb|rules|synth|diff` (see `docs/user-guide/cli.md`).

## Layout

| Package | Purpose |
|---|---|
| `io/` | fast OOXML reader (`xlsx_reader`), styles/themes, surgical patch writer (`ooxml_patch`), PDF reader |
| `engine/` | number-format renderer (`display`), formula tokenizer/parser/translator, evaluator, recalculation, linear forms, periods, statements, tables, key figures, self-test |
| `checks/` | one module per rule family; every rule is registered in `rules/rules.yaml` (versioned data, E-RULE-01) |
| `fix/` | solver (fixed point + balancing), planner, apply, verification, typed text |
| `prepare/` | Module P: TB, mapping, templates, statements builder, disclosure checklist, rollover |
| `synthetic/` | synthetic double-entry books + error injectors (tests and demos) |
| `pdf/`, `ai/`, `report/`, `audit/` | rendering, AI gate, exceptions register / HTML, SQLite audit trail |

All public entry points are in `fscore.api`; the web platform never re-implements engine logic.
