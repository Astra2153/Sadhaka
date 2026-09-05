"""
Sadhaka — Stage 3: GST / Input Tax Credit Reconciliation
=========================================================
This is the stage most reconciliation tools skip, and it is where Indian
merchants quietly lose money.

THE PROBLEM
-----------
GST on the gateway fee is deducted transaction by transaction, inside the
settlement. But it is NOT claimable from the settlement report. Input Tax
Credit can only be claimed against Razorpay's MONTHLY GST tax invoice, and only
once that invoice appears in the merchant's GSTR-2B.

So there are two separate facts that must both be true:

  1. The tax deducted across all settlements ties to the monthly invoice
     (allowing for accumulated per-transaction rounding), and
  2. That invoice is actually reflected in GSTR-2B.

If (1) fails, the books are wrong. If (2) fails, the cash was already deducted
but the credit cannot be claimed this period — a real, quantifiable working
capital cost that the merchant usually discovers months later.

ITC ELIGIBILITY GATE
--------------------
Under Sec 16 CGST Act read with Sec 16(2)(aa) and Rule 36(4), ITC requires a
valid tax invoice bearing the supplier's GSTIN, the invoice reflected in
GSTR-2B, and a place of supply consistent with the recipient's registration.
Each is checked explicitly here, and a failure names the specific condition.
"""

import config as cfg


def _i(v):
    if v is None or v == "":
        return 0
    return int(float(v))


def _valid_gstin(g):
    """Structural check only: 15 chars, 2-digit state code, 13th char is the
    entity number, 14th is 'Z' by convention. Not a government API call —
    this catches malformed or missing GSTINs, not deregistered ones."""
    if not g or len(g) != 15:
        return False, "GSTIN is not 15 characters"
    if not g[:2].isdigit():
        return False, "GSTIN state code is not numeric"
    if not (1 <= int(g[:2]) <= 38):
        return False, f"GSTIN state code '{g[:2]}' is not a valid state code"
    if g[2:7].isdigit():
        return False, "GSTIN PAN segment looks malformed"
    return True, "structurally valid"


def reconcile_gst(recon_rows, gst_invoices, order_matches, audit,
                  merchant=None, tolerances=None, gst_rate=0.18):
    """Reconcile settlement-deducted GST against the monthly tax invoice and
    determine what is actually claimable as ITC.

    Returns (report, exceptions).
    """
    merchant = merchant or cfg.DEFAULT_MERCHANT
    tol = tolerances or cfg.TOLERANCES
    stage = "stage3_gst"
    exceptions = []

    # --- 1. what was actually deducted, from the settlement data ---------
    settled_fee = sum(_i(r.get("fee")) for r in recon_rows
                      if r.get("type") == "payment"
                      and str(r.get("on_hold", "")).lower() != "true")
    settled_tax = sum(_i(r.get("tax")) for r in recon_rows
                      if r.get("type") == "payment"
                      and str(r.get("on_hold", "")).lower() != "true")

    # GST that SHOULD have been charged on those fees
    expected_tax = int(round(settled_fee * gst_rate))
    understated = expected_tax - settled_tax

    periods = []

    for inv in gst_invoices:
        inv_no = inv.get("invoice_no")
        taxable = _i(inv.get("taxable_value"))
        inv_tax = _i(inv.get("total_tax"))
        supplier_gstin = inv.get("supplier_gstin", "")
        recipient_gstin = inv.get("recipient_gstin", "")
        pos = str(inv.get("place_of_supply", ""))
        in_2b = str(inv.get("reflected_in_gstr2b", "")).lower() == "yes"

        # --- 2. invoice vs settlement tie-out --------------------------
        tax_diff = inv_tax - settled_tax
        fee_diff = taxable - settled_fee

        if abs(tax_diff) <= tol.gst_invoice_tolerance_paise:
            tie_code = "ROUNDING" if tax_diff != 0 else None
            tie_conf = 0.97
            tie_reason = (
                f"Monthly GST invoice {inv_no} declares tax of "
                f"{cfg.rupees(inv_tax)}; the sum of per-transaction GST across "
                f"all settled rows is {cfg.rupees(settled_tax)}. Difference "
                f"{cfg.rupees(tax_diff)}, within the "
                f"{cfg.rupees(tol.gst_invoice_tolerance_paise)} monthly "
                f"tolerance. This is accumulated per-transaction rounding, not "
                f"an error — book a rounding journal for the difference."
            )
        else:
            tie_code = "TAX_DEDUCTION"
            tie_conf = 0.40
            tie_reason = (
                f"Monthly GST invoice {inv_no} declares tax of "
                f"{cfg.rupees(inv_tax)} but the settlements deducted "
                f"{cfg.rupees(settled_tax)} — a difference of "
                f"{cfg.rupees(tax_diff)}, outside the "
                f"{cfg.rupees(tol.gst_invoice_tolerance_paise)} tolerance. "
                f"Claiming ITC on the invoice figure while the books carry the "
                f"settlement figure would create a reconciling item at audit."
            )

        if tie_code:
            exceptions.append({
                "subject_type": "invoice", "subject_id": inv_no,
                "variance_code": tie_code, "confidence": tie_conf,
                "amount": inv_tax, "variance_paise": tax_diff,
                "reason": tie_reason,
            })
        audit.record(stage, "invoice", inv_no,
                     "MATCHED" if tie_code in (None, "ROUNDING") else "EXCEPTION",
                     tie_conf, "invoice_vs_settlement_tax_tieout", tie_reason,
                     variance_code=tie_code, amount_subject=inv_tax,
                     amount_counterpart=settled_tax, variance_paise=tax_diff,
                     evidence={"taxable_value": taxable,
                               "settled_fee": settled_fee,
                               "fee_difference": fee_diff,
                               "tolerance": tol.gst_invoice_tolerance_paise})

        # --- 3. ITC eligibility gate -----------------------------------
        blockers = []

        ok, why = _valid_gstin(supplier_gstin)
        if not ok:
            blockers.append(f"supplier GSTIN invalid ({why})")

        ok, why = _valid_gstin(recipient_gstin)
        if not ok:
            blockers.append(f"recipient GSTIN invalid ({why})")
        elif recipient_gstin != merchant.gstin:
            blockers.append(
                f"invoice is addressed to GSTIN {recipient_gstin} but the "
                f"merchant is registered as {merchant.gstin}")

        if not in_2b:
            blockers.append(
                "invoice is not reflected in GSTR-2B, so under Sec 16(2)(aa) "
                "the credit cannot be claimed this period even though the GST "
                "was already deducted at source")

        if pos and pos != merchant.state_code:
            blockers.append(
                f"place of supply is state {pos} but the merchant is registered "
                f"in state {merchant.state_code}; a mismatched place of supply "
                f"makes the credit ineligible")

        if inv_tax <= 0:
            blockers.append("invoice declares no tax, so there is nothing to claim")

        claimable = 0 if blockers else inv_tax

        if blockers:
            reason = (f"ITC of {cfg.rupees(inv_tax)} on invoice {inv_no} is NOT "
                      f"claimable: " + "; ".join(blockers) + ".")
            exceptions.append({
                "subject_type": "invoice", "subject_id": inv_no,
                "variance_code": "TAX_DEDUCTION", "confidence": 0.95,
                "amount": inv_tax, "reason": reason,
            })
            audit.record(stage, "invoice", inv_no, "EXCEPTION", 0.95,
                         "itc_blocked", reason, variance_code="TAX_DEDUCTION",
                         amount_subject=inv_tax,
                         evidence={"blockers": blockers})
        else:
            reason = (f"ITC of {cfg.rupees(inv_tax)} on invoice {inv_no} is "
                      f"claimable: valid supplier and recipient GSTINs, invoice "
                      f"reflected in GSTR-2B, place of supply consistent with "
                      f"the merchant's registration in state {merchant.state_code}.")
            audit.record(stage, "invoice", inv_no, "MATCHED", 0.97,
                         "itc_eligible", reason, amount_subject=inv_tax,
                         evidence={"claimable_paise": claimable,
                                   "gstr2b": in_2b})

        periods.append({
            "invoice_no": inv_no,
            "period": inv.get("period"),
            "supplier": inv.get("supplier_name"),
            "supplier_gstin": supplier_gstin,
            "taxable_value": taxable,
            "invoice_tax": inv_tax,
            "settlement_tax": settled_tax,
            "tax_difference": tax_diff,
            "within_tolerance": abs(tax_diff) <= tol.gst_invoice_tolerance_paise,
            "reflected_in_gstr2b": in_2b,
            "itc_claimable": claimable,
            "itc_blockers": blockers,
        })

    # --- 4. understated GST across the settlement data -------------------
    if abs(understated) > tol.gst_invoice_tolerance_paise:
        reason = (
            f"Across all settled transactions, GST actually deducted was "
            f"{cfg.rupees(settled_tax)}, but {gst_rate*100:.0f}% of the "
            f"{cfg.rupees(settled_fee)} of fees charged would be "
            f"{cfg.rupees(expected_tax)} — GST is understated by "
            f"{cfg.rupees(understated)}. Every rupee of understated GST is a "
            f"rupee of input tax credit the merchant cannot claim."
        )
        exceptions.append({
            "subject_type": "gst_period", "subject_id": "settlement_gst_total",
            "variance_code": "TAX_DEDUCTION", "confidence": 0.94,
            "amount": settled_tax, "variance_paise": -understated,
            "reason": reason,
        })
        audit.record(stage, "gst_period", "settlement_gst_total", "EXCEPTION",
                     0.94, "gst_understated_vs_fees", reason,
                     variance_code="TAX_DEDUCTION", amount_subject=settled_tax,
                     amount_counterpart=expected_tax,
                     variance_paise=-understated,
                     evidence={"settled_fee": settled_fee,
                               "gst_rate": gst_rate})

    # --- 5. per-instrument breakdown (explains zero-MDR rows) ------------
    by_method = {}
    for r in recon_rows:
        if r.get("type") != "payment":
            continue
        if str(r.get("on_hold", "")).lower() == "true":
            continue
        m = r.get("method") or "unknown"
        b = by_method.setdefault(m, {"count": 0, "gross": 0, "fee": 0, "tax": 0})
        b["count"] += 1
        b["gross"] += _i(r.get("amount"))
        b["fee"] += _i(r.get("fee"))
        b["tax"] += _i(r.get("tax"))

    for m, b in by_method.items():
        b["effective_mdr_pct"] = round(100 * b["fee"] / b["gross"], 4) if b["gross"] else 0.0
        b["effective_gst_pct"] = round(100 * b["tax"] / b["fee"], 2) if b["fee"] else 0.0
        b["contracted_mdr_pct"] = round(100 * cfg.MDR_RATES.get(m, 0.02), 2)
        b["statutory_note"] = (
            "Zero MDR by statute (Sec 10A PSS Act); nil fee and nil GST are correct."
            if cfg.MDR_RATES.get(m, 0.02) == 0 else
            f"Contracted MDR {b['contracted_mdr_pct']:.2f}% plus {gst_rate*100:.0f}% GST on the fee."
        )

    audit.flush()

    report = {
        "settled_fee_total": settled_fee,
        "settled_tax_total": settled_tax,
        "expected_tax_on_fees": expected_tax,
        "gst_understated": understated,
        "total_itc_claimable": sum(p["itc_claimable"] for p in periods),
        "total_itc_blocked": sum(p["invoice_tax"] for p in periods if p["itc_blockers"]),
        "invoices": periods,
        "by_instrument": by_method,
    }
    return report, exceptions
