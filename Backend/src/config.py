"""
Sadhaka — Configuration
=======================
Every statutory rate and matching tolerance lives here, with an effective date.

Design rule: a tax rate change must be a CONFIG edit, never a code edit.
Indian statutory rates change by notification (e.g. 194-O went 1% -> 0.1% on
2024-10-01; GST TCS went 1% -> 0.5% on 2024-07-10), so rates are stored as
effective-dated bands and resolved by transaction date.
"""

from datetime import date
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Effective-dated rate bands
# ---------------------------------------------------------------------------

@dataclass
class RateBand:
    """A statutory rate valid over a date range. end_date=None means 'current'."""
    rate: float
    start_date: date
    end_date: Optional[date] = None
    note: str = ""

    def covers(self, d: date) -> bool:
        if d < self.start_date:
            return False
        if self.end_date is not None and d > self.end_date:
            return False
        return True


def resolve_rate(bands: List[RateBand], d: date) -> float:
    for b in bands:
        if b.covers(d):
            return b.rate
    raise ValueError(f"No rate band covers date {d}")


# ---------------------------------------------------------------------------
# GST on MDR  — 18%, stable.
# Applies to the FEE only, never to the transaction value.
# ---------------------------------------------------------------------------
GST_ON_MDR = [
    RateBand(0.18, date(2017, 7, 1), None,
             "18% GST on payment gateway fee (standard-rated service)"),
]

# ---------------------------------------------------------------------------
# Section 194-O TDS — e-commerce operator obligation.
# NOT deducted by a pure payment aggregator. Only fires for marketplace /
# Route split-payout scenarios. Rate cut 1% -> 0.1% w.e.f. 2024-10-01.
# ---------------------------------------------------------------------------
TDS_194O = [
    RateBand(0.01, date(2020, 10, 1), date(2024, 9, 30),
             "194-O at 1% (original rate)"),
    RateBand(0.001, date(2024, 10, 1), None,
             "194-O at 0.1% (Finance (No.2) Act 2024, w.e.f. 2024-10-01)"),
]
TDS_194O_NO_PAN_RATE = 0.05          # 5% where PAN/Aadhaar not furnished
TDS_194O_INDIVIDUAL_HUF_THRESHOLD = 5_00_000_00  # Rs 5,00,000 expressed in paise.
# Bug found by the marketplace scenario printing this back as Rs 50,000: the
# constant was written 50_000_00, which is Rs 50,000, not Rs 5,00,000. Exactly
# the class of error that under-deducts silently rather than crashing, so the
# value is now written with explicit lakh grouping to make it read correctly.

# ---------------------------------------------------------------------------
# Section 52 GST TCS — marketplace ECO obligation.
# NOT applicable to a pure payment aggregator. Rate cut 1% -> 0.5% w.e.f.
# 2024-07-10 (CBIC Notification 15/2024-CT).
# ---------------------------------------------------------------------------
GST_TCS_52 = [
    RateBand(0.01, date(2018, 10, 1), date(2024, 7, 9), "TCS at 1% (original)"),
    RateBand(0.005, date(2024, 7, 10), None,
             "TCS at 0.5% (CBIC Notif. 15/2024-CT, w.e.f. 2024-07-10)"),
]


# ---------------------------------------------------------------------------
# MDR by payment instrument
# RuPay debit and BHIM-UPI (P2M) carry ZERO MDR under Sec 10A PSS Act /
# Sec 269SU IT Act (since Jan 2020) — but a platform/technology fee may still
# be levied. Modelled explicitly so the matcher does not "expect" MDR on UPI
# and then flag every UPI row as a variance.
# ---------------------------------------------------------------------------
MDR_RATES = {
    "card":        0.0200,   # 2.00% standard domestic card
    "netbanking":  0.0200,   # 2.00%
    "wallet":      0.0200,   # 2.00%
    "upi":         0.0000,   # zero MDR (statutory)
}

# Platform/technology fee still charged on zero-MDR instruments
PLATFORM_FEE_ON_ZERO_MDR = {
    "upi": 0.0000,   # modelled as zero here; configurable per merchant contract
}


# ---------------------------------------------------------------------------
# Merchant profile — drives which statutory deductions are even possible
# ---------------------------------------------------------------------------
@dataclass
class MerchantProfile:
    merchant_id: str
    legal_name: str
    gstin: str
    state_code: str
    # "payment_aggregator"  -> plain PG settlement. No 194-O, no TCS.
    # "marketplace"         -> Route/split payouts. 194-O + TCS may apply.
    settlement_model: str = "payment_aggregator"
    settlement_cycle_days: int = 2      # T+2. Post-Sept-2025 RBI Directions this
                                        # is contractual, NOT a fixed mandate.
    rolling_reserve_pct: float = 0.0
    rolling_reserve_hold_days: int = 0


DEFAULT_MERCHANT = MerchantProfile(
    merchant_id="acc_SadhakaDemo01",
    legal_name="Kalyani Retail Private Limited",
    gstin="27AABCK1234M1Z5",
    state_code="27",           # Maharashtra
    settlement_model="payment_aggregator",
    settlement_cycle_days=2,
)


# ---------------------------------------------------------------------------
# Matching tolerances
# ---------------------------------------------------------------------------
@dataclass
class MatchTolerances:
    # Amount tolerance for treating a difference as ROUNDING rather than a
    # real mismatch. Paise-level GST rounding accumulates across a batch.
    rounding_paise: int = 200            # Rs 2.00 absolute

    # Per-transaction rounding tolerance (GST on fee rounds per transaction)
    per_txn_rounding_paise: int = 5      # 5 paise

    # Bank credit can land later than settlement created_at (NEFT lag,
    # bank holidays). Window in days.
    #
    # Calibration note: this was originally 4 days, which turned out to be too
    # loose. A settlement's NEFT credit normally lands the SAME day it is
    # created, so a genuine 3-day bank delay was being silently absorbed as
    # "within tolerance" and never surfaced. 2 days allows for a weekend
    # without hiding real bank-side delays.
    date_window_days: int = 2

    # Monthly GST invoice vs summed settlement tax tolerance
    gst_invoice_tolerance_paise: int = 500   # Rs 5.00/month

    # TDS deducted vs Form 26AS reflection lag (marketplace scenarios only)
    tds_26as_lag_days: int = 30


TOLERANCES = MatchTolerances()


# ---------------------------------------------------------------------------
# Confidence scoring weights
# ---------------------------------------------------------------------------
CONFIDENCE = {
    "exact_utr_and_amount_and_date":  0.99,
    "exact_amount_and_date_fuzzy_utr": 0.85,
    "exact_amount_date_outside_window": 0.72,
    "amount_within_rounding_tolerance": 0.68,
    "amount_only_multiple_candidates": 0.40,
    "no_match": 0.0,
}

# Below this, a match is NOT auto-accepted — it goes to the exception queue
# for human review. Deliberately conservative: this is money.
AUTO_ACCEPT_THRESHOLD = 0.65


# ---------------------------------------------------------------------------
# Variance taxonomy — the exception reason codes
# ---------------------------------------------------------------------------
VARIANCE_CODES = {
    "FEE_DEDUCTION":    "MDR/platform fee differs from the contracted rate for this instrument.",
    "TAX_DEDUCTION":    "GST-on-MDR differs from 18% of the charged fee.",
    "ROUNDING":         "Sub-rupee drift from per-transaction fee/GST rounding.",
    "PARTIAL_PAYMENT":  "Refund or partial refund adjusts a prior sale, often in a later batch.",
    "TIMING_LAG":       "Correct match, but the bank credit landed outside the expected settlement window.",
    "ON_HOLD":          "Amount captured but withheld (reserve/hold); correctly excluded from this payout.",
    "CHARGEBACK":       "Dispute debit reduces the payout, typically in a batch later than the original sale.",
    "NOT_YET_SETTLED":  "Order captured but not yet included in any settlement batch (cycle not elapsed).",
    "DUPLICATE_CANDIDATE": "More than one plausible counterpart; refusing to guess which.",
    "UNEXPLAINED":      "Residual variance with no attributable cause. Escalate.",
}

# Codes that represent "correct behaviour, no action needed" vs codes that
# represent a real problem. Reported separately so the exception list is
# honest rather than alarmist.
BENIGN_CODES = {"ROUNDING", "TIMING_LAG", "ON_HOLD", "NOT_YET_SETTLED"}
ACTIONABLE_CODES = {"FEE_DEDUCTION", "TAX_DEDUCTION", "PARTIAL_PAYMENT",
                    "CHARGEBACK", "DUPLICATE_CANDIDATE", "UNEXPLAINED"}


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
AUDIT_DB = os.path.join(OUTPUT_DIR, "audit_trail.db")


def rupees(paise_value: int) -> str:
    """Format paise as an Indian-rupee string. All internal maths is in paise."""
    neg = paise_value < 0
    p = abs(int(paise_value))
    r, sub = divmod(p, 100)
    # Indian digit grouping: last 3, then pairs
    s = str(r)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts) + "," + tail
    out = f"Rs {s}.{sub:02d}"
    return ("-" + out) if neg else out
