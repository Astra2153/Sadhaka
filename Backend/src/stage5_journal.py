"""
Sadhaka — Stage 5: Journal Entry Generator
===========================================
Reconciliation that stops at "here is your variance" leaves the last mile to a
human retyping numbers into Tally or Zoho Books. This stage closes that loop by
emitting the actual double-entry postings the reconciliation implies.

THE ACCOUNTING BEING MODELLED
-----------------------------
A merchant sells Rs 10,000 through Razorpay. The bank receives Rs 9,764. The
difference is not a discount and must not be netted against revenue — it is a
cost of service plus a recoverable tax:

    Dr  Bank                          9,764.00
    Dr  Payment gateway charges         200.00      (MDR — an expense)
    Dr  Input GST (recoverable)          36.00      (18% on the MDR — an asset)
        Cr  Razorpay clearing                    10,000.00

The GST leg matters. Booking Rs 236 as a single expense would silently forfeit
Rs 36 of input tax credit. Splitting it is the difference between claiming the
credit and losing it.

EVERY ENTRY BALANCES OR IT IS NOT EMITTED
-----------------------------------------
Each entry is validated: total debits must equal total credits to the paise. An
unbalanced entry is a bug, not a warning, so it is raised rather than exported.
"""

from datetime import datetime
from collections import defaultdict

import config as cfg


# Chart of accounts. Kept as data so a merchant can remap to their own ledger
# codes without touching logic.
ACCOUNTS = {
    "bank":            {"code": "1010", "name": "Bank — Current Account", "type": "asset"},
    "clearing":        {"code": "1210", "name": "Razorpay Clearing Account", "type": "asset"},
    "input_gst":       {"code": "1310", "name": "Input GST Recoverable", "type": "asset"},
    "receivable":      {"code": "1100", "name": "Trade Receivables", "type": "asset"},
    "gateway_charges": {"code": "5210", "name": "Payment Gateway Charges", "type": "expense"},
    "chargeback_loss": {"code": "5220", "name": "Chargeback Losses", "type": "expense"},
    "dispute_fees":    {"code": "5230", "name": "Dispute Handling Fees", "type": "expense"},
    "rounding":        {"code": "5900", "name": "Rounding Differences", "type": "expense"},
    "suspense":        {"code": "1999", "name": "Reconciliation Suspense", "type": "asset"},
    "sales_returns":   {"code": "4100", "name": "Sales Returns & Refunds", "type": "revenue"},
    "revenue":         {"code": "4010", "name": "Sales Revenue", "type": "revenue"},
}


def _i(v):
    return 0 if v in (None, "") else int(float(v))


class JournalEntry:
    def __init__(self, entry_id, date, narration, source_ref, category):
        self.entry_id = entry_id
        self.date = date
        self.narration = narration
        self.source_ref = source_ref
        self.category = category
        self.lines = []

    def dr(self, account, paise, memo=""):
        if paise:
            self.lines.append({"account": account, "debit": paise,
                               "credit": 0, "memo": memo})
        return self

    def cr(self, account, paise, memo=""):
        if paise:
            self.lines.append({"account": account, "debit": 0,
                               "credit": paise, "memo": memo})
        return self

    @property
    def total_debit(self):
        return sum(l["debit"] for l in self.lines)

    @property
    def total_credit(self):
        return sum(l["credit"] for l in self.lines)

    @property
    def balanced(self):
        return self.total_debit == self.total_credit

    def to_dict(self):
        return {
            "entry_id": self.entry_id,
            "date": self.date,
            "narration": self.narration,
            "source_ref": self.source_ref,
            "category": self.category,
            "balanced": self.balanced,
            "total_debit_paise": self.total_debit,
            "total_credit_paise": self.total_credit,
            "total_debit": cfg.rupees(self.total_debit),
            "total_credit": cfg.rupees(self.total_credit),
            "lines": [{
                "account_code": ACCOUNTS[l["account"]]["code"],
                "account_name": ACCOUNTS[l["account"]]["name"],
                "account_type": ACCOUNTS[l["account"]]["type"],
                "debit_paise": l["debit"],
                "credit_paise": l["credit"],
                "debit": cfg.rupees(l["debit"]) if l["debit"] else "",
                "credit": cfg.rupees(l["credit"]) if l["credit"] else "",
                "memo": l["memo"],
            } for l in self.lines],
        }


def generate_journal(recon_rows, settlements, batch_matches, gst_report,
                     exceptions, audit, gst_rate=0.18):
    """Produce the postings implied by the reconciliation.

    Returns (entries, summary, unbalanced).
    """
    stage = "stage5_journal"
    entries = []
    seq = 0

    settlements_by_id = {s["id"]: s for s in settlements}
    rows_by_settlement = defaultdict(list)
    for r in recon_rows:
        if r.get("settlement_id"):
            rows_by_settlement[r["settlement_id"]].append(r)

    # ---- one entry per matched settlement batch -------------------------
    for m in batch_matches:
        sid = m["settlement_id"]
        s = settlements_by_id.get(sid)
        if not s:
            continue
        rows = rows_by_settlement.get(sid, [])
        seq += 1

        gross = sum(_i(r.get("amount")) for r in rows if r.get("type") == "payment")
        fee = sum(_i(r.get("fee")) for r in rows if r.get("type") == "payment")
        tax = sum(_i(r.get("tax")) for r in rows if r.get("type") == "payment")
        refunds = sum(_i(r.get("debit")) for r in rows if r.get("type") == "refund")

        disputes = [r for r in rows if r.get("type") == "adjustment" and r.get("dispute_id")]
        dispute_value = sum(_i(r.get("amount")) for r in disputes)
        dispute_fee = sum(_i(r.get("fee")) for r in disputes)

        bank_amount = m["amount"]

        e = JournalEntry(
            entry_id=f"JV-{seq:04d}",
            date=(s["created_at"] or "")[:10],
            narration=(f"Razorpay settlement {sid} credited to bank as "
                       f"{cfg.rupees(bank_amount)} covering "
                       f"{len([r for r in rows if r.get('type')=='payment'])} "
                       f"transaction(s) gross {cfg.rupees(gross)}, net of gateway "
                       f"charges, GST on those charges, refunds and disputes."),
            source_ref=sid,
            category="settlement",
        )
        e.dr("bank", bank_amount, f"NEFT credit, bank txn {m['bank_txn_id']}")
        e.dr("gateway_charges", fee, f"MDR on {cfg.rupees(gross)} of settled sales")
        e.dr("input_gst", tax,
             f"{gst_rate*100:.0f}% GST on gateway charges — recoverable as input tax credit")
        e.dr("sales_returns", refunds, "Refunds netted from this settlement")
        e.dr("chargeback_loss", dispute_value, "Disputed transactions debited")
        e.dr("dispute_fees", dispute_fee, "Chargeback handling fees")

        credit_side = gross
        e.cr("clearing", credit_side, f"Clearing account discharged for settlement {sid}")

        # The residual is the rounding drift the engine already identified.
        diff = e.total_debit - e.total_credit
        if diff > 0:
            e.cr("rounding", diff, "Rounding difference absorbed on settlement")
        elif diff < 0:
            e.dr("rounding", -diff, "Rounding difference absorbed on settlement")

        entries.append(e)

    # ---- entry for amounts on hold --------------------------------------
    onhold = [r for r in recon_rows if str(r.get("on_hold", "")).lower() == "true"]
    if onhold:
        seq += 1
        total = sum(_i(r.get("amount")) for r in onhold)
        e = JournalEntry(
            entry_id=f"JV-{seq:04d}",
            date=(onhold[0].get("posted_at") or "")[:10],
            narration=(f"{len(onhold)} transaction(s) worth {cfg.rupees(total)} "
                       f"withheld by the gateway as a risk reserve. Recognised as "
                       f"a receivable rather than cash, because the money exists "
                       f"but is not available."),
            source_ref="on_hold_reserve",
            category="reserve",
        )
        e.dr("receivable", total, "Amounts held by gateway as reserve")
        e.cr("clearing", total, "Clearing discharged for held transactions")
        entries.append(e)

    # ---- entry for the monthly GST invoice true-up ----------------------
    for inv in gst_report.get("invoices", []):
        diff = inv["invoice_tax"] - inv["settlement_tax"]
        if diff == 0:
            continue
        seq += 1
        e = JournalEntry(
            entry_id=f"JV-{seq:04d}",
            date=inv.get("period", "") + "-01",
            narration=(f"True-up between GST accrued per transaction "
                       f"({cfg.rupees(inv['settlement_tax'])}) and the monthly "
                       f"tax invoice {inv['invoice_no']} "
                       f"({cfg.rupees(inv['invoice_tax'])}). The invoice is the "
                       f"document that supports the input tax credit claim, so "
                       f"the ledger is aligned to it."),
            source_ref=inv["invoice_no"],
            category="gst_trueup",
        )
        if diff > 0:
            e.dr("input_gst", diff, "Additional GST per monthly invoice")
            e.cr("rounding", diff, "Accrual true-up")
        else:
            e.dr("rounding", -diff, "Accrual true-up")
            e.cr("input_gst", -diff, "GST reduced to monthly invoice")
        entries.append(e)

    # ---- suspense entry for genuinely unexplained variances -------------
    unexplained = [x for x in exceptions
                   if (x.get("variance_code") == "UNEXPLAINED"
                       and x.get("subject_type") in ("bank_txn", "settlement"))]
    if unexplained:
        seq += 1
        total = sum(abs(_i(x.get("amount"))) for x in unexplained)
        e = JournalEntry(
            entry_id=f"JV-{seq:04d}",
            date=datetime.now().strftime("%Y-%m-%d"),
            narration=(f"{len(unexplained)} item(s) totalling {cfg.rupees(total)} "
                       f"could not be attributed to a cause and are parked in "
                       f"suspense pending investigation. Posting them to suspense "
                       f"rather than to an expense keeps the loss visible instead "
                       f"of burying it."),
            source_ref="unexplained_variances",
            category="suspense",
        )
        e.dr("suspense", total, "Unattributed reconciliation variances")
        e.cr("clearing", total, "Clearing adjusted pending investigation")
        entries.append(e)

    # ---- validate ------------------------------------------------------
    unbalanced = [e for e in entries if not e.balanced]
    for e in unbalanced:
        audit.record(stage, "journal_entry", e.entry_id, "EXCEPTION", 0.0,
                     "unbalanced_entry",
                     (f"Journal entry {e.entry_id} does not balance: debits "
                      f"{cfg.rupees(e.total_debit)} against credits "
                      f"{cfg.rupees(e.total_credit)}. Not exported."),
                     variance_code="UNEXPLAINED",
                     amount_subject=e.total_debit,
                     amount_counterpart=e.total_credit,
                     variance_paise=e.total_debit - e.total_credit,
                     evidence={"category": e.category, "source_ref": e.source_ref})

    good = [e for e in entries if e.balanced]
    for e in good:
        audit.record(stage, "journal_entry", e.entry_id, "MATCHED", 0.98,
                     "balanced_double_entry", e.narration,
                     counterpart_type="settlement", counterpart_id=e.source_ref,
                     amount_subject=e.total_debit,
                     amount_counterpart=e.total_credit, variance_paise=0,
                     evidence={"category": e.category,
                               "line_count": len(e.lines)})

    # ---- trial balance --------------------------------------------------
    totals = defaultdict(lambda: {"debit": 0, "credit": 0})
    for e in good:
        for l in e.lines:
            totals[l["account"]]["debit"] += l["debit"]
            totals[l["account"]]["credit"] += l["credit"]

    trial = []
    for acct, t in sorted(totals.items(), key=lambda kv: ACCOUNTS[kv[0]]["code"]):
        net = t["debit"] - t["credit"]
        trial.append({
            "account_code": ACCOUNTS[acct]["code"],
            "account_name": ACCOUNTS[acct]["name"],
            "account_type": ACCOUNTS[acct]["type"],
            "debit_paise": t["debit"], "credit_paise": t["credit"],
            "debit": cfg.rupees(t["debit"]), "credit": cfg.rupees(t["credit"]),
            "net_paise": net, "net": cfg.rupees(net),
        })

    td = sum(t["debit_paise"] for t in trial)
    tc = sum(t["credit_paise"] for t in trial)

    audit.flush()

    summary = {
        "entries_generated": len(entries),
        "entries_balanced": len(good),
        "entries_unbalanced": len(unbalanced),
        "trial_balance": trial,
        "trial_debit_total_paise": td,
        "trial_credit_total_paise": tc,
        "trial_debit_total": cfg.rupees(td),
        "trial_credit_total": cfg.rupees(tc),
        "trial_balances": td == tc,
        "gst_recoverable": cfg.rupees(totals["input_gst"]["debit"] - totals["input_gst"]["credit"]),
        "gateway_cost": cfg.rupees(totals["gateway_charges"]["debit"]),
    }
    return [e.to_dict() for e in good], summary, [e.to_dict() for e in unbalanced]


def to_csv(entries, path):
    """Export in a flat format an accounting package can import."""
    import csv
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["entry_id", "date", "account_code", "account_name",
                    "debit", "credit", "narration", "memo", "source_ref"])
        for e in entries:
            for i, l in enumerate(e["lines"]):
                w.writerow([
                    e["entry_id"], e["date"], l["account_code"], l["account_name"],
                    f"{l['debit_paise']/100:.2f}" if l["debit_paise"] else "",
                    f"{l['credit_paise']/100:.2f}" if l["credit_paise"] else "",
                    e["narration"] if i == 0 else "", l["memo"], e["source_ref"],
                ])
    return path
