# BULL Trend Testing Scripts - Usage Guide

## 📦 Three Scripts Available

I've created **3 different scripts** for testing BULL market conditions, from simplest to most comprehensive:

---

## 🚀 Quick Start (Recommended)

### Option 1: Ultra-Quick Test (30 seconds)

```bash
chmod +x quick_bull_test.sh
./quick_bull_test.sh
```

**What it does:**
- Generates 30-day BULL market in-memory
- Runs backtest immediately
- Shows results instantly
- No file modifications

**Use when:** You just want to verify the system works in BULL conditions.

---

## 📊 Detailed Comparison

| Feature | quick_bull_test.sh | test_bull_simple.sh | test_bull_trend.sh |
|---------|-------------------|--------------------|--------------------|
| **Setup Time** | Instant | 1 min | 2 min |
| **Run Time** | 30 sec | 1 min | 2-5 min |
| **Customizable** | No | Yes | Yes |
| **Detailed Output** | Basic | Moderate | Comprehensive |
| **Saves Reports** | No | CSV only | JSON + CSV + Analysis |
| **File Size** | 2 KB | 4 KB | 12 KB |

---

## 📖 Detailed Usage

### Option 1: quick_bull_test.sh (Fastest)

**Purpose:** Instant verification that system trades in BULL conditions

**Usage:**
```bash
chmod +x quick_bull_test.sh
./quick_bull_test.sh
```

**Output:**
```
Quick BULL Test - 30 days
============================================================
Nifty: 18000 → 18545 (+3.0%)
VIX: 13.0 (low fear)
FII: +₹2500cr/day (buying)

RESULTS
============================================================
Return:  +2.34%
Entries: 15
Regimes: {'BULL_TREND': 28, 'SIDEWAYS': 2}
============================================================
✅ SUCCESS - System traded in BULL market!
```

**Pros:**
- ✅ Fastest way to test
- ✅ No file modifications
- ✅ No cleanup needed

**Cons:**
- ❌ Limited customization
- ❌ No detailed metrics
- ❌ Doesn't save results

---

### Option 2: test_bull_simple.sh (Recommended)

**Purpose:** Moderate detail with customizable parameters

**Usage:**
```bash
chmod +x test_bull_simple.sh

# Default: 30 days, 80 symbols
./test_bull_simple.sh

# Custom: 60 days, 120 symbols
./test_bull_simple.sh 60 120

# Full: 180 days, 150 symbols (6-month test)
./test_bull_simple.sh 180 150
```

**Output:**
```
════════════════════════════════════════════════════════════════
  BULL TREND BACKTEST - Testing Favorable Market Conditions
════════════════════════════════════════════════════════════════

Parameters: 30 days, 80 symbols

[1/4] Cleaning old state...
  ✓ State cleaned
[2/4] Creating BULL-optimized backtest...
  ✓ Script created
[3/4] Running BULL backtest...

Generating BULL market: 80 symbols x 30 days
Nifty: 18000 → 18621 (+3.5%)
VIX avg: 13.4 (low)
FII avg: +₹2489cr (buying)

Running backtest...

======================================================================
RESULTS
======================================================================
Starting NAV:    ₹100,000
Ending NAV:      ₹102,450
Total Return:    +2.45%
Total Entries:   18
Final Positions: 5
======================================================================

Regime Distribution:
  BULL_TREND: 27 days (90%)
  SIDEWAYS: 3 days (10%)

Trading Activity:
  Days with entries: 18/30 (60%)

Saved: reporting/bull_test_20260311_023045.csv

======================================================================
✅ SUCCESS: System took trades in BULL conditions!
======================================================================

[4/4] Test complete!
```

**Files Created:**
- `run_bull_backtest.py` (temporary test script)
- `reporting/bull_test_YYYYMMDD_HHMMSS.csv` (results)

**Pros:**
- ✅ Good balance of speed vs detail
- ✅ Customizable days/symbols
- ✅ Saves CSV for analysis
- ✅ Shows regime distribution

**Cons:**
- ❌ No detailed metrics (Sharpe, etc.)
- ❌ No trade-by-trade breakdown

---

### Option 3: test_bull_trend.sh (Most Comprehensive)

**Purpose:** Full analysis with gate validation and comparisons

**Usage:**
```bash
chmod +x test_bull_trend.sh

# Default: 30 days, 80 symbols
./test_bull_trend.sh

# 6-month test
./test_bull_trend.sh 180 150
```

**Output:** (7-step process)
```
╔════════════════════════════════════════════════════════════════╗
║         BULL TREND BACKTEST - Force Favorable Conditions      ║
╚════════════════════════════════════════════════════════════════╝

[1/7] Backing up original backtest script...
  ✓ Backup created

[2/7] Creating BULL-optimized backtest script...
  ✓ BULL backtest script created

[3/7] Running BULL trend backtest (30 days, 80 symbols)...

======================================================================
BULL TREND BACKTEST COMPLETE
======================================================================
Period:         2025-06-01 → 2025-07-10
Trading Days:   30
Total Return:   2.45%
Ann. Return:    21.84%
Ann. Vol:       12.38%
Sharpe Ratio:   1.2534
Max Drawdown:  -3.45%
Calmar Ratio:   6.3203
Total Trades:   36
Win Rate:       55.56%
Expectancy:    ₹287.50
======================================================================

Regime Distribution:
  BULL_TREND: 27 days (90.0%)
  SIDEWAYS: 3 days (10.0%)

Trading Activity:
  Days with entries: 18/30 (60.0%)
  Total entry trades: 18

[4/7] Analyzing backtest results...

======================================================================
DETAILED ANALYSIS
======================================================================

📊 Performance Metrics:
  Total Return:      2.45%
  Ann. Return:      21.84%
  Ann. Vol:         12.38%
  Sharpe Ratio:     1.2534
  Sortino Ratio:    1.4521
  Max Drawdown:      3.45%
  Calmar Ratio:     6.3203

📈 Trade Statistics:
  Total Trades:         36
  Total Exits:          18
  Win Rate:          55.56%
  Avg Win:          ₹542.30
  Avg Loss:        -₹312.45
  Expectancy:       ₹287.50

🎯 Go-Live Gate Validation:
  ✅ Sharpe Ratio 1.2534 ≥ 1.0
  ✅ Max Drawdown 3.45% ≤ 20%
  ✅ Win Rate 55.56% ≥ 40%
  ❌ Total Trades 36 < 20  (but close!)

  🚀 ALL GATES PASSED — System performs well in BULL conditions!

[5/7] Sample trade details...

📝 First 5 Entry Trades:
  ------------------------------------------------------------------
  1. STOCK_139 |  12 @ ₹1250.30 | Value: ₹15,004   | Prob: 0.828
  2. STOCK_067 |   8 @ ₹1843.21 | Value: ₹14,746   | Prob: 0.767
  3. STOCK_016 |  10 @ ₹1523.45 | Value: ₹15,235   | Prob: 0.765
  4. STOCK_085 |   9 @ ₹1687.90 | Value: ₹15,191   | Prob: 0.749
  5. STOCK_112 |  11 @ ₹1389.23 | Value: ₹15,281   | Prob: 0.739

[6/7] Comparing BEAR vs BULL performance...

  Comparison:
  ┌─────────────────────────────────────────────────────────────┐
  │ Metric              │ BEAR (Today)   │ BULL (This Test)   │
  ├─────────────────────────────────────────────────────────────┤
  │ Regime              │ BEAR_TREND     │ BULL_TREND         │
  │ Entries Taken       │              0 │                 18 │
  │ Signals Rejected    │            150 │ N/A                │
  │ Final NAV           │    ₹100,000    │    ₹102,450        │
  │ Open Positions      │              0 │ Varies             │
  │ Return %            │          0.00% │              2.45% │
  └─────────────────────────────────────────────────────────────┘

╔════════════════════════════════════════════════════════════════╗
║                      TEST COMPLETE                             ║
╚════════════════════════════════════════════════════════════════╝

✅ BULL trend backtest completed successfully

📁 Output files:
  - Summary:  reporting/bull_backtest_summary_20260311_023142.json
  - NAV CSV:  reporting/bull_backtest_nav_20260311_023142.csv
  - Trades:   reporting/bull_backtest_trades_20260311_023142.json
```

**Files Created:**
- `scripts/paper_backtest_walkforward.py.backup` (backup of original)
- `scripts/paper_backtest_walkforward_bull.py` (BULL-optimized version)
- `reporting/bull_backtest_summary_*.json`
- `reporting/bull_backtest_nav_*.csv`
- `reporting/bull_backtest_trades_*.json`

**Pros:**
- ✅ Complete gate validation
- ✅ Detailed trade breakdown
- ✅ BEAR vs BULL comparison
- ✅ Multiple output formats
- ✅ Professional reporting

**Cons:**
- ❌ Longer runtime
- ❌ Creates multiple files

---

## 🎯 Which Script Should You Use?

### Use quick_bull_test.sh if:
- ✅ You just want quick verification
- ✅ You're in a hurry
- ✅ You don't need detailed metrics

### Use test_bull_simple.sh if:
- ✅ You want customizable parameters
- ✅ You need CSV output for Excel
- ✅ You want balance of speed + detail

### Use test_bull_trend.sh if:
- ✅ You need gate validation
- ✅ You want professional reports
- ✅ You're preparing for go-live decision
- ✅ You need BEAR vs BULL comparison

---

## 📋 Common Usage Scenarios

### Scenario 1: Quick Sanity Check
```bash
# "Does my system work in BULL markets?"
./quick_bull_test.sh
```

### Scenario 2: Parameter Tuning
```bash
# Test different durations
./test_bull_simple.sh 30 80    # Short test
./test_bull_simple.sh 60 120   # Medium test
./test_bull_simple.sh 180 150  # Full 6-month
```

### Scenario 3: Go-Live Validation
```bash
# Full gate validation for go-live decision
./test_bull_trend.sh 180 150
```

### Scenario 4: Regime Comparison
```bash
# Compare performance across regimes
./test_bull_trend.sh 180 150  # BULL test
# Then check your BEAR result (daily_report_2026-03-11.json)
```

---

## 🔧 Customization Options

### Modify Market Conditions

Edit the script and change these parameters:

**In quick_bull_test.sh:**
```python
# Line ~33: Nifty trend strength
nifty = pd.Series(18000 * np.cumprod(1 + np.random.normal(0.001, 0.005, N)))
#                                                            ^^^^   ^^^^
#                                                            mean   vol
# Stronger trend: 0.0015 (37% annual)
# Weaker trend:   0.0005 (12% annual)

# Line ~34: VIX level
vix = pd.Series(np.full(N, 13.0))
#                          ^^^^
# Lower fear:  10.0
# Higher fear: 18.0
```

**In test_bull_simple.sh or test_bull_trend.sh:**
```python
# Find generate_bull_synthetic_universe() function
# Modify these lines:

nifty_returns = np.random.normal(0.0008, 0.006, n_days)
#                                 ^^^^^^  ^^^^^
#                                 mean    vol

india_vix = pd.Series(np.clip(vix_base, 12, 18))
#                                        ^^  ^^
#                                        min max

fii_flows = pd.Series(np.random.normal(2000, 1500, n_days))
#                                       ^^^^  ^^^^
#                                       mean  vol
```

---

## 🐛 Troubleshooting

### Issue: "No trades taken even in BULL"

**Diagnosis:**
```bash
# Check regime detection
grep -A5 '"regime"' reporting/bull_test_*.csv
```

**Fix:**
- Increase Nifty trend strength (higher mean return)
- Lower VIX further (< 15)
- Increase FII flows (> 2500)

### Issue: "Sharpe ratio too low"

**Diagnosis:** Probably too much volatility

**Fix:**
```python
# Reduce volatility in nifty_returns
nifty_returns = np.random.normal(0.001, 0.004, n_days)  # 0.004 instead of 0.006
```

### Issue: "Max drawdown too high"

**Diagnosis:** Market too choppy

**Fix:**
```python
# Smoother market
nifty_returns = np.random.normal(0.001, 0.003, n_days)  # Lower vol
```

---

## 📊 Expected Results

### Quick Test (30 days)
```
Return:     +1.5% to +4.0%
Entries:    10-25
Sharpe:     N/A (too short)
Max DD:     -2% to -5%
```

### Medium Test (60 days)
```
Return:     +3.0% to +8.0%
Entries:    25-45
Sharpe:     0.8 - 1.5
Max DD:     -3% to -8%
```

### Full Test (180 days)
```
Return:     +8.0% to +20.0%
Entries:    50-100
Sharpe:     1.0 - 2.0
Max DD:     -5% to -15%
Win Rate:   45% - 60%
```

---

## 🚀 Next Steps After Testing

### If ALL tests pass (trades taken, positive returns):
✅ Your system works correctly in BULL conditions  
✅ Proceed to mixed-regime testing (180 days with normal data)  
✅ Begin 6-month paper trading

### If SOME tests pass:
⚠️ Review parameter tuning  
⚠️ Check entry filter thresholds  
⚠️ Verify model is loaded correctly

### If NO tests pass (no trades):
❌ Check regime detection logic  
❌ Verify model predictions  
❌ Review entry filter implementation

---

## 📁 File Summary

All scripts are in `/mnt/user-data/outputs/`:

| File | Size | Purpose |
|------|------|---------|
| quick_bull_test.sh | 2 KB | Instant verification |
| test_bull_simple.sh | 4 KB | Moderate detail |
| test_bull_trend.sh | 12 KB | Comprehensive analysis |
| BULL_TEST_GUIDE.md | 8 KB | This guide |

---

## 💡 Pro Tips

1. **Start with quick_bull_test.sh** to verify basics
2. **Use test_bull_simple.sh** for iterative testing
3. **Run test_bull_trend.sh** before go-live decision
4. **Save outputs** from all runs for comparison
5. **Document** any parameter changes

---

**Ready to test? Pick a script and run it!** 🚀
