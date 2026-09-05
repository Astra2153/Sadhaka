"""
Sadhaka — Counterfactual Explanations
======================================
An exception that says "this did not match" tells a finance person that a
problem exists. It does not tell them what to do.

A counterfactual answers the question they actually have:

    "What is the smallest thing that would have to be different for this to
     reconcile?"

That reframes every exception from a complaint into an instruction:

    not  "bank credit of Rs 47,442.78 has no matching settlement"
    but  "this would match setl_XiRZ if the credit were Rs 12.40 higher —
          which is exactly the GST on the fee, so the likely cause is GST
          deducted twice"

WHY THIS IS NOT JUST NICER WORDING
----------------------------------
The minimal delta is diagnostic. If the gap equals the fee, someone deducted
the fee twice. If it equals 18% of the fee, the GST leg is duplicated or
missing. If it is a few paise, it is rounding. If it matches a refund in a
neighbouring batch, the refund landed in the wrong period. The size and shape
of the required change identifies the cause, and the engine can say so.

HONESTY CONSTRAINT
------------------
A counterfactual is only offered when a specific, checkable change is found. If
no small change would resolve the exception, the engine says that plainly
instead of inventing a plausible-sounding cause. "No single adjustment under
Rs 500 would reconcile this" is a real finding, and more useful than a guess.
"""

from datetime import datetime, timedelta

import config as cfg


def _i(v):
    return 0 if v in (None, "") else int(float(v))


def _dt(s):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except (ValueError, TypeError):
            continue
    return None


def _classify_gap(gap_paise, context):
    """Name the likely cause from the SHAPE of the required change.

    context carries the fee, tax and nearby amounts so the gap can be compared
    against quantities that would explain it.
    """
    g = abs(gap_paise)
    fee = context.get("fee", 0)
    tax = context.get("tax", 0)

    if g <= 5:
        return ("rounding", "a few paise — consistent with per-transaction "
                            "fee and GST rounding, not a real discrepancy")
    if fee and abs(g - fee) <= 5:
        return ("fee_double_counted",
                f"exactly the fee of {cfg.rupees(fee)} — consistent with the "
                f"MDR being deducted twice, or once by each system")
    if tax and abs(g - tax) <= 5:
        return ("gst_leg_missing",
                f"exactly the GST of {cfg.rupees(tax)} — consistent with the "
                f"GST leg being applied on one side only")
    if fee and tax and abs(g - (fee + tax)) <= 5:
        return ("fee_and_gst_double_counted",
                f"exactly the fee plus GST ({cfg.rupees(fee + tax)}) — "
                f"consistent with the whole deduction being applied twice")
    if fee and abs(g - int(round(fee * 0.18))) <= 5:
        return ("gst_on_fee",
                f"18% of the fee — consistent with a missing or duplicated "
                f"GST-on-MDR calculation")
    return ("unclassified",
            "no quantity in this settlement explains a gap of this size")


def counterfactual_for_unmatched_bank(bank_row, settlements, tolerances=None):
    """The minimal change that would let an unmatched bank credit match."""
    tol = tolerances or cfg.TOLERANCES
    amt = _i(bank_row["amount"])
    bdt = _dt(bank_row.get("credit_datetime") or bank_row.get("value_date"))

    best = None
    for s in settlements:
        s_amt = _i(s["amount"])
        sdt = _dt(s.get("created_at"))
        gap = amt - s_amt
        days = (bdt.date() - sdt.date()).days if (bdt and sdt) else None
        score = abs(gap) + (abs(days) * 1000 if days is not None else 0)
        if best is None or score < best["score"]:
            best = {"settlement": s, "gap": gap, "days": days, "score": score}

    if not best:
        return {"available": False,
                "narrative": "There are no settlements to compare against."}

    s = best["settlement"]
    gap, days = best["gap"], best["days"]
    changes = []

    if gap != 0:
        changes.append({
            "field": "bank credit amount",
            "current": cfg.rupees(amt),
            "required": cfg.rupees(_i(s["amount"])),
            "delta": cfg.rupees(-gap),
            "delta_paise": -gap,
            "text": (f"the credit would need to be {cfg.rupees(abs(gap))} "
                     f"{'lower' if gap > 0 else 'higher'}"),
        })

    if days is not None and (days < 0 or days > tol.date_window_days):
        changes.append({
            "field": "date window",
            "current": f"{tol.date_window_days} days",
            "required": f"{abs(days)} days",
            "text": (f"the credit landed {days} days after the settlement, so "
                     f"the {tol.date_window_days}-day window would need to be "
                     f"widened to {abs(days)} days"),
        })

    cause, explanation = _classify_gap(gap, {
        "fee": _i(s.get("fees")), "tax": _i(s.get("tax"))})

    if not changes:
        narrative = (f"This should already match {s['id']} — amount and date "
                     f"both agree. If it did not, the cause is elsewhere in the "
                     f"matching logic and is worth investigating as a bug.")
    else:
        parts = " and ".join(c["text"] for c in changes)
        narrative = (f"This would reconcile against settlement {s['id']} if "
                     f"{parts}. The gap of {cfg.rupees(abs(gap))} is "
                     f"{explanation}.")

    return {
        "available": True,
        "nearest_counterpart": s["id"],
        "gap_paise": gap,
        "gap": cfg.rupees(gap),
        "days_apart": days,
        "changes": changes,
        "likely_cause": cause,
        "cause_explanation": explanation,
        "narrative": narrative,
        "actionable": cause != "unclassified",
    }


def counterfactual_for_fee_variance(exception, recon_row, order, gst_rate=0.18):
    """What would make a fee or GST exception clear."""
    amount = _i(recon_row.get("amount"))
    charged_fee = _i(recon_row.get("fee"))
    charged_tax = _i(recon_row.get("tax"))
    method = order.get("method") or recon_row.get("method") or "card"
    rate = cfg.MDR_RATES.get(method, 0.02)
    expected_fee = int(round(amount * rate))
    expected_tax = int(round(charged_fee * gst_rate))

    code = exception.get("variance_code")
    changes = []

    if code == "FEE_DEDUCTION":
        delta = charged_fee - expected_fee
        eff = (charged_fee / amount) if amount else 0
        changes.append({
            "field": "fee charged",
            "current": cfg.rupees(charged_fee),
            "required": cfg.rupees(expected_fee),
            "delta": cfg.rupees(-delta),
            "delta_paise": -delta,
            "text": (f"the fee would need to drop by {cfg.rupees(abs(delta))} "
                     f"to {cfg.rupees(expected_fee)}"),
        })
        changes.append({
            "field": "contracted rate",
            "current": f"{rate*100:.2f}%",
            "required": f"{eff*100:.3f}%",
            "text": (f"alternatively, the contracted rate for '{method}' would "
                     f"need to be {eff*100:.3f}% instead of {rate*100:.2f}% — "
                     f"check whether a rate change was agreed and not "
                     f"reflected in the configuration"),
        })
        annual = delta * 250          # rough: a comparable transaction daily
        narrative = (
            f"This clears if the fee is corrected to {cfg.rupees(expected_fee)}, "
            f"or if the contracted rate really is {eff*100:.3f}%. "
            f"The two possibilities have very different consequences: a billing "
            f"error is recoverable, an unrecorded rate change is not. "
            f"At this transaction's frequency the difference is roughly "
            f"{cfg.rupees(annual)} a year, so it is worth resolving which it is.")

    elif code == "TAX_DEDUCTION":
        delta = charged_tax - expected_tax
        eff = (charged_tax / charged_fee) if charged_fee else 0
        changes.append({
            "field": "GST charged",
            "current": cfg.rupees(charged_tax),
            "required": cfg.rupees(expected_tax),
            "delta": cfg.rupees(-delta),
            "delta_paise": -delta,
            "text": (f"GST would need to move by {cfg.rupees(abs(delta))} to "
                     f"{cfg.rupees(expected_tax)}, which is "
                     f"{gst_rate*100:.0f}% of the {cfg.rupees(charged_fee)} fee"),
        })
        narrative = (
            f"GST here is {eff*100:.2f}% of the fee rather than the statutory "
            f"{gst_rate*100:.0f}%. Correcting it to {cfg.rupees(expected_tax)} "
            f"clears the exception and restores {cfg.rupees(abs(delta))} of "
            f"input tax credit. Understated GST is not a rounding matter — the "
            f"merchant cannot claim credit that was never charged.")

    else:
        return {"available": False,
                "narrative": "No counterfactual is defined for this exception type."}

    return {
        "available": True,
        "changes": changes,
        "narrative": narrative,
        "actionable": True,
    }


def counterfactual_for_threshold(exception, confidence):
    """For a match held back by the confidence threshold, say exactly what
    additional evidence would have been enough."""
    need = cfg.AUTO_ACCEPT_THRESHOLD - confidence
    if need <= 0:
        return {"available": False}
    options = []
    if confidence < 0.85:
        options.append("a bank reference that corresponds to the settlement UTR "
                       "would raise this to 0.85")
    if confidence < 0.99:
        options.append("an exact UTR match with the amount and date already "
                       "agreeing would raise it to 0.99")
    return {
        "available": True,
        "shortfall": round(need, 3),
        "narrative": (
            f"This scored {confidence:.2f}, which is {need:.2f} below the "
            f"{cfg.AUTO_ACCEPT_THRESHOLD:.2f} auto-accept threshold, so it was "
            f"held for review rather than matched. "
            + ("; ".join(options).capitalize() + "." if options else "")),
        "actionable": True,
    }


def explain_all(exceptions, bank_rows, settlements, recon_rows, orders,
                limit=40):
    """Attach a counterfactual to every exception that admits one."""
    bank_by_id = {b["bank_txn_id"]: b for b in bank_rows}
    recon_by_payment = {r["payment_id"]: r for r in recon_rows if r.get("payment_id")}
    orders_by_id = {o["order_id"]: o for o in orders}

    out = []
    for e in exceptions[:limit]:
        sid = str(e.get("subject_id", ""))
        code = e.get("variance_code")
        cf = None

        if sid in bank_by_id and code in ("UNEXPLAINED", "DUPLICATE_CANDIDATE"):
            cf = counterfactual_for_unmatched_bank(bank_by_id[sid], settlements)
        elif code in ("FEE_DEDUCTION", "TAX_DEDUCTION"):
            row = recon_by_payment.get(sid)
            order = orders_by_id.get(e.get("order_id") or (row or {}).get("order_id"))
            if row and order:
                cf = counterfactual_for_fee_variance(e, row, order)
        elif code == "UNEXPLAINED" and (e.get("confidence") or 0) > 0:
            cf = counterfactual_for_threshold(e, e.get("confidence", 0))

        if cf and cf.get("available"):
            out.append({
                "subject_id": sid,
                "variance_code": code,
                "original_reason": e.get("reason"),
                "counterfactual": cf,
            })
        else:
            out.append({
                "subject_id": sid,
                "variance_code": code,
                "original_reason": e.get("reason"),
                "counterfactual": {
                    "available": False,
                    "narrative": ("No single small adjustment would resolve this. "
                                  "That is itself a finding — it means the cause "
                                  "is structural rather than a value being off."),
                    "actionable": False,
                },
            })
    return out
