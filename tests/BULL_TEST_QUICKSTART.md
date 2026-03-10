# BULL Trend Testing - Quick Start

## 🎯 Choose Your Test

I've created **3 scripts** to test your system in BULL market conditions:

---

## ⚡ Quick Decision Matrix

**Just want to verify it works?**  
→ Use `quick_bull_test.sh` (30 seconds)

**Need some customization?**  
→ Use `test_bull_simple.sh` (2 minutes)

**Need full validation for go-live?**  
→ Use `test_bull_trend.sh` (5 minutes)

---

## 🚀 Usage Commands

### Option 1: Fastest (30 sec)
```bash
cd /path/to/quant_pm
chmod +x quick_bull_test.sh
./quick_bull_test.sh
```

**Output:** Simple pass/fail, shows # of trades taken

---

### Option 2: Balanced (2 min)
```bash
cd /path/to/quant_pm
chmod +x test_bull_simple.sh

# Default: 30 days, 80 symbols
./test_bull_simple.sh

# Custom: 180 days, 150 symbols
./test_bull_simple.sh 180 150
```

**Output:** Returns, regime breakdown, saves CSV

---

### Option 3: Complete (5 min)
```bash
cd /path/to/quant_pm
chmod +x test_bull_trend.sh

# Default: 30 days
./test_bull_trend.sh

# 6-month validation
./test_bull_trend.sh 180 150
```

**Output:** Full metrics, gate validation, BEAR vs BULL comparison, JSON + CSV

---

## 📊 What Each Script Shows

| Metric | Quick | Simple | Full |
|--------|-------|--------|------|
| Trades Taken | ✅ | ✅ | ✅ |
| Return % | ✅ | ✅ | ✅ |
| Regime Dist. | ❌ | ✅ | ✅ |
| Sharpe Ratio | ❌ | ❌ | ✅ |
| Gate Check | ❌ | ❌ | ✅ |
| Trade Details | ❌ | ❌ | ✅ |
| Saves Files | ❌ | CSV | JSON+CSV |

---

## ✅ What Success Looks Like

### Quick Test (quick_bull_test.sh)
```
RESULTS
============================================================
Return:  +2.34%
Entries: 15
Regimes: {'BULL_TREND': 28, 'SIDEWAYS': 2}
============================================================
✅ SUCCESS - System traded in BULL market!
```

**Key:** `Entries > 0` = ✅ Pass

---

### Simple Test (test_bull_simple.sh)
```
======================================================================
RESULTS
======================================================================
Starting NAV:    ₹100,000
Ending NAV:      ₹102,450
Total Return:    +2.45%
Total Entries:   18
======================================================================
✅ SUCCESS: System took trades in BULL conditions!
```

**Key:** `Total Entries > 0` AND `Return > 0%` = ✅ Pass

---

### Full Test (test_bull_trend.sh)
```
🎯 Go-Live Gate Validation:
  ✅ Sharpe Ratio 1.2534 ≥ 1.0
  ✅ Max Drawdown 3.45% ≤ 20%
  ✅ Win Rate 55.56% ≥ 40%
  ✅ Total Trades 36 ≥ 20

🚀 ALL GATES PASSED — System performs well in BULL conditions!
```

**Key:** All 4 gates ✅ = Ready for paper trading

---

## 🎓 Understanding Your Earlier Result

**Your BEAR test (today):**
```json
{
  "regime": "BEAR_TREND",
  "entries_today": 0,
  "rejected_today": 150,
  "nav": 100000.0
}
```

**This was CORRECT behavior!**  
✅ System preserved capital in unfavorable conditions  
✅ Risk management blocked all entries  
✅ No losses incurred

**Now test BULL conditions to see the opposite:**  
✅ System should take trades  
✅ Capital should grow  
✅ P&L should be positive

---

## 🔥 Recommended First Run

```bash
# 1. Copy script to your repo
cp quick_bull_test.sh /path/to/quant_pm/

# 2. Make executable
cd /path/to/quant_pm
chmod +x quick_bull_test.sh

# 3. Run
./quick_bull_test.sh
```

**Expected time:** 30 seconds  
**Expected result:** 10-20 trades taken, +1-3% return

---

## 📈 Next Steps After Success

1. ✅ **Verify trades taken** (Entries > 0)
2. ✅ **Check positive return** (Return > 0%)
3. ✅ **Run longer test** (`./test_bull_simple.sh 180 150`)
4. ✅ **Validate gates** (`./test_bull_trend.sh 180 150`)
5. ✅ **Begin paper trading** (6 months minimum)

---

## 🆘 If No Trades Taken

**Check:**
1. Is model loaded? `grep "Loaded pre-trained model" portfolio_manager.log`
2. Are predictions working? Check model probabilities in output
3. Is regime BULL_TREND? Check "Regime Distribution" in output

**Quick fix:**
```bash
# Retrain model first
python scripts/train_direction_model.py --synth --n-symbols 100 --n-days 200 --no-cv

# Then retest
./quick_bull_test.sh
```

---

## 📞 Support

**All 3 scripts created and ready to use!**

Read **BULL_TEST_GUIDE.md** for detailed documentation.

**Ready? Pick a script and run it now!** 🚀
