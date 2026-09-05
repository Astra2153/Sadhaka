"""
Sadhaka — Exception Reporting & Metrics
========================================
Two jobs:

1. Turn raw exceptions into a report a finance person can act on, sorted by
   what actually needs a human, not by what happens to be alphabetically first.

2. Report honest metrics — including the ones that make the engine look worse.

ON HONEST METRICS
-----------------
A single "match rate: 97%" is close to meaningless, because it depends entirely
on what you count. This module reports several rates and says what each one
counts, so a reviewer can pick the one they trust:

  * batch_match_rate    — bank credits matched to settlement batches
  * order_match_rate    — settled transactions verified clean, of all settled
  * value_match_rate    — the same thing weighted by rupees, which is the number
                          that actually matters when one large exception hides
                          behind ninety small clean rows
  * exception_rate      — split into benign vs actionable, because reporting
                          "18 exceptions" when 14 are timing lags is alarmism

It also scores the engine against the generator's answer key, including
recall on deliberately planted faults. Missing a planted fault is reported as
a miss, not quietly omitted.
"""

import json
import config as cfg


def build_exception_report(all_exceptions):
    """Group, rank and explain. Benign and actionable are kept separate."""
    benign, actionable = [], []
    for e in all_exceptions:
        code = e.get("variance_code") or "UNEXPLAINED"
        entry = dict(e)
        entry["code_meaning"] = cfg.VARIANCE_CODES.get(code, "Unclassified.")
        entry["is_benign"] = code in cfg.BENIGN_CODES
        (benign if entry["is_benign"] else actionable).append(entry)

    def money(e):
        return abs(e.get("variance_paise") or e.get("amount") or 0)

    # Actionable ranked by money at stake, then by lowest confidence
    actionable.sort(key=lambda e: (-money(e), e.get("confidence", 0)))
    benign.sort(key=lambda e: -money(e))

    by_code = {}
    for e in all_exceptions:
        code = e.get("variance_code") or "UNEXPLAINED"
        b = by_code.setdefault(code, {
            "code": code,
            "meaning": cfg.VARIANCE_CODES.get(code, "Unclassified."),
            "benign": code in cfg.BENIGN_CODES,
            "count": 0, "value_paise": 0, "examples": [],
        })
        b["count"] += 1
        b["value_paise"] += money(e)
        if len(b["examples"]) < 3:
            b["examples"].append({
                "subject_id": e.get("subject_id"),
                "order_id": e.get("order_id"),
                "reason": e.get("reason"),
            })

    return {
        "total": len(all_exceptions),
        "actionable_count": len(actionable),
        "benign_count": len(benign),
        "actionable_value_paise": sum(money(e) for e in actionable),
        "benign_value_paise": sum(money(e) for e in benign),
        "by_code": sorted(by_code.values(), key=lambda b: (-b["value_paise"], b["code"])),
        "actionable": actionable,
        "benign": benign,
    }


def compute_metrics(bank_rows, settlements, recon_rows, orders,
                    batch_matches, batch_exceptions, unmatched_settlements,
                    order_matches, order_exceptions, gst_report, exc_report):
    """Every rate is reported with its denominator stated."""

    def _i(v):
        return 0 if v in (None, "") else int(float(v))

    settled_payments = [r for r in recon_rows
                        if r.get("type") == "payment"
                        and str(r.get("on_hold", "")).lower() != "true"]

    total_bank = len(bank_rows)
    total_settled = len(settled_payments)

    batch_rate = (len(batch_matches) / total_bank * 100) if total_bank else 0.0
    order_rate = (len(order_matches) / total_settled * 100) if total_settled else 0.0

    matched_value = sum(m["amount"] for m in order_matches)
    settled_value = sum(_i(r.get("amount")) for r in settled_payments)
    value_rate = (matched_value / settled_value * 100) if settled_value else 0.0

    bank_value = sum(_i(b["amount"]) for b in bank_rows)
    matched_bank_value = sum(m["amount"] for m in batch_matches)
    bank_value_rate = (matched_bank_value / bank_value * 100) if bank_value else 0.0

    confidences = [m["confidence"] for m in batch_matches] + \
                  [m["confidence"] for m in order_matches]
    buckets = {"0.95-1.00": 0, "0.85-0.95": 0, "0.65-0.85": 0, "below-0.65": 0}
    for c in confidences:
        if c >= 0.95:
            buckets["0.95-1.00"] += 1
        elif c >= 0.85:
            buckets["0.85-0.95"] += 1
        elif c >= 0.65:
            buckets["0.65-0.85"] += 1
        else:
            buckets["below-0.65"] += 1

    return {
        "throughput": {
            "bank_credits": total_bank,
            "settlement_batches": len(settlements),
            "recon_rows": len(recon_rows),
            "settled_transactions": total_settled,
            "orders": len(orders),
            "total_records_processed": len(recon_rows) + len(bank_rows) + len(orders),
        },
        "match_rates": {
            "batch_match_rate_pct": round(batch_rate, 2),
            "batch_match_denominator": f"{len(batch_matches)} of {total_bank} bank credits",
            "order_match_rate_pct": round(order_rate, 2),
            "order_match_denominator": f"{len(order_matches)} of {total_settled} settled transactions verified with no variance",
            "value_match_rate_pct": round(value_rate, 2),
            "value_match_denominator": f"{cfg.rupees(matched_value)} of {cfg.rupees(settled_value)} settled value",
            "bank_value_match_rate_pct": round(bank_value_rate, 2),
            "bank_value_denominator": f"{cfg.rupees(matched_bank_value)} of {cfg.rupees(bank_value)} banked",
        },
        "exceptions": {
            "total": exc_report["total"],
            "actionable": exc_report["actionable_count"],
            "benign": exc_report["benign_count"],
            "actionable_value": cfg.rupees(exc_report["actionable_value_paise"]),
            "benign_value": cfg.rupees(exc_report["benign_value_paise"]),
            "unresolved_settlements": len(unmatched_settlements),
        },
        "confidence_distribution": buckets,
        "money": {
            "total_banked": cfg.rupees(bank_value),
            "total_settled_gross": cfg.rupees(settled_value),
            "total_fees_charged": cfg.rupees(gst_report["settled_fee_total"]),
            "total_gst_on_fees": cfg.rupees(gst_report["settled_tax_total"]),
            "gst_understated": cfg.rupees(gst_report["gst_understated"]),
            "itc_claimable": cfg.rupees(gst_report["total_itc_claimable"]),
            "itc_blocked": cfg.rupees(gst_report["total_itc_blocked"]),
        },
    }


# Cases where the CORRECT behaviour is to avoid a wrong match rather than to
# raise an exception. Scoring these as "detected/missed" is a category error:
# a trap is passed by NOT falling into it.
TRAP_CASES = {
    "EC7":  ("SPLIT_SETTLEMENT",
             "both halves of the split must match independently and correctly"),
    "EC10": ("NEAR_DUPLICATE_UTR",
             "the two lookalike UTRs must NOT be conflated into one match"),
}


def score_traps(key, batch_matches, order_matches, all_exceptions):
    """Evaluate trap cases: did the engine avoid the wrong answer?"""
    results = []
    matches_by_settlement = {m["settlement_id"]: m for m in batch_matches}
    matched_bank_ids = {m["bank_txn_id"]: m for m in batch_matches}

    for ec in key.get("edge_cases", []):
        if ec["id"] not in TRAP_CASES:
            continue
        _, criterion = TRAP_CASES[ec["id"]]

        if ec["id"] == "EC7":
            sids = ec.get("settlement_ids", [])
            hit = [s for s in sids if s in matches_by_settlement]
            passed = len(hit) == len(sids) and len(sids) > 0
            detail = (f"both split batches ({', '.join(sids)}) matched "
                      f"independently to their own bank credits"
                      if passed else
                      f"only {len(hit)} of {len(sids)} split batches matched")

        elif ec["id"] == "EC10":
            bids = ec.get("bank_txn_ids", [])
            targets = [matched_bank_ids.get(b) for b in bids]
            got = [t for t in targets if t]
            distinct = len({t["settlement_id"] for t in got}) == len(got)
            passed = len(got) == len(bids) and distinct
            if passed:
                detail = (f"both lookalike-UTR credits matched, to {len(got)} "
                          f"DISTINCT settlements — not conflated")
            elif not distinct:
                detail = "the two lookalike UTRs were conflated onto one settlement"
            else:
                detail = f"only {len(got)} of {len(bids)} credits matched"
        else:
            passed, detail = False, "no trap evaluator defined"

        results.append({
            "id": ec["id"], "type": ec["type"], "kind": "trap",
            "criterion": criterion, "passed": passed, "detail": detail,
            "description": ec["description"],
        })
    return results


def score_against_answer_key(answer_key_path, all_exceptions,
                             batch_matches=None, order_matches=None):
    """Did the engine catch the planted faults, and avoid the planted traps?

    Faults and traps are scored separately, because they are different claims:
      * a fault is caught by RAISING the right exception;
      * a trap is passed by NOT producing a wrong match.
    Conflating them would let the engine look good for the wrong reason.
    """
    with open(answer_key_path) as f:
        key = json.load(f)

    flagged_subjects = set()
    flagged_by_code = {}
    for e in all_exceptions:
        code = e.get("variance_code")
        for k in ("subject_id", "order_id"):
            v = e.get(k)
            if v:
                flagged_subjects.add(str(v))
                flagged_by_code.setdefault(str(v), set()).add(code)

    results = []
    caught = 0
    for ec in key.get("edge_cases", []):
        if ec["id"] in TRAP_CASES:
            continue          # scored separately by score_traps
        targets = []
        for field in ("order_ids", "settlement_ids", "bank_txn_ids"):
            targets += [str(x) for x in ec.get(field, [])]
        for field in ("bank_txn_id",):
            if ec.get(field):
                targets.append(str(ec[field]))

        if not targets:
            # global cases (e.g. the GST invoice drift) are matched by code
            hit = any(e.get("variance_code") == ec["code"] for e in all_exceptions)
            detail = ("matched by variance code across the run"
                      if hit else "no exception carried this code")
        else:
            hit_targets = [t for t in targets if t in flagged_subjects]
            hit = len(hit_targets) > 0
            detail = (f"{len(hit_targets)} of {len(targets)} target entities flagged"
                      if hit else f"none of {len(targets)} target entities were flagged")

        code_ok = True
        if targets and hit:
            codes = set()
            for t in targets:
                codes |= flagged_by_code.get(t, set())
            code_ok = ec["code"] in codes
            if not code_ok:
                detail += f"; flagged as {sorted(c for c in codes if c)} rather than {ec['code']}"

        if hit:
            caught += 1
        results.append({
            "id": ec["id"], "type": ec["type"], "expected_code": ec["code"],
            "detected": hit, "correct_code": code_ok, "detail": detail,
            "description": ec["description"],
        })

    total = len(results)
    traps = score_traps(key, batch_matches or [], order_matches or [],
                        all_exceptions)
    traps_passed = sum(1 for t in traps if t["passed"])

    return {
        "planted_faults": total,
        "detected": caught,
        "recall_pct": round(100 * caught / total, 1) if total else 0.0,
        "code_accuracy_pct": round(
            100 * sum(1 for r in results if r["detected"] and r["correct_code"])
            / caught, 1) if caught else 0.0,
        "planted_traps": len(traps),
        "traps_passed": traps_passed,
        "trap_pass_pct": round(100 * traps_passed / len(traps), 1) if traps else 0.0,
        "cases": results,
        "traps": traps,
    }
