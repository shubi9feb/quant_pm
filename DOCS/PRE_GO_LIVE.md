# Pre-Go-Live Checklist

**Purpose:** Ensure the quantitative portfolio manager is production-ready before transitioning from paper trading to live capital deployment.

**Owner:** [Your Name]  
**Reviewer:** [Reviewer Name]  
**Target Go-Live Date:** [YYYY-MM-DD]

---

## Phase 1: Model Training & Validation

### 1.1 Model Training
- [ ] Train direction model on at least **400 days** of historical data
  ```bash
  python scripts/train_direction_model.py --features-path data/features_train.parquet
  ```
- [ ] Verify model saved: `models/saved/model_xgb_v1_0_0.pkl` exists
- [ ] Review training metrics in `reporting/model_metrics_*.json`
  - [ ] OOS AUC ≥ 0.55 (minimum threshold)
  - [ ] OOS Precision ≥ 0.50
  - [ ] Walk-forward CV completed successfully (5 folds)

### 1.2 Model Calibration
- [ ] Generate calibration plots (reliability curve)
- [ ] Verify probability threshold of **0.65** is appropriate
  - [ ] Run threshold sweep: 0.55, 0.60, 0.65, 0.70, 0.75
  - [ ] Document trade-off: precision vs recall vs # trades
- [ ] Confirm no overfitting (in-sample vs OOS performance gap < 10%)

---

## Phase 2: Backtesting & Performance Validation

### 2.1 Walk-Forward Backtest (6 Months Minimum)
- [ ] Run full historical backtest on **≥180 days** of data
  ```bash
  python scripts/paper_backtest_walkforward.py --features-path data/features_backtest.parquet --n-days 180
  ```
- [ ] Review `reporting/walkforward_summary_*.json`
- [ ] **Hard Gates (Must Pass):**
  - [ ] **Sharpe Ratio ≥ 1.0**
  - [ ] **Max Drawdown ≤ 20%**
  - [ ] Win rate ≥ 40%
  - [ ] Expectancy > 0 (positive expected value per trade)
  - [ ] Total trades ≥ 20 (sufficient sample size)

### 2.2 Sensitivity Analysis
- [ ] Transaction cost sensitivity (10bps, 30bps, 50bps slippage)
  - [ ] Sharpe remains ≥ 0.8 at 30bps slippage
- [ ] Regime sensitivity
  - [ ] Test performance in BULL_TREND, SIDEWAYS, BEAR_TREND periods separately
  - [ ] Confirm drawdown protection activates correctly (12% → REDUCED_BUYS, 18% → CASH_MODE)

### 2.3 Risk Parameter Validation
- [ ] Verify risk limits enforced:
  - [ ] Max 8 positions
  - [ ] Max 15% per stock
  - [ ] 1.5% risk per trade
  - [ ] 10% gold hedge maintained
  - [ ] 10% cash buffer maintained
- [ ] Confirm stop losses triggered correctly (no "runaway losses")
- [ ] Verify trailing stops ratchet (never decrease)

---

## Phase 3: Audit & State Integrity

### 3.1 Audit Chain Verification
- [ ] Run audit verifier on all generated logs
  ```bash
  python -c "from audit.logger import AuditVerifier; print(AuditVerifier.verify_date('2026-02-20'))"
  ```
- [ ] **Result must be: `True` (hash chain valid)**
- [ ] Spot-check 5 random audit records for completeness:
  - [ ] SYSTEM_START event
  - [ ] REGIME_DETECTION events
  - [ ] ORDER_PLACED / ORDER_FILLED events
  - [ ] STOP_UPDATED events
  - [ ] DAILY_SUMMARY events

### 3.2 State Persistence
- [ ] Test save/load cycle:
  ```bash
  # Run demo, save state, restart, verify state restored
  python portfolio_manager.py  # or your demo script
  ```
- [ ] Confirm `portfolio_state.json` written atomically (no corruption on crash simulation)
- [ ] Verify `order_book.jsonl` tracks all orders correctly

### 3.3 Reconciliation
- [ ] Run `reconcile_with_broker()` after simulated trades
- [ ] Verify no `RECONCILE_MISSING_POSITION` or `RECONCILE_STALE_POSITION` events for known good state
- [ ] Intentionally create mismatch, confirm reconciliation detects it

---

## Phase 4: Operational Readiness

### 4.1 Broker Integration
- [ ] Configure Zerodha Kite Connect credentials
  - [ ] `BROKER_API_KEY` set in environment
  - [ ] `BROKER_API_SECRET` set in environment
  - [ ] API access validated (test order placement in sandbox/paper mode)
- [ ] Verify `PaperBroker` → `ZerodhaBrokerAdapter` switch works
  ```python
  # In config/settings.py
  PAPER_MODE = False  # Switch to live
  ```
- [ ] Test order placement with ₹100 (minimum) in live sandbox
- [ ] Confirm bracket orders work (entry + stop + optional target)

### 4.2 Monitoring & Alerts
- [ ] Set up log rotation (`portfolio_manager.log` max 10MB, 10 backups)
- [ ] Configure alerts for:
  - [ ] `ORDER_REJECTED` events
  - [ ] `RECONCILE_*` events (≥3 in one day)
  - [ ] Drawdown enters `CASH_MODE` (18%+)
  - [ ] AuditVerifier fails (hash chain broken)
- [ ] Test alert delivery (email/Slack/SMS)

### 4.3 Daily Operations Runbook
- [ ] Document EOD procedure:
  1. Fetch EOD prices (source: [specify])
  2. Generate features: `python data/run_feature_pipeline.py`
  3. Run EOD cycle: `python portfolio_manager.py --eod-mode`
  4. Review daily report: `reporting/daily/daily_report_YYYY-MM-DD.json`
  5. Verify audit logs: `python scripts/verify_audit.py`
  6. Check reconciliation: `python -m portfolio_manager reconcile`
- [ ] Schedule automated run (cron/systemd timer) or keep manual
- [ ] Define escalation path for failures

### 4.4 Recovery & Contingency
- [ ] Document manual intervention procedures:
  - [ ] How to manually close a position (broker UI)
  - [ ] How to restart PM after crash (state recovery)
  - [ ] How to disable trading (set `MAX_POSITIONS=0`)
- [ ] Test state recovery:
  - [ ] Simulate crash mid-EOD, restart, verify idempotence
- [ ] Emergency stop procedure (kill switch):
  ```bash
  # Close all positions immediately
  python scripts/emergency_close_all.py  # (create this script)
  ```

---

## Phase 5: Final Sign-Off

### 5.1 Code Review
- [ ] **Reviewer:** Full code review completed
  - [ ] Entry/exit logic reviewed
  - [ ] Risk engine parameters validated
  - [ ] No hard-coded credentials
  - [ ] All TODOs addressed
- [ ] **Owner:** All review comments addressed

### 5.2 Testing Sign-Off
- [ ] All unit tests pass: `pytest tests/ -v`
- [ ] CI pipeline green (GitHub Actions)
- [ ] Backtest gates passed (Sharpe ≥1.0, MaxDD ≤20%)

### 5.3 Compliance & Legal
- [ ] Risk disclosure acknowledged (owner signature)
- [ ] Confirm capital allocation: **₹1,00,000 maximum** (as per config)
- [ ] Tax implications reviewed (STCG, LTCG, STT)
- [ ] Regulatory compliance (if required for jurisdiction)

### 5.4 Go-Live Approval
- [ ] **Owner Signature:** __________________ Date: __________
- [ ] **Reviewer Signature:** __________________ Date: __________
- [ ] **Go-Live Authorized:** YES / NO

---

## Phase 6: Post-Go-Live Monitoring (First 30 Days)

### 6.1 Daily Checks (Days 1-30)
- [ ] Review daily report for anomalies
- [ ] Check order rejection rate (<5%)
- [ ] Verify reconciliation clean (no mismatches)
- [ ] Monitor realized P&L vs backtest expectations

### 6.2 Weekly Reviews (Weeks 1-4)
- [ ] Compare live vs paper performance
- [ ] Review filled prices vs expected (slippage analysis)
- [ ] Check regime detection accuracy
- [ ] Audit any manual interventions (document reasons)

### 6.3 Monthly Rebalance & Re-Score
- [ ] Run fundamental re-score (1st of month)
- [ ] Rebalance oversized positions (Fridays)
- [ ] Review drawdown distribution vs historical
- [ ] Retrain model if OOS performance degrades >10%

---

## Appendix: Key Metrics Thresholds

| Metric | Minimum | Target | Action if Below |
|--------|---------|--------|-----------------|
| Sharpe Ratio | 1.0 | 1.5+ | Halt live trading, review model |
| Max Drawdown | <20% | <15% | Reduce position sizes, tighten stops |
| Win Rate | 40% | 50%+ | Review entry filters |
| Expectancy | >0 INR | >₹500 | Check cost model, slippage |
| Model OOS AUC | 0.55 | 0.60+ | Retrain with more features |

---

## Document Control

**Version:** 1.0  
**Last Updated:** 2026-02-20  
**Next Review:** [30 days post go-live]  
**Change Log:**
- 2026-02-20: Initial checklist created (v1.0)

---

## Notes

- This checklist assumes **paper trading for ≥6 months** before go-live
- All thresholds are based on Indian equity markets (NSE cash segment)
- Adjust risk parameters if managing >₹1L capital
- Re-validate checklist quarterly or after major model changes
