"""
Sadhaka — Pipeline Runner
=========================
One command runs the whole reconciliation:

    python src/run_pipeline.py

Reads the CSVs, runs three matching stages, writes every decision to the audit
trail, scores itself against the answer key, and prints an honest summary.

Nothing here recomputes numbers for display. Every figure printed comes from
the same objects that were written to the audit trail — if the console and the
audit trail could disagree, the audit trail would be decorative.
"""

import csv
import json
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config as cfg
from audit import AuditTrail
from stage1_batch_matcher import match_batches
from stage2_order_matcher import match_orders
from stage3_gst_itc import reconcile_gst
from stage4_cash_forecast import forecast_cash
from stage5_journal import generate_journal, to_csv
from reporting import build_exception_report, compute_metrics, score_against_answer_key


def load_csv(name, data_dir=None):
    path = os.path.join(data_dir or cfg.DATA_DIR, name)
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"{name} not found at {path}. Run: python src/generate_data.py first."
        )
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def run(data_dir=None, output_dir=None, quiet=False):
    data_dir = data_dir or cfg.DATA_DIR
    output_dir = output_dir or cfg.OUTPUT_DIR
    os.makedirs(output_dir, exist_ok=True)

    orders = load_csv("orders.csv", data_dir)
    recon_rows = load_csv("settlement_recon.csv", data_dir)
    settlements = load_csv("settlements.csv", data_dir)
    bank_rows = load_csv("bank_statement.csv", data_dir)
    gst_invoices = load_csv("razorpay_gst_invoice.csv", data_dir)

    audit = AuditTrail(run_notes="full pipeline run")

    # Stage 1
    batch_matches, batch_exceptions, unmatched_settlements = match_batches(
        bank_rows, settlements, audit)

    # Stage 2
    order_matches, order_exceptions, order_summary = match_orders(
        recon_rows, orders, batch_matches, audit)

    # Stage 3
    gst_report, gst_exceptions = reconcile_gst(
        recon_rows, gst_invoices, order_matches, audit)

    # Stage 4 — forward cash position
    forecast = forecast_cash(orders, recon_rows, settlements, bank_rows, audit)

    all_exceptions = batch_exceptions + order_exceptions + gst_exceptions
    for u in unmatched_settlements:
        all_exceptions.append({
            "subject_type": "settlement", "subject_id": u["settlement_id"],
            "variance_code": "UNEXPLAINED", "confidence": 0.0,
            "amount": u["amount"], "reason": u["reason"],
        })

    # Stage 5 — journal entries implied by the reconciliation
    journal, journal_summary, unbalanced = generate_journal(
        recon_rows, settlements, batch_matches, gst_report, all_exceptions, audit)

    exc_report = build_exception_report(all_exceptions)
    metrics = compute_metrics(bank_rows, settlements, recon_rows, orders,
                              batch_matches, batch_exceptions,
                              unmatched_settlements, order_matches,
                              order_exceptions, gst_report, exc_report)

    answer_key = os.path.join(data_dir, "edge_cases.json")
    scorecard = (score_against_answer_key(answer_key, all_exceptions,
                                          batch_matches, order_matches)
                 if os.path.exists(answer_key) else None)

    audit.set_metric("metrics", metrics)
    audit.set_metric("exception_summary", {
        "by_code": exc_report["by_code"],
        "actionable_count": exc_report["actionable_count"],
        "benign_count": exc_report["benign_count"],
    })
    audit.set_metric("gst_report", gst_report)
    audit.set_metric("forecast", forecast)
    audit.set_metric("journal_summary", journal_summary)
    if scorecard:
        audit.set_metric("scorecard", scorecard)
    audit.finish()

    result = {
        "run_id": audit.run_id,
        "metrics": metrics,
        "exceptions": exc_report,
        "gst": gst_report,
        "scorecard": scorecard,
        "forecast": forecast,
        "journal": journal,
        "journal_summary": journal_summary,
        "journal_unbalanced": unbalanced,
        "order_summary": order_summary,
        "batch_matches": batch_matches,
        "order_matches": order_matches,
        "unmatched_settlements": unmatched_settlements,
    }

    with open(os.path.join(output_dir, "reconciliation_report.json"), "w") as f:
        json.dump(result, f, indent=2, default=str)
    to_csv(journal, os.path.join(output_dir, "journal_entries.csv"))

    audit.close()
    if not quiet:
        print_report(result)
    return result


def _bar(pct, width=28):
    filled = int(round(pct / 100 * width))
    return "#" * filled + "." * (width - filled)


def print_report(r):
    m, e = r["metrics"], r["exceptions"]
    W = 72
    print()
    print("=" * W)
    print("  SADHAKA — SETTLEMENT RECONCILIATION REPORT".ljust(W))
    print(f"  run: {r['run_id']}".ljust(W))
    print("=" * W)

    t = m["throughput"]
    print("\nTHROUGHPUT")
    print(f"  {t['total_records_processed']} records processed: "
          f"{t['recon_rows']} recon rows, {t['bank_credits']} bank credits, "
          f"{t['orders']} orders")
    print(f"  across {t['settlement_batches']} settlement batches")

    print("\nMATCH RATES  (denominator stated — a rate without one is meaningless)")
    for label, key, den in [
        ("Bank -> batch  ", "batch_match_rate_pct", "batch_match_denominator"),
        ("Txn  -> order  ", "order_match_rate_pct", "order_match_denominator"),
        ("By value       ", "value_match_rate_pct", "value_match_denominator"),
        ("Banked value   ", "bank_value_match_rate_pct", "bank_value_denominator"),
    ]:
        pct = m["match_rates"][key]
        print(f"  {label} {pct:6.2f}%  [{_bar(pct)}]")
        print(f"                  {m['match_rates'][den]}")

    print("\nEXCEPTIONS  (benign and actionable reported separately, on purpose)")
    ex = m["exceptions"]
    print(f"  {ex['total']} total = {ex['actionable']} actionable + {ex['benign']} benign")
    print(f"  actionable value: {ex['actionable_value']}")
    print(f"  benign value:     {ex['benign_value']}")
    print()
    print(f"  {'CODE':<22}{'N':>4}  {'VALUE':>15}   TYPE")
    print("  " + "-" * (W - 4))
    for b in e["by_code"]:
        kind = "benign" if b["benign"] else "ACTIONABLE"
        print(f"  {b['code']:<22}{b['count']:>4}  {cfg.rupees(b['value_paise']):>15}   {kind}")

    print("\nCONFIDENCE DISTRIBUTION")
    for k, v in m["confidence_distribution"].items():
        print(f"  {k:<12} {v:>4} matches  [{'#'*min(v,40)}]")

    print("\nMONEY")
    for k, v in m["money"].items():
        print(f"  {k.replace('_',' '):<24} {v:>16}")

    g = r["gst"]
    print("\nGST / INPUT TAX CREDIT")
    for inv in g["invoices"]:
        status = "CLAIMABLE" if not inv["itc_blockers"] else "BLOCKED"
        print(f"  {inv['invoice_no']}  period {inv['period']}  -> {status}")
        print(f"    invoice tax {cfg.rupees(inv['invoice_tax'])} vs settlement tax "
              f"{cfg.rupees(inv['settlement_tax'])} "
              f"(diff {cfg.rupees(inv['tax_difference'])}, "
              f"{'within' if inv['within_tolerance'] else 'OUTSIDE'} tolerance)")
        for b in inv["itc_blockers"]:
            print(f"    blocker: {b}")

    print("\n  Per-instrument effective rates:")
    print(f"    {'METHOD':<12}{'N':>4}{'GROSS':>14}{'MDR%':>8}{'GST%':>7}")
    for meth, b in sorted(g["by_instrument"].items()):
        print(f"    {meth:<12}{b['count']:>4}{cfg.rupees(b['gross']):>14}"
              f"{b['effective_mdr_pct']:>8.3f}{b['effective_gst_pct']:>7.2f}")
    for meth, b in sorted(g["by_instrument"].items()):
        if cfg.MDR_RATES.get(meth, 0.02) == 0:
            print(f"    note: {meth} — {b['statutory_note']}")

    fc = r.get("forecast")
    if fc:
        print("\nFORWARD CASH POSITION")
        b = fc["behaviour"]
        print(f"  Settlement lag learned from data: median "
              f"{b['settlement_lag']['median_days']:.0f}d "
              f"({b['settlement_lag']['source']})")
        print(f"  Bank credit lag:                  median "
              f"{b['credit_lag']['median_days']:.0f}d "
              f"({b['credit_lag']['source']})")
        if b.get("drift_note"):
            print(f"  ! {b['drift_note']}")
        print(f"  Expected over next {fc['horizon_days']} days: "
              f"{fc['expected_total']}  (confidence band: {fc['confidence_band']})")
        print(f"    {fc['confidence_reason']}")
        print(f"  {fc['inflight_count']} in-flight order(s) worth {fc['inflight_net']} net")
        print(f"  {fc['awaiting_credit_count']} settlement(s) awaiting credit worth {fc['awaiting_credit']}")
        landing = [t for t in fc["timeline"] if t["expected_paise"] > 0]
        if landing:
            print("\n    DATE          DAY      EXPECTED        CUMULATIVE  ITEMS")
            for t in landing[:10]:
                print(f"    {t['date']}   {t['weekday']}  {t['expected']:>14}  "
                      f"{t['cumulative']:>16}  {t['item_count']:>4}")
        for risk in fc["at_risk"]:
            print(f"\n  AT RISK [{risk['category']}] {risk['amount']}")
            print(f"    {risk['note']}")

    js = r.get("journal_summary")
    if js:
        print("\nJOURNAL ENTRIES  (the postings this reconciliation implies)")
        print(f"  {js['entries_balanced']} balanced entries generated"
              + (f", {js['entries_unbalanced']} REJECTED as unbalanced"
                 if js['entries_unbalanced'] else ", none unbalanced"))
        print(f"  Trial balance: debits {js['trial_debit_total']} vs credits "
              f"{js['trial_credit_total']} -> "
              f"{'BALANCED' if js['trial_balances'] else 'OUT OF BALANCE'}")
        print()
        print(f"    {'CODE':<7}{'ACCOUNT':<34}{'DEBIT':>15}{'CREDIT':>15}")
        for t in js["trial_balance"]:
            print(f"    {t['account_code']:<7}{t['account_name'][:33]:<34}"
                  f"{t['debit']:>15}{t['credit']:>15}")
        print(f"\n  Gateway cost booked as expense: {js['gateway_cost']}")
        print(f"  Input GST recoverable:          {js['gst_recoverable']}")

    sc = r["scorecard"]
    if sc:
        print("\nSELF-SCORE vs ANSWER KEY")
        print(f"  Faults to detect: {sc['detected']}/{sc['planted_faults']} "
              f"= {sc['recall_pct']}% recall, "
              f"{sc['code_accuracy_pct']}% with the expected code")
        for c in sc["cases"]:
            mark = "PASS" if c["detected"] else "MISS"
            code = "" if c["correct_code"] else "  (wrong code)"
            print(f"    [{mark}] {c['id']:<5} {c['type']:<32} {c['expected_code']}{code}")
            if not c["detected"]:
                print(f"           -> {c['detail']}")
        print(f"\n  Traps to avoid:   {sc['traps_passed']}/{sc['planted_traps']} "
              f"= {sc['trap_pass_pct']}% avoided")
        print("  (a trap is passed by NOT producing a wrong match, so it is")
        print("   scored separately from faults, which are caught by raising one)")
        for t in sc["traps"]:
            mark = "PASS" if t["passed"] else "FAIL"
            print(f"    [{mark}] {t['id']:<5} {t['type']:<32}")
            print(f"           -> {t['detail']}")

    print("\n" + "=" * W)
    print(f"  Full audit trail: {cfg.AUDIT_DB}")
    print(f"  JSON report:      {os.path.join(cfg.OUTPUT_DIR, 'reconciliation_report.json')}")
    print("=" * W + "\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Run the Sadhaka reconciliation pipeline.")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    run(args.data_dir, args.output_dir, args.quiet)
