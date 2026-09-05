# Sadhaka (साधक)

**A settlement reconciliation agent for Razorpay merchants. It matches what the gateway settled against what the bank paid and what the merchant sold — and explains, in plain English, every rupee it cannot place.**

Built for the Razorpay AI Buildathon, Track 04 — AI Finance Controller.

---

## The problem

A merchant taking payments through Razorpay ends up holding three records of the same money, and they do not agree:

1. **The bank statement** shows a single lumped NEFT credit. It has no idea which orders are inside it.
2. **Razorpay's settlement report** shows every transaction, net of MDR, 18% GST on that MDR, refunds, chargebacks and holds.
3. **The merchant's own order records** show what was actually sold.

Reconciling these is done by hand, monthly, by someone with two spreadsheets open. It is slow and it is quietly expensive, because the ways they legitimately diverge look identical to the ways they wrongly diverge:

- Refunds land in a **later** settlement batch than the sale they reverse.
- A single day's orders can be **split across two payout batches**.
- The UTR is issued by the **correspondent bank, not by Razorpay**, so it drifts — case changes, truncation — and exact string matching silently drops real matches while fuzzy matching silently merges unrelated ones.
- **UPI carries zero MDR by statute** (Sec 10A, PSS Act). Nil fee and nil GST on those rows are correct, not missing.
- GST is deducted **per transaction inside the settlement**, but is claimable as input tax credit **only against the monthly tax invoice**, and only once that invoice appears in GSTR-2B.

When a manual process finds a gap, it produces a number. It does not produce a reason — and the reason is what determines whether anyone needs to act.

## What Sadhaka does

Five stages, each answering a different question:

| Stage | Question | Method |
|---|---|---|
| **1 — Bank to batch** | Which settlement batch is this NEFT credit? | Matches on net amount within a date window. UTR is corroborating evidence that raises or lowers confidence, never the primary key. |
| **2 — Batch to order** | Which of my orders are inside this payout, and is every deduction correct? | Explodes each batch to transaction level and **independently recomputes** the MDR and the GST from the merchant's contracted rates, rather than trusting the gateway's arithmetic. |
| **3 — GST to invoice** | How much input tax credit can I actually claim? | Ties summed per-transaction GST to the monthly tax invoice, then gates ITC on the statutory conditions and names any that fail. |
| **4 — Forward cash** | How much lands this week, and how sure are we? | Learns the merchant's real settlement lag from observed history rather than the contracted cycle, then projects only money that already exists, with a confidence band derived from observed variance. |
| **5 — Journal** | What are the postings? | Emits balanced double-entry journals with the GST leg split from the fee, plus a trial balance. Any entry that does not balance to the paise is rejected rather than exported. |

A separate **marketplace scenario** covers Razorpay Route / split-payout merchants, where Section 194-O TDS and Section 52 GST TCS do apply — including the reconciliation of those deductions against Form 26AS and GSTR-8, which lag the deduction by weeks.

Every decision — match and exception alike — is written to a SQLite audit trail with the rule that fired, the confidence, the values compared, and a human-readable reason. **The dashboard and the API read from that trail. Nothing is recomputed for display.** If the screen and the audit trail could disagree, the audit trail would be decorative.

## Verifying the verifier

Track 04's premise is that **verification capacity, not generation speed, is the bottleneck**. Sadhaka applies that premise to itself.

Eleven hand-planted faults prove an engine catches eleven hand-planted faults. That is a demonstration, not evidence. So there is a second harness that attacks the engine programmatically — **1,975 faults injected one at a time**, across nine fault types and a range of magnitudes — to answer three questions the engine's own report cannot.

### 1. What is the smallest fault it can actually detect?

Borrowed from analytical chemistry, where no instrument claims to measure everything: it states the smallest quantity it can distinguish from noise and refuses to claim anything below it.

| Fault injected | Trials | Detected | 95% CI | Floor | Verdict |
|---|---|---|---|---|---|
| MDR overcharged on one transaction | 350 | 100% | 99–100% | **₹0.09** | floor established |
| GST on the fee understated | 350 | 100% | 98–100% | **₹0.09** | floor established |
| Money skimmed from the bank credit | 350 | 100% | 98–100% | **₹0.01** | floor established |
| Settled gross inflated above the order | 350 | 100% | 99–100% | **₹0.01** | floor established |
| Settled transaction with no matching order | 350 | 100% | 99–100% | **₹0.01** | floor established |
| Bank credit delayed beyond the window | 175 | 100% | 98–100% | **3.0 days** | floor established |
| Settlement created, credit never arrived | 25 | 100% | 87–100% | — | underpowered |
| The same settlement credited twice | 25 | 100% | 87–100% | — | underpowered |
| Bank reference unrelated to the UTR | 25 | 100% | 87–100% | — | underpowered |

Faults smaller than the engine's own declared tolerance are excluded from these rates, because not detecting those is documented, correct behaviour rather than a miss.

**Zero blind spots.** The first run of this harness found two: delayed credits and corrupted bank references, both stuck at 76% detection. Both turned out to be a bug in the *harness*, not the engine — see bug #7 below. The remaining "underpowered" entries are categorical faults (25 trials each) where the interval is honestly too wide to claim a 90%+ floor; more trials would tighten them, and `--thorough` already runs as many as the profile allows.

### 2. Is the confidence score honest?

The engine attaches a confidence to every match. That number is a claim: of the matches scored at 0.85, roughly 85% should be right. Almost nothing checks. Correctness here is decided by the generator's ground truth — which bank credit really belongs to which settlement — not by the engine agreeing with itself.

| Claimed | Observed | Gap | N | Direction |
|---|---|---|---|---|
| 70.6% | 100.0% | +29.4% | 328 | underconfident |
| 86.3% | 100.0% | +13.7% | 382 | underconfident |
| 99.0% | 100.0% | +1.0% | 572 | calibrated |

Expected calibration error **0.121**, Brier score **0.028**, measured over **1,282 match decisions**.

The engine is **systematically underconfident** in its lower bands: it is right more often than it claims. That is the safe direction to be wrong in — it sends work to a human that a human did not need to do — but it is still miscalibration, and it is reported as such rather than presented as caution.

### 3. Counterfactual explanations

An exception that says "this did not match" tells a finance person a problem exists. It does not tell them what to do. So each exception carries the minimal change that would resolve it:

> **FEE_DEDUCTION on pay_DSBbszpPwIv9Iv** — This clears if the fee is corrected to ₹153.33, or if the contracted rate really is 2.400%. The two possibilities have very different consequences: a billing error is recoverable, an unrecorded rate change is not. At this transaction's frequency the difference is roughly ₹7,667 a year.

The size and shape of the required change is diagnostic. A gap equal to the fee means the fee was deducted twice. A gap equal to 18% of the fee means the GST leg is duplicated or missing. A gap of a few paise is rounding. When no small change would resolve an exception, the engine says so plainly instead of inventing a plausible cause — "no single adjustment would reconcile this" is itself a finding.

```bash
python3 src/run_verification.py --quick      # ~1 min, coarse
python3 src/run_verification.py              # standard
python3 src/run_verification.py --thorough   # establishes detection floors
```

## Results

Measured across **15 independently seeded datasets, 2,234 records**, not one cherry-picked run:

| Metric | Min | Mean | Max |
|---|---|---|---|
| Fault recall | 88.9% | **99.3%** | 100% |
| Exception classified with the expected code | 88.9% | 99.3% | 100% |
| Trap avoidance | 100% | **100%** | 100% |
| Bank credit to batch match | 100% | 100% | 100% |
| Transaction to order match | 96.9% | 97.0% | 98.4% |
| Value match | 94.7% | 97.0% | 98.8% |

One planted fault was missed in 1 of 15 datasets. That miss is reported rather than tuned away — `output/robustness_results.json` has the per-seed detail.

**The test suite is 68 assertions, all passing** (`python3 src/test_suite.py`), covering arithmetic that must never drift, matching under adversarial input, statutory rules, accounting invariants, end-to-end properties, determinism, and the verification harness itself — because a harness that makes claims about the engine is worthless if the harness is wrong. Two of them cross-check the engine against itself: the GST recoverable computed through the journal must equal the ITC claimable computed by Stage 3 via a completely different path, and the journal's bank debits must equal what the bank statement actually credited.

**Faults and traps are scored separately, on purpose.** A fault is caught by raising the right exception. A trap is passed by *not* producing a wrong match. Scoring them together would let the engine look good for the wrong reason.

### The exception list is split, deliberately

Reporting "15 exceptions" when 6 are timing lags and reserve holds is alarmism. Every exception is classified benign or actionable:

- **Benign** — the engine understands the variance and no one needs to act: `TIMING_LAG`, `ON_HOLD`, `ROUNDING`, `NOT_YET_SETTLED`
- **Actionable** — a human should look: `FEE_DEDUCTION`, `TAX_DEDUCTION`, `PARTIAL_PAYMENT`, `CHARGEBACK`, `DUPLICATE_CANDIDATE`, `UNEXPLAINED`

### Refusing to guess

When two settlements match a bank credit equally well and no UTR evidence separates them, the engine does **not** pick one. It raises `DUPLICATE_CANDIDATE` for review. A coin flip on money is worse than an honest exception. Any match scoring below 0.65 confidence is held for review rather than auto-accepted.

## Where AI is used, and where it is not

Deliberately narrow. **The matching engine is fully deterministic** — rule-based, with explicit tolerances and confidence scores. No LLM decides where money went, because such a decision cannot be audited or reproduced, and a regulator asking "why was this matched" needs an answer better than "the model thought so."

The language model's job is explanation over decisions already made: turning structured variance records into readable narrative, and answering natural-language questions against the audit trail. It reads the trail; it never writes to it.

## Forward cash forecasting, honestly

The forecast learns the settlement lag from what the gateway **actually did** — median days from capture to settlement, and from settlement to bank credit — rather than assuming the contracted T+2. The September 2025 RBI Payment Aggregator Directions replaced the fixed T+1 mandate with a contractually agreed timeline, so the contract and the behaviour can legitimately differ, and the forecast follows the behaviour.

The confidence band comes from the observed variance in that lag, not from a fixed assumption. A merchant whose settlements always land on day two gets a tight band; one whose settlements scatter gets a wide one and is told why.

**It deliberately does not forecast future sales.** Predicting demand from ten days of history would be a fabricated number wearing a confidence interval. It projects only money that already exists — captured orders and created settlements — which is the part that can be projected honestly. Amounts on hold are excluded rather than counted as delayed, because that money is not scheduled to land at all.

## Journal entries

Reconciliation that stops at "here is your variance" leaves someone retyping numbers into Tally. Stage 5 emits the postings:

```
Dr  Bank                        9,764.00
Dr  Payment gateway charges       200.00     (MDR — expense)
Dr  Input GST recoverable          36.00     (18% on MDR — asset)
    Cr  Razorpay clearing                  10,000.00
```

The GST leg matters. Booking ₹236 as a single expense silently forfeits ₹36 of input tax credit. Splitting it is the difference between claiming the credit and losing it.

## Reviewer credentials

Most of the app needs no sign-in — reconciliation results, GST/ITC, forecast, journal,
audit trail and verification are all open reads. Two surfaces are credentialed on
purpose, verified server-side rather than in the browser:

| Page | Role | Demo key |
|---|---|---|
| `/ask` (natural-language Q&A) | operator | `ashmit` |
| `/admin` (post ledger adjustments) | admin | `ashmit123` |

These are demo-only values, set via `SADHAKA_OPERATOR_KEY` and `SADHAKA_ADMIN_KEY`
when the backend starts. A visitor cannot grant themselves either role — the frontend
sends whatever is typed as a header, and the server independently verifies it against
these environment variables, rejecting anything that doesn't match. In a real
deployment each would be a private secret; publishing them here is reasonable only
because this is a reviewer-facing demo running on synthetic data.

## Running it

```bash
git clone <repo> && cd sadhaka
./run.sh
```

That installs dependencies, generates the dataset, runs all three stages, and prints the report. No API keys needed.

```bash
python3 src/test_suite.py                        # 56 assertions
python3 src/test_robustness.py --seeds 15        # multi-dataset robustness
python3 src/marketplace_scenario.py              # 194-O TDS and GST TCS scenario
uvicorn api.main:app --port 8000                 # API + Swagger at /docs
python3 -m http.server 5173 --directory frontend # dashboard
```

The dashboard bundles a snapshot of the last run, so it renders even with no backend — a cold server cannot break the demo. Live API data always takes precedence.

## Architecture

```
data/                                  synthetic, mirrors Razorpay's real schema
  orders.csv                           merchant's own records
  settlement_recon.csv                 26-column recon report (real field set)
  settlements.csv                      batch-level Settlement entity
  bank_statement.csv                   lumped NEFT credits
  razorpay_gst_invoice.csv             monthly tax invoice — the ITC document
  edge_cases.json                      answer key; the engine never reads this

src/
  config.py                 effective-dated rates + tolerances (a rate change is a config edit)
  generate_data.py          dataset generator with 11 planted faults and traps
  stage1_batch_matcher.py   bank credit  -> settlement batch
  stage2_order_matcher.py   batch        -> orders, recomputing fee and GST
  stage3_gst_itc.py         settlement GST -> monthly invoice -> ITC eligibility
  stage4_cash_forecast.py   forward cash position with learned lag + confidence band
  stage5_journal.py         double-entry postings and trial balance
  marketplace_scenario.py   194-O TDS and Sec 52 TCS for Route/split payouts
  reporting.py              exception report, metrics, self-scoring
  audit.py                  SQLite decision log
  run_pipeline.py           orchestration
  adversarial.py            fault injection: 9 attack types with declared noise floors
  verification.py           detection limits and confidence calibration
  counterfactual.py         minimal-change explanations for every exception
  run_verification.py       the adversarial harness orchestrator
  test_suite.py             68 assertions across 7 groups
  test_robustness.py        multi-seed harness

api/main.py                 FastAPI over the audit trail (Swagger at /docs)
frontend/index.html         reconciliation dashboard, offline snapshot fallback
frontend/verify.html        verification report: detection curves, calibration plot
```

## Statutory position

Modelled against the rules as they actually apply to a payment aggregator, because getting this wrong produces confident, wrong exceptions:

- **GST on MDR — 18%**, on the fee only, never on transaction value. Itemised in its own column. This is the only statutory line item in a pure PG settlement.
- **Section 194-O TDS — absent from the default flow by design.** It is an obligation of the *e-commerce operator*. A payment aggregator settling a merchant's own sales does not deduct it. Implemented and exercised in the marketplace scenario at 0.1% (5% without PAN), with the ₹5,00,000 Individual/HUF threshold applied and companies/firms/LLPs deducted from the first rupee.
- **Section 52 GST TCS — absent from the default flow by design.** Applies to marketplace operators collecting on behalf of third-party sellers. Implemented in the marketplace scenario at 0.5%, with sellers lacking a GSTIN flagged as an onboarding gap under Section 24(ix) rather than a calculation error.
- **Statement lag is modelled, not ignored.** TDS reaches Form 26AS only after the quarterly return is filed and processed, and TCS reaches GSTR-8 on the operator's filing. A correctly deducted amount therefore looks like a shortfall to the seller for weeks. The engine classifies that as a timing difference and states how many days remain before it stops being benign.
- **Settlement timing is configurable, not hardcoded.** The RBI Payment Aggregator Directions (effective 15 September 2025) removed the fixed T+1 mandate in favour of a contractually agreed timeline, so the cycle is a per-merchant parameter.

Rates are stored as effective-dated bands (194-O moved 1% → 0.1% on 2024-10-01; GST TCS 1% → 0.5% on 2024-07-10), so a notification change is a data edit rather than a code change.

## What broke, and how it got fixed

Three real failures during the build, kept here because they are the most informative part of the record.

**1. Refunds silently vanished from the dataset.**
Refunds and chargebacks were attached to a settlement batch by exact date match. Refunds are created 3–6 days after the original order, so any refund dated past the final batch had nothing to attach to and disappeared. The dataset looked completely healthy — every batch tied out to the paise, because the missing rows were missing from *both* sides of the equation. It only surfaced from counting row types: 2 refunds present out of 4 generated, and 0 of 1 disputes. Fixed by routing each event to the next available batch on or after its date, which is also what actually happens.

**2. Successful matches with a caveat never reached the exception report.**
Stage 1 recorded a variance code on the match object but only emitted an *exception* when matching failed. So a credit that matched correctly but arrived three days late, or whose bank reference had drifted, was recorded as a clean match and never shown. Recall sat at 7/11 and the report looked cleaner than reality. Fixed by emitting benign observations for matches that carry a variance code — a correct match with something worth knowing is still something the finance team should see.

**3. The scorer had no way to express "correctly avoided a trap."**
Two planted cases — the split settlement and the near-duplicate UTRs — are passed by *not* producing a wrong match. The scorer only knew how to check whether an exception was raised, so it recorded both as misses even though the engine had handled them correctly. Fixed by splitting the answer key into faults (caught by raising the right exception) and traps (passed by not falling in), scored separately.

**4. A statutory constant was wrong by a factor of ten.**
The Section 194-O threshold for resident Individual/HUF sellers is ₹5,00,000, and the constant was written `50_000_00` — which is ₹50,000. Nothing crashed. The rule simply applied to sellers it should have exempted, and it only surfaced because the marketplace scenario prints its rates back in formatted rupees and the number looked wrong on screen. Now written with explicit lakh grouping so it reads correctly. This is why the engine prints its own configuration: a wrong constant is invisible until something displays it.

**5. The verification harness reported two false blind spots.**
Its first run declared MDR overcharge and GST understatement undetectable. Both were fine. With 6 trials per magnitude level, a single miss reads as 83%, which never clears a 95% bar — so the harness had mistaken sampling noise for a detection failure and confidently published it. Fixed by reporting Wilson confidence intervals on every rate, refusing to claim a floor the sample size cannot support, and distinguishing "blind spot" (the detector fails) from "underpowered" (the measurement was too small to conclude anything). The second is a fact about the harness, not the engine, and conflating them was the bug.

**6. And the fix for that introduced its own error.**
Requiring the interval's *lower bound* to clear 95% sounds rigorous, but it needs roughly 73 trials even at flawless detection — so a detector scoring 25/25 was still labelled a blind spot. Separately, the harness was counting faults *smaller than the engine's own tolerance* as misses, which penalised the engine for behaving exactly as documented and dragged every rate down: delayed-credit detection read 65% when the true figure above tolerance was 89%. Fixed by having each fault type declare the threshold below which non-detection is correct, and by reporting the limit of detection as a point estimate with its interval, which is how any field that publishes one states it.

**7. Two "real" blind spots turned out to be a third harness bug — contamination between the seed dataset and the attack.**
After fixes 5 and 6, DATE_SHIFT and UTR_CORRUPT still sat at 76% detection with a tight enough interval to look genuine, and shipped as reported blind spots. They weren't. The synthetic dataset ships with its own built-in edge cases (a UTR-drift settlement, a delayed credit) so the *reconciliation engine* has something realistic to prove itself against. But those same two entities were also eligible targets for the *attack harness* — and when an attack landed on an entity clean data already flagged for an unrelated reason, the baseline-subtraction logic couldn't tell "already flagged" from "correctly caught the new problem," and scored a real detection as a miss. 2 of 11 bank rows were affected — almost exactly the ~24% miss rate observed. Fixed by excluding any entity already flagged on clean data from the injector's candidate pool, so every trial attacks a genuinely clean target. Detection on both went from 76% to 100%, and the harness now reports **zero blind spots** across all nine fault types. This is the deepest bug in the build: not the engine, not the first-order harness logic, but an interaction between the answer key and the harness that measures against it — and it stayed hidden behind two numbers that looked exactly like real, defensible findings.

**And one calibration error found by the scorer.** The bank credit date window was set to 4 days. A settlement's NEFT credit normally lands the same day, so a genuine 3-day bank delay was being absorbed as "within tolerance" and never surfaced. Tightened to 2 days — enough for a weekend, not enough to hide a real delay. This is exactly the class of bug that makes a reconciliation tool dangerous: it does not crash, it just quietly under-reports.

## Limitations

- All data is synthetic. The schema mirrors Razorpay's published settlement recon fields, but exact CSV headers vary by merchant configuration and should be validated against a live export.
- The GSTIN check is structural (length, state code, format). It does not verify registration status against the GST portal.
- 194-O and TCS are exercised only in the marketplace scenario, since neither applies to a pure payment aggregator. Real deployments should confirm their own classification with a tax advisor, because the definition of "e-commerce operator" is broad and the practical position rests on CBDT circulars rather than an explicit statutory exclusion.
- The cash forecast projects only existing money. It has no view of future sales, and says so rather than implying a fuller picture than it has.
- Tolerances are calibrated against this dataset's characteristics. Real merchant data would need recalibration, which is why they are config values with the reasoning written next to them.
- CORS is open for development and would be scoped to the deployed frontend origin before production use.
- The two reported blind spots are real and unfixed. They are reporting gaps on matches the engine makes correctly, and the honest thing is to ship them visible rather than quietly widen a tolerance until the harness stops complaining.
- Detection floors are established only for fault types with enough trials at the `--thorough` profile. The rest are reported as underpowered, which is a statement about the measurement rather than the engine.

## Author

Ashmit Sanjay Katale
