"""
Sadhaka — Stage 4: Forward Cash Position Forecaster
====================================================
Reconciliation is backward-looking: it tells the merchant what already
happened. The question a finance controller actually asks on a Monday morning
is forward-looking:

    "How much cash lands in my account this week, and how sure are we?"

That is a different problem, and it is genuinely uncertain, so this module
refuses to answer it with a single number. Every projection carries a
confidence band derived from the settlement behaviour actually observed in the
data, not from an assumption.

HOW THE FORECAST IS BUILT
-------------------------
1. Learn the merchant's REAL settlement lag from history — the observed gap
   between a transaction's capture and its settlement, and between the
   settlement and the bank credit landing. The contracted cycle (T+2) is the
   prior; observed behaviour overrides it, because the RBI's September 2025
   Payment Aggregator Directions made the cycle contractual rather than
   mandated, so what the contract says and what the gateway does can differ.

2. Project each in-flight order forward using that learned lag, net of the
   fee and GST that will be deducted — forecasting gross would overstate the
   cash position by roughly the MDR plus GST on it.

3. Attach a confidence band from the observed VARIANCE in that lag. A merchant
   whose settlements have always landed on day 2 gets a tight band; one whose
   settlements scatter across days 1-5 gets a wide one, and is told so.

4. Flag what is at risk: amounts on hold, and settlements already created whose
   bank credit has not appeared within the expected window.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not forecast future SALES. Predicting demand from ten days of synthetic
order data would be a fabricated number wearing a confidence interval. This
forecasts only money that already exists — captured orders and created
settlements — which is the part that can be projected honestly.
"""

from datetime import datetime, timedelta
from collections import defaultdict
import statistics

import config as cfg


def _dt(s):
    if not s:
        return None
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def _i(v):
    return 0 if v in (None, "") else int(float(v))


def learn_settlement_behaviour(recon_rows, settlements, bank_rows):
    """Derive the merchant's actual settlement rhythm from observed history."""
    settle_lags = []          # capture -> settlement, in days
    for r in recon_rows:
        if r.get("type") != "payment" or not r.get("settled_at"):
            continue
        c, s = _dt(r.get("created_at")), _dt(r.get("settled_at"))
        if c and s:
            settle_lags.append((s.date() - c.date()).days)

    setl_by_utr = {s["utr"]: s for s in settlements}
    setl_by_amount = defaultdict(list)
    for s in settlements:
        setl_by_amount[_i(s["amount"])].append(s)

    credit_lags = []          # settlement created -> bank credit, in days
    for b in bank_rows:
        s = setl_by_utr.get(b.get("reference"))
        if not s:
            cands = setl_by_amount.get(_i(b["amount"]), [])
            s = cands[0] if len(cands) == 1 else None
        if not s:
            continue
        sd, bd = _dt(s["created_at"]), _dt(b.get("credit_datetime") or b.get("value_date"))
        if sd and bd:
            credit_lags.append((bd.date() - sd.date()).days)

    def summarise(vals, fallback_median, label):
        if not vals:
            return {
                "observations": 0,
                "median_days": fallback_median,
                "p90_days": fallback_median + 1,
                "stdev_days": 0.0,
                "source": f"no observations; falling back to the contracted {label}",
            }
        vals_sorted = sorted(vals)
        p90 = vals_sorted[min(len(vals_sorted) - 1, int(round(0.9 * (len(vals_sorted) - 1))))]
        return {
            "observations": len(vals),
            "median_days": statistics.median(vals),
            "p90_days": p90,
            "stdev_days": round(statistics.pstdev(vals), 2) if len(vals) > 1 else 0.0,
            "min_days": min(vals),
            "max_days": max(vals),
            "source": f"learned from {len(vals)} observed {label}",
        }

    contracted = cfg.DEFAULT_MERCHANT.settlement_cycle_days
    settle = summarise(settle_lags, contracted, "settlement cycle")
    credit = summarise(credit_lags, 0, "bank credit lag")

    drift = None
    if settle["observations"]:
        d = settle["median_days"] - contracted
        if abs(d) >= 1:
            drift = (f"Observed settlement lag has a median of "
                     f"{settle['median_days']:.0f} days against a contracted "
                     f"T+{contracted}. The forecast follows observed behaviour, "
                     f"not the contract.")
    return {"settlement_lag": settle, "credit_lag": credit,
            "contracted_cycle_days": contracted, "drift_note": drift}


def forecast_cash(orders, recon_rows, settlements, bank_rows, audit,
                  horizon_days=14, as_of=None, gst_rate=0.18):
    """Project net cash landing per day over the horizon.

    Returns a forecast report.
    """
    stage = "stage4_forecast"
    behaviour = learn_settlement_behaviour(recon_rows, settlements, bank_rows)

    settle_median = behaviour["settlement_lag"]["median_days"]
    settle_p90 = behaviour["settlement_lag"]["p90_days"]
    credit_median = behaviour["credit_lag"]["median_days"]
    credit_p90 = behaviour["credit_lag"]["p90_days"]

    total_lag_median = settle_median + credit_median
    total_lag_p90 = settle_p90 + credit_p90

    # anchor: the latest thing that happened in the data
    all_dates = [_dt(o["created_at"]) for o in orders if _dt(o["created_at"])]
    as_of = as_of or (max(all_dates) if all_dates else datetime.now())

    settled_order_ids = {r.get("order_id") for r in recon_rows
                         if r.get("type") == "payment" and r.get("settlement_id")}
    onhold_rows = [r for r in recon_rows
                   if str(r.get("on_hold", "")).lower() == "true"]

    # ---- 1. in-flight orders: captured, not yet in any settlement --------
    inflight = []
    for o in orders:
        if o["order_id"] in settled_order_ids:
            continue
        gross = _i(o["amount"])
        method = o.get("method", "card")
        rate = cfg.MDR_RATES.get(method, 0.02)
        fee = int(round(gross * rate))
        tax = int(round(fee * gst_rate))
        net = gross - fee - tax
        captured = _dt(o["created_at"])
        if not captured:
            continue
        inflight.append({
            "order_id": o["order_id"], "gross": gross, "fee": fee, "tax": tax,
            "net": net, "method": method,
            "captured": captured,
            "expected_date": (captured + timedelta(days=total_lag_median)).date(),
            "late_date": (captured + timedelta(days=total_lag_p90)).date(),
        })

    # ---- 2. settlements created but not yet credited --------------------
    credited_amounts = defaultdict(int)
    for b in bank_rows:
        credited_amounts[_i(b["amount"])] += 1
    awaiting = []
    for s in settlements:
        amt = _i(s["amount"])
        if credited_amounts.get(amt, 0) > 0:
            credited_amounts[amt] -= 1
            continue
        created = _dt(s["created_at"])
        if not created:
            continue
        awaiting.append({
            "settlement_id": s["id"], "net": amt, "created": created,
            "expected_date": (created + timedelta(days=credit_median)).date(),
            "late_date": (created + timedelta(days=credit_p90)).date(),
            "days_outstanding": (as_of.date() - created.date()).days,
        })

    # ---- 3. build the daily projection ----------------------------------
    daily = defaultdict(lambda: {"expected": 0, "late_case": 0,
                                 "sources": [], "count": 0})
    for f in inflight:
        daily[f["expected_date"]]["expected"] += f["net"]
        daily[f["expected_date"]]["count"] += 1
        daily[f["late_date"]]["late_case"] += f["net"]
        if len(daily[f["expected_date"]]["sources"]) < 4:
            daily[f["expected_date"]]["sources"].append(
                f"order {f['order_id']} ({cfg.rupees(f['net'])} net)")
    for a in awaiting:
        daily[a["expected_date"]]["expected"] += a["net"]
        daily[a["expected_date"]]["count"] += 1
        daily[a["late_date"]]["late_case"] += a["net"]
        if len(daily[a["expected_date"]]["sources"]) < 4:
            daily[a["expected_date"]]["sources"].append(
                f"settlement {a['settlement_id']} ({cfg.rupees(a['net'])})")

    start = as_of.date()
    timeline = []
    running = 0
    for d in range(horizon_days + 1):
        day = start + timedelta(days=d)
        row = daily.get(day, {"expected": 0, "late_case": 0, "sources": [], "count": 0})
        running += row["expected"]
        timeline.append({
            "date": day.isoformat(),
            "weekday": day.strftime("%a"),
            "expected_paise": row["expected"],
            "expected": cfg.rupees(row["expected"]),
            "late_case_paise": row["late_case"],
            "cumulative_paise": running,
            "cumulative": cfg.rupees(running),
            "item_count": row["count"],
            "sources": row["sources"],
        })

    # ---- 4. confidence band ---------------------------------------------
    spread = (behaviour["settlement_lag"].get("stdev_days", 0) +
              behaviour["credit_lag"].get("stdev_days", 0))
    if behaviour["settlement_lag"]["observations"] < 5:
        band, band_reason = "wide", (
            f"only {behaviour['settlement_lag']['observations']} settlement "
            f"observations available; not enough history to narrow the band")
    elif spread <= 0.5:
        band, band_reason = "tight", (
            f"settlement timing is highly consistent (combined standard "
            f"deviation {spread:.2f} days), so the projected dates are reliable")
    elif spread <= 1.5:
        band, band_reason = "moderate", (
            f"settlement timing varies by roughly {spread:.2f} days; expect "
            f"projected dates to move by a day either way")
    else:
        band, band_reason = "wide", (
            f"settlement timing is inconsistent (combined standard deviation "
            f"{spread:.2f} days), so treat individual dates as indicative and "
            f"the horizon total as the reliable figure")

    # ---- 5. at-risk amounts ---------------------------------------------
    at_risk = []
    onhold_total = sum(_i(r.get("amount")) for r in onhold_rows)
    if onhold_total:
        at_risk.append({
            "category": "ON_HOLD",
            "amount_paise": onhold_total,
            "amount": cfg.rupees(onhold_total),
            "count": len(onhold_rows),
            "note": (f"{len(onhold_rows)} transaction(s) worth "
                     f"{cfg.rupees(onhold_total)} are flagged on hold as a risk "
                     f"reserve. This money exists but is not scheduled to land, "
                     f"so it is excluded from the forecast rather than counted "
                     f"as delayed."),
        })

    overdue = [a for a in awaiting
               if a["days_outstanding"] > credit_p90 + 1]
    if overdue:
        tot = sum(a["net"] for a in overdue)
        at_risk.append({
            "category": "CREDIT_OVERDUE",
            "amount_paise": tot,
            "amount": cfg.rupees(tot),
            "count": len(overdue),
            "note": (f"{len(overdue)} settlement(s) worth {cfg.rupees(tot)} were "
                     f"created more than {credit_p90 + 1} days ago with no "
                     f"matching bank credit. Follow these up before assuming "
                     f"the cash is merely late."),
        })

    horizon_total = sum(t["expected_paise"] for t in timeline)
    late_total = sum(t["late_case_paise"] for t in timeline)

    reason = (
        f"Projected {cfg.rupees(horizon_total)} landing over the next "
        f"{horizon_days} days from {len(inflight)} in-flight order(s) and "
        f"{len(awaiting)} settlement(s) awaiting credit. "
        f"Lag learned from history: {settle_median:.0f} days to settle plus "
        f"{credit_median:.0f} days to credit. Confidence band: {band} — {band_reason}."
    )

    audit.record(stage, "forecast", f"horizon_{horizon_days}d", "MATCHED", 
                 {"tight": 0.90, "moderate": 0.75, "wide": 0.55}[band],
                 "forward_cash_projection", reason,
                 amount_subject=horizon_total,
                 evidence={"inflight_orders": len(inflight),
                           "awaiting_credit": len(awaiting),
                           "settlement_lag_median": settle_median,
                           "credit_lag_median": credit_median,
                           "confidence_band": band,
                           "as_of": as_of.isoformat()})

    for a in at_risk:
        audit.record(stage, "forecast_risk", a["category"], "EXCEPTION", 0.85,
                     "cash_at_risk", a["note"],
                     variance_code=("ON_HOLD" if a["category"] == "ON_HOLD"
                                    else "UNEXPLAINED"),
                     amount_subject=a["amount_paise"],
                     evidence={"count": a["count"]})

    audit.flush()

    return {
        "as_of": as_of.date().isoformat(),
        "horizon_days": horizon_days,
        "behaviour": behaviour,
        "confidence_band": band,
        "confidence_reason": band_reason,
        "expected_total_paise": horizon_total,
        "expected_total": cfg.rupees(horizon_total),
        "late_case_total": cfg.rupees(late_total),
        "inflight_count": len(inflight),
        "inflight_net_paise": sum(f["net"] for f in inflight),
        "inflight_net": cfg.rupees(sum(f["net"] for f in inflight)),
        "awaiting_credit_count": len(awaiting),
        "awaiting_credit_paise": sum(a["net"] for a in awaiting),
        "awaiting_credit": cfg.rupees(sum(a["net"] for a in awaiting)),
        "timeline": timeline,
        "at_risk": at_risk,
        "inflight_detail": [
            {**f, "captured": f["captured"].isoformat(),
             "expected_date": f["expected_date"].isoformat(),
             "late_date": f["late_date"].isoformat(),
             "net_display": cfg.rupees(f["net"])}
            for f in sorted(inflight, key=lambda x: -x["net"])[:20]
        ],
        "awaiting_detail": [
            {**a, "created": a["created"].isoformat(),
             "expected_date": a["expected_date"].isoformat(),
             "late_date": a["late_date"].isoformat(),
             "net_display": cfg.rupees(a["net"])}
            for a in sorted(awaiting, key=lambda x: -x["net"])[:20]
        ],
    }
