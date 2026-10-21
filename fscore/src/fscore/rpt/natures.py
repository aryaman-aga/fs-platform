"""Canonical natures of related-party transactions and balances (02 §6.9)."""

from __future__ import annotations

import re

# transactions (flows for the period)
TRANSACTION_NATURES = (
    "equity_issue", "investment", "loan_received", "loan_given", "loan_repaid", "loan_repayment_received",
    "interest_paid", "interest_received", "remuneration", "rent", "reimbursement", "services_rendered",
    "services_received", "sales", "purchases", "guarantee", "dividend", "other_transaction",
)
# balances (as at the period end)
BALANCE_NATURES = (
    "trade_payable", "trade_receivable", "loan_payable", "loan_receivable", "recoverable", "advance_received",
    "advance_given", "interest_payable", "equity_balance", "investment_balance", "other_balance",
)

_TXN_RX: list[tuple[str, re.Pattern[str]]] = [
    ("equity_issue", re.compile(r"(issue|allot(ment)?|subscription)\s+of\s+(equity\s+)?shares|equity\s+(shares\s+)?(issued|infusion|contribution)|shares\s+issued|share\s+capital\s+(issued|received|infused)|capital\s+(contribution|infusion)", re.I)),
    ("investment", re.compile(r"investments?\s+(made\s+)?in\s+(the\s+)?(subsidiar|equity|shares)|purchase\s+of\s+investments?|acquisition\s+of\s+shares", re.I)),
    ("loan_repayment_received", re.compile(r"(repayment\s+of\s+loans?\s+given|loans?\s+given\s+repaid|repayment\s+received)", re.I)),
    ("loan_repaid", re.compile(r"(repayment\s+of\s+(loans?|borrowings?)|loans?\s+repaid|borrowings?\s+repaid)", re.I)),
    ("loan_received", re.compile(r"loans?\s+(received|taken|availed|borrowed|obtained)|proceeds\s+from\s+(borrowings|loans?)|borrowings?\s+(received|availed|taken)", re.I)),
    ("loan_given", re.compile(r"loans?\s+(given|granted|advanced|extended)", re.I)),
    ("interest_received", re.compile(r"interest\s+(income|received|earned)", re.I)),
    ("interest_paid", re.compile(r"interest\s+(expense|paid|payable|on\s+(the\s+)?(loans?|borrowings?|inter[\s-]*company))|finance\s+costs?|interest\s+credited", re.I)),
    ("remuneration", re.compile(r"remuneration|salar(y|ies)|sitting\s+fees|compensation\s+(to|of)\s+(kmp|key)|short[\s-]*term\s+employee\s+benefits", re.I)),
    ("rent", re.compile(r"\brent(al)?s?\b|lease\s+rentals?", re.I)),
    ("dividend", re.compile(r"\bdividends?\b", re.I)),
    ("guarantee", re.compile(r"\bguarantees?\b", re.I)),
    ("reimbursement", re.compile(r"reimburs|recover(y|ies)\s+of\s+(expenses|costs)|expenses\s+(paid|incurred)\s+on\s+(behalf|our\s+behalf)|cross[\s-]*charge|recharges?", re.I)),
    ("services_rendered", re.compile(r"(support\s+)?services?\s+(rendered|provided|income|to\s+group)|revenue\s+from\s+operations|sale\s+of\s+services|income\s+from\s+services|support\s+service\s+(income|charges?\s+(income|recovered|billed))|services?\s+billed", re.I)),
    ("services_received", re.compile(r"(support\s+|management\s+|professional\s+|it\s+)?services?\s+(received|availed|cost|expenses?|from\s+group)|professional\s+fees|(support|management)\s+service\s+charges?|\bic\s+(pushback|cost\s+plus)|purchase\s+of\s+services", re.I)),
    ("sales", re.compile(r"\bsales?\b|sale\s+of\s+(goods|products)", re.I)),
    ("purchases", re.compile(r"\bpurchases?\b|purchase\s+of\s+(goods|materials|products)", re.I)),
]

_BAL_RX: list[tuple[str, re.Pattern[str]]] = [
    ("interest_payable", re.compile(r"interest\s+(accrued|payable)", re.I)),
    ("loan_payable", re.compile(r"loans?\s+(payable|from|taken)|borrowings?|unsecured\s+loans?\s+(from|payable)|inter[\s-]*corporate\s+loans?\s+(from|payable)", re.I)),
    ("loan_receivable", re.compile(r"loans?\s+(receivable|given|to)|loans?\s+and\s+advances\s+(given|to)", re.I)),
    ("trade_payable", re.compile(r"trade\s+payables?|payables?\s+(to|for)|amounts?\s+payable|sundry\s+creditors|\bcreditors\b|\bpayables?\b", re.I)),
    ("trade_receivable", re.compile(r"trade\s+receivables?|receivables?\s+from|amounts?\s+receivable|sundry\s+debtors|\bdebtors\b|\breceivables?\b", re.I)),
    ("recoverable", re.compile(r"recoverable|other\s+(financial\s+)?(assets|receivables)|reimbursements?\s+receivable|due\s+from", re.I)),
    ("advance_received", re.compile(r"advances?\s+(received|from)", re.I)),
    ("advance_given", re.compile(r"advances?\s+(given|paid|to)", re.I)),
    ("equity_balance", re.compile(r"equity\s+share\s+capital|share\s+capital\s+(held|outstanding)", re.I)),
    ("investment_balance", re.compile(r"investments?", re.I)),
]

SECTION_RX = {
    "parties": re.compile(r"names?\s+of\s+(the\s+)?related\s+part|related\s+part(y|ies)\s+(and|with\s+whom)|nature\s+of\s+relationship|list\s+of\s+related\s+part|relationships?$", re.I),
    "transactions": re.compile(r"transactions?\s+(during|with|for|entered)|details\s+of\s+(related\s+party\s+)?transactions|nature\s+of\s+transactions?|^transactions$|transactions\s+during\s+the", re.I),
    "balances": re.compile(r"(outstanding\s+)?balances?\s+(outstanding|as\s+at|at\s+the\s+(year|period)[\s-]+end)|closing\s+balances|amounts?\s+(due|outstanding)\s+(to|from|as\s+at)|balances\s+with\s+related|^balances$|outstanding\s+balances", re.I),
}

RELATIONSHIP_RX: list[tuple[str, re.Pattern[str]]] = [
    ("ultimate_holding", re.compile(r"ultimate\s+(holding|parent)", re.I)),
    ("intermediate_holding", re.compile(r"intermediate\s+(holding|parent)", re.I)),
    ("holding", re.compile(r"\b(holding|parent)\s+(company|entity)\b|^holding$", re.I)),
    ("step_down_subsidiary", re.compile(r"(step|setp)[\s-]*down\s+subsidiar", re.I)),
    ("fellow_subsidiary", re.compile(r"fellow\s+subsidiar", re.I)),
    ("subsidiary", re.compile(r"\bsubsidiar(y|ies)\b", re.I)),
    ("associate", re.compile(r"\bassociates?\b", re.I)),
    ("joint_venture", re.compile(r"joint\s+ventures?", re.I)),
    ("kmp", re.compile(r"key\s+(management|managerial)\s+personnel|\bkmp\b|directors?\b", re.I)),
    ("kmp_relative", re.compile(r"relatives?\s+of\s+(kmp|key)", re.I)),
    ("kmp_enterprise", re.compile(r"enterprises?\s+(over\s+which|owned|controlled|in\s+which)", re.I)),
]

MISSPELLINGS = re.compile(r"\b(setp[\s-]*down|subsidary|subsidaries|subsidiery|fellow\s+subsidery|holdng|compnay|comapny|relaionship|relationhip|managment|personel)\b", re.I)


def transaction_nature(text: str) -> str | None:
    for nature, rx in _TXN_RX:
        if rx.search(text or ""):
            return nature
    return None


def balance_nature(text: str) -> str | None:
    for nature, rx in _BAL_RX:
        if rx.search(text or ""):
            return nature
    return None


def relationship_of(text: str) -> str | None:
    t = text or ""
    if re.search(r"relatives?\s+of\s+(kmp|key)", t, re.I):
        return "kmp_relative"
    # "Holding company and ultimate holding company": the immediate holding company (which is also the ultimate one)
    if re.match(r"^\s*(holding|parent)\s+(company|entity)\b", t, re.I):
        return "holding"
    for rel, rx in RELATIONSHIP_RX:
        if rx.search(t):
            return rel
    return None


# TB mapping-key prefixes (Module P chart) → nature
KEY_NATURES: list[tuple[str, str, str]] = [
    # (prefix, nature, kind) kind: bs | pl
    ("tr.", "trade_receivable", "bs"),
    ("tp.", "trade_payable", "bs"),
    ("borr.", "loan_payable", "bs"),
    ("ofa.c.recoverable", "recoverable", "bs"),
    ("ofa.", "recoverable", "bs"),
    ("oca.", "advance_given", "bs"),
    ("ocl.advance", "advance_received", "bs"),
    ("ofl.interest", "interest_payable", "bs"),
    ("eq.share_capital", "equity_balance", "bs"),
    ("inv.", "investment_balance", "bs"),
    ("rev.", "services_rendered", "pl"),
    ("oi.interest", "interest_received", "pl"),
    ("oi.", "services_rendered", "pl"),
    ("fin.interest", "interest_paid", "pl"),
    ("emp.", "remuneration", "pl"),
    ("oth.support", "services_received", "pl"),
    ("oth.", "services_received", "pl"),
]

LIABILITY_NATURES = {"trade_payable", "loan_payable", "advance_received", "interest_payable", "equity_balance"}
ASSET_NATURES = {"trade_receivable", "loan_receivable", "recoverable", "advance_given", "investment_balance"}
INCOME_NATURES = {"services_rendered", "interest_received", "sales"}
EXPENSE_NATURES = {"services_received", "interest_paid", "remuneration", "purchases", "rent", "reimbursement"}

# natures that are reasonably interchangeable between how the TB and the note describe them
NATURE_FAMILY = {
    "services_received": "services_received", "purchases": "services_received",
    "services_rendered": "services_rendered", "sales": "services_rendered",
    "trade_payable": "trade_payable", "interest_payable": "trade_payable",
    "trade_receivable": "trade_receivable",
    "recoverable": "recoverable", "advance_given": "recoverable", "loan_receivable": "loan_receivable",
    "loan_payable": "loan_payable",
}


def family(nature: str) -> str:
    return NATURE_FAMILY.get(nature, nature)
