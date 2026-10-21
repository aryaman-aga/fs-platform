# Synthetic fixtures

Everything in this folder is **invented**: the Corvus group, its people, identifiers
(CIN, DINs, FRN, membership numbers) and every figure come from a seeded,
double-entry synthetic ledger (`fscore.synthetic.ledger`). No client data is used
anywhere in the generator, the injectors or these files. Real workbooks belong in
`fixtures/private/`, which is git-ignored and never leaves the firm.

## How fixtures are made

```
python tools/make_synthetic_workbook.py out.xlsx [--seed 7] [--exact] [--layout annual|spfs] \
       [--inject NAME ...] [--all] [--expected expected.json]
python tools/make_synthetic_workbook.py --list            # the injection catalogue
```

1. **Ledger** – two financial years of balanced journals (loss in the previous
   year, profit in the current year, equity infusion by the holding company).
   The trial balances net to zero and the cash flow ties by construction.
   `--exact` keeps whole rupees and paise, so the usual print-rounding
   differences appear naturally; the default round mode keeps every amount a
   multiple of one printed paisa, so the clean workbook casts exactly.
2. **Module P** (`fscore.prepare.statements`) lays out the statements in house
   style: BS, P&L, SOCIE, cash flow (indirect), accounting policies, Notes 3–34
   (roll-forwards, ageing, share capital, DBO, leases, Ind AS 7 financing table,
   Ind AS 115 disaggregation, tax reconciliation, EPS, related parties, Going
   Concern, Schedule III ratios, financial instruments) plus the backups
   `NS_TB_FT27` (trial balance), `FAR` and `Ageing`. Every printed figure links
   back to the trial balance through formulas; signature blocks sit on the
   statements and the last notes page; print sheets are tab-grouped.
3. **Injectors** (`fscore.synthetic.inject`) reproduce the error classes of the
   functional specification (§2.3, classes 1–30) and the group-engagement
   patterns (going concern, related parties, multi-period). Each returns the
   findings a correct engine must report: rule ID, sheet, cells, severity, and
   whether the finding is *required* or an acceptable alternative.
4. **Oracle** (`fscore.synthetic.oracle`) re-reads the written file and lists
   every printed total that does not add up, the Balance Sheet / cash-flow ties,
   and the gaps left after a totals-only rewrite (what the balancing lines must
   close). These are added to the expected findings as `natural` (clean exact
   mode) or `collateral` (side effects of an injection).

## Files

| File | What it is |
|---|---|
| `corvus_clean_annual.xlsx` | Clean annual workbook (seed 7, ₹ lakhs): zero expected findings |
| `corvus_all_errors_annual.xlsx` | The same workbook with every annual injection applied |
| `corvus_all_errors_annual.expected.json` | The expected findings, gaps and protected cells for it |

Regenerate them with the commands above; tests generate their own copies on the fly.
LibreOffice recalculates every print-sheet formula to exactly the cached values these
files carry (`fscore/tests/test_synthetic_spfs_lo.py`, marker `libreoffice`).
