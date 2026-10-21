"""``fscheck`` command line (02 §11).

Exit codes (for scripted use):
  0  ok
  1  High findings remain (or a blocking gate failed: TB validation, final PDF refused)
  2  usage or input error (missing file, unreadable workbook, bad configuration, module unavailable)
  3  verification failed (post-write verification, PDF render verification)

Outputs go to ``<workbook dir>/fscheck-out/`` unless ``--out-dir`` is given;
nothing is ever written next to (or over) the source workbook. Every command
is recorded in the local audit store (``~/.fscheck/audit.db``, or
``--audit-db`` / ``FSCHECK_AUDIT_DB``).
"""

from __future__ import annotations

import hashlib
import importlib
import inspect
import json
import logging
import re
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from fscore import api
from fscore.audit.store import AuditStore, canonical_json
from fscore.config import EngagementConfig, StatementSheets, dump_config, load_config
from fscore.models import CheckResult, FixKind, FixPlan, Severity
from fscore.report.common import pdf_needs_reprint, register_filename, sorted_findings

EXIT_OK, EXIT_HIGH, EXIT_INPUT, EXIT_VERIFY = 0, 1, 2, 3

app = typer.Typer(
    name="fscheck",
    help="FS-Platform: offline casting, cross-reference and review engine for Ind AS financial statements.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)
console = Console(highlight=False, soft_wrap=True)
log = logging.getLogger("fscore.cli")

AuditDb = Annotated[Path | None, typer.Option("--audit-db", envvar="FSCHECK_AUDIT_DB", help="Audit database (default ~/.fscheck/audit.db).")]
ConfigOpt = Annotated[Path | None, typer.Option("--config", "-c", help="Engagement configuration YAML.")]
OutDir = Annotated[Path | None, typer.Option("--out-dir", "-o", help="Output folder (default: <workbook dir>/fscheck-out).")]
Verbose = Annotated[bool, typer.Option("--verbose", "-v", help="Verbose local debug log (may include cell contents).")]


class CliError(Exception):
    def __init__(self, message: str, code: int = EXIT_INPUT) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- helpers


def _fail(message: str, code: int = EXIT_INPUT) -> typer.Exit:
    console.print(f"[bold red]Error:[/bold red] {message}")
    return typer.Exit(code)


@contextmanager
def _guard() -> Iterator[None]:
    """Map input/processing errors to exit code 2 with a clean message (no stack traces)."""
    try:
        yield
    except typer.Exit:
        raise
    except CliError as exc:
        raise _fail(str(exc), exc.code) from exc
    except FileNotFoundError as exc:
        raise _fail(f"file not found: {exc.filename or exc}") from exc
    except Exception as exc:
        from fscore.io.xlsx_reader import WorkbookReadError

        if isinstance(exc, WorkbookReadError):
            raise _fail(str(exc)) from exc
        log.debug("unhandled error", exc_info=True)
        raise _fail(f"{type(exc).__name__}: {exc}") from exc


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.WARNING, format="%(levelname)s %(name)s: %(message)s")


def _existing(path: Path, what: str = "file") -> Path:
    p = Path(path).expanduser()
    if not p.exists():
        raise CliError(f"{what} not found: {p}")
    return p


def _config(path: Path | None) -> EngagementConfig:
    if path is None:
        return EngagementConfig()
    try:
        return load_config(_existing(path, "configuration"))
    except CliError:
        raise
    except Exception as exc:
        raise CliError(f"invalid configuration {path.name}: {exc}") from exc


def _out_dir(workbook: Path, out_dir: Path | None) -> Path:
    d = Path(out_dir).expanduser() if out_dir else workbook.resolve().parent / "fscheck-out"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_target(source: Path, target: Path) -> Path:
    if target.resolve() == source.resolve():
        raise CliError("refusing to write over the source workbook; choose another output path")
    return target


def _store(db: Path | None, verbose: bool = False) -> AuditStore:
    return AuditStore(db, verbose=verbose)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _progress(verbose: bool) -> Callable[[str, float], None] | None:
    if not verbose:
        return None

    def report(stage: str, fraction: float) -> None:
        console.print(f"[dim]{fraction:5.0%} {stage}[/dim]")

    return report


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    for n in names:
        if isinstance(obj, dict) and n in obj:
            return obj[n]
        if not isinstance(obj, dict) and hasattr(obj, n):
            return getattr(obj, n)
    return default


def _call_flex(fn: Callable[..., Any], primary: Any = None, **candidates: Any) -> Any:
    """Call a facade function whose exact signature is owned by another module.

    Keyword candidates are passed when the function accepts them (or takes
    ``**kwargs``); a required first positional parameter that matches none of
    them receives ``primary``.
    """
    params = list(inspect.signature(fn).parameters.values())
    named = {p.name for p in params if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)}
    kwargs = {k: v for k, v in candidates.items() if k in named}
    if any(p.kind == p.VAR_KEYWORD for p in params):
        kwargs.update({k: v for k, v in candidates.items() if k not in named and v is not None})
    args: list[Any] = []
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if primary is not None and positional:
        first = positional[0]
        if first.name not in kwargs and first.default is first.empty:
            args.append(primary)
    return fn(*args, **kwargs)


def _optional_module(name: str) -> Any | None:
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


def _severity_table(result: CheckResult) -> Table:
    t = Table(title="Findings", show_lines=False)
    t.add_column("Severity")
    t.add_column("Count", justify="right")
    colours = {"High": "red", "Medium": "yellow", "Low": "cyan", "Info": "dim"}
    for sev, n in result.counts().items():
        t.add_row(f"[{colours.get(sev, 'white')}]{sev}[/]", str(n))
    return t


def _summarise(result: CheckResult, *, plan: FixPlan | None = None, applied: Any = None, limit: int = 15) -> None:
    console.print(_severity_table(result))
    high = [f for f in sorted_findings(result.findings) if f.severity == Severity.HIGH]
    if high:
        t = Table(title=f"High findings (first {min(limit, len(high))} of {len(high)})")
        for col in ("#", "Rule", "Where", "Issue"):
            t.add_column(col, overflow="fold")
        for f in high[:limit]:
            t.add_row(f.id, f.rule_id, f"{f.sheet or ''}!{','.join(f.cells)}", f.explanation[:160])
        console.print(t)
    auto = sum(1 for f in result.findings if f.fix_kind == FixKind.AUTO)
    approval = sum(1 for f in result.findings if f.fix_kind == FixKind.APPROVAL)
    manual = sum(1 for f in result.findings if f.fix_kind == FixKind.MANUAL)
    console.print(f"Auto-fixable: {auto} · Need approval: {approval} · Manual: {manual}")
    console.print("PDF must be re-printed: " + ("[bold red]yes[/]" if pdf_needs_reprint(result, plan, applied) else "no"))


def _load_result(path: Path) -> CheckResult:
    data = json.loads(_existing(path, "results file").read_text(encoding="utf-8"))
    if isinstance(data, dict) and "run" in data:
        return CheckResult.model_validate(data)
    raise CliError(f"{path.name} is not a check result (expected the JSON written by `fscheck check --json`)")


# --------------------------------------------------------------------------- init


_ROLE_PATTERNS: dict[str, tuple[str, str]] = {
    # role: (sheet-name regex, title regex inside the sheet)
    "balance_sheet": (r"^\s*(bs|b\.s\.|balance\s*sheet)\s*$", r"balance\s+sheet\s+as\s+at"),
    "profit_loss": (r"^\s*(p\s*&\s*l|p\s*-?\s*l|pl|p-pl|profit.*loss|soi|income\s+statement)\s*$", r"statement\s+of\s+profit\s+and\s+loss"),
    "cash_flow": (r"^\s*(cf|cfs|cash\s*flows?)\s*$", r"cash\s+flow\s+statement|statement\s+of\s+cash\s+flows"),
    "soce": (r"^\s*(soce|socie|sce|changes\s+in\s+equity)\s*$", r"statement\s+of\s+changes\s+in\s+equity"),
    "rpt": (r"^\s*(rpt|related\s*part(y|ies).*)\s*$", r"related\s+part(y|ies)"),
    "ratios": (r"^\s*ratios?\s*$", r"ratios"),
    "trial_balance": (r"^\s*(ns_tb.*|tb.*|trial\s*balance.*)\s*$", r"trial\s+balance"),
}


def detect_statements(wb: Any) -> StatementSheets:
    found: dict[str, str] = {}
    for role, (name_rx, _title) in _ROLE_PATTERNS.items():
        for s in wb.sheets:
            if re.match(name_rx, s.name, re.I):
                found.setdefault(role, s.name)
    for role, (_name, title_rx) in _ROLE_PATTERNS.items():
        if role in found or role == "trial_balance":
            continue
        for s in wb.sheets:
            if not s.visible:
                continue
            texts = [str(c.value) for c in s.iter_cells() if c.row <= 12 and isinstance(c.value, str)]
            if any(re.search(title_rx, t, re.I) for t in texts):
                found.setdefault(role, s.name)
                break
    return StatementSheets(**found)


_DATE_RX = re.compile(
    r"(?:as\s+at|ended|ending)\s+(?:on\s+)?((?:\d{1,2}(?:st|nd|rd|th)?\s+)?(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*(?:\d{1,2}(?:st|nd|rd|th)?,?\s*)?\d{4})",
    re.I,
)
_UNIT_RX = [("crores", re.compile(r"\bcrores?\b", re.I)), ("millions", re.compile(r"\bmillions?\b|\bmn\b", re.I)),
            ("lakhs", re.compile(r"\blakhs?\b|\blacs?\b", re.I)), ("thousands", re.compile(r"\bthousands?\b|'000", re.I))]


def detect_year_end(wb: Any, sheet_name: str | None) -> date | None:
    from dateutil import parser as dparser

    sheets = [wb.get_sheet(sheet_name)] if sheet_name else []
    sheets += [s for s in wb.sheets if s.visible][:3]
    for s in [x for x in sheets if x is not None]:
        for c in s.iter_cells():
            if c.row > 15 or not isinstance(c.value, str):
                continue
            m = _DATE_RX.search(c.value)
            if m:
                try:
                    return dparser.parse(m.group(1), dayfirst=False, fuzzy=True).date()
                except (ValueError, OverflowError):
                    continue
    return None


_AMOUNT_LINE = re.compile(r"amount|₹|\brs\.?(?=\s|$)|\binr\b", re.I)


def detect_units(wb: Any) -> str | None:
    for s in [x for x in wb.sheets if x.visible][:4]:
        for c in s.iter_cells():
            if c.row > 12 or not isinstance(c.value, str) or not _AMOUNT_LINE.search(c.value):
                continue
            for unit, rx in _UNIT_RX:
                if rx.search(c.value):
                    return unit
    return None


_DIN_RX = re.compile(r"\bDIN\s*(?:No\.?)?\s*[:\-]?\s*(\d{8}|X{2,})", re.I)
_MEM_RX = re.compile(r"\bMembership\s*(?:No\.?|Number)?\s*[:\-]?\s*(\d{6})", re.I)
_ROLE_WORDS = re.compile(r"^(director|managing director|whole[- ]time director|partner|proprietor|chief financial officer|cfo|company secretary|cs|ceo)$", re.I)


def detect_signatories(wb: Any, sheet_name: str | None) -> list[dict[str, str]]:
    sheet = wb.get_sheet(sheet_name) if sheet_name else next((s for s in wb.sheets if s.visible), None)
    if sheet is None:
        return []
    out: list[dict[str, str]] = []
    for c in sheet.iter_cells():
        if not isinstance(c.value, str):
            continue
        m = _DIN_RX.search(c.value) or _MEM_RX.search(c.value)
        if not m:
            continue
        role, name = "", ""
        for r in range(c.row - 1, max(0, c.row - 5), -1):
            above = sheet.cell(r, c.col)
            if above is None or not isinstance(above.value, str) or not above.value.strip():
                continue
            text = above.value.strip()
            if _ROLE_WORDS.match(text) and not role:
                role = text
                continue
            name = text
            break
        entry = {"name": name, "role": role or ("Partner" if m.re is _MEM_RX else "Director")}
        entry["din" if m.re is _DIN_RX else "membership_no"] = m.group(1)
        if entry not in out:
            out.append(entry)
    return out


@app.command()
def init(
    workbook: Annotated[Path, typer.Argument(help="Workbook to configure.")],
    out: Annotated[Path | None, typer.Option("--out", help="Where to write config.yaml.")] = None,
    client: Annotated[str | None, typer.Option(help="Client name for reports.")] = None,
    year_end: Annotated[str | None, typer.Option(help="Year end (YYYY-MM-DD); detected from headings when omitted.")] = None,
    units: Annotated[str | None, typer.Option(help="lakhs | millions | crores | thousands | rupees")] = None,
    force: Annotated[bool, typer.Option("--force", help="Overwrite an existing config file.")] = False,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Create config.yaml with auto-detected print sheets, statements and signatories."""
    _setup_logging(verbose)
    with _guard():
        from fscore.io.xlsx_reader import Workbook

        wb_path = _existing(workbook, "workbook")
        target = Path(out) if out else _out_dir(wb_path, None) / "config.yaml"
        if target.exists() and not force:
            raise CliError(f"{target} exists; use --force to overwrite")
        with Workbook.open(wb_path) as wb:
            from fscore.engine.printsel import select_print_sheets

            cfg = EngagementConfig(client=client or wb_path.stem)
            stmts = detect_statements(wb)
            cfg.statements = stmts
            cfg.print_sheets.explicit = [s.name for s in select_print_sheets(wb, cfg)]
            ye = date.fromisoformat(year_end) if year_end else detect_year_end(wb, stmts.balance_sheet)
            if ye:
                cfg.year_end = ye
            detected_units = units or detect_units(wb)
            from fscore.config import UNIT_DIVISORS

            if detected_units and detected_units not in UNIT_DIVISORS:
                raise CliError(f"--units must be one of {', '.join(UNIT_DIVISORS)}")
            if detected_units:
                cfg.units = detected_units  # type: ignore[assignment]
            signatories = detect_signatories(wb, stmts.balance_sheet)
            sha = wb.sha256
        if signatories:
            sig_path = target.parent / "signatories.yaml"
            if not sig_path.exists() or force:
                sig_path.write_text(
                    "# Detected from the Balance Sheet signature block — confirm before use.\n"
                    + yaml.safe_dump({"signatories": signatories}, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )
            cfg.signatories = str(sig_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# fscheck configuration — review the detected values before use.\n" + dump_config(cfg), encoding="utf-8")
        with _store(audit_db, verbose) as store:
            store.append_event("config.init", object_type="file", object_id=sha[:16], details={"file_sha256": sha, "config_hash": cfg.config_hash()})
        console.print(f"Wrote {target}")
        console.print(f"Print sheets: {', '.join(cfg.print_sheets.explicit) or '(none)'}")
        roles = {k: v for k, v in stmts.model_dump().items() if v}
        console.print("Statements: " + (", ".join(f"{k}={v}" for k, v in roles.items()) or "(none detected)"))
        if signatories:
            console.print(f"Signatories: {len(signatories)} detected (see signatories.yaml)")


# --------------------------------------------------------------------------- check


@app.command()
def check(
    workbook: Annotated[Path, typer.Argument(help="Workbook to check.")],
    pdf: Annotated[Path | None, typer.Option("--pdf", help="Printed PDF to reconcile (PDF-01…03).")] = None,
    py: Annotated[Path | None, typer.Option("--py", help="Signed prior-year statements (PDF or XLSX).")] = None,
    config: ConfigOpt = None,
    out_dir: OutDir = None,
    html: Annotated[bool, typer.Option("--html/--no-html", help="Also write the HTML report.")] = False,
    json_out: Annotated[bool, typer.Option("--json/--no-json", help="Also write the results as JSON.")] = False,
    register: Annotated[bool, typer.Option("--register/--no-register", help="Write the exceptions register.")] = True,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Run every rule and write the exceptions register."""
    _setup_logging(verbose)
    with _guard():
        wb_path = _existing(workbook, "workbook")
        cfg = _config(config)
        pdf_path = _existing(pdf, "PDF") if pdf else None
        py_path = _existing(py, "prior-year file") if py else None
        result = api.check_workbook(wb_path, cfg, pdf_path=pdf_path, prior_year_path=py_path, progress=_progress(verbose))
        out = _out_dir(wb_path, out_dir)
        written = _write_outputs(result, out, cfg, wb_path, register=register, html=html, json_out=json_out)
        with _store(audit_db, verbose) as store:
            run_id = store.record_check(result, params={"pdf": bool(pdf_path), "py": bool(py_path), "config_hash": cfg.config_hash()})
        _summarise(result)
        for p in written:
            console.print(f"Wrote {p}")
        console.print(f"Audit run {run_id}")
    raise typer.Exit(EXIT_HIGH if result.high_count else EXIT_OK)


def _write_outputs(result: CheckResult, out: Path, cfg: EngagementConfig, wb_path: Path, *, register: bool, html: bool, json_out: bool,
                   plan: FixPlan | None = None, applied: Any = None) -> list[Path]:
    written: list[Path] = []
    if register:
        written.append(api.write_register(result, out / register_filename(cfg.client if cfg.client != "Client" else wb_path.stem), plan=plan, applied=applied))
    if html:
        written.append(api.write_html_report(result, out / f"{wb_path.stem} FS-Check.html", plan=plan))
    if json_out:
        p = out / f"{wb_path.stem}.check.json"
        p.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        written.append(p)
    return written


# --------------------------------------------------------------------------- xref


@app.command()
def xref(
    workbook: Annotated[Path, typer.Argument(help="Workbook.")],
    propose: Annotated[bool, typer.Option("--propose/--no-propose", help="Write the proposed cross-reference map for confirmation.")] = True,
    config: ConfigOpt = None,
    out: Annotated[Path | None, typer.Option("--out", help="xref_map.yaml path.")] = None,
    out_dir: OutDir = None,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Propose the cross-reference map (XREF-02) for user confirmation."""
    _setup_logging(verbose)
    with _guard():
        wb_path = _existing(workbook, "workbook")
        cfg = _config(config)
        result = api.check_workbook(wb_path, cfg, progress=_progress(verbose))
        t = Table(title=f"Cross-reference pairs ({len(result.xref_map)})")
        for col in ("A", "Printed A", "B", "Printed B", "Status", "Source"):
            t.add_column(col, overflow="fold")
        for p in result.xref_map:
            t.add_row(str(p.a), p.printed_a or "", str(p.b), p.printed_b or "", p.status, p.source)
        console.print(t)
        if propose:
            target = Path(out) if out else _out_dir(wb_path, out_dir) / "xref_map.yaml"
            pairs = [
                {"a": str(p.a), "b": str(p.b), "caption_a": p.caption_a, "caption_b": p.caption_b, "printed_a": p.printed_a,
                 "printed_b": p.printed_b, "status": p.status, "source": p.source, "confirmed": p.confirmed}
                for p in result.xref_map
            ]
            target.write_text(
                "# Proposed cross-reference map: set confirmed: true for each pair that must agree.\n"
                + yaml.safe_dump({"file_sha256": result.run.file_sha256, "pairs": pairs}, sort_keys=False, allow_unicode=True),
                encoding="utf-8",
            )
            console.print(f"Wrote {target}")
        with _store(audit_db, verbose) as store:
            store.record_check(result, kind="xref")
    mismatches = sum(1 for p in result.xref_map if p.status in ("mismatch", "rounding_mismatch"))
    raise typer.Exit(EXIT_HIGH if mismatches or result.high_count else EXIT_OK)


# --------------------------------------------------------------------------- fix


@app.command()
def fix(
    workbook: Annotated[Path, typer.Argument(help="Workbook to correct (never modified).")],
    policy: Annotated[str, typer.Option("--policy", help="totals_only | line_adjust")] = "totals_only",
    approve: Annotated[str | None, typer.Option("--approve", help="Comma-separated edit IDs approved for this run (e.g. E-0003,E-0010).")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Show the change list and post-fix tie-outs; write nothing.")] = False,
    config: ConfigOpt = None,
    out: Annotated[Path | None, typer.Option("--out", help="Corrected copy path (default '<name> - casting corrected.xlsx').")] = None,
    out_dir: OutDir = None,
    html: Annotated[bool, typer.Option("--html/--no-html")] = False,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Plan the corrections and write a verified corrected copy."""
    _setup_logging(verbose)
    if policy not in ("totals_only", "line_adjust"):
        raise _fail("--policy must be totals_only or line_adjust")
    exit_code = EXIT_OK
    with _guard():
        wb_path = _existing(workbook, "workbook")
        cfg = _config(config)
        cfg.policy.casting = policy  # type: ignore[assignment]
        approved = {x.strip() for x in (approve or "").split(",") if x.strip()}
        before = api.check_workbook(wb_path, cfg, progress=_progress(verbose))
        plan = api.propose_fixes(wb_path, cfg, check_result=before)
        unknown = approved - {e.id for e in plan.edits}
        if unknown:
            raise CliError(f"unknown edit id(s) in --approve: {', '.join(sorted(unknown))}")
        with _store(audit_db, verbose) as store:
            run_id = store.record_check(before, kind="fix", params={"policy": policy, "dry_run": dry_run})
            plan_id = store.record_plan(plan, run_id=run_id)
            if approved:
                store.record_approval(plan_id, store.actor, plan.plan_sha256, sorted(approved))
        _print_plan(plan, approved)
        if dry_run:
            console.print("[bold]Dry run[/bold]: nothing was written.")
            raise typer.Exit(EXIT_HIGH if before.high_count else EXIT_OK)
        out_folder = _out_dir(wb_path, out_dir)
        target = _safe_target(wb_path, Path(out) if out else out_folder / f"{wb_path.stem} - casting corrected.xlsx")
        applied = api.apply_fixes(wb_path, plan, target, approved_edit_ids=approved or None, config=cfg, backup_dir=out_folder / "backups")
        with _store(audit_db, verbose) as store:
            store.record_apply(plan_id, applied, run_id=run_id)
        if not applied.ok or not applied.verification.ok:
            console.print(f"[bold red]Post-write verification failed[/bold red]: {applied.message}")
            for step in applied.verification.steps:
                if not step.ok:
                    console.print(f"  ✗ {step.name}: {step.detail}")
            raise typer.Exit(EXIT_VERIFY)
        console.print(f"Wrote {applied.out_path} ({len(applied.applied_edit_ids)} edits applied, verification passed)")
        after = api.check_workbook(Path(applied.out_path or target), cfg, progress=_progress(verbose))
        with _store(audit_db, verbose) as store:
            store.record_check(after, kind="post-fix")
        written = _write_outputs(after, out_folder, cfg, wb_path, register=True, html=html, json_out=False, plan=plan, applied=applied)
        _summarise(after, plan=plan, applied=applied)
        for p in written:
            console.print(f"Wrote {p}")
        exit_code = EXIT_HIGH if after.high_count else EXIT_OK
    raise typer.Exit(exit_code)


def _print_plan(plan: FixPlan, approved: set[str]) -> None:
    t = Table(title=f"Fix plan ({len(plan.edits)} edits, policy {plan.policy}, sha256 {plan.plan_sha256[:12]})")
    for col in ("Edit", "Kind", "Cell", "Old", "New", "Status"):
        t.add_column(col, overflow="fold")
    for e in plan.edits:
        old = f"={e.old_formula}" if e.old_formula else (e.old_value or "")
        new = f"={e.new_formula}" if e.new_formula else (e.new_value or "")
        if e.fix_kind == FixKind.AUTO:
            status = "auto"
        elif e.id in approved or e.approved:
            status = "approved"
        else:
            status = f"{e.fix_kind.value} (not applied)"
        t.add_row(e.id, e.group.value, f"{e.sheet}!{e.cell}", old[:60], new[:60], status)
    console.print(t)
    for b in plan.balancing:
        console.print(f"Balancing {b.statement} {b.column}: gap {b.gap} → {b.status} {b.adjustment} {b.note}")
    if plan.printed_moves:
        console.print(f"Printed figures that will move: {len(plan.printed_moves)} (PY: {sum(1 for m in plan.printed_moves if m.is_py)}, key figures: {sum(1 for m in plan.printed_moves if m.is_key)})")
    for u in plan.unresolved:
        console.print(f"[yellow]Unresolved:[/yellow] {u}")
    if not plan.converged:
        console.print(f"[yellow]Fixed point not reached after {plan.passes} passes.[/yellow]")


# --------------------------------------------------------------------------- PDF


@app.command("pdf")
def pdf_cmd(
    workbook: Annotated[Path, typer.Argument(help="Workbook.")],
    printed_pdf: Annotated[Path, typer.Argument(help="The printed PDF.")],
    config: ConfigOpt = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Write the reconciliation as JSON to this path.")] = None,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Is the printed PDF current? Lists missing values and the re-print list (PDF-01…03)."""
    _setup_logging(verbose)
    with _guard():
        wb_path = _existing(workbook, "workbook")
        pdf_path = _existing(printed_pdf, "PDF")
        cfg = _config(config)
        rec = api.reconcile_pdf(wb_path, pdf_path, cfg)
        console.print(f"Pages: {rec.pages} · values checked: {rec.values_checked} · missing: {len(rec.misses)}")
        if rec.stale:
            console.print("[bold red]The PDF appears to be printed from an older version of the workbook.[/bold red]")
        if rec.misses:
            t = Table(title="Values not found in the PDF")
            for col in ("Sheet", "Cell", "Caption", "Printed", "Pages"):
                t.add_column(col, overflow="fold")
            for m in rec.misses[:50]:
                t.add_row(m.sheet, m.cell, m.caption or "", m.printed, ", ".join(map(str, m.pages)))
            console.print(t)
        for tok in rec.error_tokens:
            console.print(f"[red]{tok}[/red]")
        for tm in rec.text_mismatches:
            console.print(f"[yellow]{tm}[/yellow]")
        if rec.reprint_pages:
            console.print("Re-print pages: " + ", ".join(map(str, rec.reprint_pages)))
        if json_out:
            Path(json_out).write_text(rec.model_dump_json(indent=2), encoding="utf-8")
        with _store(audit_db, verbose) as store:
            store.append_event("pdf.reconciled", object_type="file", object_id=_sha256(wb_path)[:16],
                               details={"pdf_sha256": rec.pdf_sha256, "misses": len(rec.misses), "stale": rec.stale, "pages": rec.pages})
        bad = rec.stale or bool(rec.misses) or bool(rec.error_tokens)
    raise typer.Exit(EXIT_HIGH if bad else EXIT_OK)


def _pdf_name(cfg: EngagementConfig, wb_path: Path, out_dir: Path) -> Path:
    entity = re.sub(r"[^A-Za-z0-9-]+", "-", cfg.entity or (cfg.client if cfg.client != "Client" else wb_path.stem)).strip("-") or "Entity"
    stmt_set = {"spfs": "SPFS", "interim": "Interim-FS"}.get(cfg.period_type, "FS")
    ye = cfg.year_end
    period = f"Qtr-{ye:%b-%y}" if cfg.period_type in ("spfs", "interim") else f"FY{ye.year - 1}-{ye:%y}"
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    version = 1
    pattern = re.compile(rf"^{re.escape(entity)}_{re.escape(stmt_set)}_{re.escape(period)}_v(\d+)_")
    for p in out_dir.glob("*.pdf"):
        m = pattern.match(p.name)
        if m:
            version = max(version, int(m.group(1)) + 1)
    return out_dir / f"{entity}_{stmt_set}_{period}_v{version}_{stamp}.pdf"


@app.command("pdf-export")
def pdf_export(
    workbook: Annotated[Path, typer.Argument(help="Workbook to render.")],
    draft: Annotated[bool, typer.Option("--draft/--final", help="Draft (watermarked) or final (only with zero High findings).")] = True,
    engine: Annotated[str, typer.Option("--engine", help="auto | excel | libreoffice | native")] = "auto",
    config: ConfigOpt = None,
    out: Annotated[Path | None, typer.Option("--out", help="PDF path (default: versioned name in the output folder).")] = None,
    out_dir: OutDir = None,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Render the print-ready PDF and verify it against the workbook."""
    _setup_logging(verbose)
    with _guard():
        wb_path = _existing(workbook, "workbook")
        cfg = _config(config)
        folder = _out_dir(wb_path, out_dir)
        target = Path(out) if out else _pdf_name(cfg, wb_path, folder)
        if target.exists():
            raise CliError(f"{target.name} exists; PDFs are never overwritten")
        mode = "draft" if draft else "final"
        with _store(audit_db, verbose) as store:
            run_id = store.start_run("render", file_name=wb_path.name, file_sha256=_sha256(wb_path), config_hash=cfg.config_hash(), params={"mode": mode, "engine": engine})
        res = api.render_pdf(wb_path, target, mode=mode, engine=engine, config=cfg, run_id=run_id)
        misses = len(res.verification.misses) if res.verification else 0
        status = "ok" if res.ok and misses == 0 else "failed"
        with _store(audit_db, verbose) as store:
            store.finish_run(run_id, status=status, summary={"pdf_sha256": res.sha256, "pages": res.pages, "engine": res.engine,
                                                             "engine_version": res.engine_version, "misses": misses, "mode": mode})
        if not res.ok:
            console.print(f"[bold red]PDF not produced[/bold red]: {res.message}")
            raise typer.Exit(EXIT_HIGH if "high" in res.message.lower() else EXIT_VERIFY)
        console.print(f"Wrote {res.pdf_path} ({res.pages} pages, engine {res.engine} {res.engine_version})")
        if misses:
            console.print(f"[bold red]Verification failed[/bold red]: {misses} printed value(s) not found in the PDF")
            raise typer.Exit(EXIT_VERIFY)
        console.print("PDF-vs-workbook verification: 0 misses")
    raise typer.Exit(EXIT_OK)


# --------------------------------------------------------------------------- AI review


def _payload_text(req: Any) -> str:
    payload = _get(req, "payload_text", "payload_json", "payload", "body", "content", default=req)
    if hasattr(payload, "model_dump_json"):
        return str(payload.model_dump_json(indent=2))
    if isinstance(payload, (dict, list)):
        return json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    if isinstance(payload, bytes):
        return payload.decode("utf-8")
    return str(payload)


@app.command("review-ai")
def review_ai(
    findings: Annotated[Path, typer.Option("--findings", help="Check results JSON (from `fscheck check --json`).")],
    provider: Annotated[str, typer.Option("--provider", help="none | anthropic | bedrock | vertex | foundry | local | gateway")] = "none",
    use_case: Annotated[str, typer.Option("--use-case", help="triage | narrative | xref | explain")] = "triage",
    model: Annotated[str | None, typer.Option("--model")] = None,
    region: Annotated[str | None, typer.Option("--region", help="Required for external providers.")] = None,
    approve_sha256: Annotated[str | None, typer.Option("--approve-sha256", help="Pre-approve exactly the payload with this SHA-256.")] = None,
    config: ConfigOpt = None,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Show the exact pseudonymised payload, ask for approval, then send it (02 §9)."""
    _setup_logging(verbose)
    with _guard():
        result = _load_result(findings)
        cfg = _config(config)
        cfg.ai.provider = provider  # type: ignore[assignment]
        if model:
            cfg.ai.model = model
        if region:
            cfg.ai.region = region
        mod = _optional_module("fscore.ai")
        prepare = getattr(mod, "prepare_request", None) if mod else None
        send = getattr(mod, "send_request", None) if mod else None
        if not callable(prepare) or not callable(send):
            raise CliError("the AI review module is not available in this build")
        req = _call_flex(prepare, result.findings, findings=result.findings, check_result=result, result=result, config=cfg, ai_config=cfg.ai,
                         provider=provider, use_case=use_case, model=cfg.ai.model, region=cfg.ai.region)
        text = _payload_text(req)
        sha = _get(req, "payload_sha256", "sha256", "payload_hash") or hashlib.sha256(text.encode("utf-8")).hexdigest()
        tokens = _get(req, "token_estimate", "tokens", "estimated_tokens")
        console.rule("Payload preview — this exact text is what would be sent")
        console.print(text, markup=False)
        console.rule()
        console.print(f"Destination: provider [bold]{_get(req, 'provider', default=provider)}[/bold] · model {_get(req, 'model', default=cfg.ai.model)} · region {_get(req, 'region', default=cfg.ai.region) or '(not set)'}")
        console.print(f"Size: {len(text.encode('utf-8')):,} bytes" + (f" · ~{tokens:,} tokens" if isinstance(tokens, int) else "") + f" · SHA-256 {sha}")
        reason = _get(req, "reason", "purpose", default=use_case)
        console.print(f"Reason: {reason}")
        with _store(audit_db, verbose) as store:
            if provider == "none":
                store.record_ai_call(payload=text, provider="none", model=None, region=None, decision="not_sent", retain=cfg.ai.retain_payloads)
                console.print("Provider is 'none': nothing was sent.")
                raise typer.Exit(EXIT_OK)
            if approve_sha256 is not None:
                approved = approve_sha256.strip().lower() == str(sha).lower()
                if not approved:
                    console.print("[bold red]--approve-sha256 does not match this payload; nothing was sent.[/bold red]")
            else:
                answer = typer.prompt("Type APPROVE to send exactly this payload (anything else cancels)", default="", show_default=False)
                approved = answer.strip() == "APPROVE"
            if not approved:
                store.record_ai_call(payload=text, provider=provider, model=cfg.ai.model, region=cfg.ai.region, decision="rejected", retain=cfg.ai.retain_payloads)
                console.print("Not sent.")
                raise typer.Exit(EXIT_OK if approve_sha256 is None else EXIT_INPUT)
            token: Any = sha
            approve_fn = getattr(mod, "approve_request", None)
            if callable(approve_fn):
                token = _call_flex(approve_fn, req, request=req, approver=store.actor, approved_by=store.actor, payload_sha256=sha)
            resp = _call_flex(send, req, request=req, approval=token, approval_token=token, token=token, approved_by=store.actor,
                              approver=store.actor, payload_sha256=sha, config=cfg, ai_config=cfg.ai)
            resp_text = _payload_text(resp)
            stop = _get(resp, "stop_reason")
            store.record_ai_call(payload=text, provider=provider, model=cfg.ai.model, region=cfg.ai.region, decision="sent",
                                 retain=cfg.ai.retain_payloads, approved_by=store.actor, stop_reason=str(stop) if stop else None, response=resp_text)
        console.rule("AI proposals (advisory only — the engine re-checks every cited number)")
        console.print(resp_text, markup=False)
        if stop in ("refusal", "max_tokens"):
            console.print(f"[yellow]The model stopped with stop_reason={stop}; see the audit log.[/yellow]")
    raise typer.Exit(EXIT_OK)


# --------------------------------------------------------------------------- report / rules / diff


@app.command()
def report(
    run: Annotated[str, typer.Argument(help="Check results JSON, an audit run ID, or 'latest'.")],
    html: Annotated[bool, typer.Option("--html/--no-html")] = True,
    register: Annotated[bool, typer.Option("--register/--no-register")] = False,
    out_dir: Annotated[Path | None, typer.Option("--out-dir", "-o")] = None,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Re-create the HTML report / exceptions register for a stored run."""
    _setup_logging(verbose)
    with _guard():
        if run.endswith(".json") or Path(run).exists():
            result = _load_result(Path(run))
            base = Path(run).resolve().parent
        else:
            with _store(audit_db, verbose) as store:
                run_id = store.latest_check_run() if run == "latest" else run
                stored = store.check_result(run_id) if run_id else None
            if stored is None:
                raise CliError(f"no stored check result for {run!r}")
            result = stored
            base = Path.cwd()
        folder = Path(out_dir) if out_dir else base / "fscheck-out"
        folder.mkdir(parents=True, exist_ok=True)
        stem = Path(result.run.file_name).stem or "workbook"
        if html:
            console.print(f"Wrote {api.write_html_report(result, folder / f'{stem} FS-Check.html')}")
        if register:
            console.print(f"Wrote {api.write_register(result, folder / register_filename(stem))}")
        with _store(audit_db, verbose) as store:
            store.append_event("report.generated", object_type="file", object_id=result.run.file_sha256[:16], details={"html": html, "register": register})
    raise typer.Exit(EXIT_OK)


@app.command()
def rules(json_out: Annotated[bool, typer.Option("--json", help="Print the catalogue as JSON.")] = False) -> None:
    """List the rule catalogue (rule-set version and hash)."""
    from fscore.registry import catalogue_json, list_rules, rule_set_hash, rule_set_version

    if json_out:
        console.print(catalogue_json(), markup=False)
        raise typer.Exit(EXIT_OK)
    t = Table(title=f"Rule set {rule_set_version()} ({rule_set_hash()[:12]})")
    for col in ("Rule", "Family", "Severity", "Fix", "Title"):
        t.add_column(col, overflow="fold")
    for r in list_rules():
        t.add_row(r.id, r.family, r.severity.value, r.fix_kind.value, r.title)
    console.print(t)
    raise typer.Exit(EXIT_OK)


@app.command("diff")
def diff_cmd(
    a: Annotated[Path, typer.Argument(help="Earlier version.")],
    b: Annotated[Path, typer.Argument(help="Later version.")],
    config: ConfigOpt = None,
    json_out: Annotated[Path | None, typer.Option("--json", help="Write the diff as JSON to this path.")] = None,
    limit: Annotated[int, typer.Option(help="Rows to show per table.")] = 40,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Cell-level diff and printed figures moved between two versions."""
    _setup_logging(verbose)
    with _guard():
        pa, pb = _existing(a, "workbook"), _existing(b, "workbook")
        cfg = _config(config)
        d = api.diff_workbooks(pa, pb, cfg)
        kinds: dict[str, int] = {}
        for c in d.changes:
            kinds[c.kind] = kinds.get(c.kind, 0) + 1
        console.print(f"Cell changes: {len(d.changes)} " + (f"({', '.join(f'{k} {n}' for k, n in sorted(kinds.items()))})" if kinds else ""))
        if d.sheets_added:
            console.print("Sheets added: " + ", ".join(d.sheets_added))
        if d.sheets_removed:
            console.print("Sheets removed: " + ", ".join(d.sheets_removed))
        if d.changes:
            t = Table(title="Changed cells")
            for col in ("Sheet", "Cell", "Kind", "Before", "After"):
                t.add_column(col, overflow="fold")
            for c in d.changes[:limit]:
                t.add_row(c.sheet, c.cell, c.kind, (c.before or "")[:50], (c.after or "")[:50])
            console.print(t)
        if d.printed_moves:
            t = Table(title=f"Printed figures moved ({len(d.printed_moves)})")
            for col in ("Sheet", "Cell", "Caption", "Period", "Before", "After", "Flags"):
                t.add_column(col, overflow="fold")
            for m in d.printed_moves[:limit]:
                flags = " ".join(x for x, on in (("PY", m.is_py), ("KEY", m.is_key)) if on)
                t.add_row(m.sheet, m.cell, (m.caption or "")[:40], m.period or "", m.before or "", m.after or "", flags)
            console.print(t)
        for h in d.hidden_changes[:limit]:
            console.print(f"• {h}")
        if json_out:
            Path(json_out).write_text(d.model_dump_json(indent=2), encoding="utf-8")
        with _store(audit_db, verbose) as store:
            store.append_event("diff", object_type="file", object_id=d.b_sha256[:16],
                               details={"a_sha256": d.a_sha256, "b_sha256": d.b_sha256, "changes": len(d.changes), "printed_moves": len(d.printed_moves)})
    raise typer.Exit(EXIT_OK)


# --------------------------------------------------------------------------- Module P / TB / synthetic


def _parse_periods(text: str | None) -> dict[str, str] | None:
    if not text:
        return None
    out: dict[str, str] = {}
    for part in text.split(","):
        if "=" not in part:
            raise CliError("--periods must look like CY=Mar-26,PY=Mar-25")
        k, v = part.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _load_mapping(path: Path) -> Any:
    from fscore.prepare.mapping import MappingVersion

    p = _existing(path, "mapping file")
    data = yaml.safe_load(p.read_text(encoding="utf-8")) if p.suffix.lower() in (".yaml", ".yml") else json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, dict) and "rows" in data:
        return MappingVersion.model_validate(data)
    if isinstance(data, dict):
        return MappingVersion(rows={str(k): str(v) for k, v in data.items()})
    raise CliError(f"{p.name}: expected a mapping {{account: key}} or a mapping version")


@app.command("validate-tb")
def validate_tb_cmd(
    tb: Annotated[Path, typer.Argument(help="Trial balance (.csv or .xlsx).")],
    sheet: Annotated[str | None, typer.Option(help="TB sheet name (xlsx).")] = None,
    periods: Annotated[str | None, typer.Option(help="Period columns, e.g. CY=Mar-26,PY=Mar-25.")] = None,
    mapping: Annotated[Path | None, typer.Option(help="Mapping file (YAML/JSON).")] = None,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """TB validation gate (E-DATA-01): nets to zero, mapped, signs, periods, FX."""
    _setup_logging(verbose)
    with _guard():
        from fscore.prepare.tb import load_tb, validate_tb

        p = _existing(tb, "trial balance")
        tbal = load_tb(p, sheet=sheet, periods=_parse_periods(periods))
        if mapping:
            tbal.apply_mapping(_load_mapping(mapping))
        res = validate_tb(tbal)
        console.print(f"Accounts: {len(tbal.accounts)} · periods: {', '.join(tbal.period_labels.get(k, k) for k in tbal.periods)}")
        for k, v in res.totals.items():
            console.print(f"  Net total {tbal.period_labels.get(k, k)}: {v}")
        for issue in res.issues:
            colour = "red" if issue.severity == "block" else "yellow"
            console.print(f"[{colour}]{issue.code}[/] {issue.message}" + (f" ({', '.join(issue.accounts[:10])})" if issue.accounts else ""))
        console.print("Gate: " + ("[bold green]passed[/]" if res.ok else "[bold red]blocked[/]"))
        with _store(audit_db, verbose) as store:
            store.append_event("tb.validated", object_type="file", object_id=_sha256(p)[:16],
                               details={"ok": res.ok, "issues": [i.code for i in res.issues], "accounts": len(tbal.accounts)})
    raise typer.Exit(EXIT_OK if res.ok else EXIT_HIGH)


@app.command()
def prepare(
    tb: Annotated[Path, typer.Option("--tb", help="Trial balance (.csv/.xlsx).")],
    mapping: Annotated[Path, typer.Option("--mapping", help="Mapping file.")],
    out: Annotated[Path, typer.Option("--out", help="Output workbook path.")],
    entity: Annotated[str | None, typer.Option(help="Entity name.")] = None,
    config: ConfigOpt = None,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Module P: prepare the statements from a TB and mapping."""
    _setup_logging(verbose)
    with _guard():
        tb_path, map_path = _existing(tb, "trial balance"), _existing(mapping, "mapping file")
        cfg = _config(config)
        mod = _optional_module("fscore.prepare.api")
        fn = getattr(mod, "prepare", None) if mod else None
        if not callable(fn):
            raise CliError("Module P (fscore.prepare.api.prepare) is not available in this build")
        res = _call_flex(fn, tb_path, tb_path=tb_path, tb=tb_path, mapping_path=map_path, mapping=map_path, out_path=out, out=out,
                         config=cfg, entity=entity, entity_name=entity)
        path = _get(res, "out_path", "path", "workbook", default=res if isinstance(res, (str, Path)) else out)
        console.print(f"Wrote {path}")
        highs = _get(res, "high_count", default=None)
        with _store(audit_db, verbose) as store:
            store.append_event("prepare", object_type="file", object_id=_sha256(tb_path)[:16],
                               details={"tb_sha256": _sha256(tb_path), "mapping_sha256": _sha256(map_path), "out": Path(str(path)).name})
    raise typer.Exit(EXIT_HIGH if isinstance(highs, int) and highs > 0 else EXIT_OK)


@app.command()
def synth(
    out: Annotated[Path, typer.Argument(help="Synthetic workbook to write.")],
    inject: Annotated[str | None, typer.Option("--inject", help="Comma-separated error classes to inject.")] = None,
    exact: Annotated[bool, typer.Option("--exact", help="Rupee-exact amounts (natural rounding differences).")] = False,
    seed: Annotated[int, typer.Option(help="Random seed.")] = 7,
    audit_db: AuditDb = None,
    verbose: Verbose = False,
) -> None:
    """Generate a synthetic Ind AS workbook (tests and demos; invented data only)."""
    _setup_logging(verbose)
    with _guard():
        mod = _optional_module("fscore.synthetic.generator")
        fn = getattr(mod, "make_workbook", None) if mod else None
        if not callable(fn):
            raise CliError("the synthetic generator (fscore.synthetic.generator.make_workbook) is not available in this build")
        injections = [x.strip() for x in (inject or "").split(",") if x.strip()]
        res = _call_flex(fn, out, out_path=out, path=out, out=out, inject=injections, injections=injections, exact=exact, seed=seed)
        path = _get(res, "path", "out_path", default=res if isinstance(res, (str, Path)) else out)
        console.print(f"Wrote {path}")
        expected = _get(res, "expected", "expected_findings", default=None)
        if isinstance(expected, list):
            console.print(f"Injected errors: {len(expected)} expected finding(s)")
        with _store(audit_db, verbose) as store:
            store.append_event("synthetic.generated", object_type="file", object_id=Path(str(path)).name,
                               details={"inject": injections, "exact": exact, "seed": seed})
    raise typer.Exit(EXIT_OK)


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())  # type: ignore[func-returns-value]

__all__ = ["app", "canonical_json", "detect_signatories", "detect_statements", "detect_units", "detect_year_end", "main"]
