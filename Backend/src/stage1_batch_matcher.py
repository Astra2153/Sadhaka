"""
Sadhaka — Stage 1: Bank Credit <-> Settlement Batch Matcher
============================================================
The bank only ever sees a lumped NEFT credit. It has no idea which orders,
fees or refunds make up that number. This stage answers one question:

    "Which Razorpay settlement batch is this bank credit?"

WHY NOT JUST MATCH ON UTR
-------------------------
The UTR is issued by the correspondent bank, not by Razorpay. In practice it
drifts: case changes, truncation, extra padding, or the bank recording a
slightly different reference in the narration. Two real failure modes follow:

  * exact UTR matching silently DROPS legitimate matches;
  * fuzzy UTR matching silently CONFLATES two unrelated settlements whose
    references happen to look alike.

So UTR is used as CORROBORATING evidence, never as the primary key. The
primary key is (net amount, date window). UTR agreement raises confidence;
UTR disagreement lowers it but does not veto a match that is otherwise sound.

DELIBERATE REFUSAL TO GUESS
---------------------------
When two settlements have the same net amount inside the same date window and
no UTR evidence separates them, the engine does NOT pick one. It emits
DUPLICATE_CANDIDATE for human review. A 50/50 coin flip on money is worse than
an honest exception.
"""

from datetime import datetime, timedelta
import config as cfg


def _parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def normalise_utr(u):
    """Strip the cosmetic differences banks introduce, without pretending two
    genuinely different references are the same."""
    if not u:
        return ""
    return "".join(ch for ch in str(u).lower() if ch.isalnum())


def utr_relationship(bank_ref, settlement_utr):
    """Classify how a bank reference relates to a settlement UTR.

    Returns one of: exact | normalised | prefix | unrelated | absent
    'prefix' covers the very common truncation case, and is treated as weaker
    evidence than a full normalised match.
    """
    if not bank_ref or not settlement_utr:
        return "absent"
    if bank_ref == settlement_utr:
        return "exact"
    a, b = normalise_utr(bank_ref), normalise_utr(settlement_utr)
    if a == b:
        return "normalised"
    if a and b and (a.startswith(b) or b.startswith(a)):
        # One is a truncation of the other. Require a meaningful overlap so
        # two short references don't trivially "prefix match".
        if min(len(a), len(b)) >= 8:
            return "prefix"
    return "unrelated"


def match_batches(bank_rows, settlements, audit, tolerances=None):
    """Match every bank credit to a settlement batch.

    Returns (matches, exceptions, unmatched_settlements).
    """
    tol = tolerances or cfg.TOLERANCES
    stage = "stage1_bank_batch"

    remaining = {s["id"]: s for s in settlements}
    matches, exceptions = [], []

    for b in bank_rows:
        bank_amt = int(b["amount"])
        bank_dt = _parse_dt(b.get("credit_datetime") or b.get("value_date"))
        bank_ref = (b.get("reference") or "").strip()

        # ---- candidate generation -------------------------------------
        # Primary key: net amount (exact, or within rounding tolerance).
        # Date window filters, it does not select.
        candidates = []
        for s in remaining.values():
            s_amt = int(s["amount"])
            s_dt = _parse_dt(s["created_at"])
            diff = bank_amt - s_amt

            if abs(diff) > tol.rounding_paise:
                continue

            days_apart = None
            if bank_dt and s_dt:
                days_apart = (bank_dt.date() - s_dt.date()).days
                # A credit before the settlement was even created is impossible
                if days_apart < 0:
                    continue

            candidates.append({
                "settlement": s,
                "amount_diff": diff,
                "days_apart": days_apart,
                "utr_rel": utr_relationship(bank_ref, s.get("utr", "")),
            })

        if not candidates:
            exceptions.append({
                "subject_type": "bank_txn", "subject_id": b["bank_txn_id"],
                "variance_code": "UNEXPLAINED", "confidence": 0.0,
                "amount": bank_amt,
                "reason": (f"Bank credit of {cfg.rupees(bank_amt)} on "
                           f"{b.get('value_date')} has no settlement batch with a "
                           f"comparable net amount (searched all {len(remaining)} "
                           f"open batches, tolerance {cfg.rupees(tol.rounding_paise)})."),
            })
            audit.record(stage, "bank_txn", b["bank_txn_id"], "EXCEPTION", 0.0,
                         "no_candidate_by_amount", exceptions[-1]["reason"],
                         variance_code="UNEXPLAINED", amount_subject=bank_amt,
                         evidence={"bank_reference": bank_ref,
                                   "open_batches": len(remaining),
                                   "amount_tolerance_paise": tol.rounding_paise})
            continue

        # ---- scoring ---------------------------------------------------
        for c in candidates:
            c["confidence"], c["rule"], c["notes"] = _score(c, tol)

        candidates.sort(key=lambda c: (-c["confidence"],
                                       abs(c["amount_diff"]),
                                       abs(c["days_apart"] or 99)))
        best = candidates[0]

        # ---- refuse to guess between equally-plausible candidates ------
        tied = [c for c in candidates
                if abs(c["confidence"] - best["confidence"]) < 1e-9]
        if len(tied) > 1:
            ids = [c["settlement"]["id"] for c in tied]
            reason = (f"Bank credit of {cfg.rupees(bank_amt)} matches "
                      f"{len(tied)} settlement batches equally well ({', '.join(ids)}). "
                      f"No UTR evidence separates them. Refusing to guess — "
                      f"a wrong match here misstates which batch is reconciled.")
            exceptions.append({
                "subject_type": "bank_txn", "subject_id": b["bank_txn_id"],
                "variance_code": "DUPLICATE_CANDIDATE",
                "confidence": best["confidence"], "amount": bank_amt,
                "reason": reason,
            })
            audit.record(stage, "bank_txn", b["bank_txn_id"], "EXCEPTION",
                         best["confidence"], "ambiguous_candidates", reason,
                         variance_code="DUPLICATE_CANDIDATE",
                         amount_subject=bank_amt,
                         evidence={"tied_settlement_ids": ids,
                                   "bank_reference": bank_ref})
            continue

        s = best["settlement"]

        if best["confidence"] < cfg.AUTO_ACCEPT_THRESHOLD:
            reason = (f"Best candidate {s['id']} scored "
                      f"{best['confidence']:.2f}, below the auto-accept threshold "
                      f"of {cfg.AUTO_ACCEPT_THRESHOLD:.2f}. {best['notes']} "
                      f"Held for review rather than auto-matched.")
            exceptions.append({
                "subject_type": "bank_txn", "subject_id": b["bank_txn_id"],
                "variance_code": "UNEXPLAINED",
                "confidence": best["confidence"], "amount": bank_amt,
                "reason": reason,
            })
            audit.record(stage, "bank_txn", b["bank_txn_id"], "EXCEPTION",
                         best["confidence"], "below_auto_accept_threshold", reason,
                         counterpart_type="settlement", counterpart_id=s["id"],
                         variance_code="UNEXPLAINED", amount_subject=bank_amt,
                         amount_counterpart=int(s["amount"]),
                         variance_paise=best["amount_diff"],
                         evidence={"utr_relationship": best["utr_rel"],
                                   "days_apart": best["days_apart"]})
            continue

        # ---- accepted --------------------------------------------------
        variance_code = None
        if best["amount_diff"] != 0:
            variance_code = "ROUNDING"
        elif best["days_apart"] is not None and best["days_apart"] > tol.date_window_days:
            variance_code = "TIMING_LAG"
        elif best["utr_rel"] in ("prefix", "unrelated"):
            variance_code = "TIMING_LAG"   # benign reference drift

        reason = (f"Bank credit {cfg.rupees(bank_amt)} matched to settlement "
                  f"{s['id']}. {best['notes']}")

        matches.append({
            "bank_txn_id": b["bank_txn_id"],
            "settlement_id": s["id"],
            "amount": bank_amt,
            "confidence": best["confidence"],
            "variance_code": variance_code,
            "amount_diff": best["amount_diff"],
            "days_apart": best["days_apart"],
            "utr_relationship": best["utr_rel"],
            "reason": reason,
        })

        # A match that carries a variance code is still an OBSERVATION the
        # finance team should see. Earlier this was recorded only on the match
        # object, so a correctly-matched-but-late credit never appeared in the
        # exception report at all — the report looked cleaner than reality.
        if variance_code:
            if best["amount_diff"] != 0:
                obs = (f"Matched, with a {cfg.rupees(best['amount_diff'])} "
                       f"difference absorbed as rounding.")
            elif best["days_apart"] is not None and best["days_apart"] > tol.date_window_days:
                obs = (f"Matched, but the credit landed {best['days_apart']} days "
                       f"after the settlement was created, outside the expected "
                       f"{tol.date_window_days}-day window. Amount agrees exactly, "
                       f"so this is a bank-side delay, not missing money.")
            else:
                obs = (f"Matched on amount and date, but the bank reference "
                       f"'{bank_ref}' does not exactly correspond to the "
                       f"settlement UTR '{s.get('utr','')}' "
                       f"({best['utr_rel']}). Reference drift of this kind is "
                       f"routine; the match itself is sound.")
            exceptions.append({
                "subject_type": "bank_txn", "subject_id": b["bank_txn_id"],
                "counterpart_id": s["id"],
                "variance_code": variance_code,
                "confidence": best["confidence"],
                "amount": bank_amt,
                "reason": f"{obs} (settlement {s['id']})",
            })
        audit.record(stage, "bank_txn", b["bank_txn_id"], "MATCHED",
                     best["confidence"], best["rule"], reason,
                     counterpart_type="settlement", counterpart_id=s["id"],
                     variance_code=variance_code,
                     amount_subject=bank_amt, amount_counterpart=int(s["amount"]),
                     variance_paise=best["amount_diff"],
                     evidence={"utr_relationship": best["utr_rel"],
                               "days_apart": best["days_apart"],
                               "bank_reference": bank_ref,
                               "settlement_utr": s.get("utr", "")})
        del remaining[s["id"]]

    # ---- settlements with no bank credit at all ------------------------
    unmatched_settlements = []
    for s in remaining.values():
        reason = (f"Settlement {s['id']} for {cfg.rupees(int(s['amount']))} "
                  f"created {s['created_at']} has no corresponding bank credit. "
                  f"Either the payout has not landed yet, or it landed with an "
                  f"amount outside the {cfg.rupees(tol.rounding_paise)} tolerance.")
        unmatched_settlements.append({
            "settlement_id": s["id"], "amount": int(s["amount"]), "reason": reason,
        })
        audit.record(stage, "settlement", s["id"], "UNMATCHED", 0.0,
                     "no_bank_credit_found", reason,
                     variance_code="UNEXPLAINED", amount_subject=int(s["amount"]),
                     evidence={"settlement_utr": s.get("utr", ""),
                               "created_at": s["created_at"]})

    audit.flush()
    return matches, exceptions, unmatched_settlements


def _score(candidate, tol):
    """Confidence, the rule that produced it, and a human explanation.

    Scores are deliberately conservative. An exact amount match with no UTR
    corroboration is NOT treated as near-certain, because amounts collide.
    """
    diff = candidate["amount_diff"]
    days = candidate["days_apart"]
    rel = candidate["utr_rel"]
    exact_amt = (diff == 0)
    in_window = (days is not None and 0 <= days <= tol.date_window_days)

    if exact_amt and in_window and rel in ("exact", "normalised"):
        return (cfg.CONFIDENCE["exact_utr_and_amount_and_date"],
                "amount_date_utr_all_agree",
                f"Amount, date (+{days}d) and UTR all agree.")

    if exact_amt and in_window and rel == "prefix":
        return (0.88, "amount_date_agree_utr_truncated",
                f"Amount and date (+{days}d) agree; the bank reference is a "
                f"truncation of the settlement UTR.")

    if exact_amt and in_window:
        return (cfg.CONFIDENCE["exact_amount_and_date_fuzzy_utr"],
                "amount_date_agree_utr_differs",
                f"Amount and date (+{days}d) agree exactly, but the bank "
                f"reference does not correspond to the settlement UTR "
                f"({rel}). Matched on amount and date.")

    if exact_amt and not in_window:
        return (cfg.CONFIDENCE["exact_amount_date_outside_window"],
                "amount_agrees_date_outside_window",
                f"Amount agrees exactly but the credit landed {days} days "
                f"after the settlement, outside the expected "
                f"{tol.date_window_days}-day window.")

    if abs(diff) <= tol.rounding_paise and in_window:
        return (cfg.CONFIDENCE["amount_within_rounding_tolerance"],
                "amount_within_rounding_tolerance",
                f"Amount differs by {cfg.rupees(diff)}, within the "
                f"{cfg.rupees(tol.rounding_paise)} rounding tolerance; "
                f"date (+{days}d) agrees.")

    return (cfg.CONFIDENCE["amount_only_multiple_candidates"],
            "weak_amount_only",
            f"Only a weak amount correspondence (differs by {cfg.rupees(diff)}).")
