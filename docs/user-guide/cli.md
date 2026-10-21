# `fscheck` — the engine on the command line

The same engine as the web platform, for a single PC, fully offline (02 §11). Install with
`pip install fscore` (add `fscore[ai]` only if the firm uses the AI reviewer). Every command writes outputs
into `<workbook folder>/fscheck-out` unless `--out-dir` / `--out` says otherwise, records the run in a local
audit database (`~/.fscheck/audit.db`, override with `--audit-db`), and **never modifies the source workbook**.
Run `fscheck <command> --help` for the full option list.

**Exit codes** (for scripts): `0` success · `1` High findings remain · `2` input or configuration error ·
`3` post-write or PDF verification failed.

## init
`fscheck init "<workbook.xlsx>" [--out config.yaml] [--client NAME] [--year-end 2026-03-31] [--units lakhs]`
Creates a starter `config.yaml` with auto-detected print sheets, statements, year end, units and the signatory
block. Review and correct it — it is the engagement's configuration (02 §8).

## check
`fscheck check "<workbook.xlsx>" [-c config.yaml] [--pdf "<printed.pdf>"] [--py "<signed PY.pdf|xlsx>"] [--html] [--json]`
Runs every registered rule (CAST, PLUG, TYPED, HID, ROLL, XREF, SUB, RATIO, TXT, NARR, GC, RPT, MULTI, PDF) on
displayed values and writes the exceptions register (`… FS-Check <date>.xlsx`), optionally an HTML report and a
JSON results file. With `--pdf`, the printed PDF is reconciled (is it current? does every printed number match?).

## xref
`fscheck xref "<workbook.xlsx>" --propose [--out xref_map.yaml]`
Proposes the cross-reference map (figures that should agree across statements, notes and schedules) for you to
confirm; later years reuse the confirmed map.

## fix
`fscheck fix "<workbook.xlsx>" [--policy totals_only|line_adjust] [--approve E-0003,E-0010] [--dry-run] [--out "<copy>.xlsx"]`
Plans and applies corrections to a **copy** (`<name> - casting corrected.xlsx`): failing totals rewritten to add the
displayed components, typed plugs removed, rounding gaps ≤ 0.20 balanced through the configured lines, approval
items applied only when listed in `--approve`. `--dry-run` prints the change list and post-fix tie-outs without
writing. Every write is verified (only planned cells changed, XML valid, re-check clean); a failed verification
deletes the output and exits with code 3. A timestamped backup is kept before overwriting an earlier copy, and the
command refuses to write while the target is open in Excel.

## pdf
`fscheck pdf "<workbook.xlsx>" "<printed.pdf>" [--json out.json]`
Staleness check of an existing PDF against the workbook and the re-print list of pages (PDF-01…03).

## pdf-export
`fscheck pdf-export "<workbook.xlsx>" [--draft|--final] [--engine auto|excel|libreoffice|native] [--out file.pdf]`
Renders the print-ready PDF (Excel on Windows if available, else LibreOffice, else the built-in renderer for drafts)
and verifies it against the workbook. Drafts carry a watermark and the run ID; `--final` is refused while High
findings remain or signatory checks fail. PDFs are never overwritten (versioned file names).

## review-ai
`fscheck review-ai --findings results.json --provider anthropic|bedrock|vertex|foundry|local|gateway --region <region> [--use-case triage|narrative|xref|explain]`
Builds the minimised, pseudonymised payload, shows it with its size, destination and SHA-256, and sends it only after
you approve that exact payload (or pass `--approve-sha256`). Results come back as proposals; numbers are re-checked
by the engine. Provider `none` (default) does nothing.

## report
`fscheck report <results.json | run-id | latest> [--html] [--register]`
Re-creates the HTML report or exceptions register from a stored run.

## Other commands
- `fscheck rules [--json]` — the rule catalogue with versions (E-RULE-01).
- `fscheck diff "<v1.xlsx>" "<v2.xlsx>" [--json out.json]` — cells changed and printed figures moved.
- `fscheck validate-tb "<tb.csv|xlsx>" [--periods CY=Mar-26,PY=Mar-25] [--mapping mapping.yaml]` — the TB validation gate (E-DATA-01).
- `fscheck prepare --tb "<tb>" --mapping mapping.yaml --out statements.xlsx [--entity NAME]` — Module P: prepare the
  statements and notes from the trial balance, then review them automatically.
- `fscheck synthetic out.xlsx [--inject CLASS,…] [--seed 7]` — synthetic Ind AS workbook with injected error classes
  (for training and tests).
