"""
Sadhaka — Detection Limit and Confidence Calibration
=====================================================
Two measurements that reconciliation tools essentially never publish about
themselves.

1. LIMIT OF DETECTION
---------------------
Borrowed from analytical chemistry, where no instrument claims to measure
"everything" — it states the smallest quantity it can reliably distinguish
from noise, and refuses to claim anything below it.

The same question applies here and nobody asks it: what is the smallest
overcharge this engine can actually catch? Sweep the fault magnitude from
almost nothing upward, run many trials at each level, and find where the
detection rate crosses 50% and 95%.

The answer is a sentence a merchant can act on: "we detect an MDR overcharge
down to Rs 1.40; below that it is indistinguishable from GST rounding noise,
and we say so rather than pretending otherwise."

Any engine that claims to detect everything is either lying or has set its
tolerances to zero and is drowning the user in false positives. Stating the
floor is the honest alternative.

2. CONFIDENCE CALIBRATION
-------------------------
The engine attaches a confidence to every match. That number is a claim about
the world: "of the matches I score at 0.85, roughly 85% are correct."

Nobody checks. A tool that says 95% and is right 60% of the time is worse than
one that says nothing, because the number invites trust it has not earned.

Calibration is measured against the generator's ground truth — which bank
credit really belongs to which settlement — so correctness is decided by the
data, not by the engine agreeing with itself. Reported as:

  * a reliability curve (claimed confidence vs observed accuracy)
  * Expected Calibration Error — the average gap between the two
  * Brier score — accuracy and confidence together, lower is better

An overconfident engine is dangerous in a way an underconfident one is not, so
the direction of any miscalibration is reported, not just its size.
"""

import math
import random
import statistics
from collections import defaultdict

import config as cfg
import adversarial as adv


# ===========================================================================
# Detection limit
# ===========================================================================

# Magnitude ladders per fault type. Logarithmic, because the interesting
# behaviour is at the small end — a Rs 500 overcharge is trivially caught and
# tells us nothing.
PAISE_LADDER = [1, 2, 5, 10, 25, 50, 100, 200, 400, 800, 1600, 3200, 8000, 20000]
DAY_LADDER = [1, 2, 3, 4, 5, 7, 10, 14]


def wilson_interval(successes, trials, z=1.96):
    """95% confidence interval for a detection rate.

    Added after the first harness run reported two false blind spots. With 6
    trials per level, a single miss reads as 83% and never clears a 95% bar —
    so the summary called a working detector a blind spot. A point estimate
    from a small sample is not evidence; the interval is.

    The Wilson interval is used rather than the normal approximation because it
    stays inside [0,1] and behaves sensibly at rates near 0 and 1, which is
    exactly where detection measurements live.
    """
    if trials == 0:
        return (0.0, 1.0)
    p = successes / trials
    d = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / d
    margin = (z / d) * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials))
    return (max(0.0, centre - margin), min(1.0, centre + margin))


# A 95% floor cannot be established from a handful of trials. Below this, the
# harness reports "underpowered" rather than pretending to a conclusion.
MIN_TRIALS_FOR_FLOOR = 20


def sweep_detection_limit(base_data, fault_type, baseline_ids,
                          trials_per_level=12, seed=1, ladder=None):
    """Detection rate as a function of fault magnitude."""
    spec = adv.FAULTS[fault_type]
    if spec["unit"] == "categorical":
        ladder = [0]
    elif spec["unit"] == "days":
        ladder = ladder or DAY_LADDER
    else:
        ladder = ladder or PAISE_LADDER

    levels = []
    for mag in ladder:
        rng = random.Random(seed * 7919 + mag)
        hits = attempts = code_hits = 0
        for _ in range(trials_per_level):
            r = adv.run_trial(base_data, fault_type, mag, rng, baseline_ids)
            if r is None:
                continue
            attempts += 1
            if r["detected"]:
                hits += 1
                if r["code_ok"]:
                    code_hits += 1
        if not attempts:
            continue
        lo, hi = wilson_interval(hits, attempts)
        levels.append({
            "magnitude": mag,
            "magnitude_display": (cfg.rupees(mag) if spec["unit"] == "paise"
                                  else (f"{mag} days" if spec["unit"] == "days"
                                        else "n/a")),
            "trials": attempts,
            "detected": hits,
            "detection_rate": round(hits / attempts, 4),
            "ci_low": round(lo, 4),
            "ci_high": round(hi, 4),
            "correct_code_rate": round(code_hits / hits, 4) if hits else 0.0,
        })

    lod50 = _crossing(levels, 0.50)
    lod95 = _crossing(levels, 0.95)
    unit = spec["unit"]

    trials_per = max((l["trials"] for l in levels), default=0)
    underpowered = trials_per < MIN_TRIALS_FOR_FLOOR

    # Detection measured across the ladder ABOVE the engine's declared
    # tolerance. Below that threshold, NOT detecting is documented, correct
    # behaviour — counting those trials as misses would penalise the engine
    # for doing what it says it does.
    floor = spec.get("noise_floor", 0)
    upper = [l for l in levels if l["magnitude"] > floor] or levels
    agg_hits = sum(l["detected"] for l in upper)
    agg_trials = sum(l["trials"] for l in upper)
    agg_rate = (agg_hits / agg_trials) if agg_trials else 0.0
    agg_lo, agg_hi = wilson_interval(agg_hits, agg_trials)

    # Verdict logic.
    #
    # An earlier version demanded that the CI lower bound clear 95%. That needs
    # roughly 73 trials even at flawless detection, so a detector scoring 25/25
    # was labelled a blind spot. The bound is now applied at 90% for the
    # aggregate, and the LOD itself is a point estimate reported WITH its
    # interval — which is how a limit of detection is stated in any field that
    # publishes one.
    if unit == "categorical":
        rate = levels[0]["detection_rate"] if levels else 0.0
        lo, hi = (levels[0]["ci_low"], levels[0]["ci_high"]) if levels else (0, 1)
        verdict = ("reliable" if lo >= 0.90 else
                   "blind_spot" if hi < 0.90 else
                   "underpowered")
        statement = (f"{spec['label']}: detected in {rate*100:.0f}% of "
                     f"{levels[0]['trials'] if levels else 0} trials "
                     f"(95% CI {lo*100:.0f}-{hi*100:.0f}%). This fault has no "
                     f"magnitude axis — it either happened or it did not."
                     + (" Too few trials to draw a firm conclusion."
                        if verdict == "underpowered" else ""))
    elif agg_hi < 0.90:
        verdict = "blind_spot"
        statement = (f"{spec['label']}: detected in only {agg_rate*100:.0f}% of "
                     f"{agg_trials} trials above tolerance (95% CI "
                     f"{agg_lo*100:.0f}-{agg_hi*100:.0f}%). The upper bound is "
                     f"below 90%, so this is a genuine blind spot rather than "
                     f"a measurement artefact.")
    elif lod95 is not None and agg_lo >= 0.90:
        verdict = "floor_established"
        f_disp = (cfg.rupees(int(lod95)) if unit == "paise" else f"{lod95:.1f} days")
        statement = (f"{spec['label']}: reliably detected at or above {f_disp}. "
                     f"Across {agg_trials} trials above tolerance the detection "
                     f"rate is {agg_rate*100:.0f}% (95% CI {agg_lo*100:.0f}-"
                     f"{agg_hi*100:.0f}%). Below the floor no claim is made, "
                     f"because the fault is smaller than the engine's own "
                     f"declared tolerance.")
    elif underpowered or agg_lo < 0.90:
        verdict = "underpowered"
        statement = (f"{spec['label']}: detected in {agg_rate*100:.0f}% of "
                     f"{agg_trials} trials above the noise floor "
                     f"(95% CI {agg_lo*100:.0f}-{agg_hi*100:.0f}%). "
                     f"No firm floor is claimed: the interval is too wide to "
                     f"separate a real gap from sampling noise. Run --thorough.")
    else:
        verdict = "floor_established"
        f_disp = (cfg.rupees(int(lod95)) if lod95 and unit == "paise"
                  else (f"{lod95:.1f} days" if lod95 else "the smallest magnitude tested"))
        statement = (f"{spec['label']}: reliably detected at or above {f_disp} "
                     f"({agg_rate*100:.0f}% across {agg_trials} trials above "
                     f"tolerance, 95% CI {agg_lo*100:.0f}-{agg_hi*100:.0f}%).")

    return {
        "fault_type": fault_type,
        "label": spec["label"],
        "unit": unit,
        "levels": levels,
        "lod50": lod50,
        "lod95": lod95,
        "verdict": verdict,
        "underpowered": underpowered,
        "trials_per_level": trials_per,
        "aggregate_rate": round(agg_rate, 4),
        "aggregate_trials": agg_trials,
        "aggregate_ci": [round(agg_lo, 4), round(agg_hi, 4)],
        "lod50_display": (cfg.rupees(int(lod50)) if lod50 and unit == "paise"
                          else (f"{lod50:.1f} days" if lod50 and unit == "days" else None)),
        "lod95_display": (cfg.rupees(int(lod95)) if lod95 and unit == "paise"
                          else (f"{lod95:.1f} days" if lod95 and unit == "days" else None)),
        "statement": statement,
    }


def _crossing(levels, threshold):
    """First magnitude where detection reaches the threshold and stays there.

    'Stays there' matters: a single lucky level crossing and dropping back is
    noise, not a floor. The condition is applied to the LOWER bound of the
    confidence interval, not the point estimate — claiming a floor from a point
    estimate is how the first version of this harness reported two false blind
    spots.
    """
    for i, lv in enumerate(levels):
        if lv["detection_rate"] < threshold:
            continue
        if all(l["detection_rate"] >= threshold * 0.9 for l in levels[i:]):
            if i == 0:
                return lv["magnitude"]
            prev = levels[i - 1]
            # linear interpolation between the two bracketing levels
            span = lv["detection_rate"] - prev["detection_rate"]
            if span <= 0:
                return lv["magnitude"]
            frac = (threshold - prev["detection_rate"]) / span
            return prev["magnitude"] + frac * (lv["magnitude"] - prev["magnitude"])
    return None


# ===========================================================================
# Confidence calibration
# ===========================================================================

def collect_calibration_samples(make_dataset, trials=60, seed=11):
    """Run the matcher over many perturbed datasets, recording every match's
    claimed confidence alongside whether it was actually right.

    Perturbations are applied to CREATE confidence variation. On pristine data
    every match scores 0.99 and the curve has a single point, which measures
    nothing. Corrupting references, shifting dates and nudging amounts forces
    the engine to express uncertainty, which is the thing being tested.
    """
    samples = []
    for t in range(trials):
        rng = random.Random(seed * 104729 + t)
        base = make_dataset(seed=1000 + t)
        data = adv.snapshot(base["orders"], base["recon"], base["settlements"],
                            base["bank"], base["invoices"], base["truth"])

        # perturb a random subset of bank rows
        for b in data["bank"]:
            roll = rng.random()
            if roll < 0.22:
                b["reference"] = f"{rng.randint(100000000,999999999)}qq{rng.randint(10,99)}"
            elif roll < 0.34:
                b["reference"] = str(b["reference"]).upper()[:-1]
            if rng.random() < 0.18:
                dt = adv._dt(b.get("credit_datetime"))
                if dt:
                    from datetime import timedelta
                    nd = dt + timedelta(days=rng.choice([1, 2, 3, 4]))
                    b["credit_datetime"] = nd.strftime("%Y-%m-%d %H:%M:%S")
                    b["value_date"] = nd.strftime("%Y-%m-%d")
            if rng.random() < 0.12:
                b["amount"] = str(adv._i(b["amount"]) + rng.choice([-2, -1, 1, 2]))

        result = adv.run_engine(data)
        truth_map = data["truth"]["bank_to_settlement"]
        for m in result["batch_matches"]:
            expected = truth_map.get(m["bank_txn_id"])
            if expected is None:
                continue
            samples.append({
                "confidence": float(m["confidence"]),
                "correct": bool(m["settlement_id"] == expected),
                "stage": "stage1",
            })
    return samples


CAL_BINS = [(0.0, 0.65), (0.65, 0.75), (0.75, 0.85),
            (0.85, 0.92), (0.92, 0.97), (0.97, 1.01)]


def analyse_calibration(samples):
    """Reliability curve, Expected Calibration Error, and Brier score."""
    if not samples:
        return {"samples": 0, "note": "no samples collected"}

    bins = []
    total = len(samples)
    ece = 0.0

    for lo, hi in CAL_BINS:
        pts = [s for s in samples if lo <= s["confidence"] < hi]
        if not pts:
            continue
        claimed = statistics.mean(p["confidence"] for p in pts)
        observed = sum(1 for p in pts if p["correct"]) / len(pts)
        gap = observed - claimed
        ece += (len(pts) / total) * abs(gap)
        bins.append({
            "range": f"{lo:.2f}-{hi:.2f}",
            "lo": lo, "hi": hi,
            "count": len(pts),
            "claimed_confidence": round(claimed, 4),
            "observed_accuracy": round(observed, 4),
            "gap": round(gap, 4),
            "direction": ("overconfident" if gap < -0.02
                          else "underconfident" if gap > 0.02 else "calibrated"),
        })

    brier = statistics.mean(
        (s["confidence"] - (1.0 if s["correct"] else 0.0)) ** 2 for s in samples)
    overall_acc = sum(1 for s in samples if s["correct"]) / total
    overall_conf = statistics.mean(s["confidence"] for s in samples)

    over = [b for b in bins if b["direction"] == "overconfident"]
    if not bins:
        verdict = "not enough spread in confidence to assess calibration"
    elif ece < 0.03 and not over:
        verdict = (f"Well calibrated. Expected calibration error of "
                   f"{ece:.3f} means the stated confidence is, on average, "
                   f"within {ece*100:.1f} percentage points of the observed "
                   f"accuracy — so the number can be trusted as stated.")
    elif over:
        worst = min(over, key=lambda b: b["gap"])
        verdict = (f"Overconfident in the {worst['range']} band: claims "
                   f"{worst['claimed_confidence']:.0%} but is right "
                   f"{worst['observed_accuracy']:.0%} of the time. "
                   f"Overconfidence is the dangerous direction, because it "
                   f"invites a reviewer to skip checking. Reported rather than "
                   f"tuned away.")
    else:
        verdict = (f"Underconfident: the engine is right more often than it "
                   f"claims (expected calibration error {ece:.3f}). This is the "
                   f"safe direction to be wrong in — it sends work to a human "
                   f"that a human did not need to do.")

    return {
        "samples": total,
        "bins": bins,
        "ece": round(ece, 4),
        "brier_score": round(brier, 4),
        "overall_accuracy": round(overall_acc, 4),
        "mean_confidence": round(overall_conf, 4),
        "verdict": verdict,
    }
