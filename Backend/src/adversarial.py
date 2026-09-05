"""
Sadhaka — Adversarial Fault Injection
======================================
Eleven hand-planted faults prove that the engine catches eleven hand-planted
faults. That is a demonstration, not evidence.

This module attacks the engine programmatically. It takes a clean dataset,
injects exactly ONE fault of a chosen type at a chosen magnitude, runs the
engine, and asks whether the engine noticed. Repeated across thousands of
trials it answers questions a hand-written answer key cannot:

  * What is the SMALLEST fault of each type the engine reliably detects?
  * Does the engine's confidence score mean anything?
  * Which fault types does it systematically miss?

DESIGN RULES
------------
1. One fault per trial. If two are injected and the engine raises one
   exception, you cannot tell which one it caught.

2. The clean baseline is measured too. A trial with NO fault injected must
   produce no new exception. Otherwise the "detections" could just be the
   engine's normal noise, and the whole measurement is worthless.

3. Detection means the engine flagged THE INJECTED ENTITY, not merely that it
   raised some exception somewhere. Counting any exception as a hit would
   reward an engine that flags everything.

4. Faults are drawn from how money actually goes missing in payments —
   skimming a few paise off a payout, inflating an MDR rate, understating GST,
   a credit that never arrives — not from arbitrary data corruption.
"""

import copy
import random
from datetime import datetime, timedelta

import config as cfg


class NullAudit:
    """Audit sink that discards everything.

    The harness runs the engine tens of thousands of times. Writing every
    decision to SQLite would dominate the runtime and produce a database
    nobody reads. The engine's interface is unchanged; only the destination is.
    """
    run_id = "harness"

    def record(self, *a, **k):
        pass

    def flush(self):
        pass

    def set_metric(self, *a, **k):
        pass

    def finish(self):
        pass

    def close(self):
        pass


def _i(v):
    return 0 if v in (None, "") else int(float(v))


def _excluding(rows, key, exclude_ids):
    """Filter out rows whose key is already in exclude_ids.

    Without this, an injector can choose a target that clean, untampered data
    already flags for an unrelated reason (e.g. a settlement that ships with
    a built-in UTR-drift edge case). The baseline-subtraction scoring then
    can't tell "already flagged" from "correctly detected", and a genuine
    detection gets scored as a miss — this is what produced the two false
    blind spots (DATE_SHIFT, UTR_CORRUPT) in the first --thorough run.
    """
    if not exclude_ids:
        return rows
    return [r for r in rows if r.get(key) not in exclude_ids]


def _dt(s):
    for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, f)
        except (ValueError, TypeError):
            continue
    return None


# ===========================================================================
# Fault injectors
#
# Each returns a `truth` dict describing what was done, including which entity
# ids the engine must flag, and which variance code would be the correct
# classification. Returning None means the fault could not be applied to this
# particular dataset (e.g. no card transactions present) and the trial is
# skipped rather than counted as a miss.
# ===========================================================================

def inject_fee_overcharge(data, magnitude_paise, rng, exclude_ids=None):
    """The gateway quietly charges more MDR than contracted.

    Magnitude is the rupee overcharge on a single transaction. This is the
    classic silent leak: 0.05% extra on every card transaction is invisible
    per row and material per quarter.
    """
    rows = [r for r in data["recon"]
            if r["type"] == "payment" and _i(r["fee"]) > 0
            and r["on_hold"] != "true"]
    rows = _excluding(rows, "payment_id", exclude_ids)
    if not rows:
        return None
    row = rng.choice(rows)
    row["fee"] = str(_i(row["fee"]) + magnitude_paise)
    row["credit"] = str(_i(row["credit"]) - magnitude_paise)
    _rebalance_settlement(data, row["settlement_id"], -magnitude_paise)
    return {
        "type": "FEE_OVERCHARGE",
        "magnitude_paise": magnitude_paise,
        "target_ids": [row["payment_id"], row["order_id"]],
        "expected_code": "FEE_DEDUCTION",
        "description": (f"MDR on {row['payment_id']} inflated by "
                        f"{cfg.rupees(magnitude_paise)} above the contracted rate"),
    }


def inject_gst_understate(data, magnitude_paise, rng, exclude_ids=None):
    """GST on the fee is understated.

    Directly reduces the merchant's claimable input tax credit, so it is a real
    cash loss even though no settlement amount looks wrong.
    """
    rows = [r for r in data["recon"]
            if r["type"] == "payment" and _i(r["tax"]) > magnitude_paise
            and r["on_hold"] != "true"]
    rows = _excluding(rows, "payment_id", exclude_ids)
    if not rows:
        return None
    row = rng.choice(rows)
    row["tax"] = str(_i(row["tax"]) - magnitude_paise)
    row["credit"] = str(_i(row["credit"]) + magnitude_paise)
    _rebalance_settlement(data, row["settlement_id"], magnitude_paise)
    return {
        "type": "GST_UNDERSTATE",
        "magnitude_paise": magnitude_paise,
        "target_ids": [row["payment_id"], row["order_id"]],
        "expected_code": "TAX_DEDUCTION",
        "description": (f"GST on {row['payment_id']} understated by "
                        f"{cfg.rupees(magnitude_paise)}, reducing claimable ITC"),
    }


def inject_payout_skim(data, magnitude_paise, rng, exclude_ids=None):
    """Money is skimmed from the bank credit itself.

    The settlement report says one number; the bank received less. This is the
    fault that matters most, because it is theft rather than a fee dispute.
    """
    candidates = _excluding(data["bank"], "bank_txn_id", exclude_ids)
    if not candidates:
        return None
    b = rng.choice(candidates)
    b["amount"] = str(_i(b["amount"]) - magnitude_paise)
    return {
        "type": "PAYOUT_SKIM",
        "magnitude_paise": magnitude_paise,
        "target_ids": [b["bank_txn_id"], data["truth"]["bank_to_settlement"].get(b["bank_txn_id"], "")],
        "expected_code": None,          # any exception on this entity counts
        "description": (f"Bank credit {b['bank_txn_id']} short by "
                        f"{cfg.rupees(magnitude_paise)} against the settlement"),
    }


def inject_order_tamper(data, magnitude_paise, rng, exclude_ids=None):
    """The settled gross does not match the merchant's own order value."""
    rows = [r for r in data["recon"]
            if r["type"] == "payment" and r["on_hold"] != "true"]
    rows = _excluding(rows, "payment_id", exclude_ids)
    if not rows:
        return None
    row = rng.choice(rows)
    row["amount"] = str(_i(row["amount"]) + magnitude_paise)
    return {
        "type": "ORDER_TAMPER",
        "magnitude_paise": magnitude_paise,
        "target_ids": [row["payment_id"], row["order_id"]],
        "expected_code": "UNEXPLAINED",
        "description": (f"Settled gross for {row['order_id']} inflated by "
                        f"{cfg.rupees(magnitude_paise)} above the order record"),
    }


def inject_missing_credit(data, magnitude_paise, rng, exclude_ids=None):
    """A settlement was created but the bank credit never arrived.

    Magnitude is not meaningful here; the fault is categorical.
    """
    candidates = _excluding(data["bank"], "bank_txn_id", exclude_ids)
    if len(candidates) < 2:
        return None
    b = rng.choice(candidates)
    sid = data["truth"]["bank_to_settlement"].get(b["bank_txn_id"], "")
    data["bank"] = [x for x in data["bank"] if x["bank_txn_id"] != b["bank_txn_id"]]
    return {
        "type": "MISSING_CREDIT",
        "magnitude_paise": _i(b["amount"]),
        "target_ids": [sid],
        "expected_code": "UNEXPLAINED",
        "description": (f"Settlement {sid} worth {cfg.rupees(_i(b['amount']))} "
                        f"has no bank credit at all"),
    }


def inject_phantom_transaction(data, magnitude_paise, rng, exclude_ids=None):
    # Creates a brand-new entity id, so there is nothing to exclude here —
    # the parameter exists only so run_trial can call every injector uniformly.
    """A settled transaction references an order the merchant never placed."""
    rows = [r for r in data["recon"] if r["type"] == "payment" and r["settlement_id"]]
    if not rows:
        return None
    src = rng.choice(rows)
    ghost = copy.deepcopy(src)
    ghost["payment_id"] = ghost["entity_id"] = f"pay_GHOST{rng.randint(1000,9999)}"
    ghost["order_id"] = f"order_GHOST{rng.randint(1000,9999)}"
    ghost["amount"] = str(magnitude_paise)
    ghost["fee"] = "0"
    ghost["tax"] = "0"
    ghost["credit"] = str(magnitude_paise)
    data["recon"].append(ghost)
    return {
        "type": "PHANTOM_TXN",
        "magnitude_paise": magnitude_paise,
        "target_ids": [ghost["payment_id"], ghost["order_id"]],
        "expected_code": "UNEXPLAINED",
        "description": (f"Settled transaction {ghost['payment_id']} for "
                        f"{cfg.rupees(magnitude_paise)} references an order "
                        f"that does not exist"),
    }


def inject_duplicate_credit(data, magnitude_paise, rng, exclude_ids=None):
    """The same settlement is credited twice — an over-payment that a naive
    engine happily matches and never questions."""
    candidates = _excluding(data["bank"], "bank_txn_id", exclude_ids)
    if not candidates:
        return None
    b = rng.choice(candidates)
    dup = copy.deepcopy(b)
    dup["bank_txn_id"] = f"bnk_DUP{rng.randint(1000,9999)}"
    data["bank"].append(dup)
    return {
        "type": "DUPLICATE_CREDIT",
        "magnitude_paise": _i(b["amount"]),
        "target_ids": [dup["bank_txn_id"]],
        "expected_code": None,
        "description": (f"Settlement credited twice; duplicate "
                        f"{dup['bank_txn_id']} for {cfg.rupees(_i(b['amount']))}"),
    }


def inject_date_shift(data, magnitude_paise, rng, days=None, exclude_ids=None):
    """The bank credit lands far outside the expected window.

    Magnitude here is measured in DAYS, passed via the days argument.
    """
    candidates = _excluding(data["bank"], "bank_txn_id", exclude_ids)
    if not candidates:
        return None
    days = days if days is not None else 5
    b = rng.choice(candidates)
    dt = _dt(b.get("credit_datetime"))
    if not dt:
        return None
    nd = dt + timedelta(days=days)
    b["credit_datetime"] = nd.strftime("%Y-%m-%d %H:%M:%S")
    b["value_date"] = nd.strftime("%Y-%m-%d")
    return {
        "type": "DATE_SHIFT",
        "magnitude_paise": days,          # days, not paise
        "magnitude_unit": "days",
        "target_ids": [b["bank_txn_id"]],
        "expected_code": "TIMING_LAG",
        "description": f"Bank credit {b['bank_txn_id']} delayed by {days} days",
    }


def inject_utr_corrupt(data, magnitude_paise, rng, exclude_ids=None):
    """The bank reference bears no relationship to the settlement UTR.

    Correct behaviour is NOT to drop the match — the amount and date still
    agree — but to lower confidence and say the reference does not correspond.
    """
    candidates = _excluding(data["bank"], "bank_txn_id", exclude_ids)
    if not candidates:
        return None
    b = rng.choice(candidates)
    b["reference"] = f"{rng.randint(100000000, 999999999)}zz{rng.randint(10,99)}"
    return {
        "type": "UTR_CORRUPT",
        "magnitude_paise": 0,
        "target_ids": [b["bank_txn_id"]],
        "expected_code": "TIMING_LAG",
        "description": f"Bank reference on {b['bank_txn_id']} replaced with an unrelated value",
        "must_still_match": True,
    }


def _rebalance_settlement(data, settlement_id, delta_paise):
    """Keep the settlement header consistent with its rows.

    Without this the injected fault would ALSO break the batch total, and the
    engine could catch it at stage 1 for the wrong reason — inflating the
    measured detection rate. Keeping the header consistent forces the engine to
    catch the fault where it actually lives.
    """
    if not settlement_id:
        return
    for s in data["settlements"]:
        if s["id"] == settlement_id:
            s["amount"] = str(_i(s["amount"]) + delta_paise)
            break
    for b in data["bank"]:
        if data["truth"]["bank_to_settlement"].get(b["bank_txn_id"]) == settlement_id:
            b["amount"] = str(_i(b["amount"]) + delta_paise)
            break


# Registry.
#   unit         — what the magnitude axis measures
#   noise_floor  — the magnitude at or below which NOT detecting is CORRECT,
#                  because the fault is smaller than the engine's own declared
#                  tolerance. Scoring those trials as misses would penalise the
#                  engine for behaving exactly as documented, and would drag
#                  every detection rate down for no reason.
FAULTS = {
    "FEE_OVERCHARGE":   {"fn": inject_fee_overcharge,     "unit": "paise",
                         "noise_floor": cfg.TOLERANCES.per_txn_rounding_paise,
                         "label": "MDR overcharged on one transaction"},
    "GST_UNDERSTATE":   {"fn": inject_gst_understate,     "unit": "paise",
                         "noise_floor": cfg.TOLERANCES.per_txn_rounding_paise,
                         "label": "GST on the fee understated"},
    "PAYOUT_SKIM":      {"fn": inject_payout_skim,        "unit": "paise",
                         "noise_floor": cfg.TOLERANCES.rounding_paise,
                         "label": "Money skimmed from the bank credit"},
    "ORDER_TAMPER":     {"fn": inject_order_tamper,       "unit": "paise",
                         "noise_floor": cfg.TOLERANCES.per_txn_rounding_paise,
                         "label": "Settled gross inflated above the order"},
    "PHANTOM_TXN":      {"fn": inject_phantom_transaction, "unit": "paise",
                         "noise_floor": 0,
                         "label": "Settled transaction with no matching order"},
    "MISSING_CREDIT":   {"fn": inject_missing_credit,     "unit": "categorical",
                         "noise_floor": 0,
                         "label": "Settlement created, bank credit never arrived"},
    "DUPLICATE_CREDIT": {"fn": inject_duplicate_credit,   "unit": "categorical",
                         "noise_floor": 0,
                         "label": "The same settlement credited twice"},
    "DATE_SHIFT":       {"fn": inject_date_shift,         "unit": "days",
                         "noise_floor": cfg.TOLERANCES.date_window_days,
                         "label": "Bank credit delayed beyond the window"},
    "UTR_CORRUPT":      {"fn": inject_utr_corrupt,        "unit": "categorical",
                         "noise_floor": 0,
                         "label": "Bank reference bears no relation to the UTR"},
}


# ===========================================================================
# Trial execution
# ===========================================================================

def snapshot(orders, recon, settlements, bank, invoices, truth):
    """Deep copy so an injected fault cannot leak into the next trial."""
    return {
        "orders": copy.deepcopy(orders),
        "recon": copy.deepcopy(recon),
        "settlements": copy.deepcopy(settlements),
        "bank": copy.deepcopy(bank),
        "invoices": copy.deepcopy(invoices),
        "truth": truth,
    }


def run_engine(data):
    """Run the matching stages with no audit writes. Returns exceptions and
    stage-1 matches (needed for calibration)."""
    from stage1_batch_matcher import match_batches
    from stage2_order_matcher import match_orders
    from stage3_gst_itc import reconcile_gst

    a = NullAudit()
    bm, be, unmatched = match_batches(data["bank"], data["settlements"], a)
    om, oe, _ = match_orders(data["recon"], data["orders"], bm, a)
    gr, ge = reconcile_gst(data["recon"], data["invoices"], om, a)

    exceptions = list(be) + list(oe) + list(ge)
    for u in unmatched:
        exceptions.append({
            "subject_type": "settlement", "subject_id": u["settlement_id"],
            "variance_code": "UNEXPLAINED", "confidence": 0.0,
            "amount": u["amount"], "reason": u["reason"],
        })
    return {"exceptions": exceptions, "batch_matches": bm,
            "order_matches": om, "unmatched": unmatched}


def flagged_ids(result):
    """Every entity the engine raised an exception about."""
    ids = set()
    for e in result["exceptions"]:
        for k in ("subject_id", "order_id", "counterpart_id"):
            v = e.get(k)
            if v:
                ids.add(str(v))
    return ids


def detected(result, truth, baseline_ids):
    """Did the engine flag the injected entity, and only because of the fault?

    Subtracting the baseline matters: the clean dataset already raises benign
    exceptions (on-hold reserves, unsettled orders). Without subtracting them,
    a fault injected onto an already-flagged entity would be scored as a
    detection the engine did not actually make.
    """
    new_ids = flagged_ids(result) - baseline_ids
    hit = any(str(t) in new_ids for t in truth["target_ids"] if t)

    code_ok = True
    if hit and truth.get("expected_code"):
        codes = {e.get("variance_code") for e in result["exceptions"]
                 if any(str(e.get(k)) in [str(x) for x in truth["target_ids"]]
                        for k in ("subject_id", "order_id", "counterpart_id"))}
        code_ok = truth["expected_code"] in codes

    return hit, code_ok


def run_trial(base_data, fault_type, magnitude, rng, baseline_ids):
    """One attack. Returns None if the fault could not be applied.

    baseline_ids is passed through to the injector as exclude_ids, so a fault
    is never planted on an entity clean data already flags for an unrelated
    reason — otherwise a correct detection can be scored as a miss purely
    because the baseline-subtraction can't distinguish the two.
    """
    data = snapshot(base_data["orders"], base_data["recon"],
                    base_data["settlements"], base_data["bank"],
                    base_data["invoices"], base_data["truth"])

    spec = FAULTS[fault_type]
    if spec["unit"] == "days":
        truth = spec["fn"](data, 0, rng, days=int(magnitude), exclude_ids=baseline_ids)
    else:
        truth = spec["fn"](data, int(magnitude), rng, exclude_ids=baseline_ids)
    if truth is None:
        return None

    result = run_engine(data)
    hit, code_ok = detected(result, truth, baseline_ids)

    # For faults where the correct behaviour is to keep matching (UTR
    # corruption), a "detection" that DROPS the match is a failure, not a pass.
    if truth.get("must_still_match"):
        still = any(str(m["bank_txn_id"]) in [str(t) for t in truth["target_ids"]]
                    for m in result["batch_matches"])
        return {"detected": hit and still, "code_ok": code_ok,
                "truth": truth, "kept_match": still}

    return {"detected": hit, "code_ok": code_ok, "truth": truth}
