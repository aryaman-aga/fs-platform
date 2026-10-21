"""Recalculation of a set of formula cells in dependency order.

Used by (a) the fix engine's fixed-point iteration (incremental updates),
(b) the workbook builder, which must write Excel-compatible cached values,
and (c) verification. Cells outside the formula set keep their given (cached)
values: heavy backup sheets are never recalculated (02 §3.9).
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from typing import Any

from fscore.engine import formula as F
from fscore.engine.evaluator import Evaluator, RangeVal, Unsupported
from fscore.engine.formula import ExcelError
from fscore.engine.refs import MAX_COL, MAX_ROW

Key = tuple[str, int, int]


class Recalculator:
    def __init__(
        self,
        formulas: dict[Key, str],
        base_value: Callable[[Key], Any],
        *,
        name_lookup: Callable[[str, str], str | None] | None = None,
        bounds: Callable[[str], tuple[int, int]] | None = None,
        date1904: bool = False,
        fallback: Callable[[Key], Any] | None = None,
        canon: Callable[[str], str] | None = None,
    ) -> None:
        self.canon = canon or (lambda s: s)
        self.formulas: dict[Key, str] = {(self.canon(k[0]), k[1], k[2]): v for k, v in formulas.items()}
        self.base_value = base_value
        self.fallback = fallback or base_value
        self.values: dict[Key, Any] = {}
        self.unsupported: dict[Key, str] = {}
        self.errors: dict[Key, str] = {}
        self.cycles: set[Key] = set()
        self._bounds = bounds or (lambda _s: (MAX_ROW, MAX_COL))
        self.ev = Evaluator(self._get, name_lookup=name_lookup, bounds=lambda s: self._bounds(self.canon(s)), date1904=date1904)
        self._parsed: dict[Key, F.Node | None] = {}
        self._active: set[Key] = set()
        self._order: list[Key] | None = None
        self._pos: dict[Key, int] = {}
        self._users: dict[Key, list[Key]] = {}
        self._refs: dict[Key, list[tuple[str, int, int, int, int]]] = {}

    # ------------------------------------------------------------------ access
    def _get(self, sheet: str, row: int, col: int) -> Any:
        key = (self.canon(sheet), row, col)
        if key in self.values:
            return self.values[key]
        if key in self.formulas:
            return self._eval_one(key)
        return self.base_value(key)

    def value(self, key: Key) -> Any:
        return self._get(*key)

    def set_formula(self, key: Key, formula: str | None) -> None:
        key = (self.canon(key[0]), key[1], key[2])
        if formula is None:
            self.formulas.pop(key, None)
        else:
            self.formulas[key] = formula[1:] if formula.startswith("=") else formula
        self._parsed.pop(key, None)
        self._refs.pop(key, None)
        self._order = None

    def _node(self, key: Key) -> F.Node | None:
        if key not in self._parsed:
            try:
                self._parsed[key] = self.ev.parse(self.formulas[key])
            except F.FormulaError as exc:
                self.errors[key] = str(exc)
                self._parsed[key] = None
        return self._parsed[key]

    def _eval_one(self, key: Key) -> Any:
        if key in self._active:
            self.cycles.add(key)
            return self.fallback(key)
        node = self._node(key)
        if node is None:
            v = self.fallback(key)
            self.values[key] = v
            return v
        self._active.add(key)
        try:
            v = self.ev.eval(node, key[0], key[1], key[2])
            if isinstance(v, RangeVal):
                v = self.ev._implicit(v, key[1], key[2])
            if isinstance(v, list):
                v = v[0][0] if v and v[0] else None
            if v is None:
                v = 0.0
            self.unsupported.pop(key, None)
        except Unsupported as exc:
            self.unsupported[key] = exc.what
            v = self.fallback(key)
        except (RecursionError, ZeroDivisionError, OverflowError, ValueError, TypeError) as exc:
            self.errors[key] = type(exc).__name__
            v = self.fallback(key)
        finally:
            self._active.discard(key)
        self.values[key] = v
        return v

    # ------------------------------------------------------------------ graph
    def _key_refs(self, key: Key) -> list[tuple[str, int, int, int, int]]:
        if key not in self._refs:
            node = self._node(key)
            out = []
            if node is not None:
                for ref in F.references(node):
                    p = ref.parts
                    if p.kind == "error":
                        continue
                    out.append((self.canon(ref.sheet or key[0]), p.r1, p.c1, p.r2, p.c2))
            self._refs[key] = out
        return self._refs[key]

    def dependency_order(self) -> list[Key]:
        """Topological order of the formula cells (precedents first)."""
        by_sheet_row: dict[str, dict[int, list[int]]] = defaultdict(lambda: defaultdict(list))
        for (s, r, c) in self.formulas:
            by_sheet_row[s][r].append(c)
        deps: dict[Key, set[Key]] = {}
        for key in self.formulas:
            found: set[Key] = set()
            for sh, r1, c1, r2, c2 in self._key_refs(key):
                rows = by_sheet_row.get(sh)
                if not rows:
                    continue
                if r1 == r2 and c1 == c2:
                    if (sh, r1, c1) in self.formulas:
                        found.add((sh, r1, c1))
                    continue
                if r2 - r1 < len(rows):
                    for r in range(r1, r2 + 1):
                        for c in rows.get(r, ()):
                            if c1 <= c <= c2:
                                found.add((sh, r, c))
                else:
                    for r, cols in rows.items():
                        if r1 <= r <= r2:
                            for c in cols:
                                if c1 <= c <= c2:
                                    found.add((sh, r, c))
            found.discard(key)
            deps[key] = found
        indeg = {k: len(v) for k, v in deps.items()}
        users: dict[Key, list[Key]] = defaultdict(list)
        for k, ds in deps.items():
            for d in ds:
                users[d].append(k)
        q = deque(sorted(k for k, n in indeg.items() if n == 0))
        order: list[Key] = []
        while q:
            k = q.popleft()
            order.append(k)
            for u in users.get(k, ()):
                indeg[u] -= 1
                if indeg[u] == 0:
                    q.append(u)
        if len(order) < len(deps):
            rest = [k for k in deps if indeg.get(k, 0) > 0]
            self.cycles.update(rest)
            order.extend(sorted(rest))
        self._order = order
        self._pos = {k: i for i, k in enumerate(order)}
        self._users = dict(users)
        return order

    # ------------------------------------------------------------------ bulk
    def compute_all(self) -> dict[Key, Any]:
        self.values = {}
        for key in self.dependency_order():
            if key not in self.values:
                self._eval_one(key)
        return self.values

    def recompute(self) -> dict[Key, Any]:
        return self.compute_all()

    def update(self, changed: Iterable[Key]) -> set[Key]:
        """Incrementally re-evaluate what depends on ``changed`` (formula or constant cells).

        Returns the formula keys whose value changed.
        """
        if self._order is None:
            self.dependency_order()
        changed = {(self.canon(k[0]), k[1], k[2]) for k in changed}
        dirty: set[Key] = {k for k in changed if k in self.formulas}
        consts = [k for k in changed if k not in self.formulas]
        if consts:
            for key in self.formulas:
                for sh, r1, c1, r2, c2 in self._key_refs(key):
                    if any(k[0] == sh and r1 <= k[1] <= r2 and c1 <= k[2] <= c2 for k in consts):
                        dirty.add(key)
                        break
        stack = list(dirty)
        while stack:
            k = stack.pop()
            for u in self._users.get(k, ()):
                if u not in dirty:
                    dirty.add(u)
                    stack.append(u)
        before = {k: self.values.get(k) for k in dirty}
        for k in dirty:
            self.values.pop(k, None)
        for k in sorted(dirty, key=lambda x: self._pos.get(x, 1 << 30)):
            if k not in self.values:
                self._eval_one(k)
        return {k for k in dirty if not _same(before.get(k), self.values.get(k))}


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (a != a and b != b)
    return bool(a == b) and type(a) is type(b)


def is_error(v: Any) -> bool:
    return isinstance(v, ExcelError)
