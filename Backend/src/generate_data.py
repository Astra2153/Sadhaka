"""
Sadhaka — Synthetic Data Generator (v2)
========================================
Rebuilt against Razorpay's REAL settlement recon report schema.

Generates five linked datasets:

1. orders.csv              — the merchant's own order records (their source of truth)
2. settlement_recon.csv    — Razorpay's transaction-level settlement recon report
                             (real column set from /v1/settlements/recon/combined)
3. settlements.csv         — the batch-level Settlement entity
4. bank_statement.csv      — the merchant's bank credits (lumped NEFT, per UTR)
5. razorpay_gst_invoice.csv— Razorpay's MONTHLY GST tax invoice
                             (this, not the settlement report, is the ITC document)

WHY FIVE FILES, NOT TWO
-----------------------
A Razorpay settlement is a single lumped NEFT credit covering many orders, net
of MDR + 18% GST on MDR + refunds + disputes + holds. The bank sees only the
lump. The recon report bridges bank <-> orders. And ITC is claimable only off
the monthly tax invoice, which must separately tie back to the summed
per-transaction tax. That is three distinct reconciliations, so the data has to
support all three.

TAX MODELLING (corrected against the statutory position)
--------------------------------------------------------
* GST on MDR: 18%, charged on the FEE only, never on transaction value.
  Present on every fee-bearing row. Itemised in its own `tax` column.
* UPI: zero MDR (statutory, Sec 10A PSS Act) -> fee 0 -> GST 0.
  A matcher that "expects 2% everywhere" will wrongly flag every UPI row.
* 194-O TDS and Sec 52 GST TCS: NOT deducted in a pure payment-aggregator
  settlement. Deliberately ABSENT from this data.

All amounts are in PAISE, matching Razorpay's API convention.
"""

import csv
import json
import random
import string
import argparse
from datetime import datetime, timedelta
from collections import defaultdict

import config as cfg


def rand_id(prefix, length=14):
    chars = string.ascii_letters + string.digits
    return f"{prefix}_{''.join(random.choices(chars, k=length))}"


def rand_utr():
    """Bank-issued UTR. Deliberately a DIFFERENT format from Razorpay IDs,
    because the UTR comes from the correspondent bank, not from Razorpay."""
    return f"{random.randint(100000000, 999999999)}{random.choice(string.ascii_lowercase)}{random.randint(10,99)}"


def paise(rupees_float):
    return int(round(rupees_float * 100))


CARD_NETWORKS = ["Visa", "MasterCard", "RuPay", "Amex"]
CARD_ISSUERS = ["HDFC", "ICICI", "SBIN", "AXIS", "KKBK"]
CARD_TYPES = ["credit", "debit"]


def generate(seed=42, num_orders=68, base_date=datetime(2026, 7, 1),
             merchant=cfg.DEFAULT_MERCHANT, out_dir=None):

    random.seed(seed)
    out_dir = out_dir or cfg.DATA_DIR
    edge_cases = []

    GST_RATE = cfg.resolve_rate(cfg.GST_ON_MDR, base_date.date())

    # -----------------------------------------------------------------------
    # 1. Orders — the merchant's own records
    # -----------------------------------------------------------------------
    orders = []
    for i in range(1, num_orders + 1):
        method = random.choices(
            ["card", "upi", "netbanking", "wallet"], weights=[40, 38, 14, 8]
        )[0]
        amount = paise(round(random.uniform(249, 18500), 2))
        created = base_date + timedelta(
            days=random.randint(0, 9), hours=random.randint(0, 23),
            minutes=random.randint(0, 59)
        )
        orders.append({
            "order_id": f"order_{2000 + i}",
            "order_receipt": f"RCPT-2026-{2000 + i}",
            "payment_id": rand_id("pay"),
            "amount": amount,
            "currency": "INR",
            "method": method,
            "created_at": created,
            "status": "captured",
            "customer_state": random.choice(["27", "29", "07", "33", "24"]),
        })

    order_by_id = {o["order_id"]: o for o in orders}

    # ---------------- EDGE CASES ----------------

    # EC1: Partial refunds landing in a LATER batch than the original sale
    refund_orders = random.sample(orders, 4)
    refunds = []
    for o in refund_orders:
        pct = random.uniform(0.25, 0.60)
        refunds.append({
            "refund_id": rand_id("rfnd"),
            "payment_id": o["payment_id"],
            "order_id": o["order_id"],
            "amount": paise(round((o["amount"] / 100) * pct, 2)),
            "created_at": o["created_at"] + timedelta(days=random.randint(3, 6)),
        })
    edge_cases.append({
        "id": "EC1", "code": "PARTIAL_PAYMENT", "type": "REFUND_IN_LATER_BATCH",
        "description": "Partial refunds issued after the original sale settled; the refund debit appears in a later settlement batch than the sale it relates to.",
        "order_ids": [o["order_id"] for o in refund_orders],
        "expected_behaviour": "Matcher must link the refund to its original payment_id across batches, not flag it as an unexplained debit.",
    })

    # EC2: Chargeback / dispute debit
    dispute_order = random.choice([o for o in orders if o not in refund_orders])
    disputes = [{
        "dispute_id": rand_id("disp"),
        "payment_id": dispute_order["payment_id"],
        "order_id": dispute_order["order_id"],
        "amount": dispute_order["amount"],
        "fee": paise(750.00),
        "created_at": dispute_order["created_at"] + timedelta(days=random.randint(5, 8)),
    }]
    edge_cases.append({
        "id": "EC2", "code": "CHARGEBACK", "type": "DISPUTE_DEBIT",
        "description": "A chargeback debits both the disputed transaction value and a Rs 750 handling fee from a later settlement.",
        "order_ids": [dispute_order["order_id"]],
        "expected_behaviour": "Classified CHARGEBACK, linked to the original payment, not counted as a fee variance.",
    })

    # EC3: Orders captured but not yet settled
    unsettled = random.sample(
        [o for o in orders if o not in refund_orders and o is not dispute_order], 3)
    unsettled_ids = {o["order_id"] for o in unsettled}
    edge_cases.append({
        "id": "EC3", "code": "NOT_YET_SETTLED", "type": "ORDER_IN_FLIGHT",
        "description": "Orders captured inside the settlement cycle window, so they legitimately appear in no settlement batch yet.",
        "order_ids": sorted(unsettled_ids),
        "expected_behaviour": "Reported NOT_YET_SETTLED (benign), never as missing money.",
    })

    # EC4: On-hold reserve
    onhold_order = random.choice(
        [o for o in orders if o["order_id"] not in unsettled_ids
         and o not in refund_orders and o is not dispute_order])
    onhold_ids = {onhold_order["order_id"]}
    edge_cases.append({
        "id": "EC4", "code": "ON_HOLD", "type": "RESERVE_HOLD",
        "description": "A captured transaction flagged on_hold=true and withheld from the payout as a risk reserve.",
        "order_ids": sorted(onhold_ids),
        "expected_behaviour": "Excluded from the expected payout total. Must NOT be treated as missing money.",
    })

    # -----------------------------------------------------------------------
    # 2. Settlement batches
    # -----------------------------------------------------------------------
    settleable = [o for o in orders if o["order_id"] not in unsettled_ids]
    by_day = defaultdict(list)
    for o in settleable:
        by_day[o["created_at"].date()].append(o)

    settlements = []
    recon_rows = []

    def fee_for(order):
        rate = cfg.MDR_RATES.get(order["method"], 0.02)
        return int(round(order["amount"] * rate))

    fee_anomaly_order = gst_anomaly_order = None
    candidates = [o for o in settleable if o["method"] in ("card", "netbanking")]
    if len(candidates) >= 2:
        fee_anomaly_order, gst_anomaly_order = random.sample(candidates, 2)

    # Pre-compute the batch calendar so refunds/disputes can be routed to the
    # NEXT available settlement on or after their creation date. Without this,
    # a refund dated after the final batch silently vanishes from the data.
    batch_days = sorted(by_day.keys())
    batch_meta = {}
    for day in batch_days:
        batch_meta[day] = {
            "settlement_id": rand_id("setl"),
            "utr": rand_utr(),
            "settle_dt": datetime.combine(
                day + timedelta(days=merchant.settlement_cycle_days),
                datetime.min.time()) + timedelta(hours=random.randint(9, 14)),
        }

    def route_to_batch(event_date):
        """Next settlement batch on or after event_date; else the last batch."""
        for d in batch_days:
            if d >= event_date:
                return d
        return batch_days[-1] if batch_days else None

    refund_routing = defaultdict(list)
    for r in refunds:
        d = route_to_batch(r["created_at"].date())
        if d is not None:
            refund_routing[d].append(r)

    dispute_routing = defaultdict(list)
    for dsp in disputes:
        d = route_to_batch(dsp["created_at"].date())
        if d is not None:
            dispute_routing[d].append(dsp)

    for day in batch_days:
        day_orders = by_day[day]
        meta = batch_meta[day]
        settle_dt = meta["settle_dt"]
        settlement_id = meta["settlement_id"]
        utr = meta["utr"]
        batch_credit = batch_debit = batch_fee = batch_tax = 0

        for o in day_orders:
            on_hold = o["order_id"] in onhold_ids
            fee = fee_for(o)
            if fee_anomaly_order and o["order_id"] == fee_anomaly_order["order_id"]:
                fee = int(round(o["amount"] * 0.024))       # EC5
            tax = int(round(fee * GST_RATE))
            if gst_anomaly_order and o["order_id"] == gst_anomaly_order["order_id"]:
                tax = int(round(fee * 0.12))                # EC6

            recon_rows.append({
                "entity_id": o["payment_id"], "type": "payment",
                "debit": 0, "credit": o["amount"] - fee - tax,
                "amount": o["amount"], "currency": "INR",
                "fee": fee, "tax": tax,
                "on_hold": str(on_hold).lower(),
                "settled": str(not on_hold).lower(),
                "created_at": o["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
                "settled_at": "" if on_hold else settle_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "settlement_id": "" if on_hold else settlement_id,
                "posted_at": settle_dt.strftime("%Y-%m-%d %H:%M:%S"),
                "credit_type": "default", "description": "", "notes": "",
                "payment_id": o["payment_id"],
                "settlement_utr": "" if on_hold else utr,
                "order_id": o["order_id"], "order_receipt": o["order_receipt"],
                "method": o["method"],
                "card_network": random.choice(CARD_NETWORKS) if o["method"] == "card" else "",
                "card_issuer": random.choice(CARD_ISSUERS) if o["method"] == "card" else "",
                "card_type": random.choice(CARD_TYPES) if o["method"] == "card" else "",
                "dispute_id": "",
            })
            if not on_hold:
                batch_credit += o["amount"] - fee - tax
                batch_fee += fee
                batch_tax += tax

        for r in refund_routing.get(day, []):
            if True:
                orig = order_by_id[r["order_id"]]
                recon_rows.append({
                    "entity_id": r["refund_id"], "type": "refund",
                    "debit": r["amount"], "credit": 0, "amount": r["amount"],
                    "currency": "INR", "fee": 0, "tax": 0,
                    "on_hold": "false", "settled": "true",
                    "created_at": r["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
                    "settled_at": settle_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "settlement_id": settlement_id,
                    "posted_at": settle_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "credit_type": "default",
                    "description": f"Refund against {r['payment_id']}",
                    "notes": "", "payment_id": r["payment_id"],
                    "settlement_utr": utr, "order_id": r["order_id"],
                    "order_receipt": orig["order_receipt"], "method": orig["method"],
                    "card_network": "", "card_issuer": "", "card_type": "",
                    "dispute_id": "",
                })
                batch_debit += r["amount"]

        for d in dispute_routing.get(day, []):
            if True:
                orig = order_by_id[d["order_id"]]
                total_debit = d["amount"] + d["fee"]
                recon_rows.append({
                    "entity_id": d["dispute_id"], "type": "adjustment",
                    "debit": total_debit, "credit": 0, "amount": d["amount"],
                    "currency": "INR", "fee": d["fee"], "tax": 0,
                    "on_hold": "false", "settled": "true",
                    "created_at": d["created_at"].strftime("%Y-%m-%d %H:%M:%S"),
                    "settled_at": settle_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "settlement_id": settlement_id,
                    "posted_at": settle_dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "credit_type": "default",
                    "description": f"Chargeback debit + handling fee for {d['payment_id']}",
                    "notes": "", "payment_id": d["payment_id"],
                    "settlement_utr": utr, "order_id": d["order_id"],
                    "order_receipt": orig["order_receipt"], "method": orig["method"],
                    "card_network": "", "card_issuer": "", "card_type": "",
                    "dispute_id": d["dispute_id"],
                })
                batch_debit += total_debit

        net = batch_credit - batch_debit
        if net <= 0:
            continue

        settlements.append({
            "id": settlement_id, "entity": "settlement", "amount": net,
            "status": "processed", "fees": batch_fee, "tax": batch_tax,
            "utr": utr, "created_at": settle_dt.strftime("%Y-%m-%d %H:%M:%S"),
        })

    if fee_anomaly_order:
        edge_cases.append({
            "id": "EC5", "code": "FEE_DEDUCTION", "type": "WRONG_MDR_RATE",
            "description": "One transaction charged MDR at 2.4% instead of the contracted 2.0%.",
            "order_ids": [fee_anomaly_order["order_id"]],
            "expected_behaviour": "Flagged FEE_DEDUCTION with the rupee overcharge quantified.",
        })
    if gst_anomaly_order:
        edge_cases.append({
            "id": "EC6", "code": "TAX_DEDUCTION", "type": "WRONG_GST_RATE",
            "description": "GST on the fee computed at 12% instead of the statutory 18%.",
            "order_ids": [gst_anomaly_order["order_id"]],
            "expected_behaviour": "Flagged TAX_DEDUCTION; understated GST distorts the ITC claim.",
        })

    # EC7: split settlement
    if len(settlements) >= 3:
        src = settlements[1]
        src_rows = [r for r in recon_rows
                    if r["settlement_id"] == src["id"] and r["type"] == "payment"]
        if len(src_rows) >= 4:
            move = src_rows[:len(src_rows) // 2]
            new_id, new_utr = rand_id("setl"), rand_utr()
            moved_net = moved_fee = moved_tax = 0
            for r in move:
                r["settlement_id"] = new_id
                r["settlement_utr"] = new_utr
                moved_net += r["credit"]
                moved_fee += r["fee"]
                moved_tax += r["tax"]
            settlements.append({
                "id": new_id, "entity": "settlement", "amount": moved_net,
                "status": "processed", "fees": moved_fee, "tax": moved_tax,
                "utr": new_utr, "created_at": src["created_at"],
            })
            src["amount"] -= moved_net
            src["fees"] -= moved_fee
            src["tax"] -= moved_tax
            edge_cases.append({
                "id": "EC7", "code": "PARTIAL_PAYMENT", "type": "SPLIT_SETTLEMENT",
                "description": f"A single day's transactions were paid out across two settlement batches ({src['id']} and {new_id}).",
                "settlement_ids": [src["id"], new_id],
                "expected_behaviour": "Both batches match independently; the day's orders reconcile only when combined.",
            })

    # -----------------------------------------------------------------------
    # 3. Bank statement
    # -----------------------------------------------------------------------
    bank_rows = []
    for s in settlements:
        created = datetime.strptime(s["created_at"], "%Y-%m-%d %H:%M:%S")
        credit_dt = created + timedelta(hours=random.randint(2, 10))
        bank_rows.append({
            "_truth_settlement_id": s["id"],
            "bank_txn_id": rand_id("bnk", 10),
            "value_date": credit_dt.strftime("%Y-%m-%d"),
            "credit_datetime": credit_dt.strftime("%Y-%m-%d %H:%M:%S"),
            "amount": s["amount"],
            "narration": f"NEFT CR-HDFC0000123-RAZORPAY SOFTWARE PVT LTD-{s['utr']}-SETTLEMENT",
            "reference": s["utr"],
        })

    # EC8: delayed credit
    if bank_rows:
        late = random.choice(bank_rows)
        dt = datetime.strptime(late["credit_datetime"], "%Y-%m-%d %H:%M:%S") + timedelta(days=3)
        late["credit_datetime"] = dt.strftime("%Y-%m-%d %H:%M:%S")
        late["value_date"] = dt.strftime("%Y-%m-%d")
        edge_cases.append({
            "id": "EC8", "code": "TIMING_LAG", "type": "DELAYED_BANK_CREDIT",
            "description": f"Bank credit for UTR {late['reference']} landed 3 days after the settlement date (bank holiday).",
            "bank_txn_id": late["bank_txn_id"],
            "expected_behaviour": "Still matched (amount agrees) but flagged TIMING_LAG, which is benign.",
        })

    # EC9: UTR formatting drift
    if len(bank_rows) >= 3:
        drifted = bank_rows[2]
        true_utr = drifted["reference"]
        mangled = true_utr.upper()[:-1]
        drifted["reference"] = mangled
        drifted["narration"] = drifted["narration"].replace(true_utr, mangled)
        edge_cases.append({
            "id": "EC9", "code": "TIMING_LAG", "type": "UTR_FORMAT_DRIFT",
            "description": f"Bank recorded the UTR as '{mangled}' where Razorpay recorded '{true_utr}' (uppercased and truncated).",
            "bank_txn_id": drifted["bank_txn_id"],
            "expected_behaviour": "Exact UTR string match fails. Must still match on amount+date with downgraded confidence, NOT drop the match.",
        })

    # EC10: near-duplicate UTR
    if len(bank_rows) >= 5:
        a, b = bank_rows[0], bank_rows[4]
        similar = a["reference"][:-2] + "xy"
        b["reference"] = similar
        b["narration"] = f"NEFT CR-HDFC0000123-RAZORPAY SOFTWARE PVT LTD-{similar}-SETTLEMENT"
        for s in settlements:
            if s["amount"] == b["amount"]:
                s["utr"] = similar
                for r in recon_rows:
                    if r["settlement_id"] == s["id"]:
                        r["settlement_utr"] = similar
                break
        edge_cases.append({
            "id": "EC10", "code": "DUPLICATE_CANDIDATE", "type": "NEAR_DUPLICATE_UTR",
            "description": f"Two unrelated settlements carry visually similar UTRs ('{a['reference']}' vs '{similar}').",
            "bank_txn_ids": [a["bank_txn_id"], b["bank_txn_id"]],
            "expected_behaviour": "A fuzzy-UTR matcher would conflate these. Correct behaviour is to match on amount + date and keep them distinct.",
        })

    # -----------------------------------------------------------------------
    # 4. Monthly GST tax invoice
    # -----------------------------------------------------------------------
    total_fee = sum(s["fees"] for s in settlements)
    total_tax = sum(s["tax"] for s in settlements)
    invoice_tax = total_tax + random.choice([-217, -143, 168, 231])   # EC11

    gst_invoice = [{
        "invoice_no": f"RZP/{base_date.strftime('%Y%m')}/{random.randint(100000,999999)}",
        "invoice_date": (base_date.replace(day=1) + timedelta(days=32)).replace(day=1).strftime("%Y-%m-%d"),
        "period": base_date.strftime("%Y-%m"),
        "supplier_gstin": "29AAGCR4375J1ZU",
        "supplier_name": "RAZORPAY SOFTWARE PRIVATE LIMITED",
        "recipient_gstin": merchant.gstin,
        "recipient_name": merchant.legal_name,
        "place_of_supply": merchant.state_code,
        "taxable_value": total_fee,
        "igst": invoice_tax, "cgst": 0, "sgst": 0,
        "total_tax": invoice_tax,
        "invoice_total": total_fee + invoice_tax,
        "reflected_in_gstr2b": "yes",
    }]
    edge_cases.append({
        "id": "EC11", "code": "ROUNDING", "type": "GST_INVOICE_VS_SETTLEMENT_DRIFT",
        "description": f"Monthly GST invoice tax ({cfg.rupees(invoice_tax)}) differs from summed per-transaction tax ({cfg.rupees(total_tax)}) by {cfg.rupees(invoice_tax - total_tax)} from accumulated per-transaction rounding.",
        "expected_behaviour": "Within monthly tolerance -> ROUNDING, ITC still claimable, journal adjustment noted.",
    })

    # -----------------------------------------------------------------------
    # Write
    # -----------------------------------------------------------------------
    def write_csv(name, rows, fields):
        with open(f"{out_dir}/{name}", "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in fields})

    write_csv("orders.csv",
              [{**o, "created_at": o["created_at"].strftime("%Y-%m-%d %H:%M:%S")} for o in orders],
              ["order_id", "order_receipt", "payment_id", "amount", "currency",
               "method", "created_at", "status", "customer_state"])

    write_csv("settlement_recon.csv", recon_rows,
              ["entity_id", "type", "debit", "credit", "amount", "currency",
               "fee", "tax", "on_hold", "settled", "created_at", "settled_at",
               "settlement_id", "posted_at", "credit_type", "description",
               "notes", "payment_id", "settlement_utr", "order_id",
               "order_receipt", "method", "card_network", "card_issuer",
               "card_type", "dispute_id"])

    write_csv("settlements.csv", settlements,
              ["id", "entity", "amount", "status", "fees", "tax", "utr", "created_at"])

    write_csv("bank_statement.csv", bank_rows,
              ["bank_txn_id", "value_date", "credit_datetime", "amount",
               "narration", "reference"])

    write_csv("razorpay_gst_invoice.csv", gst_invoice,
              ["invoice_no", "invoice_date", "period", "supplier_gstin",
               "supplier_name", "recipient_gstin", "recipient_name",
               "place_of_supply", "taxable_value", "igst", "cgst", "sgst",
               "total_tax", "invoice_total", "reflected_in_gstr2b"])

    # -----------------------------------------------------------------------
    # Ground truth — the true answers, kept separate from the engine's inputs.
    #
    # The answer key (edge_cases.json) records the faults a human planted.
    # This records the objective truth of the dataset: which bank credit really
    # belongs to which settlement, and what every fee and tax SHOULD have been.
    # The adversarial harness measures the engine against this, so correctness
    # is decided by the data rather than by the engine agreeing with itself.
    # -----------------------------------------------------------------------
    ground_truth = {
        "bank_to_settlement": {b["bank_txn_id"]: b["_truth_settlement_id"]
                               for b in bank_rows},
        "settlement_to_orders": {},
        "expected_fee_by_payment": {},
        "expected_tax_by_payment": {},
    }
    for r in recon_rows:
        if r["type"] != "payment":
            continue
        sid = r["settlement_id"] or "__unsettled__"
        ground_truth["settlement_to_orders"].setdefault(sid, []).append(r["order_id"])
        o = order_by_id.get(r["order_id"])
        if o:
            rate = cfg.MDR_RATES.get(o["method"], 0.02)
            true_fee = int(round(o["amount"] * rate))
            ground_truth["expected_fee_by_payment"][r["payment_id"]] = true_fee
            ground_truth["expected_tax_by_payment"][r["payment_id"]] = int(round(true_fee * GST_RATE))

    with open(f"{out_dir}/ground_truth.json", "w") as f:
        json.dump(ground_truth, f, indent=2)

    with open(f"{out_dir}/edge_cases.json", "w") as f:
        json.dump({
            "note": "ANSWER KEY. The reconciliation engine never reads this file. "
                    "It exists so the engine can be scored honestly against known truth.",
            "merchant": {
                "merchant_id": merchant.merchant_id,
                "legal_name": merchant.legal_name,
                "gstin": merchant.gstin,
                "settlement_model": merchant.settlement_model,
                "settlement_cycle": f"T+{merchant.settlement_cycle_days}",
            },
            "statutory_position": {
                "gst_on_mdr": "18% on fee only. Present throughout.",
                "tds_194o": "NOT deducted. Razorpay acts as payment aggregator, not e-commerce operator. Absent by design.",
                "gst_tcs_52": "NOT applicable. Not a marketplace ECO. Absent by design.",
                "upi_mdr": "Zero MDR by statute (Sec 10A PSS Act). Fee and tax are 0 on UPI rows.",
            },
            "edge_cases": edge_cases,
        }, f, indent=2)

    return {
        "orders": len(orders), "recon_rows": len(recon_rows),
        "settlements": len(settlements), "bank_rows": len(bank_rows),
        "edge_cases": len(edge_cases),
        "total_fee": total_fee, "total_tax": total_tax,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--orders", type=int, default=68)
    args = ap.parse_args()

    stats = generate(seed=args.seed, num_orders=args.orders)

    print("Sadhaka — synthetic dataset generated")
    print("=" * 58)
    print(f"  orders.csv                {stats['orders']:>5} rows")
    print(f"  settlement_recon.csv      {stats['recon_rows']:>5} rows")
    print(f"  settlements.csv           {stats['settlements']:>5} batches")
    print(f"  bank_statement.csv        {stats['bank_rows']:>5} credits")
    print(f"  razorpay_gst_invoice.csv      1 monthly invoice")
    print(f"  edge_cases.json           {stats['edge_cases']:>5} deliberate cases (answer key)")
    print()
    print(f"  Total MDR/fees charged:   {cfg.rupees(stats['total_fee'])}")
    print(f"  Total GST on fees:        {cfg.rupees(stats['total_tax'])}")
