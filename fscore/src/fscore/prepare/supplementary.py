"""Supplementary schedules for Module P as data (YAML / JSON ⇄ ``YearData``).

The trial balance gives balances; notes such as the fixed-asset roll-forward,
leases, gratuity (actuarial), share capital, tax and ageing need movement
data that is not in a TB. The firm supplies it per year in this format
(``fscheck prepare --supplementary schedules.yaml``, or the platform's
"prepare" run parameter ``supplementary``). ``template()`` writes an empty,
fully-keyed file to fill in; amounts are in rupees.
"""

from __future__ import annotations

import dataclasses
import json
import types
import typing
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import yaml

from fscore.prepare.inputs import EntityInfo, Roll, YearData

FORMAT_VERSION = "supplementary/1"


def _encode(v: Any) -> Any:
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, date):
        return v.isoformat()
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return {f.name: _encode(getattr(v, f.name)) for f in dataclasses.fields(v)}
    if isinstance(v, dict):
        return {str(k): _encode(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_encode(x) for x in v]
    return v


def _decode_scalar(v: Any) -> Any:
    """Numbers become Decimal, ISO dates become date; everything else as is."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int, float)):
        return Decimal(str(v))
    if isinstance(v, str):
        s = v.strip()
        try:
            if len(s) == 10 and s[4] == "-" and s[7] == "-":
                return date.fromisoformat(s)
        except ValueError:
            pass
        try:
            return Decimal(s.replace(",", ""))
        except ArithmeticError:
            return v
    return v


def _decode(tp: Any, v: Any) -> Any:
    if v is None:
        return None
    args = get_args(tp)
    if get_origin(tp) in (types.UnionType, typing.Union):  # X | None
        inner = [a for a in args if a is not type(None)]
        return _decode(inner[0], v) if len(inner) == 1 else _decode_scalar(v)
    if dataclasses.is_dataclass(tp) and isinstance(v, dict):
        hints = get_type_hints(tp)
        kw = {f.name: _decode(hints.get(f.name, Any), v[f.name]) for f in dataclasses.fields(tp) if f.name in v}
        return typing.cast(Any, tp)(**kw)
    if tp is date and isinstance(v, str):
        return date.fromisoformat(v)
    if tp is str:
        return str(v)
    if tp is bool:
        return bool(v)
    origin = get_origin(tp)
    if origin in (list, tuple) and isinstance(v, (list, tuple)):
        et = args[0] if args else Any
        items = [_decode(et, x) for x in v]
        return tuple(items) if origin is tuple else items
    if origin is dict and isinstance(v, dict):
        _kt, vt = get_args(tp) or (str, Any)
        return {str(k): _decode(vt, x) for k, x in v.items()}
    if tp is Decimal:
        return Decimal(str(v)) if v not in (None, "") else Decimal(0)
    if isinstance(v, dict):
        return {str(k): _decode_scalar(x) if not isinstance(x, dict) else _decode(Any, x) for k, x in v.items()}
    return _decode_scalar(v)


def dump(years: dict[str, YearData], *, opening: dict[str, Decimal] | None = None, entity: EntityInfo | None = None) -> dict[str, Any]:
    """The inputs bundle: entity master, prior-year closing balances (account → rupees) and per-year schedules."""
    out: dict[str, Any] = {"format": FORMAT_VERSION}
    if entity is not None:
        out["entity"] = _encode(entity)
    if opening:
        out["opening"] = {str(k): _encode(v) for k, v in opening.items()}
    out["years"] = {k: _encode(y) for k, y in years.items()}
    return out


def _read(source: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    if isinstance(source, (str, Path)) and Path(source).exists():
        text = Path(source).read_text(encoding="utf-8")
        data = json.loads(text) if str(source).lower().endswith(".json") else yaml.safe_load(text)
        return data if isinstance(data, dict) else {}
    raise ValueError("supplementary schedules must be a YAML/JSON file or a mapping")


def load(source: str | Path | dict[str, Any]) -> dict[str, YearData]:
    """Per-year schedules from a YAML/JSON file or an already-parsed mapping."""
    data = _read(source)
    years = data.get("years", data)
    return {str(k): _decode(YearData, v or {}) for k, v in years.items() if k not in ("format", "entity", "opening")}


def load_bundle(source: str | Path | dict[str, Any]) -> tuple[dict[str, YearData], dict[str, Decimal], EntityInfo | None]:
    """(years, opening balances, entity master or None)."""
    data = _read(source)
    opening = {str(k): Decimal(str(v)) for k, v in (data.get("opening") or {}).items()}
    entity = _decode(EntityInfo, data["entity"]) if isinstance(data.get("entity"), dict) else None
    return load(data), opening, entity


def template(classes: tuple[str, ...] = ("computers", "furniture", "office_equipment")) -> dict[str, Any]:
    """An empty schedule set with every key present (amounts in rupees)."""
    blank = YearData(far={c: Roll() for c in classes})
    return dump({"CY": blank, "PY": YearData(far={c: Roll() for c in classes})})


def write_template(path: str | Path) -> Path:
    p = Path(path)
    p.write_text(yaml.safe_dump(template(), sort_keys=False, allow_unicode=True), encoding="utf-8")
    return p
