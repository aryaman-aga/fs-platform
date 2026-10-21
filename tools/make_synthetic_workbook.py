"""Generate a synthetic Ind AS workbook for tests and demos (02 §13 item 1).

    python tools/make_synthetic_workbook.py out.xlsx [--seed 7] [--exact] [--layout annual|spfs]
                                            [--units lakhs|millions] [--inject NAME ...] [--all]
                                            [--expected expected.json] [--list]

The workbook belongs to the fictional Corvus group; all names and figures are
invented. ``--expected`` writes the findings a correct engine must report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from fscore.synthetic.generator import list_injections, make_workbook

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out", nargs="?", type=Path)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--exact", action="store_true", help="whole-rupee amounts: natural print-rounding differences")
    ap.add_argument("--layout", choices=["annual", "spfs"], default="annual")
    ap.add_argument("--units", choices=["lakhs", "millions", "crores"], default="lakhs")
    ap.add_argument("--inject", nargs="*", default=[], help="error injections (see --list)")
    ap.add_argument("--all", action="store_true", help="apply every injection valid for the layout")
    ap.add_argument("--expected", type=Path, help="write the expected findings as JSON")
    ap.add_argument("--list", action="store_true", help="list the available injections and exit")
    args = ap.parse_args(argv)
    if args.list:
        for name, desc in list_injections(args.layout).items():
            print(f"{name:32s} {desc}")
        return 0
    if args.out is None:
        ap.error("an output path is required")
    names = list(list_injections(args.layout)) if args.all else args.inject
    r = make_workbook(args.out, seed=args.seed, exact=args.exact, units=args.units, layout=args.layout, inject=names)
    print(f"wrote {r.path} with {len(names)} injection(s); {len(r.expected)} expected finding(s); gaps {json.dumps(r.gaps, default=str)}")
    if args.expected:
        payload = {
            "workbook": str(r.path), "seed": args.seed, "exact": args.exact, "layout": args.layout, "injections": names,
            "gaps": r.gaps, "protected_cells": r.protected_cells,
            "expected": [e.__dict__ for e in r.expected],
        }
        args.expected.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
