# Quick Reference — Daily Operations

## 🚀 Essential Commands (Copy-Paste Ready)

### Train Model (First Time)
```bash
# Synthetic (for testing)
python scripts/train_direction_model.py --synth --n-symbols 200 --n-days 400 --seed 42

# Real data
python scripts/train_direction_model.py --features-path data/features_train.parquet
```

### Run Backtest
```bash
# 30-day smoke test
python scripts/paper_backtest_walkforward.py --synth --n-days 30 --n-symbols 80

# 6-month validation
python scripts/paper_backtest_walkforward.py --synth --n-days 180 --n-symbols 150
```

### Run All Tests
```bash
pytest tests/ -v --tb=short
```

### Run EOD Cycle (Daily)
```bash
python portfolio_manager.py  # Demo with synthetic data
# Or implement your EOD script with real data feed
```

### Verify Audit Chain
```bash
python -c "from audit.logger import AuditVerifier; print('✅ Valid' if AuditVerifier.verify_date('2026-02-20') else '❌ Invalid')"
```

---

## 📊 Acceptance Gates

### Before Go-Live (Must Pass All)
| Gate | Threshold | Check |
|------|-----------|-------|
| Sharpe Ratio | ≥ 1.0 | Backtest summary JSON |
| Max Drawdown | ≤ 20% | Backtest summary JSON |
| Paper Duration | ≥ 6 months | Start date tracking |
| Win Rate | ≥ 40% | Backtest summary JSON |
| Total Trades | ≥ 20 | Backtest summary JSON |
| Audit Chain | Valid | AuditVerifier.verify_date() |
| All Tests | Passing | pytest exit code 0 |

### Check Backtest Results
```bash
# View latest backtest summary
cat reporting/walkforward_summary_*.json | jq '.sharpe_ratio, .max_drawdown_pct, .win_rate_pct'
```

---

## 🔧 Troubleshooting

### Tests Fail
```bash
# Check which test failed
pytest tests/ -v --tb=short

# Run specific test
pytest tests/test_orders_and_exits.py::test_order_rejection_no_cash_change -v
```

### Training Fails
```bash
# Check logs
tail -100 portfolio_manager.log

# Try with smaller dataset
python scripts/train_direction_model.py --synth --n-symbols 50 --n-days 100 --no-cv
```

### Backtest Crashes
```bash
# Check for state corruption
rm portfolio_state.json order_book.jsonl

# Re-run
python scripts/paper_backtest_walkforward.py --synth --n-days 10 --n-symbols 20
```

---

## 📁 Key Files

| File | Purpose |
|------|---------|
| `portfolio_state.json` | Current positions, cash, realized P&L |
| `order_book.jsonl` | All orders (requested, filled, remaining) |
| `audit/logs/audit_YYYY-MM-DD.jsonl` | Daily immutable decision log |
| `reporting/daily/daily_report_YYYY-MM-DD.json` | Daily performance report |
| `reporting/walkforward_summary_*.json` | Backtest metrics |
| `models/saved/model_xgb_v1_0_0.pkl` | Trained XGBoost model |

---

## 🚨 Emergency Procedures

### Halt All Trading
```python
# In config/settings.py
MAX_POSITIONS = 0  # Prevents new entries
```

### Manual Position Close
1. Log into broker UI (Zerodha/Kite)
2. Manually close position
3. Update `portfolio_state.json` to reflect closure
4. Run reconciliation: `python -c "from portfolio_manager import PortfolioManager; PortfolioManager().reconcile_with_broker()"`

### Restart After Crash
```bash
# State auto-loads on init
python portfolio_manager.py

# Check reconciliation audit events
grep "RECONCILE" audit/logs/audit_*.jsonl
```

---

## 📈 Daily Monitoring Checklist

- [ ] Check latest daily report: `ls -lt reporting/daily/`
- [ ] Verify no order rejections: `grep ORDER_REJECTED audit/logs/audit_$(date +%Y-%m-%d).jsonl`
- [ ] Check reconciliation clean: `grep RECONCILE audit/logs/audit_$(date +%Y-%m-%d).jsonl`
- [ ] Review realized P&L: `jq '.portfolio_summary.realised_pnl_today' reporting/daily/daily_report_*.json | tail -1`
- [ ] Check drawdown state: `jq '.risk_status.drawdown_state' reporting/daily/daily_report_*.json | tail -1`

---

## 🎯 Go-Live Transition

```bash
# 1. Complete PRE_GO_LIVE.md checklist
cat DOCS/PRE_GO_LIVE.md

# 2. Verify all gates passed
pytest tests/ -v
python scripts/paper_backtest_walkforward.py --synth --n-days 180 --n-symbols 150

# 3. Switch to live mode
# In config/settings.py:
PAPER_MODE = False
BROKER_API_KEY = "your_api_key"
BROKER_API_SECRET = "your_secret"

# 4. Start with small capital
# In config/settings.py:
CAPITAL = 100000  # ₹1L as specified

# 5. First live run
python portfolio_manager.py --live  # Implement --live flag if needed
```

---

**Print this card for quick reference during development and paper trading!**
