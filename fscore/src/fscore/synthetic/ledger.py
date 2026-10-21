"""Synthetic double-entry ledger for a fictional company (tests and demos only).

Every amount is posted as a balanced journal, so the trial balances net to
zero, the Balance Sheet balances and the cash flow ties *by construction*.
With ``exact=False`` all amounts are multiples of one printed paisa of the
presentation unit, so the clean statements cast exactly; with ``exact=True``
amounts are in whole rupees and the usual print-rounding problems appear.

All names, identifiers and figures are invented.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from fscore.prepare.inputs import UNIT_DIVISORS, UNIT_LABELS, EntityInfo, Roll, Signatory, YearData
from fscore.prepare.tb import TBAccount, TrialBalance

D = Decimal

# account code -> (name, key, levels[1..5], counterparty)
ACCOUNTS: dict[str, tuple[str, str, tuple[str, str, str, str, str], str | None]] = {}


def _acc(code: str, name: str, key: str, l1: str, l2: str, l3: str, l4: str | None = None, cp: str | None = None) -> None:
    ACCOUNTS[code] = (name, key, (l1, l2, l3, l4 or name, name), cp)


HOLDING = "Corvus Holdings Private Limited"
FELLOW_US = "Corvus Technologies Inc."
FELLOW_UK = "Corvus Analytics Limited"
FELLOW_SG = "Corvus Systems Pte. Ltd."
SUBSIDIARY = "Corvus Data Services FZ-LLC"

A, L, E, INC, X = "Assets", "Liabilities", "Equity", "Income", "Expenses"
_acc("11010", "Computers - Gross", "ppe.gross.computers", A, "Non Current Assets", "Property Plant and Equipment", "Computers")
_acc("11020", "Furniture and Fixtures - Gross", "ppe.gross.furniture", A, "Non Current Assets", "Property Plant and Equipment", "Furniture")
_acc("11030", "Office Equipment - Gross", "ppe.gross.office_equipment", A, "Non Current Assets", "Property Plant and Equipment", "Office Equipment")
_acc("11110", "Accumulated Depreciation - Computers", "ppe.accdep.computers", A, "Non Current Assets", "Property Plant and Equipment", "Computers")
_acc("11120", "Accumulated Depreciation - Furniture", "ppe.accdep.furniture", A, "Non Current Assets", "Property Plant and Equipment", "Furniture")
_acc("11130", "Accumulated Depreciation - Office Equipment", "ppe.accdep.office_equipment", A, "Non Current Assets", "Property Plant and Equipment", "Office Equipment")
_acc("11500", "Capital Work in Progress", "cwip", A, "Non Current Assets", "Capital Work in Progress")
_acc("11600", "Right of Use Asset - Office Premises", "rou.gross.buildings", A, "Non Current Assets", "Right of Use Assets")
_acc("11610", "Accumulated Depreciation - ROU Asset", "rou.accdep.buildings", A, "Non Current Assets", "Right of Use Assets")
_acc("11700", "Software Licences - Gross", "intangibles.gross.software", A, "Non Current Assets", "Intangible Assets")
_acc("11710", "Accumulated Amortisation - Software", "intangibles.accamort.software", A, "Non Current Assets", "Intangible Assets")
_acc("12000", "Investment in Subsidiary - Equity Shares", "inv.nc.subsidiary", A, "Non Current Assets", "Investments", cp=SUBSIDIARY)
_acc("12100", "Security Deposits - Premises", "ofa.nc.security_deposits", A, "Non Current Assets", "Other Financial Assets")
_acc("12200", "Deferred Tax Asset", "dta", A, "Non Current Assets", "Deferred Tax")
_acc("12300", "Capital Advances", "onca.capital_advances", A, "Non Current Assets", "Other Non Current Assets")
_acc("13000", "Trade Receivables - Domestic", "tr.good.third_party", A, "Current Assets", "Trade Receivables")
_acc("13010", "Trade Receivables - Export", "tr.good.third_party", A, "Current Assets", "Trade Receivables")
_acc("13020", "Intercompany Receivable - Corvus Technologies Inc.", "tr.good.related", A, "Current Assets", "Trade Receivables", "Intercompany Receivables", cp=FELLOW_US)
_acc("13030", "Trade Receivables - Credit Impaired", "tr.impaired", A, "Current Assets", "Trade Receivables")
_acc("13090", "Allowance for Expected Credit Loss", "tr.allowance", A, "Current Assets", "Trade Receivables")
_acc("14000", "Bank - Current Account (Operations)", "cash.bank_current", A, "Current Assets", "Cash and Bank")
_acc("14010", "Bank - Current Account (Collections)", "cash.bank_current", A, "Current Assets", "Cash and Bank")
_acc("14100", "Petty Cash", "cash.on_hand", A, "Current Assets", "Cash and Bank")
_acc("15000", "Interest Accrued on Deposits", "ofa.c.interest_accrued", A, "Current Assets", "Other Financial Assets")
_acc("15010", "Unbilled Revenue", "ofa.c.unbilled", A, "Current Assets", "Other Financial Assets")
_acc("15020", "Recoverable from Group Companies - Corvus Analytics Limited", "ofa.c.recoverable_group", A, "Current Assets", "Other Financial Assets", "Receivables from related parties", cp=FELLOW_UK)
_acc("16000", "Prepaid Expenses", "oca.prepaid", A, "Current Assets", "Other Current Assets")
_acc("16010", "GST Input Credit", "oca.govt_balances", A, "Current Assets", "Other Current Assets")
_acc("16020", "Advance to Suppliers", "oca.advances_suppliers", A, "Current Assets", "Other Current Assets")
_acc("20000", "Equity Share Capital - Intercompany", "eq.share_capital", E, "Equity", "Share Capital", cp=HOLDING)
_acc("20100", "Securities Premium", "oe.securities_premium", E, "Equity", "Other Equity")
_acc("20200", "Retained Earnings", "oe.retained_earnings", E, "Equity", "Other Equity")
_acc("21000", "Term Loan - Bank (Secured)", "borr.nc.term_loan_bank", L, "Non Current Liabilities", "Borrowings")
_acc("21010", "Intercompany Loan - Corvus Holdings Private Limited", "borr.nc.loan_holding", L, "Non Current Liabilities", "Borrowings", "Intercompany Loans", cp=HOLDING)
_acc("21100", "Current Maturities of Term Loan", "borr.c.current_maturities", L, "Current Liabilities", "Borrowings")
_acc("21200", "Lease Liability - Non Current", "lease.nc", L, "Non Current Liabilities", "Lease Liabilities")
_acc("21210", "Lease Liability - Current", "lease.c", L, "Current Liabilities", "Lease Liabilities")
_acc("21300", "Provision for Gratuity - Non Current", "prov.nc.gratuity", L, "Non Current Liabilities", "Provisions")
_acc("21310", "Provision for Gratuity - Current", "prov.c.gratuity", L, "Current Liabilities", "Provisions")
_acc("21320", "Provision for Compensated Absences", "prov.c.compensated_absences", L, "Current Liabilities", "Provisions")
_acc("22000", "Trade Payables - MSME", "tp.msme", L, "Current Liabilities", "Trade Payables")
_acc("22010", "Trade Payables - Others", "tp.others", L, "Current Liabilities", "Trade Payables")
_acc("22020", "Intercompany Payable - Corvus Holdings Private Limited", "tp.related", L, "Current Liabilities", "Trade Payables", "Intercompany Payables", cp=HOLDING)
_acc("23000", "Salary Payable", "ofl.employee_payable", L, "Current Liabilities", "Other Financial Liabilities")
_acc("23010", "Interest Accrued on Borrowings", "ofl.interest_accrued", L, "Current Liabilities", "Other Financial Liabilities")
_acc("23020", "Capital Creditors", "ofl.capital_creditors", L, "Current Liabilities", "Other Financial Liabilities")
_acc("24000", "TDS Payable", "ocl.statutory_dues", L, "Current Liabilities", "Other Current Liabilities")
_acc("24010", "PF and ESI Payable", "ocl.statutory_dues", L, "Current Liabilities", "Other Current Liabilities")
_acc("24020", "Advance from Customers", "ocl.advance_customers", L, "Current Liabilities", "Other Current Liabilities")
_acc("25000", "Provision for Income Tax (net of advance tax)", "ctl.net", L, "Current Liabilities", "Current Tax Liabilities")
_acc("40000", "Revenue - Sale of Services Domestic", "rev.services_domestic", INC, "Revenue from Operations", "Sale of Services")
_acc("40010", "Revenue - Sale of Services Export", "rev.services_export", INC, "Revenue from Operations", "Sale of Services")
_acc("40020", "Support Service Charges - IC Cost Plus - Corvus Technologies Inc.", "rev.related_support", INC, "Revenue from Operations", "Intercompany Revenue", cp=FELLOW_US)
_acc("41000", "Interest Income on Fixed Deposits", "oi.interest_deposits", INC, "Other Income", "Interest Income")
_acc("41010", "Miscellaneous Income", "oi.misc", INC, "Other Income", "Other Non Operating Income")
_acc("50000", "Salaries and Wages", "emp.salaries", X, "Employee Benefits Expense", "Salaries")
_acc("50010", "Bonus and Incentives", "emp.salaries", X, "Employee Benefits Expense", "Salaries")
_acc("50020", "Contribution to Provident Fund", "emp.pf", X, "Employee Benefits Expense", "Contributions")
_acc("50030", "Gratuity Expense", "emp.gratuity", X, "Employee Benefits Expense", "Gratuity")
_acc("50040", "Staff Welfare", "emp.welfare", X, "Employee Benefits Expense", "Staff Welfare")
_acc("51000", "Interest on Term Loan", "fin.interest_bank", X, "Finance Costs", "Interest")
_acc("51010", "Interest on Intercompany Loan - Corvus Holdings Private Limited", "fin.interest_holding", X, "Finance Costs", "Interest", "Intercompany Interest", cp=HOLDING)
_acc("51020", "Interest on Lease Liabilities", "fin.interest_lease", X, "Finance Costs", "Interest")
_acc("52000", "Depreciation on PPE", "dep.ppe", X, "Depreciation and Amortisation", "Depreciation")
_acc("52010", "Depreciation on ROU Assets", "dep.rou", X, "Depreciation and Amortisation", "Depreciation")
_acc("52020", "Amortisation of Software", "dep.intangibles", X, "Depreciation and Amortisation", "Amortisation")
_acc("53000", "Legal and Professional Charges", "oth.legal_professional", X, "Other Expenses", "Professional Fees")
_acc("53010", "Travelling and Conveyance", "oth.travel", X, "Other Expenses", "Travel")
_acc("53020", "Communication Expenses", "oth.communication", X, "Other Expenses", "Communication")
_acc("53030", "Software Subscriptions", "oth.software", X, "Other Expenses", "IT Expenses")
_acc("53040", "Provision for Expected Credit Loss", "oth.ecl", X, "Other Expenses", "Provisions")
_acc("53050", "Audit Fees", "oth.auditor", X, "Other Expenses", "Audit Fees")
_acc("53060", "IC Support Services - Corvus Holdings Private Limited", "oth.support_services_group", X, "Other Expenses", "Intercompany Charges", cp=HOLDING)
_acc("53090", "Miscellaneous Expenses", "oth.misc", X, "Other Expenses", "Miscellaneous")
_acc("54000", "Current Tax Expense", "tax.current", X, "Tax Expense", "Current Tax")
_acc("54010", "Deferred Tax Expense / (Credit)", "tax.deferred", X, "Tax Expense", "Deferred Tax")
_acc("55000", "Remeasurement of Defined Benefit Plans (OCI)", "oci.remeasurement", X, "Other Comprehensive Income", "Remeasurements")
_acc("55010", "Tax on OCI Items", "oci.tax", X, "Other Comprehensive Income", "Tax on OCI")


PL_PREFIXES = ("rev.", "oi.", "emp.", "fin.", "dep.", "oth.", "tax.", "oci.")
PPE_CLASSES = ("computers", "furniture", "office_equipment")
PPE_RATES = {"computers": D("0.30"), "furniture": D("0.10"), "office_equipment": D("0.20")}
CASH_ACCOUNTS = ("14000", "14010", "14100")
TAX_RATE = D("0.2517")


@dataclass
class SyntheticBooks:
    entity: EntityInfo
    tb: TrialBalance  # periods CY, PY (BS closing balances; PL = year activity; RE = opening of the year)
    opening: dict[str, Decimal]  # account balances at the start of PY (PY-1 close), BS accounts only
    years: dict[str, YearData]  # "CY", "PY"
    quantum: Decimal
    divisor: int
    units: str = "lakhs"
    exact: bool = False
    quarter_fraction: dict[str, Decimal] = field(default_factory=dict)  # PL account -> Q4 share (SPFS)
    fx: dict[str, dict[str, Decimal]] = field(default_factory=dict)  # period -> {closing, average, historical}


class _Ledger:
    def __init__(self) -> None:
        self.bal: dict[str, Decimal] = defaultdict(lambda: D(0))

    def post(self, dr: str, cr: str, amount: Decimal) -> None:
        if amount == 0:
            return
        if amount < 0:
            dr, cr, amount = cr, dr, -amount
        self.bal[dr] += amount
        self.bal[cr] -= amount

    def get(self, code: str) -> Decimal:
        return self.bal.get(code, D(0))

    def key_total(self, key: str) -> Decimal:
        return sum((v for c, v in self.bal.items() if ACCOUNTS[c][1] == key), D(0))


def _is_pl(code: str) -> bool:
    return ACCOUNTS[code][1].startswith(PL_PREFIXES)


def corvus_entity(units: str = "lakhs") -> EntityInfo:
    """The fictional reporting entity used by every synthetic fixture."""
    return EntityInfo(
        name="Corvus Data Services Private Limited",
        short="Corvus Data Services",
        cin="U72900KA2015PTC081234",
        address="4th Floor, Synthetic Tech Park, Outer Ring Road, Bengaluru 560103, Karnataka",
        activity="data engineering, analytics and software support services",
        units=units,
        unit_label=UNIT_LABELS[units],
        directors=[Signatory("Asha Raman", "Director", din="01234567"), Signatory("Vikram Sethi", "Director", din="07654321")],
        cfo=Signatory("Neha Kapoor", "Chief Financial Officer"),
        auditor_firm="Synthetic & Co.",
        auditor_frn="123456W",
        auditor_partner=Signatory("Rohit Mehra", "Partner", membership_no="234567"),
        place="Bengaluru",
        holding=HOLDING,
        ultimate_holding=HOLDING,
        group_name="Corvus",
        fellow_subsidiaries=(FELLOW_US, FELLOW_UK, FELLOW_SG),
        subsidiary=SUBSIDIARY,
    )


def generate_books(*, seed: int = 7, exact: bool = False, units: str = "lakhs", scale: float = 1.0) -> SyntheticBooks:
    """Generate two years of consistent books (PY loss, CY profit) for the fictional entity."""
    rng = random.Random(seed)
    rng_paise = random.Random(seed + 7919)  # separate stream: exact mode keeps the same economics
    divisor = UNIT_DIVISORS[units]
    quantum = D(1) if exact else D(divisor) / 100

    def amt(base: float, spread: float = 0.15) -> Decimal:
        v = base * scale * (1 + rng.uniform(-spread, spread))
        if exact:
            # whole rupees plus paise so that printed rounding differences occur naturally
            return (D(str(round(v))) + D(rng_paise.randint(0, 99)) / 100).quantize(D("0.01"))
        return (D(str(round(v))) / quantum).quantize(D(1), rounding=ROUND_HALF_UP) * quantum

    def frac(x: Decimal, f: float | Decimal) -> Decimal:
        v = x * D(str(f))
        q = D("0.01") if exact else quantum
        return (v / q).quantize(D(1), rounding=ROUND_HALF_UP) * q

    led = _Ledger()
    entity = corvus_entity(units)

    # ---------------------------------------------------------------- opening balances (31-Mar-2024)
    open_post: list[tuple[str, str, Decimal]] = [
        ("11010", "20200", amt(42e6)), ("11020", "20200", amt(18e6)), ("11030", "20200", amt(9e6)),
        ("20200", "11110", amt(21e6)), ("20200", "11120", amt(5e6)), ("20200", "11130", amt(3.5e6)),
        ("11500", "20200", amt(1.2e6)),
        ("11600", "20200", amt(36e6)), ("20200", "11610", amt(12e6)),
        ("11700", "20200", amt(8e6)), ("20200", "11710", amt(3e6)),
        ("12000", "20200", amt(25e6, 0)),
        ("12100", "20200", amt(4.5e6)), ("12200", "20200", amt(3.1e6)), ("12300", "20200", amt(0.8e6)),
        ("13000", "20200", amt(52e6)), ("13010", "20200", amt(31e6)), ("13020", "20200", amt(14e6)),
        ("13030", "20200", amt(1.6e6)), ("20200", "13090", amt(1.6e6)),
        ("14000", "20200", amt(38e6)), ("14010", "20200", amt(9e6)), ("14100", "20200", amt(0.15e6)),
        ("15010", "20200", amt(6e6)), ("15020", "20200", amt(2.2e6)),
        ("16000", "20200", amt(3.3e6)), ("16010", "20200", amt(5.4e6)), ("16020", "20200", amt(1.1e6)),
        ("20200", "20000", D(100_000_000)), ("20200", "20100", D(150_000_000)),
        ("20200", "21000", amt(30e6)), ("20200", "21010", amt(40e6)), ("20200", "21100", amt(10e6)),
        ("20200", "21200", amt(20e6)), ("20200", "21210", amt(6e6)),
        ("20200", "21300", amt(11e6)), ("20200", "21310", amt(1.4e6)), ("20200", "21320", amt(3.2e6)),
        ("20200", "22000", amt(4.1e6)), ("20200", "22010", amt(17e6)), ("20200", "22020", amt(8.5e6)),
        ("20200", "23000", amt(12e6)), ("20200", "23010", amt(0.4e6)), ("20200", "23020", amt(0.9e6)),
        ("20200", "24000", amt(3.6e6)), ("20200", "24010", amt(2.1e6)), ("20200", "24020", amt(2.8e6)),
        ("20200", "25000", amt(1.9e6)),
    ]
    for dr, cr, a in open_post:
        led.post(dr, cr, a)
    opening = {c: led.get(c) for c in ACCOUNTS if not _is_pl(c)}

    years: dict[str, YearData] = {}
    tbs: dict[str, dict[str, Decimal]] = {}
    shares_open = 10_000_000  # face value ₹10

    for yk, rg, cg in (("PY", 1.0, 1.0), ("CY", 1.32, 1.12)):
        yd = YearData()
        cf: dict[str, Decimal] = defaultdict(lambda: D(0))
        bs_before = {c: led.get(c) for c in ACCOUNTS if not _is_pl(c)}
        yd.bs_open = dict(bs_before)
        # ---------------- revenue and receivables (Ind AS 115)
        rev_dom, rev_exp, rev_ic = amt(160e6 * rg), amt(100e6 * rg), amt(70e6 * rg)
        led.post("13000", "40000", rev_dom)
        led.post("13010", "40010", rev_exp)
        led.post("13020", "40020", rev_ic)
        unbilled_delta = amt(0.9e6 * rg, 0.9)
        led.post("15010", "40000", unbilled_delta)
        for code, rev, close_target in (("13000", rev_dom, 0.17), ("13010", rev_exp, 0.16), ("13020", rev_ic, 0.19)):
            led.post(CASH_ACCOUNTS[1], code, led.get(code) - frac(rev, close_target))
        ecl = amt(0.7e6 * cg)
        led.post("53040", "13090", ecl)
        led.post("13030", "13000", ecl)  # balances covered by the new allowance become credit impaired
        yd.revenue_geo = {"India": rev_dom + unbilled_delta, "Outside India": rev_exp + rev_ic}
        # ---------------- other income
        int_inc = amt(2.4e6 * rg)
        led.post("15000", "41000", int_inc)
        led.post("14000", "15000", int_inc)  # received within the year
        led.post("14000", "41010", amt(0.6e6 * rg))
        # ---------------- employees
        sal, bonus, pf, welfare = amt(205e6 * cg), amt(18e6 * cg), amt(11e6 * cg), amt(3.2e6 * cg)
        led.post("50000", "23000", sal)
        led.post("50010", "23000", bonus)
        pay_target = frac(sal + bonus, 0.055)
        led.post("23000", "14000", -led.get("23000") - pay_target)
        led.post("50020", "24010", pf)
        led.post("24010", "14000", pf - amt(0.1e6, 0.9))
        led.post("50040", "14000", welfare)
        led.post("50000", "21320", amt(0.9e6 * cg))  # compensated absences
        yd.remuneration = amt(9.6e6 * cg, 0.05)  # KMP remuneration included in salaries
        # gratuity (actuarial valuation)
        gr_open = -(led.get("21300") + led.get("21310"))
        sc, ic_cost = amt(2.6e6 * cg), frac(gr_open, 0.072)
        remeasure = amt(0.8e6 * cg, 0.5)
        paid = amt(0.9e6 * cg)
        led.post("50030", "21300", sc + ic_cost)
        led.post("55000", "21300", remeasure)
        led.post("21300", "14000", paid)
        gr_close = gr_open + sc + ic_cost + remeasure - paid
        gr_current = frac(gr_close, 0.11)
        led.post("21300", "21310", gr_current + led.get("21310"))  # re-split current / non-current
        yd.gratuity = {"opening": gr_open, "service_cost": sc, "interest_cost": ic_cost, "remeasurement": remeasure,
                       "benefits_paid": paid, "closing": gr_close, "current": gr_current, "noncurrent": gr_close - gr_current}
        # ---------------- other expenses through payables
        exp_lines = {"53000": amt(14e6 * cg), "53010": amt(7.5e6 * cg), "53020": amt(3.1e6 * cg), "53030": amt(9.4e6 * cg),
                     "53050": amt(1.8e6, 0.05), "53090": amt(2.7e6 * cg)}
        for code, a in exp_lines.items():
            led.post(code, "22000" if code in ("53050",) else "22010", a)
        led.post("22010", "14000", -led.get("22010") - frac(sum(exp_lines.values(), D(0)), 0.36))
        msme_target = frac(exp_lines["53050"] + exp_lines["53000"], 0.12)
        if -led.get("22000") > msme_target:
            led.post("22000", "14000", -led.get("22000") - msme_target)
        ic_support = amt(46e6 * cg)
        led.post("53060", "22020", ic_support)
        led.post("22020", "14000", -led.get("22020") - frac(ic_support, 0.21))
        reimb = amt(3.4e6 * cg)
        led.post("15020", "14000", reimb)  # expenses paid on behalf of a fellow subsidiary
        led.post("14000", "15020", reimb - amt(0.4e6 * cg, 0.9))
        for code, base in (("16000", 0.5e6), ("16010", 0.9e6), ("16020", -0.3e6), ("12100", 0.4e6), ("12300", -0.2e6)):
            led.post(code, "14000", amt(base * cg, 0.6))
        led.post("14000", "24020", amt(0.7e6 * rg, 0.8))  # advance from customers
        # ---------------- PPE, CWIP, intangibles
        cwip_add = amt(2.5e6 * cg)
        cwip_cap = led.get("11500") + frac(cwip_add, 0.6)
        led.post("11500", "14000", cwip_add)
        roll: dict[str, Roll] = {}
        for cls, acc_g, acc_d, base_add in (("computers", "11010", "11110", 12e6), ("furniture", "11020", "11120", 3e6), ("office_equipment", "11030", "11130", 2e6)):
            r = Roll(opening=led.get(acc_g), dep_opening=-led.get(acc_d))
            add_cash = amt(base_add * cg)
            transfer = cwip_cap if cls == "computers" else D(0)
            led.post(acc_g, "14000", add_cash)
            if transfer:
                led.post(acc_g, "11500", transfer)
            disp = frac(r.opening, 0.04) if cls != "office_equipment" else D(0)
            disp_dep = frac(disp, 0.7)
            if disp:
                led.post("14000", acc_g, disp - disp_dep)  # proceeds = net book value (no gain / loss)
                led.post(acc_d, acc_g, disp_dep)
            charge = frac(r.opening + add_cash / 2, PPE_RATES[cls] / 2)
            led.post("52000", acc_d, charge)
            r.additions, r.transfers, r.disposals = add_cash + transfer, transfer, disp
            r.closing, r.dep_charge, r.dep_disposals, r.dep_closing = led.get(acc_g), charge, disp_dep, -led.get(acc_d)
            roll[cls] = r
            cf["purchase_ppe"] -= add_cash
            cf["proceeds_ppe"] += disp - disp_dep
        cf["purchase_ppe"] -= cwip_add
        yd.far = roll
        yd.cwip = Roll(opening=bs_before["11500"], additions=cwip_add, transfers=cwip_cap, closing=led.get("11500"))
        sw = Roll(opening=led.get("11700"), dep_opening=-led.get("11710"))
        sw_add = amt(2.2e6 * cg)
        led.post("11700", "14000", sw_add)
        sw_amort = frac(sw.opening + sw_add, 0.18)
        led.post("52020", "11710", sw_amort)
        sw.additions, sw.closing, sw.dep_charge, sw.dep_closing = sw_add, led.get("11700"), sw_amort, -led.get("11710")
        yd.software = sw
        cf["purchase_intangibles"] -= sw_add
        # ---------------- leases (Ind AS 116)
        lease_open = -(led.get("21200") + led.get("21210"))
        rou = Roll(opening=led.get("11600"), dep_opening=-led.get("11610"))
        new_lease = amt(6e6, 0.5) if yk == "CY" else D(0)
        if new_lease:
            led.post("11600", "21200", new_lease)
        lease_int = frac(lease_open, 0.085)
        led.post("51020", "21200", lease_int)
        lease_pay = frac(lease_open, 0.32)
        led.post("21200", "14000", lease_pay)
        rou_dep = frac(rou.opening + new_lease, 0.16)
        led.post("52010", "11610", rou_dep)
        rou.additions, rou.closing, rou.dep_charge, rou.dep_closing = new_lease, led.get("11600"), rou_dep, -led.get("11610")
        yd.rou = rou
        lease_close = lease_open + new_lease + lease_int - lease_pay
        lease_cur = frac(lease_close, 0.3)
        led.post("21200", "21210", lease_cur + led.get("21210"))
        yd.lease = {"opening": lease_open, "additions": new_lease, "interest": lease_int, "payments": lease_pay,
                    "closing": lease_close, "current": lease_cur, "noncurrent": lease_close - lease_cur}
        und_1 = frac(lease_pay, 1.05)
        yd.lease_maturity = {"Not later than one year": und_1, "Later than one year and not later than five years": frac(lease_close, 0.95),
                             "Later than five years": D(0)}
        cf["lease_principal"] -= lease_pay - lease_int
        cf["lease_interest"] -= lease_int
        # ---------------- borrowings
        tl_open = -(led.get("21000") + led.get("21100"))
        repay = -led.get("21100")  # last year's current maturities are repaid
        led.post("21100", "14000", repay)
        drawn = amt(12e6, 0.3) if yk == "CY" else D(0)
        if drawn:
            led.post("14000", "21000", drawn)
        tl_close = tl_open - repay + drawn
        tl_cur = frac(tl_close, 0.25)
        led.post("21000", "21100", tl_cur)
        int_bank = frac(tl_open, 0.095)
        led.post("51000", "14000", int_bank)
        yd.term_loan = {"opening": tl_open, "drawn": drawn, "repaid": repay, "closing": tl_close, "current": tl_cur,
                        "noncurrent": tl_close - tl_cur, "interest": int_bank}
        hl_open = -led.get("21010")
        hl_recv = amt(8e6, 0.4) if yk == "PY" else D(0)
        hl_repaid = amt(5e6, 0.3) if yk == "CY" else D(0)
        hl_int = frac(hl_open, 0.08)
        if not exact:
            hl_int = (hl_int / (quantum * 10)).quantize(D(1)) * quantum * 10
        tds = frac(hl_int, 0.10)
        led.post("14000", "21010", hl_recv)
        led.post("21010", "14000", hl_repaid)
        led.post("51010", "21010", hl_int - tds)  # interest credited to the loan, net of TDS
        led.post("51010", "24000", tds)
        led.post("24000", "14000", tds)  # TDS deposited with the government within the year
        yd.holding_loan = {"opening": hl_open, "received": hl_recv, "interest": hl_int, "tds": tds, "credited": hl_int - tds,
                           "repaid": hl_repaid, "closing": -led.get("21010")}
        cf["borrow_proceeds"] += drawn + hl_recv
        cf["borrow_repaid"] -= repay + hl_repaid
        cf["interest_paid"] -= int_bank + tds
        # ---------------- equity infusion (CY) and investment in subsidiary
        issued_shares = 500_000 if yk == "CY" else 0
        premium_ps = D(90)
        issue_date = date(2025, 12, 15) if issued_shares else None
        if issued_shares:
            led.post("14000", "20000", D(issued_shares * 10))
            led.post("14000", "20100", D(issued_shares) * premium_ps)
            cf["share_issue"] += D(issued_shares * 10) + D(issued_shares) * premium_ps
        year_end = entity.year_end if yk == "CY" else entity.py_end
        days = 365
        weighted = D(shares_open)
        if issue_date:
            weighted += (D(issued_shares) * D((year_end - issue_date).days) / D(days)).quantize(D(1), rounding=ROUND_HALF_UP)
        yd.shares = {"opening": shares_open, "issued": issued_shares, "closing": shares_open + issued_shares, "face_value": 10,
                     "premium_per_share": premium_ps, "issue_date": issue_date, "weighted": int(weighted)}
        shares_open += issued_shares
        inv_add = amt(4e6, 0.2) if yk == "CY" else D(0)
        led.post("12000", "14000", inv_add)
        cf["investments"] -= inv_add
        # ---------------- tax
        pbt = -sum((v for c, v in led.bal.items() if _is_pl(c) and ACCOUNTS[c][1].split(".")[0] in ("rev", "oi", "emp", "fin", "dep", "oth")), D(0))
        cur_tax = frac(pbt * D("1.05"), TAX_RATE) if pbt > 0 else amt(0.4e6)
        led.post("54000", "25000", cur_tax)
        ctl_open = -bs_before["25000"]
        adv_tax = cur_tax - frac(cur_tax, 0.12)
        led.post("25000", "14000", ctl_open + adv_tax)
        def_tax = -amt(1.1e6 * cg, 0.5)  # negative = deferred tax credit
        led.post("12200", "54010", -def_tax)
        oci_tax = frac(remeasure, TAX_RATE)
        led.post("12200", "55010", oci_tax)
        expected = frac(pbt, TAX_RATE)
        yd.tax = {"current": cur_tax, "deferred": def_tax, "oci_tax": -oci_tax, "paid": ctl_open + adv_tax, "pbt": pbt,
                  "expected": expected, "other": cur_tax + def_tax - expected}
        yd.ctl = {"opening": ctl_open, "current": cur_tax, "paid": ctl_open + adv_tax, "closing": -led.get("25000")}
        yd.dta = {"opening": bs_before["12200"], "pl": -def_tax, "oci": oci_tax, "closing": led.get("12200")}
        # ---------------- related-party summary (rupees, positive)
        yd.ic = {"revenue_support": rev_ic, "support_services": ic_support, "interest_holding": hl_int, "loan_received": hl_recv,
                 "loan_repaid": hl_repaid, "reimbursement": reimb, "equity_issued": D(issued_shares) * (10 + premium_ps),
                 "investment": inv_add}
        yd.contingent = {"Income-tax demands under appeal": amt(1.24e6 * cg, 0.05), "Indirect tax matters under dispute": amt(0.62e6, 0.05)}
        yd.commitments = amt(0.32e6 * cg, 0.05)
        # ---------------- ageing (gross receivables; payables MSME / others)
        tr_gross = led.key_total("tr.good.third_party") + led.key_total("tr.good.related") + led.key_total("tr.impaired")
        buckets = ["Not due", "Less than 6 months", "6 months - 1 year", "1-2 years", "2-3 years", "More than 3 years"]
        rest = tr_gross
        ageing: dict[str, Decimal] = {}
        for b, sh in zip(buckets[:-1], (0.52, 0.31, 0.09, 0.05, 0.02), strict=True):
            ageing[b] = frac(tr_gross, sh)
            rest -= ageing[b]
        ageing[buckets[-1]] = rest
        yd.receivable_ageing = ageing
        tp_msme = -led.key_total("tp.msme")
        tp_oth = -(led.key_total("tp.others") + led.key_total("tp.related"))
        a1, a2 = frac(tp_oth, 0.6), frac(tp_oth, 0.3)
        yd.payable_ageing = {
            "MSME": {"Not due": frac(tp_msme, 0.7), "Less than 1 year": tp_msme - frac(tp_msme, 0.7), "1-2 years": D(0)},
            "Others": {"Not due": a1, "Less than 1 year": a2, "1-2 years": tp_oth - a1 - a2},
        }
        yd.msme = {"principal": tp_msme, "interest": D(0)}
        # ---------------- snapshot
        tbs[yk] = dict(led.bal)
        yd.bs_close = {c: led.get(c) for c in ACCOUNTS if not _is_pl(c)}
        yd.pl = {c: led.get(c) for c in ACCOUNTS if _is_pl(c)}
        yd.cash_flow = dict(cf)
        yd.cash_flow["cash_open"] = sum((bs_before[c] for c in CASH_ACCOUNTS), D(0))
        yd.cash_flow["cash_close"] = sum((yd.bs_close[c] for c in CASH_ACCOUNTS), D(0))
        years[yk] = yd
        # ---------------- close the year: profit and loss to retained earnings
        if yk == "PY":
            for c in list(led.bal):
                if _is_pl(c) and led.bal[c] != 0:
                    led.post("20200", c, led.bal[c])

    tb = TrialBalance(entity=entity.name, currency="INR", periods=["CY", "PY"],
                      period_labels={"CY": "Mar-26", "PY": "Mar-25"},
                      period_ends={"CY": entity.year_end, "PY": entity.py_end})
    for code, (name, key, levels, cp) in ACCOUNTS.items():
        acc = TBAccount(code=code, name=name, levels=list(levels), key=key, counterparty=cp)
        for yk in ("CY", "PY"):
            acc.amounts[yk] = tbs[yk].get(code, D(0))
        tb.accounts.append(acc)
    for yk in ("CY", "PY"):
        if tb.total(yk) != 0:
            raise AssertionError(f"synthetic TB {yk} does not net to zero")
    qf = {code: D(str(round(rng.uniform(0.23, 0.29), 2))) for code in ACCOUNTS if _is_pl(code)}
    fx = {"CY": {"closing": D("85.20"), "average": D("84.40"), "historical": D("74.10")},
          "PY": {"closing": D("83.10"), "average": D("82.60"), "historical": D("74.10")}}
    return SyntheticBooks(entity=entity, tb=tb, opening=opening, years=years, quantum=quantum, divisor=divisor,
                          units=units, exact=exact, quarter_fraction=qf, fx=fx)


def key_balance(balances: dict[str, Decimal], key: str) -> Decimal:
    """Sum of account balances (debit +) mapped to a key."""
    return sum((v for c, v in balances.items() if ACCOUNTS.get(c, ("", ""))[1] == key), D(0))


def ledger_cash_flow(books: SyntheticBooks, yk: str) -> dict[str, Decimal]:
    """Indirect-method cash flow recomputed from balances (rupees) — the identity the statements must satisfy."""
    yd = books.years[yk]
    o, c, pl = yd.bs_open, yd.bs_close, yd.pl

    def bal(d: dict[str, Decimal], *keys: str) -> Decimal:
        return sum((key_balance(d, k) for k in keys), D(0))

    def plk(*keys: str) -> Decimal:
        return sum((key_balance(pl, k) for k in keys), D(0))

    income = -plk("rev.services_domestic", "rev.services_export", "rev.related_support", "oi.interest_deposits", "oi.misc")
    expenses = plk(*[k for k in {a[1] for a in ACCOUNTS.values()} if k.split(".")[0] in ("emp", "fin", "dep", "oth")])
    pbt = income - expenses
    out: dict[str, Decimal] = {}
    out["pbt"] = pbt
    out["dep"] = plk("dep.ppe", "dep.rou", "dep.intangibles")
    out["finance"] = plk("fin.interest_bank", "fin.interest_holding", "fin.interest_lease")
    out["interest_income"] = plk("oi.interest_deposits")  # credit balance -> negative add-back
    tr = ("tr.good.third_party", "tr.good.related", "tr.impaired", "tr.allowance")
    out["wc_tr"] = bal(o, *tr) - bal(c, *tr)
    ofa = ("ofa.nc.security_deposits", "ofa.c.interest_accrued", "ofa.c.unbilled", "ofa.c.recoverable_group")
    out["wc_ofa"] = bal(o, *ofa) - bal(c, *ofa)
    oa = ("onca.capital_advances", "oca.prepaid", "oca.govt_balances", "oca.advances_suppliers")
    out["wc_oa"] = bal(o, *oa) - bal(c, *oa)
    out["wc_tp"] = -(bal(c, "tp.msme", "tp.others", "tp.related") - bal(o, "tp.msme", "tp.others", "tp.related"))
    out["wc_ofl"] = -(bal(c, "ofl.employee_payable", "ofl.interest_accrued", "ofl.capital_creditors") - bal(o, "ofl.employee_payable", "ofl.interest_accrued", "ofl.capital_creditors"))
    out["wc_ocl"] = -(bal(c, "ocl.statutory_dues", "ocl.advance_customers") - bal(o, "ocl.statutory_dues", "ocl.advance_customers"))
    prov = ("prov.nc.gratuity", "prov.c.gratuity", "prov.c.compensated_absences")
    out["wc_prov"] = -(bal(c, *prov) - bal(o, *prov))
    out["remeasurement"] = -plk("oci.remeasurement")
    out["taxes_paid"] = -yd.ctl["paid"]
    out["operating"] = sum((out[k] for k in ("pbt", "dep", "finance", "interest_income", "wc_tr", "wc_ofa", "wc_oa", "wc_tp", "wc_ofl", "wc_ocl", "wc_prov", "remeasurement", "taxes_paid")), D(0))
    out["purchase_ppe"] = yd.cash_flow.get("purchase_ppe", D(0))
    out["proceeds_ppe"] = yd.cash_flow.get("proceeds_ppe", D(0))
    out["purchase_intangibles"] = yd.cash_flow.get("purchase_intangibles", D(0))
    out["investments"] = yd.cash_flow.get("investments", D(0))
    out["interest_received"] = -plk("oi.interest_deposits")
    out["investing"] = sum((out[k] for k in ("purchase_ppe", "proceeds_ppe", "purchase_intangibles", "investments", "interest_received")), D(0))
    out["share_issue"] = yd.cash_flow.get("share_issue", D(0))
    out["borrow_proceeds"] = yd.cash_flow.get("borrow_proceeds", D(0))
    out["borrow_repaid"] = yd.cash_flow.get("borrow_repaid", D(0))
    out["lease_principal"] = yd.cash_flow.get("lease_principal", D(0))
    out["lease_interest"] = yd.cash_flow.get("lease_interest", D(0))
    out["interest_paid"] = yd.cash_flow.get("interest_paid", D(0))
    out["financing"] = sum((out[k] for k in ("share_issue", "borrow_proceeds", "borrow_repaid", "lease_principal", "lease_interest", "interest_paid")), D(0))
    out["net_change"] = out["operating"] + out["investing"] + out["financing"]
    out["cash_open"] = yd.cash_flow.get("cash_open", D(0))
    out["cash_close"] = yd.cash_flow.get("cash_close", D(0))
    return out
