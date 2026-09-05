"""
Sadhaka — Stage 2: Settlement Batch <-> Order Matcher
======================================================
Stage 1 proved the bank credit IS a given settlement batch. That is not
reconciliation — it only proves the lump sum arrived. Stage 2 answers the
question the merchant's accountant actually has:

    "Which of MY orders are inside this payout, and is every deduction correct?"

This is where the money is actually verified. For every transaction row the
engine independently recomputes what the fee and GST SHOULD have been from the
merchant's own contracted rates, and compares that against what Razorpay
actually charged. It never trusts the gateway's arithmetic — recomputing it is
the entire point.

THREE THINGS THAT LOOK LIKE ERRORS BUT ARE NOT
----------------------------------------------
  * UPI rows carry zero fee and zero GST. That is statutory (zero MDR under
    Sec 10A PSS Act), not a missing deduction. An engine that "expects 2%
    everywhere" will flag every UPI row and drown the real exceptions.
  * on_hold rows are captured but deliberately withheld from the payout.
    Correctly excluded, never "missing money".
  * Refunds and chargebacks routinely appear in a LATER batch than the sale
    they relate to. They are linked back by payment_id across batches.
"""

import config as cfg


def _i(v):
    """Ints from CSV strings, tolerant of blanks."""
    if v is None or v == "":
        return 0
    return int(float(v))


def _expected_fee(amount, method, merchant=None):
    """What the fee SHOULD be under the merchant's contracted rates."""
    rate = cfg.MDR_RATES.get(method, cfg.MDR_RATES["card"])
    return int(round(amount * rate)), rate


def match_orders(recon_rows, orders, batch_matches, audit, tolerances=None,
                 gst_rate=0.18):
    """Reconcile every settled transaction row against the merchant's orders.

    Returns (matched, exceptions, summary).
    """
    tol = tolerances or cfg.TOLERANCES
    stage = "stage2_order"

    orders_by_id = {o["order_id"]: o for o in orders}
    orders_by_payment = {o["payment_id"]: o for o in orders}
    matched_settlement_ids = {m["settlement_id"] for m in batch_matches}

    matched, exceptions = [], []
    seen_order_ids = set()

    fee_overcharge_total = 0
    tax_variance_total = 0
    rounding_total = 0

    for r in recon_rows:
        rtype = r.get("type", "payment")
        sid = r.get("settlement_id", "")
        amount = _i(r.get("amount"))
        fee = _i(r.get("fee"))
        tax = _i(r.get("tax"))
        on_hold = str(r.get("on_hold", "")).lower() == "true"
        payment_id = r.get("payment_id", "")
        order_id = r.get("order_id", "")

        # ---------------- on-hold: correct, not missing ----------------
        if on_hold:
            reason = (f"Transaction {payment_id} ({cfg.rupees(amount)}) is "
                      f"flagged on_hold and withheld from the payout as a "
                      f"reserve. Correctly excluded from the settled total — "
                      f"this is not missing money.")
            exceptions.append({
                "subject_type": "payment", "subject_id": payment_id,
                "order_id": order_id, "variance_code": "ON_HOLD",
                "confidence": 0.99, "amount": amount, "reason": reason,
            })
            audit.record(stage, "payment", payment_id, "EXCEPTION", 0.99,
                         "on_hold_excluded", reason, variance_code="ON_HOLD",
                         amount_subject=amount,
                         evidence={"order_id": order_id, "settled": False})
            seen_order_ids.add(order_id)
            continue

        # ---------------- refunds ----------------
        if rtype == "refund":
            orig = orders_by_payment.get(payment_id)
            debit = _i(r.get("debit"))
            if orig:
                reason = (f"Refund of {cfg.rupees(debit)} against payment "
                          f"{payment_id} (order {orig['order_id']}, original "
                          f"{cfg.rupees(_i(orig['amount']))}). Debited in "
                          f"settlement {sid}, which is a later batch than the "
                          f"original sale — expected behaviour for a refund "
                          f"issued after the sale settled.")
                conf = 0.97
                code = "PARTIAL_PAYMENT"
            else:
                reason = (f"Refund of {cfg.rupees(debit)} references payment "
                          f"{payment_id}, which does not appear in the "
                          f"merchant's order records.")
                conf = 0.30
                code = "UNEXPLAINED"
            exceptions.append({
                "subject_type": "refund", "subject_id": r.get("entity_id"),
                "order_id": order_id, "variance_code": code,
                "confidence": conf, "amount": debit, "reason": reason,
            })
            audit.record(stage, "refund", r.get("entity_id"), "EXCEPTION", conf,
                         "refund_linked_to_payment", reason,
                         counterpart_type="payment", counterpart_id=payment_id,
                         variance_code=code, amount_subject=debit,
                         evidence={"settlement_id": sid, "order_id": order_id})
            continue

        # ---------------- chargebacks / adjustments ----------------
        if rtype == "adjustment":
            debit = _i(r.get("debit"))
            dispute_id = r.get("dispute_id", "")
            handling = fee
            code = "CHARGEBACK" if dispute_id else "UNEXPLAINED"
            reason = (f"Chargeback debit of {cfg.rupees(debit)} against payment "
                      f"{payment_id} (dispute {dispute_id}), comprising the "
                      f"disputed value {cfg.rupees(amount)} plus a handling fee "
                      f"of {cfg.rupees(handling)}. Debited in settlement {sid}."
                      if dispute_id else
                      f"Adjustment of {cfg.rupees(debit)} in settlement {sid} "
                      f"with no dispute reference: {r.get('description') or 'no description supplied'}.")
            exceptions.append({
                "subject_type": "adjustment", "subject_id": r.get("entity_id"),
                "order_id": order_id, "variance_code": code,
                "confidence": 0.95 if dispute_id else 0.35,
                "amount": debit, "reason": reason,
            })
            audit.record(stage, "adjustment", r.get("entity_id"), "EXCEPTION",
                         0.95 if dispute_id else 0.35,
                         "chargeback_debit" if dispute_id else "unexplained_adjustment",
                         reason, counterpart_type="payment",
                         counterpart_id=payment_id, variance_code=code,
                         amount_subject=debit,
                         evidence={"dispute_id": dispute_id,
                                   "handling_fee": handling,
                                   "settlement_id": sid})
            continue

        # ---------------- payments: the real verification ----------------
        order = orders_by_id.get(order_id)
        if not order:
            reason = (f"Settled transaction {payment_id} for {cfg.rupees(amount)} "
                      f"references order {order_id}, which does not exist in the "
                      f"merchant's order records. Money was settled for an order "
                      f"the merchant has no record of.")
            exceptions.append({
                "subject_type": "payment", "subject_id": payment_id,
                "order_id": order_id, "variance_code": "UNEXPLAINED",
                "confidence": 0.0, "amount": amount, "reason": reason,
            })
            audit.record(stage, "payment", payment_id, "EXCEPTION", 0.0,
                         "order_not_found", reason, variance_code="UNEXPLAINED",
                         amount_subject=amount,
                         evidence={"order_id": order_id, "settlement_id": sid})
            continue

        seen_order_ids.add(order_id)
        method = order.get("method") or r.get("method") or "card"
        order_amount = _i(order["amount"])

        # (a) does the settled gross agree with the merchant's own order value?
        if amount != order_amount:
            delta = amount - order_amount
            reason = (f"Settled gross {cfg.rupees(amount)} for order {order_id} "
                      f"does not equal the merchant's recorded order value "
                      f"{cfg.rupees(order_amount)} (difference {cfg.rupees(delta)}).")
            exceptions.append({
                "subject_type": "payment", "subject_id": payment_id,
                "order_id": order_id, "variance_code": "UNEXPLAINED",
                "confidence": 0.20, "amount": amount, "reason": reason,
            })
            audit.record(stage, "payment", payment_id, "EXCEPTION", 0.20,
                         "gross_amount_mismatch", reason,
                         counterpart_type="order", counterpart_id=order_id,
                         variance_code="UNEXPLAINED", amount_subject=amount,
                         amount_counterpart=order_amount, variance_paise=delta,
                         evidence={"settlement_id": sid, "method": method})
            continue

        # (b) recompute the fee independently
        exp_fee, rate = _expected_fee(amount, method)
        fee_delta = fee - exp_fee

        # (c) recompute GST on the fee ACTUALLY charged
        exp_tax = int(round(fee * gst_rate))
        tax_delta = tax - exp_tax

        problems = []

        if abs(fee_delta) > tol.per_txn_rounding_paise:
            eff = (fee / amount) if amount else 0
            problems.append({
                "code": "FEE_DEDUCTION",
                "delta": fee_delta,
                "text": (f"MDR charged {cfg.rupees(fee)} on {cfg.rupees(amount)} "
                         f"({eff*100:.3f}%) but the contracted rate for "
                         f"'{method}' is {rate*100:.2f}%, which would be "
                         f"{cfg.rupees(exp_fee)}. "
                         f"{'Overcharged' if fee_delta > 0 else 'Undercharged'} by "
                         f"{cfg.rupees(abs(fee_delta))}."),
            })
        elif fee_delta != 0:
            rounding_total += abs(fee_delta)

        if abs(tax_delta) > tol.per_txn_rounding_paise:
            eff_gst = (tax / fee) if fee else 0
            problems.append({
                "code": "TAX_DEDUCTION",
                "delta": tax_delta,
                "text": (f"GST charged {cfg.rupees(tax)} on a fee of "
                         f"{cfg.rupees(fee)} is {eff_gst*100:.2f}%, not the "
                         f"statutory {gst_rate*100:.0f}% which would be "
                         f"{cfg.rupees(exp_tax)}. Difference "
                         f"{cfg.rupees(tax_delta)}. Understated GST reduces the "
                         f"input tax credit the merchant can legitimately claim."),
            })
        elif tax_delta != 0:
            rounding_total += abs(tax_delta)

        # (d) does credit = amount - fee - tax?
        credit = _i(r.get("credit"))
        exp_credit = amount - fee - tax
        credit_delta = credit - exp_credit
        if abs(credit_delta) > tol.per_txn_rounding_paise:
            problems.append({
                "code": "UNEXPLAINED",
                "delta": credit_delta,
                "text": (f"Net credit {cfg.rupees(credit)} does not equal gross "
                         f"{cfg.rupees(amount)} minus fee {cfg.rupees(fee)} minus "
                         f"GST {cfg.rupees(tax)} = {cfg.rupees(exp_credit)}. "
                         f"Unexplained difference {cfg.rupees(credit_delta)}."),
            })

        if problems:
            for p in problems:
                if p["code"] == "FEE_DEDUCTION":
                    fee_overcharge_total += p["delta"]
                elif p["code"] == "TAX_DEDUCTION":
                    tax_variance_total += p["delta"]

                exceptions.append({
                    "subject_type": "payment", "subject_id": payment_id,
                    "order_id": order_id, "variance_code": p["code"],
                    "confidence": 0.96, "amount": amount,
                    "variance_paise": p["delta"], "reason": p["text"],
                })
                audit.record(stage, "payment", payment_id, "EXCEPTION", 0.96,
                             f"recomputed_{p['code'].lower()}", p["text"],
                             counterpart_type="order", counterpart_id=order_id,
                             variance_code=p["code"], amount_subject=amount,
                             variance_paise=p["delta"],
                             evidence={"charged_fee": fee, "expected_fee": exp_fee,
                                       "charged_tax": tax, "expected_tax": exp_tax,
                                       "method": method,
                                       "contracted_rate": rate,
                                       "settlement_id": sid})
            continue

        # ---------------- clean match ----------------
        note = ("zero-MDR instrument (UPI), so fee and GST are correctly nil"
                if method == "upi" else
                f"fee {cfg.rupees(fee)} at {rate*100:.2f}% and GST "
                f"{cfg.rupees(tax)} at {gst_rate*100:.0f}% both recomputed and agree")
        reason = (f"Order {order_id} ({cfg.rupees(amount)}, {method}) settled in "
                  f"{sid}: {note}. Net credit {cfg.rupees(credit)}.")

        matched.append({
            "order_id": order_id, "payment_id": payment_id,
            "settlement_id": sid, "amount": amount, "fee": fee, "tax": tax,
            "credit": credit, "method": method, "confidence": 0.99,
            "reason": reason,
        })
        audit.record(stage, "payment", payment_id, "MATCHED", 0.99,
                     "fee_and_gst_recomputed_and_agree", reason,
                     counterpart_type="order", counterpart_id=order_id,
                     amount_subject=amount, amount_counterpart=order_amount,
                     variance_paise=0,
                     evidence={"fee": fee, "tax": tax, "credit": credit,
                               "method": method, "settlement_id": sid})

    # ---------------- orders that appear in NO settlement ----------------
    for o in orders:
        if o["order_id"] in seen_order_ids:
            continue
        amt = _i(o["amount"])
        reason = (f"Order {o['order_id']} ({cfg.rupees(amt)}, captured "
                  f"{o['created_at']}) appears in no settlement batch. Within a "
                  f"T+{cfg.DEFAULT_MERCHANT.settlement_cycle_days} cycle this is "
                  f"expected for recent orders — the payout has not been "
                  f"generated yet, so this is not missing money.")
        exceptions.append({
            "subject_type": "order", "subject_id": o["order_id"],
            "order_id": o["order_id"], "variance_code": "NOT_YET_SETTLED",
            "confidence": 0.90, "amount": amt, "reason": reason,
        })
        audit.record(stage, "order", o["order_id"], "EXCEPTION", 0.90,
                     "captured_not_yet_settled", reason,
                     variance_code="NOT_YET_SETTLED", amount_subject=amt,
                     evidence={"created_at": o["created_at"],
                               "method": o.get("method")})

    audit.flush()

    summary = {
        "fee_overcharge_paise": fee_overcharge_total,
        "tax_variance_paise": tax_variance_total,
        "absorbed_rounding_paise": rounding_total,
        "orders_seen": len(seen_order_ids),
        "orders_total": len(orders),
    }
    return matched, exceptions, summary
