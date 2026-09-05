"""
Sadhaka — Marketplace Scenario (194-O TDS and Section 52 GST TCS)
==================================================================
The default pipeline models a merchant selling its own goods through Razorpay
as a payment aggregator. In that model neither Section 194-O TDS nor Section 52
GST TCS is deducted, and the engine is built to know that — modelling them
would generate confident, wrong exceptions on every row.

But there is a real second case. When the platform operates a MARKETPLACE and
pays third-party sellers (Razorpay Route / split settlements), the platform
becomes the seller-side e-commerce operator making the final payment, and both
obligations can attach:

  * Section 194-O TDS — currently 0.1% of gross (cut from 1% on 2024-10-01),
    5% where PAN is not furnished. Resident Individual/HUF sellers are exempt
    below Rs 5,00,000 of gross sales in the financial year; companies, firms
    and LLPs have no threshold.

  * Section 52 GST TCS — currently 0.5% of net taxable supplies (halved from
    1% on 2024-07-10), collected against the seller's GSTIN.

THE RECONCILIATION PROBLEM THIS CREATES
---------------------------------------
Both are deducted at payout, but neither becomes visible to the seller
immediately:

  * TDS appears in Form 26AS only after the deductor files the quarterly
    return and the challan is processed — commonly a 7 to 30 day lag, and
    systematically wider at quarter end.
  * TCS appears in the seller's GSTR-2B only after the operator files GSTR-8.

So a correctly deducted amount looks like an unexplained shortfall to the
seller until the statement catches up. An engine that does not model the lag
will report a real deduction as missing money every single quarter.

This module reconciles the deduction against the statement and, where the
statement has not caught up yet, classifies it as a TIMING difference rather
than a loss — and says how many days remain before it stops being benign.
"""

import random
from datetime import datetime, timedelta

import config as cfg


def _i(v):
    return 0 if v in (None, "") else int(float(v))


def _dt(s):
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Seller register — who is being paid, and what applies to them
# ---------------------------------------------------------------------------

def build_seller_register(seed=42, count=6):
    """Synthetic third-party sellers with the attributes that drive the rules."""
    rng = random.Random(seed)
    entity_types = ["individual", "huf", "company", "llp", "firm"]
    sellers = []
    for i in range(1, count + 1):
        etype = rng.choice(entity_types)
        has_pan = rng.random() > 0.15          # ~15% without PAN on file
        state = rng.choice(["27", "29", "07", "33", "24"])
        # YTD gross before this batch, used for the Rs 5 lakh threshold
        ytd = rng.randint(50_000_00, 900_000_00)
        sellers.append({
            "seller_id": f"acc_SELLER{i:03d}",
            "legal_name": f"Seller {i} Enterprises",
            "entity_type": etype,
            "pan_on_file": has_pan,
            "gstin": (f"{state}AABCS{1000+i}M1Z{i%10}" if rng.random() > 0.1 else ""),
            "state_code": state,
            "ytd_gross_paise": ytd,
        })
    return sellers


# ---------------------------------------------------------------------------
# Statutory computation
# ---------------------------------------------------------------------------

def compute_194o(gross_paise, seller, txn_date):
    """TDS under Section 194-O. Returns (amount_paise, rate, explanation)."""
    rate = cfg.resolve_rate(cfg.TDS_194O, txn_date)

    if not seller["pan_on_file"]:
        rate = cfg.TDS_194O_NO_PAN_RATE
        amt = int(round(gross_paise * rate))
        return amt, rate, (
            f"TDS deducted at {rate*100:.0f}% because no PAN is on file for "
            f"{seller['legal_name']}. Section 206AA applies the higher rate "
            f"until the seller furnishes a PAN.")

    if seller["entity_type"] in ("individual", "huf"):
        threshold = cfg.TDS_194O_INDIVIDUAL_HUF_THRESHOLD
        projected = seller["ytd_gross_paise"] + gross_paise
        if projected <= threshold:
            return 0, 0.0, (
                f"No TDS. {seller['legal_name']} is a resident "
                f"{seller['entity_type']} with PAN on file and projected gross "
                f"of {cfg.rupees(projected)} for the year, below the "
                f"{cfg.rupees(threshold)} Section 194-O threshold.")
        amt = int(round(gross_paise * rate))
        return amt, rate, (
            f"TDS at {rate*100:.2f}%. {seller['legal_name']} is an "
            f"{seller['entity_type']} whose projected annual gross of "
            f"{cfg.rupees(projected)} exceeds the {cfg.rupees(threshold)} "
            f"threshold, so the exemption no longer applies.")

    amt = int(round(gross_paise * rate))
    return amt, rate, (
        f"TDS at {rate*100:.2f}% on gross {cfg.rupees(gross_paise)}. "
        f"{seller['legal_name']} is a {seller['entity_type']}, for which "
        f"Section 194-O has no turnover threshold — deduction applies from the "
        f"first rupee.")


def compute_tcs(taxable_paise, seller, txn_date):
    """TCS under Section 52 of the CGST Act."""
    rate = cfg.resolve_rate(cfg.GST_TCS_52, txn_date)
    if not seller["gstin"]:
        return 0, 0.0, (
            f"No TCS collected: {seller['legal_name']} has no GSTIN on file. "
            f"Section 24(ix) makes registration mandatory for sellers supplying "
            f"through a TCS-collecting operator, so this seller should not be "
            f"transacting on the platform until registered. Flagged for onboarding.")
    amt = int(round(taxable_paise * rate))
    return amt, rate, (
        f"TCS at {rate*100:.2f}% of net taxable supplies "
        f"{cfg.rupees(taxable_paise)}, collected against GSTIN "
        f"{seller['gstin']} and reported in GSTR-8.")


# ---------------------------------------------------------------------------
# Scenario generation and reconciliation
# ---------------------------------------------------------------------------

def generate_marketplace_payouts(sellers, seed=42, count=24,
                                 base_date=datetime(2026, 7, 1)):
    """Synthetic Route-style split payouts to third-party sellers."""
    rng = random.Random(seed + 1)
    payouts = []
    for i in range(1, count + 1):
        seller = rng.choice(sellers)
        gross = rng.randint(1_500_00, 85_000_00)
        commission = int(round(gross * rng.uniform(0.08, 0.18)))
        txn_date = base_date + timedelta(days=rng.randint(0, 20))

        tds, tds_rate, tds_why = compute_194o(gross, seller, txn_date.date())
        taxable = gross - commission
        tcs, tcs_rate, tcs_why = compute_tcs(taxable, seller, txn_date.date())

        net = gross - commission - tds - tcs

        payouts.append({
            "payout_id": f"pout_{3000+i}",
            "seller_id": seller["seller_id"],
            "seller_name": seller["legal_name"],
            "entity_type": seller["entity_type"],
            "gross_paise": gross,
            "commission_paise": commission,
            "tds_194o_paise": tds,
            "tds_rate": tds_rate,
            "tds_reason": tds_why,
            "tcs_52_paise": tcs,
            "tcs_rate": tcs_rate,
            "tcs_reason": tcs_why,
            "net_paid_paise": net,
            "txn_date": txn_date,
        })
    return payouts


def generate_statements(payouts, as_of, seed=42):
    """Form 26AS and GSTR-8 entries — deliberately incomplete, because the
    statutory statements genuinely lag the deduction."""
    rng = random.Random(seed + 2)
    form26as, gstr8 = [], []
    for p in payouts:
        age = (as_of.date() - p["txn_date"].date()).days
        # 26AS reflects after the quarterly return is filed and processed
        lag = rng.randint(7, 34)
        if p["tds_194o_paise"] and age >= lag:
            form26as.append({
                "seller_id": p["seller_id"],
                "payout_id": p["payout_id"],
                "tds_paise": p["tds_194o_paise"],
                "reflected_on": (p["txn_date"] + timedelta(days=lag)).date().isoformat(),
            })
        g_lag = rng.randint(5, 22)
        if p["tcs_52_paise"] and age >= g_lag:
            gstr8.append({
                "seller_id": p["seller_id"],
                "payout_id": p["payout_id"],
                "tcs_paise": p["tcs_52_paise"],
                "reflected_on": (p["txn_date"] + timedelta(days=g_lag)).date().isoformat(),
            })
    return form26as, gstr8


def reconcile_marketplace(payouts, sellers, form26as, gstr8, audit,
                          as_of=None, tolerances=None):
    """Reconcile statutory deductions against the statements that report them."""
    tol = tolerances or cfg.TOLERANCES
    stage = "stage6_marketplace"
    as_of = as_of or max(p["txn_date"] for p in payouts)
    exceptions = []

    tds_by_payout = {r["payout_id"]: r for r in form26as}
    tcs_by_payout = {r["payout_id"]: r for r in gstr8}
    sellers_by_id = {s["seller_id"]: s for s in sellers}

    matched = pending = overdue = 0
    total_tds = total_tcs = 0
    reflected_tds = reflected_tcs = 0

    for p in payouts:
        age = (as_of.date() - p["txn_date"].date()).days
        total_tds += p["tds_194o_paise"]
        total_tcs += p["tcs_52_paise"]

        # ---- TDS ----
        if p["tds_194o_paise"]:
            rec = tds_by_payout.get(p["payout_id"])
            if rec and rec["tds_paise"] == p["tds_194o_paise"]:
                matched += 1
                reflected_tds += rec["tds_paise"]
                reason = (f"TDS of {cfg.rupees(p['tds_194o_paise'])} on payout "
                          f"{p['payout_id']} to {p['seller_name']} is reflected in "
                          f"Form 26AS as of {rec['reflected_on']}, and the amount "
                          f"agrees exactly. {p['tds_reason']}")
                audit.record(stage, "payout", p["payout_id"], "MATCHED", 0.98,
                             "tds_reflected_in_26as", reason,
                             counterpart_type="form26as",
                             counterpart_id=rec["payout_id"],
                             amount_subject=p["tds_194o_paise"],
                             amount_counterpart=rec["tds_paise"],
                             variance_paise=0,
                             evidence={"seller_id": p["seller_id"],
                                       "rate": p["tds_rate"],
                                       "entity_type": p["entity_type"]})
            elif rec:
                d = p["tds_194o_paise"] - rec["tds_paise"]
                reason = (f"TDS deducted was {cfg.rupees(p['tds_194o_paise'])} but "
                          f"Form 26AS reflects {cfg.rupees(rec['tds_paise'])} — a "
                          f"difference of {cfg.rupees(d)}. The seller can only "
                          f"claim what 26AS shows, so this gap is a real cost to "
                          f"them until the deductor files a correction return.")
                exceptions.append({
                    "subject_type": "payout", "subject_id": p["payout_id"],
                    "variance_code": "TAX_DEDUCTION", "confidence": 0.93,
                    "amount": p["tds_194o_paise"], "variance_paise": d,
                    "reason": reason})
                audit.record(stage, "payout", p["payout_id"], "EXCEPTION", 0.93,
                             "tds_amount_mismatch_vs_26as", reason,
                             variance_code="TAX_DEDUCTION",
                             amount_subject=p["tds_194o_paise"],
                             amount_counterpart=rec["tds_paise"],
                             variance_paise=d,
                             evidence={"seller_id": p["seller_id"]})
            else:
                within = age <= tol.tds_26as_lag_days
                if within:
                    pending += 1
                    remaining = tol.tds_26as_lag_days - age
                    code, conf = "TIMING_LAG", 0.88
                    reason = (f"TDS of {cfg.rupees(p['tds_194o_paise'])} on payout "
                              f"{p['payout_id']} has not yet appeared in Form 26AS. "
                              f"The payout is {age} days old and 26AS commonly lags "
                              f"deduction by up to {tol.tds_26as_lag_days} days, so "
                              f"this is expected for another {remaining} day(s), "
                              f"not a shortfall.")
                else:
                    overdue += 1
                    code, conf = "UNEXPLAINED", 0.45
                    reason = (f"TDS of {cfg.rupees(p['tds_194o_paise'])} on payout "
                              f"{p['payout_id']} is {age} days old and still absent "
                              f"from Form 26AS, beyond the "
                              f"{tol.tds_26as_lag_days}-day tolerance. The seller "
                              f"cannot claim this credit. Verify the deductor filed "
                              f"the return with the correct PAN and quarter.")
                exceptions.append({
                    "subject_type": "payout", "subject_id": p["payout_id"],
                    "variance_code": code, "confidence": conf,
                    "amount": p["tds_194o_paise"], "reason": reason})
                audit.record(stage, "payout", p["payout_id"], "EXCEPTION", conf,
                             "tds_not_yet_in_26as", reason, variance_code=code,
                             amount_subject=p["tds_194o_paise"],
                             evidence={"days_outstanding": age,
                                       "tolerance_days": tol.tds_26as_lag_days,
                                       "seller_id": p["seller_id"]})
        else:
            audit.record(stage, "payout", p["payout_id"], "MATCHED", 0.97,
                         "tds_correctly_not_deducted", p["tds_reason"],
                         amount_subject=0,
                         evidence={"seller_id": p["seller_id"],
                                   "entity_type": p["entity_type"],
                                   "ytd_gross": sellers_by_id[p["seller_id"]]["ytd_gross_paise"]})

        # ---- TCS ----
        if p["tcs_52_paise"]:
            rec = tcs_by_payout.get(p["payout_id"])
            if rec:
                reflected_tcs += rec["tcs_paise"]
                audit.record(stage, "payout", p["payout_id"], "MATCHED", 0.97,
                             "tcs_reflected_in_gstr8",
                             (f"TCS of {cfg.rupees(p['tcs_52_paise'])} is reflected "
                              f"in GSTR-8 as of {rec['reflected_on']}. "
                              f"{p['tcs_reason']}"),
                             counterpart_type="gstr8",
                             counterpart_id=rec["payout_id"],
                             amount_subject=p["tcs_52_paise"],
                             amount_counterpart=rec["tcs_paise"],
                             variance_paise=0,
                             evidence={"seller_id": p["seller_id"]})
            else:
                reason = (f"TCS of {cfg.rupees(p['tcs_52_paise'])} on payout "
                          f"{p['payout_id']} has not yet appeared in GSTR-8. The "
                          f"seller cannot use the credit in their electronic cash "
                          f"ledger until the operator files.")
                exceptions.append({
                    "subject_type": "payout", "subject_id": p["payout_id"],
                    "variance_code": "TIMING_LAG", "confidence": 0.86,
                    "amount": p["tcs_52_paise"], "reason": reason})
                audit.record(stage, "payout", p["payout_id"], "EXCEPTION", 0.86,
                             "tcs_not_yet_in_gstr8", reason,
                             variance_code="TIMING_LAG",
                             amount_subject=p["tcs_52_paise"],
                             evidence={"seller_id": p["seller_id"]})
        elif not sellers_by_id[p["seller_id"]]["gstin"]:
            reason = (f"Payout {p['payout_id']} to {p['seller_name']} carries no "
                      f"TCS because the seller has no GSTIN on file. Under Section "
                      f"24(ix) registration is mandatory to supply through a "
                      f"TCS-collecting operator, so this is an onboarding gap, not "
                      f"a calculation error.")
            exceptions.append({
                "subject_type": "seller", "subject_id": p["seller_id"],
                "variance_code": "UNEXPLAINED", "confidence": 0.90,
                "amount": p["gross_paise"], "reason": reason})
            audit.record(stage, "seller", p["seller_id"], "EXCEPTION", 0.90,
                         "seller_missing_gstin", reason,
                         variance_code="UNEXPLAINED",
                         amount_subject=p["gross_paise"],
                         evidence={"payout_id": p["payout_id"]})

    audit.flush()

    gross_total = sum(p["gross_paise"] for p in payouts)
    return {
        "as_of": as_of.date().isoformat(),
        "payout_count": len(payouts),
        "seller_count": len(sellers),
        "gross_paise": gross_total,
        "gross": cfg.rupees(gross_total),
        "commission": cfg.rupees(sum(p["commission_paise"] for p in payouts)),
        "tds_194o_deducted": cfg.rupees(total_tds),
        "tds_194o_in_26as": cfg.rupees(reflected_tds),
        "tds_not_yet_visible": cfg.rupees(total_tds - reflected_tds),
        "tcs_52_collected": cfg.rupees(total_tcs),
        "tcs_52_in_gstr8": cfg.rupees(reflected_tcs),
        "net_paid": cfg.rupees(sum(p["net_paid_paise"] for p in payouts)),
        "tds_matched": matched,
        "tds_pending_within_tolerance": pending,
        "tds_overdue": overdue,
        "rates_applied": {
            "tds_194o_standard_pct": cfg.resolve_rate(cfg.TDS_194O, as_of.date()) * 100,
            "tds_194o_no_pan_pct": cfg.TDS_194O_NO_PAN_RATE * 100,
            "tcs_52_pct": cfg.resolve_rate(cfg.GST_TCS_52, as_of.date()) * 100,
            "individual_huf_threshold": cfg.rupees(cfg.TDS_194O_INDIVIDUAL_HUF_THRESHOLD),
        },
        "payouts": [
            {**{k: v for k, v in p.items() if k != "txn_date"},
             "txn_date": p["txn_date"].date().isoformat(),
             "gross": cfg.rupees(p["gross_paise"]),
             "tds": cfg.rupees(p["tds_194o_paise"]),
             "tcs": cfg.rupees(p["tcs_52_paise"]),
             "net_paid": cfg.rupees(p["net_paid_paise"])}
            for p in payouts
        ],
    }


def run_scenario(seed=42, audit=None, quiet=False):
    """Standalone entry point: python3 src/marketplace_scenario.py"""
    from audit import AuditTrail
    own = audit is None
    audit = audit or AuditTrail(run_notes="marketplace scenario (194-O / TCS)")

    sellers = build_seller_register(seed=seed)
    payouts = generate_marketplace_payouts(sellers, seed=seed)
    as_of = max(p["txn_date"] for p in payouts) + timedelta(days=5)
    f26, g8 = generate_statements(payouts, as_of, seed=seed)
    report = reconcile_marketplace(payouts, sellers, f26, g8, audit, as_of=as_of)

    audit.set_metric("marketplace", report)
    if own:
        audit.finish()
        audit.close()

    if not quiet:
        W = 72
        print("\n" + "=" * W)
        print("  MARKETPLACE SCENARIO — Section 194-O TDS and Section 52 GST TCS")
        print("=" * W)
        print("\n  This scenario exists because the default pipeline models a pure")
        print("  payment aggregator, where neither deduction applies. Here the")
        print("  platform pays third-party sellers, so both can attach.\n")
        r = report["rates_applied"]
        print(f"  Rates in force at {report['as_of']}:")
        print(f"    194-O standard          {r['tds_194o_standard_pct']:.2f}%")
        print(f"    194-O without PAN       {r['tds_194o_no_pan_pct']:.2f}%")
        print(f"    Individual/HUF threshold {r['individual_huf_threshold']}")
        print(f"    Section 52 GST TCS      {r['tcs_52_pct']:.2f}%")
        print(f"\n  {report['payout_count']} payouts to {report['seller_count']} sellers")
        print(f"    Gross                   {report['gross']:>16}")
        print(f"    Platform commission     {report['commission']:>16}")
        print(f"    194-O TDS deducted      {report['tds_194o_deducted']:>16}")
        print(f"    Section 52 TCS          {report['tcs_52_collected']:>16}")
        print(f"    Net paid to sellers     {report['net_paid']:>16}")
        print(f"\n  TDS visibility to sellers:")
        print(f"    reflected in Form 26AS  {report['tds_194o_in_26as']:>16}")
        print(f"    deducted but not yet visible {report['tds_not_yet_visible']:>11}")
        print(f"    {report['tds_matched']} matched, "
              f"{report['tds_pending_within_tolerance']} pending within tolerance, "
              f"{report['tds_overdue']} overdue")
        print("\n  Sample of the reasoning applied per seller type:")
        seen = set()
        for p in payouts:
            key = (p["entity_type"], p["tds_194o_paise"] == 0)
            if key in seen:
                continue
            seen.add(key)
            print(f"\n    {p['payout_id']} — {p['seller_name']} ({p['entity_type']})")
            print(f"      {p['tds_reason']}")
            if len(seen) >= 4:
                break
        print("\n" + "=" * W + "\n")
    return report


if __name__ == "__main__":
    run_scenario()
