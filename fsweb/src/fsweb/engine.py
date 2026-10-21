"""The only bridge between the web platform and the engine (decision D3).

``RealEngine`` delegates to ``fscore.api`` (and, when present, ``fscore.registry``
and ``fscore.ai``). ``FakeEngine`` is a deterministic stand-in used by unit tests so
the platform's controls can be tested independently of the engine's rule logic.
Select with ``FSWEB_ENGINE=fake|real`` (default ``real``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol

from fscore.config import AIConfig, EngagementConfig
from fscore.models import (
    ApplyResult,
    BalancingAdjustment,
    CellRef,
    Certainty,
    CheckResult,
    Edit,
    EditGroup,
    EditKind,
    Evidence,
    Finding,
    FixKind,
    FixPlan,
    KeyFigure,
    PdfReconResult,
    PrintedMove,
    RenderResult,
    RunInfo,
    Severity,
    TextEdit,
    VerificationReport,
    VerificationStep,
    WorkbookDiff,
)

ProgressFn = Callable[[str, float], None]


class EngineUnavailable(RuntimeError):
    """The engine (or one of its functions) is not installed / implemented yet."""


class Engine(Protocol):
    name: str

    def engine_version(self) -> str: ...

    def rule_set(self) -> tuple[str, str]: ...

    def list_rules(self) -> list[dict[str, Any]]: ...

    def check_workbook(self, path: Path, config: EngagementConfig, *, pdf_path: Path | None = None, progress: ProgressFn | None = None,
                       previous_sheet_states: dict[str, dict[str, Any]] | None = None) -> CheckResult: ...

    def check_pdf_only(self, pdf_path: Path, config: EngagementConfig) -> CheckResult: ...

    def propose_fixes(self, path: Path, config: EngagementConfig, *, check_result: CheckResult | None = None, progress: ProgressFn | None = None) -> FixPlan: ...

    def apply_fixes(self, path: Path, plan: FixPlan, out_path: Path, *, approved_edit_ids: set[str], config: EngagementConfig) -> ApplyResult: ...

    def apply_text_edits(self, path: Path, edits: list[TextEdit], out_path: Path, config: EngagementConfig) -> ApplyResult: ...

    def render_pdf(self, path: Path, out_path: Path, *, mode: str, config: EngagementConfig, run_id: str, check_result: CheckResult | None = None, engine: str = "auto") -> RenderResult: ...

    def reconcile_pdf(self, path: Path, pdf_path: Path, config: EngagementConfig) -> PdfReconResult: ...

    def diff_workbooks(self, a: Path, b: Path, config: EngagementConfig) -> WorkbookDiff: ...

    def write_register(self, result: CheckResult, out_path: Path, *, plan: FixPlan | None = None, applied: ApplyResult | None = None) -> Path: ...

    def write_html_report(self, result: CheckResult, out_path: Path, *, plan: FixPlan | None = None) -> Path: ...

    def watermark_pdf(self, src: Path, out_path: Path, text: str) -> Path: ...

    def ai_build_payload(self, kind: str, findings: list[dict[str, Any]], context: dict[str, Any], config: AIConfig) -> dict[str, Any]: ...

    def ai_send(self, payload: dict[str, Any], config: AIConfig, *, provider: str, mapping: dict[str, Any] | None = None,
                requester: str | None = None, approver: str | None = None) -> dict[str, Any]: ...


# ============================================================ shared fallbacks


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fallback_watermark(src: Path, out_path: Path, text: str) -> Path:
    """Diagonal watermark on every page (E-SEC-10) using pypdf + reportlab."""
    import io

    from pypdf import PdfReader, PdfWriter
    from reportlab.lib.colors import Color
    from reportlab.pdfgen import canvas

    reader = PdfReader(str(src))
    writer = PdfWriter()
    for page in reader.pages:
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        buf = io.BytesIO()
        c = canvas.Canvas(buf, pagesize=(w, h))
        c.setFillColor(Color(0.75, 0.1, 0.1, alpha=0.18))
        c.setFont("Helvetica-Bold", max(18, min(w, h) / 18))
        c.saveState()
        c.translate(w / 2, h / 2)
        c.rotate(35)
        for i, line in enumerate(text.splitlines() or [text]):
            c.drawCentredString(0, -i * (min(w, h) / 14), line)
        c.restoreState()
        c.setFont("Helvetica", 7)
        c.setFillColor(Color(0.3, 0.3, 0.3, alpha=0.8))
        c.drawString(18, 10, text.replace("\n", "  |  ")[:180])
        c.save()
        buf.seek(0)
        writer.add_page(page).merge_page(PdfReader(buf).pages[0])  # merge on the writer's copy (pypdf 7 refuses reader pages)
    writer.add_metadata({"/FSPlatformWatermark": text.replace("\n", " | ")[:200]})
    with open(out_path, "wb") as fh:
        writer.write(fh)
    return out_path


_DIN = re.compile(r"\b\d{8}\b")
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_CIN = re.compile(r"\b[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}\b")
_GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]\b")
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")


def fallback_pseudonymise(obj: Any, names: dict[str, str]) -> tuple[Any, dict[str, str]]:
    """Replace known names and identifier patterns with stable tokens (02 §9.1)."""
    mapping: dict[str, str] = {}
    counters: dict[str, int] = {}

    def token(kind: str, value: str) -> str:
        for t, v in mapping.items():
            if v == value:
                return t
        counters[kind] = counters.get(kind, 0) + 1
        t = f"{kind}_{counters[kind]}"
        mapping[t] = value
        return t

    ordered = sorted(names.items(), key=lambda kv: -len(kv[0]))

    def scrub(s: str) -> str:
        for value, kind in ordered:
            if value and value in s:
                s = s.replace(value, token(kind, value))
        for kind, rx in (("CIN", _CIN), ("GSTIN", _GSTIN), ("PAN", _PAN), ("EMAIL", _EMAIL), ("DIN", _DIN)):
            s = rx.sub(lambda m, k=kind: token(k, m.group(0)), s)
        return s

    def walk(o: Any) -> Any:
        if isinstance(o, str):
            return scrub(o)
        if isinstance(o, list):
            return [walk(x) for x in o]
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items()}
        return o

    return walk(obj), mapping


def fallback_payload(kind: str, findings: list[dict[str, Any]], context: dict[str, Any], config: AIConfig) -> dict[str, Any]:
    keep = ("fid", "rule_id", "severity", "certainty", "sheet", "cells", "caption", "printed", "expected", "diff", "explanation", "suggested_fix")
    minimal = [{k: f.get(k) for k in keep if f.get(k) is not None} for f in findings]
    body = {
        "task": kind,
        "instructions_version": "triage-v1",
        "units": context.get("units", "lakhs"),
        "rules": "Return proposals only. Never invent numbers. Cite the sheet and cell for every claim.",
        "findings": minimal,
        "metrics": context.get("metrics", {}),
    }
    names = dict(context.get("names") or {})
    redacted, mapping = fallback_pseudonymise(body, names) if config.redact else (body, {})
    return {"payload": redacted, "mapping": mapping}


# ============================================================ real engine


class RealEngine:
    name = "fscore"

    def _api(self) -> Any:
        try:
            import fscore.api as api
        except ImportError as exc:  # pragma: no cover
            raise EngineUnavailable(str(exc)) from exc
        return api

    def _call(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except (ImportError, NotImplementedError) as exc:
            raise EngineUnavailable(f"{getattr(fn, '__name__', fn)}: {exc}") from exc

    def engine_version(self) -> str:
        import fscore

        return str(getattr(fscore, "ENGINE_VERSION", getattr(fscore, "__version__", "unknown")))

    def rule_set(self) -> tuple[str, str]:
        try:
            from fscore import registry

            return str(registry.rule_set_version()), str(registry.rule_set_hash())
        except (ImportError, AttributeError):
            return "unregistered", ""

    def list_rules(self) -> list[dict[str, Any]]:
        try:
            from fscore import registry

            out: list[dict[str, Any]] = []
            for r in registry.list_rules():
                if hasattr(r, "as_dict"):
                    out.append(json.loads(json.dumps(r.as_dict(), default=str)))
                elif hasattr(r, "model_dump"):
                    out.append(r.model_dump(mode="json"))
                else:
                    import dataclasses

                    out.append(json.loads(json.dumps(dataclasses.asdict(r), default=str)))
            return out
        except (ImportError, AttributeError):
            return []

    def check_workbook(self, path: Path, config: EngagementConfig, *, pdf_path: Path | None = None, progress: ProgressFn | None = None,
                       previous_sheet_states: dict[str, dict[str, Any]] | None = None) -> CheckResult:
        if previous_sheet_states:
            # ENG-02 (sheets the user unhid since the last run) needs the previous run's sheet states;
            # the facade does not take them yet, so call the runner directly when it supports them.
            try:
                import inspect

                from fscore.runner import run_checks

                if "previous_sheet_states" in inspect.signature(run_checks).parameters:
                    return self._call(run_checks, Path(path), config, pdf_path=pdf_path, progress=progress,  # type: ignore[no-any-return]
                                      previous_sheet_states=previous_sheet_states)
            except ImportError:
                pass
        return self._call(self._api().check_workbook, path, config, pdf_path=pdf_path, progress=progress)  # type: ignore[no-any-return]

    def check_pdf_only(self, pdf_path: Path, config: EngagementConfig) -> CheckResult:
        """PDF-04: casting / cross-reference checks on a PDF alone (lower certainty, never workbook fixes)."""
        api = self._api()
        fn = getattr(api, "check_pdf_only", None)
        if fn is None:
            raise EngineUnavailable("fscore.api.check_pdf_only is not available")
        out = self._call(fn, pdf_path, config)
        if isinstance(out, CheckResult):
            return out
        findings, recon = out
        for i, f in enumerate(findings, 1):
            f.id = f.id or f"F-{i:04d}"
            f.fingerprint = f.fingerprint or f.compute_fingerprint()
        version, digest = self.rule_set()
        now = datetime.now(UTC)
        info = RunInfo(engine_version=self.engine_version(), rule_set_version=version, rule_set_hash=digest, file_name=Path(pdf_path).name,
                       file_sha256=sha256_file(Path(pdf_path)), config_hash=config.config_hash(), started_at=now, finished_at=now)
        return CheckResult(run=info, findings=list(findings), pdf=recon)

    def propose_fixes(self, path: Path, config: EngagementConfig, *, check_result: CheckResult | None = None, progress: ProgressFn | None = None) -> FixPlan:
        return self._call(self._api().propose_fixes, path, config, check_result=check_result, progress=progress)  # type: ignore[no-any-return]

    def apply_fixes(self, path: Path, plan: FixPlan, out_path: Path, *, approved_edit_ids: set[str], config: EngagementConfig) -> ApplyResult:
        return self._call(self._api().apply_fixes, path, plan, out_path, approved_edit_ids=approved_edit_ids, config=config)  # type: ignore[no-any-return]

    def apply_text_edits(self, path: Path, edits: list[TextEdit], out_path: Path, config: EngagementConfig) -> ApplyResult:
        return self._call(self._api().apply_text_edits, path, edits, out_path, config)  # type: ignore[no-any-return]

    def render_pdf(self, path: Path, out_path: Path, *, mode: str, config: EngagementConfig, run_id: str, check_result: CheckResult | None = None, engine: str = "auto") -> RenderResult:
        return self._call(  # type: ignore[no-any-return]
            self._api().render_pdf, path, out_path, mode=mode, engine=engine, config=config, run_id=run_id, check_result=check_result
        )

    def reconcile_pdf(self, path: Path, pdf_path: Path, config: EngagementConfig) -> PdfReconResult:
        return self._call(self._api().reconcile_pdf, path, pdf_path, config)  # type: ignore[no-any-return]

    def diff_workbooks(self, a: Path, b: Path, config: EngagementConfig) -> WorkbookDiff:
        return self._call(self._api().diff_workbooks, a, b, config)  # type: ignore[no-any-return]

    def write_register(self, result: CheckResult, out_path: Path, *, plan: FixPlan | None = None, applied: ApplyResult | None = None) -> Path:
        return Path(self._call(self._api().write_register, result, out_path, plan=plan, applied=applied))

    def write_html_report(self, result: CheckResult, out_path: Path, *, plan: FixPlan | None = None) -> Path:
        return Path(self._call(self._api().write_html_report, result, out_path, plan=plan))

    def watermark_pdf(self, src: Path, out_path: Path, text: str) -> Path:
        try:
            return Path(self._api().watermark_pdf(src, out_path, text))
        except (ImportError, NotImplementedError, AttributeError):
            return fallback_watermark(src, out_path, text)

    def ai_build_payload(self, kind: str, findings: list[dict[str, Any]], context: dict[str, Any], config: AIConfig) -> dict[str, Any]:
        try:
            from fscore import ai as fai

            builder = fai.build_payload
        except (ImportError, AttributeError):
            return fallback_payload(kind, findings, context, config)
        result = builder(kind=kind, findings=findings, context=context, config=config)
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        return dict(result)

    def ai_send(self, payload: dict[str, Any], config: AIConfig, *, provider: str, mapping: dict[str, Any] | None = None,
                requester: str | None = None, approver: str | None = None) -> dict[str, Any]:
        if provider == "mock" and not (mapping and "preview" in mapping):
            return MockAIProvider().send(payload, config)
        try:
            from fscore import ai as fai

            sender = fai.send_payload
        except (ImportError, AttributeError) as exc:
            raise EngineUnavailable("fscore.ai.send_payload is not available") from exc
        result = sender(payload=payload, config=config.model_copy(update={"provider": provider}), mapping=mapping,
                        requester=requester, approver=approver)
        if hasattr(result, "model_dump"):
            result = result.model_dump(mode="json")
        return dict(result)


class MockAIProvider:
    """Offline provider for tests and demos: structured, proposal-only output."""

    def send(self, payload: dict[str, Any], config: AIConfig) -> dict[str, Any]:
        body = payload.get("payload", payload)
        items = []
        for f in body.get("findings", []):
            items.append(
                {
                    "finding_id": f.get("fid"),
                    "classification": "genuine_error" if f.get("severity") == "High" else "presentation_only",
                    "reason": f"Mock triage of {f.get('rule_id')}",
                    "suggested_wording": None,
                }
            )
        return {
            "stop_reason": "end_turn",
            "model": config.model,
            "output": {"items": items},
            "usage": {"input_tokens": len(json.dumps(body)) // 4, "output_tokens": 40 * max(1, len(items))},
        }


# ============================================================ fake engine

FAKE_FIXED = b"#FSWEB-FAKE-FIXED"
FAKE_TEXT = b"#FSWEB-FAKE-TEXT"


class FakeEngine(RealEngine):
    """Deterministic engine double. A workbook is 'clean' once it carries the fixed marker."""

    name = "fake"

    def engine_version(self) -> str:
        return "fake-1.0"

    def rule_set(self) -> tuple[str, str]:
        return "fake-rules-1", "f" * 64

    def list_rules(self) -> list[dict[str, Any]]:
        return [
            {"id": "CAST-01", "family": "CAST", "severity": "High", "version": "1", "description": "Vertical casting"},
            {"id": "TXT-01", "family": "TXT", "severity": "High", "version": "1", "description": "Signatories"},
        ]

    def check_pdf_only(self, pdf_path: Path, config: EngagementConfig) -> CheckResult:
        now = datetime.now(UTC)
        f = Finding(id="F-0001", rule_id="PDF-04", family="PDF", severity=Severity.MEDIUM, certainty=Certainty.REVIEW, sheet=None,
                    caption="Total current liabilities", printed="500.00", expected="500.01", explanation="PDF-only casting difference", fix_kind=FixKind.MANUAL)
        f.fingerprint = f.compute_fingerprint()
        info = RunInfo(engine_version=self.engine_version(), rule_set_version="fake-rules-1", rule_set_hash="f" * 64, file_name=Path(pdf_path).name,
                       file_sha256=sha256_file(Path(pdf_path)), config_hash=config.config_hash(), started_at=now, finished_at=now)
        return CheckResult(run=info, findings=[f], pdf=PdfReconResult(pdf_sha256=sha256_file(Path(pdf_path)), pages=1))

    def check_workbook(self, path: Path, config: EngagementConfig, *, pdf_path: Path | None = None, progress: ProgressFn | None = None,
                       previous_sheet_states: dict[str, dict[str, Any]] | None = None) -> CheckResult:
        data = Path(path).read_bytes()
        fixed = FAKE_FIXED in data
        now = datetime.now(UTC)
        if progress:
            progress("reading", 0.3)
            progress("checking", 0.8)
        findings: list[Finding] = []
        if not fixed:
            findings = [
                Finding(rule_id="CAST-01", family="CAST", severity=Severity.HIGH, sheet="BS", cells=["D20"], caption="Total current liabilities",
                        printed="500.00", expected="500.01", difference=Decimal("0.01"), explanation="Subtotal does not cast on print",
                        fix_kind=FixKind.AUTO, evidence=[Evidence(label="D18", ref=CellRef(sheet="BS", addr="D18"), printed="250.01")]),
                Finding(rule_id="PLUG-01", family="PLUG", severity=Severity.HIGH, sheet="CF", cells=["E40"], caption="Net cash flow",
                        printed="12.00", explanation="Typed plug -0.01 inside total", fix_kind=FixKind.AUTO),
                Finding(rule_id="TXT-01", family="TXT", severity=Severity.HIGH, sheet="BS", cells=["C60"], caption="DIN of Director B",
                        printed="DIN: 01234567", expected="DIN: 07654321", explanation="Two different directors share one DIN",
                        fix_kind=FixKind.APPROVAL),
                Finding(rule_id="GC-01", family="GC", severity=Severity.MEDIUM, certainty=Certainty.REVIEW, sheet="Notes", cells=["B12"],
                        caption="Going concern", printed="9.32", expected="8.23", explanation="Quoted loss differs from P&L", fix_kind=FixKind.MANUAL),
            ]
        else:
            findings = [Finding(rule_id="TXT-07", family="TXT", severity=Severity.LOW, sheet="Notes", cells=["C5"], explanation="Mixed decimals", fix_kind=FixKind.MANUAL)]
        for i, f in enumerate(findings, 1):
            f.id = f"F-{i:04d}"
            f.fingerprint = f.compute_fingerprint()
        run = RunInfo(
            engine_version=self.engine_version(), rule_set_version="fake-rules-1", rule_set_hash="f" * 64,
            file_name=Path(path).name, file_sha256=hashlib.sha256(data).hexdigest(), config_hash=config.config_hash(),
            started_at=now, finished_at=now, print_sheets=["BS", "PL", "CF", "Notes"],
        )
        kf = [KeyFigure(key="pat", label="Profit for the year", period="CY", value=Decimal("8.23"), printed="8.23", ref=CellRef(sheet="PL", addr="D30"))]
        proposals = [] if fixed else [TextEdit(sheet="Notes", cell="B12", new_text="The Company incurred a loss of ₹ 8.23 lakhs for the year ended March 31, 2026.",
                                                reason="GC-06: quoted figure corrected to the statements")]
        return CheckResult(run=run, findings=findings, key_figures=kf, statements={"balance_sheet": "BS", "profit_loss": "PL"},
                           rpt_recon=[{"counterparty": "PARTY_1", "nature": "payable", "period": "FY CY", "rpt": "1.00", "tb": "1.00", "fs": "1.00", "status": "ok"}],
                           text_proposals=proposals, sheet_states={"BS": {"state": "visible"}, "Notes": {"state": "visible"}})

    def propose_fixes(self, path: Path, config: EngagementConfig, *, check_result: CheckResult | None = None, progress: ProgressFn | None = None) -> FixPlan:
        sha = sha256_file(path)
        edits = [
            Edit(id="E-0001", sheet="BS", cell="D20", kind=EditKind.FORMULA, group=EditGroup.TOTALS_REWRITE, old_formula="SUM(D15:D19)",
                 new_formula="SUMPRODUCT(ROUND(D15:D19,2))", reason="Total does not cast on displayed values", finding_ids=["F-0001"], fix_kind=FixKind.AUTO),
            Edit(id="E-0002", sheet="CF", cell="E40", kind=EditKind.FORMULA, group=EditGroup.PLUG_REMOVAL, old_formula="SUM(E10:E39)-0.01",
                 new_formula="SUMPRODUCT(ROUND(E10:E39,2))", reason="Remove typed plug", finding_ids=["F-0002"], fix_kind=FixKind.AUTO),
            Edit(id="E-0003", sheet="BS", cell="C60", kind=EditKind.TEXT, group=EditGroup.LINK, old_value="DIN: 01234567",
                 new_value="DIN: 07654321", reason="Link DIN to signatory master", finding_ids=["F-0003"], fix_kind=FixKind.APPROVAL),
            Edit(id="E-0004", sheet="Notes", cell="D44", kind=EditKind.FORMULA, group=EditGroup.BALANCING, old_formula="SUM(D40:D43)",
                 new_formula="SUM(D40:D43)+0.02", reason="Balance Sheet rounding gap to Other expenses", fix_kind=FixKind.AUTO),
        ]
        bal = [BalancingAdjustment(statement="balance_sheet", column="D", period="CY", gap=Decimal("0.02"),
                                   target=CellRef(sheet="Notes", addr="D44"), adjustment=Decimal("0.02"), status="applied")]
        moves = [PrintedMove(sheet="BS", cell="D20", caption="Total current liabilities", before="500.00", after="500.01", period="CY", is_key=False)]
        return FixPlan(id="P-fake", source_file=Path(path).name, source_sha256=sha, config_hash=config.config_hash(), edits=edits,
                       balancing=bal, printed_moves=moves, converged=True, passes=2, created_at=datetime.now(UTC))

    def apply_fixes(self, path: Path, plan: FixPlan, out_path: Path, *, approved_edit_ids: set[str], config: EngagementConfig) -> ApplyResult:
        data = Path(path).read_bytes()
        out_path.write_bytes(data + b"\n" + FAKE_FIXED)
        applied = [e.id for e in plan.edits if e.fix_kind == FixKind.AUTO or e.id in approved_edit_ids]
        skipped = [e.id for e in plan.edits if e.id not in applied]
        ver = VerificationReport(ok=True, steps=[VerificationStep(name="cell diff", ok=True), VerificationStep(name="xml validation", ok=True),
                                                 VerificationStep(name="casting re-run", ok=True)], selftest_match_rate=1.0)
        return ApplyResult(ok=True, out_path=str(out_path), sha256=sha256_file(out_path), applied_edit_ids=applied,
                           skipped_edit_ids=skipped, verification=ver, printed_moves=plan.printed_moves, message="applied (fake)")

    def apply_text_edits(self, path: Path, edits: list[TextEdit], out_path: Path, config: EngagementConfig) -> ApplyResult:
        for e in edits:
            if e.new_text.lstrip().startswith("="):
                return ApplyResult(ok=False, message="formulas are not allowed in narrative cells (C6)")
        data = Path(path).read_bytes()
        out_path.write_bytes(data + b"\n" + FAKE_TEXT)
        return ApplyResult(ok=True, out_path=str(out_path), sha256=sha256_file(out_path), applied_edit_ids=[f"{e.sheet}!{e.cell}" for e in edits],
                           verification=VerificationReport(ok=True, steps=[VerificationStep(name="typed text", ok=True)]))

    def render_pdf(self, path: Path, out_path: Path, *, mode: str, config: EngagementConfig, run_id: str, check_result: CheckResult | None = None, engine: str = "auto") -> RenderResult:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        c = canvas.Canvas(str(out_path), pagesize=A4)
        c.setFont("Helvetica", 12)
        c.drawString(72, 760, f"Synthetic statements ({mode.upper()}) run {run_id[:8]}")
        c.drawString(72, 740, f"Workbook sha256 {sha256_file(path)[:16]}")
        c.showPage()
        c.save()
        recon = PdfReconResult(pdf_sha256=sha256_file(out_path), pages=1, values_checked=12, misses=[])
        return RenderResult(ok=True, pdf_path=str(out_path), sha256=sha256_file(out_path), engine="fake", engine_version="1.0", mode="final" if mode == "final" else "draft",
                            pages=1, bookmarks=["Balance Sheet"], verification=recon)

    def reconcile_pdf(self, path: Path, pdf_path: Path, config: EngagementConfig) -> PdfReconResult:
        return PdfReconResult(pdf_sha256=sha256_file(pdf_path), pages=1, values_checked=12, misses=[])

    def diff_workbooks(self, a: Path, b: Path, config: EngagementConfig) -> WorkbookDiff:
        sa, sb = sha256_file(a), sha256_file(b)
        moves = [] if sa == sb else [PrintedMove(sheet="BS", cell="D20", caption="Total current liabilities", before="500.00", after="500.01")]
        return WorkbookDiff(a_sha256=sa, b_sha256=sb, printed_moves=moves)

    def write_register(self, result: CheckResult, out_path: Path, *, plan: FixPlan | None = None, applied: ApplyResult | None = None) -> Path:
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        assert ws is not None
        ws.title = "Exceptions"
        ws.append(["#", "Priority", "Rule", "Sheet", "Cells", "Issue"])
        for f in result.findings:
            ws.append([f.id, f.severity.value, f.rule_id, f.sheet, ",".join(f.cells), f.explanation])
        wb.save(out_path)
        return out_path

    def write_html_report(self, result: CheckResult, out_path: Path, *, plan: FixPlan | None = None) -> Path:
        rows = "".join(f"<tr><td>{f.id}</td><td>{f.rule_id}</td><td>{f.severity.value}</td></tr>" for f in result.findings)
        out_path.write_text(f"<html><body><h1>FS-Check report</h1><table>{rows}</table></body></html>", encoding="utf-8")
        return out_path

    def watermark_pdf(self, src: Path, out_path: Path, text: str) -> Path:
        return fallback_watermark(src, out_path, text)

    def ai_build_payload(self, kind: str, findings: list[dict[str, Any]], context: dict[str, Any], config: AIConfig) -> dict[str, Any]:
        return fallback_payload(kind, findings, context, config)

    def ai_send(self, payload: dict[str, Any], config: AIConfig, *, provider: str, mapping: dict[str, Any] | None = None,
                requester: str | None = None, approver: str | None = None) -> dict[str, Any]:
        return MockAIProvider().send(payload, config)


def make_engine(kind: str | None = None) -> Engine:
    kind = (kind or os.environ.get("FSWEB_ENGINE") or "real").lower()
    if kind == "fake":
        return FakeEngine()
    return RealEngine()


def copy_file(src: Path, dst: Path) -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst
