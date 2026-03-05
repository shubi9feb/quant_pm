# File Application Guide — What to Copy Where

## 🎯 Quick Copy Commands

All files are in `/mnt/user-data/outputs/phase2_complete/`

```bash
# Navigate to your repo root
cd /path/to/quant_pm

# Set source path (adjust as needed)
SRC=/path/to/outputs/phase2_complete

# Copy new directories
cp -r $SRC/utils ./
cp -r $SRC/tests ./
cp -r $SRC/scripts ./
cp -r $SRC/.github ./
cp -r $SRC/DOCS ./

# Copy new root files
cp $SRC/order_book.py ./
cp $SRC/verify_phase1.py ./

# Update existing files (BACKUP FIRST!)
cp $SRC/portfolio_manager.py ./portfolio_manager.py.new
cp $SRC/README.md ./README.md.new

# Review diffs before overwriting
diff portfolio_manager.py portfolio_manager.py.new
diff README.md README.md.new

# If satisfied, replace
mv portfolio_manager.py.new portfolio_manager.py
mv README.md.new README.md
```

---

## 📋 Complete File List

### New Files (11 files)

**Foundation (Phase 1):**
```
utils/
  __init__.py          (0 bytes, package marker)
  fs_atomic.py         (96 lines, atomic JSON operations)

tests/
  __init__.py          (0 bytes, package marker)
  mocks.py             (172 lines, MockBroker)

order_book.py          (182 lines, persistent order registry)
verify_phase1.py       (78 lines, verification script)
```

**Tests (Phase 2):**
```
tests/
  test_orders_and_exits.py        (434 lines, order flow tests)
  test_state_persistence.py       (361 lines, save/load + reconciliation)
  test_training_and_backtest.py   (271 lines, integration tests)
```

**Scripts (Phase 2):**
```
scripts/
  __init__.py                     (0 bytes, package marker)
  train_direction_model.py        (331 lines, training pipeline)
  paper_backtest_walkforward.py   (369 lines, backtest runner)
```

**CI/CD (Phase 2):**
```
.github/
  workflows/
    ci.yml                        (173 lines, GitHub Actions)
```

**Documentation (Phase 2):**
```
DOCS/
  PRE_GO_LIVE.md                  (380 lines, go-live checklist)
```

### Modified Files (2 files)

```
portfolio_manager.py              (5 changes: imports, __init__, entry/exit tracking, load_state)
README.md                         (Quick Start section updated)
```

---

## 🔍 Changes to Existing Files

### portfolio_manager.py (5 changes)

**Change 1: Add imports (line 53)**
```python
from utils.fs_atomic import atomic_write_json
from order_book import OrderBook  # <-- ADD THIS LINE
```

**Change 2: Initialize order_book (line 177)**
```python
self.report_builder   = DailyReportBuilder()
self.order_book       = OrderBook()  # <-- ADD THIS LINE

self.run_id           = str(uuid.uuid4())[:8]

self._load_model_if_available()

# Load persisted state if available      # <-- ADD THESE 5 LINES
self.load_state()

# Reconcile with broker after state load
self.reconcile_with_broker()

self.audit.log_system_start({
```

**Change 3: Track entry orders (lines 504-523)**
```python
# Deduct cash only for the filled amount
self.cash -= total_cost

# ADD THIS BLOCK (17 lines) -->
# Track order in persistent order book
self.order_book.add_order(
    client_order_id=getattr(resp, "client_order_id", getattr(order, "client_order_id", None)),
    symbol=sym,
    side="BUY",
    requested_qty=size.shares,
    entry_price=fill_price
)

# Update fill status in order book
self.order_book.update_fill(
    client_order_id=getattr(resp, "client_order_id", getattr(order, "client_order_id", None)),
    filled_qty=filled_qty,
    status="FILLED" if filled_qty == size.shares else "PARTIAL"
)
# <-- END BLOCK

# Register open position using resp info
```

**Change 4: Track exit orders (lines 299-320)**
```python
self.realised_pnl += realized
self.cash += position_value - costs["total_cost"]

# ADD THIS BLOCK (17 lines) -->
# Track exit order in order book
exit_order_id = getattr(resp, "client_order_id", getattr(exit_order, "client_order_id", None))
self.order_book.add_order(
    client_order_id=exit_order_id,
    symbol=sym,
    side="SELL",
    requested_qty=pos.quantity,
    entry_price=fill_price
)
self.order_book.update_fill(
    client_order_id=exit_order_id,
    filled_qty=filled_qty,
    status="FILLED" if filled_qty >= pos.quantity else "PARTIAL"
)
# <-- END BLOCK

exits_today.append(TradeRecord(
```

**Change 5: Update load_state() (lines 712-737)**
```python
def load_state(self, path: str = "portfolio_state.json"):
    """Restore portfolio state from disk using atomic read."""  # <-- CHANGE DOCSTRING
    from utils.fs_atomic import atomic_read_json              # <-- ADD IMPORT
    
    state = atomic_read_json(path, default=None)              # <-- CHANGE to atomic_read_json
    if state is None:                                         # <-- CHANGE None check
        log.info(f"[PM] No state file at {path}, starting fresh")
        return
    
    self.cash         = state.get("cash", CAPITAL * (1 - GOLD_HEDGE_WEIGHT))     # <-- ADD .get() with defaults
    self.gold_value   = state.get("gold_value", CAPITAL * GOLD_HEDGE_WEIGHT)
    self.realised_pnl = state.get("realised_pnl", 0.0)
    
    for sym, p in state.get("positions", {}).items():         # <-- ADD .get()
        # ... rest unchanged
```

### README.md (Quick Start section)

Replace lines 216-244 (Quick Start section) with the new expanded version from `phase2_complete/README.md`.

The new section includes:
- Prerequisites (venv setup)
- Train Model (synthetic + real options)
- Run Paper Backtest (30-day + 6-month)
- Run Unit Tests
- Run Daily EOD Cycle
- Verify Audit Chain
- Check Go-Live Eligibility
- Testing & CI section

---

## ✅ Verification After Copying

```bash
# 1. Check directory structure
tree -L 2 -I '__pycache__|*.pyc'

# Expected:
# .
# ├── utils/
# │   ├── __init__.py
# │   └── fs_atomic.py
# ├── tests/
# │   ├── __init__.py
# │   ├── mocks.py
# │   ├── test_orders_and_exits.py
# │   ├── test_state_persistence.py
# │   └── test_training_and_backtest.py
# ├── scripts/
# │   ├── __init__.py
# │   ├── train_direction_model.py
# │   └── paper_backtest_walkforward.py
# ├── .github/
# │   └── workflows/
# │       └── ci.yml
# ├── DOCS/
# │   └── PRE_GO_LIVE.md
# ├── order_book.py
# ├── verify_phase1.py
# ├── portfolio_manager.py  (modified)
# └── README.md              (modified)

# 2. Verify imports work
python -c "from utils.fs_atomic import atomic_write_json; print('✅ utils imports')"
python -c "from order_book import OrderBook; print('✅ order_book imports')"
python -c "from tests.mocks import MockBroker; print('✅ mocks imports')"

# 3. Run Phase 1 verification
python verify_phase1.py

# 4. Run all tests
pytest tests/ -v

# 5. Train synthetic model
python scripts/train_direction_model.py --synth --n-symbols 50 --n-days 100 --no-cv

# 6. Run short backtest
python scripts/paper_backtest_walkforward.py --synth --n-days 10 --n-symbols 20
```

---

## 🚨 Troubleshooting Copy Issues

### "ModuleNotFoundError: No module named 'utils'"
**Cause:** Missing `utils/__init__.py`  
**Fix:** `touch utils/__init__.py`

### "ModuleNotFoundError: No module named 'config'"
**Cause:** Running from wrong directory  
**Fix:** `cd /path/to/quant_pm` and run from repo root

### "ImportError: cannot import name 'OrderBook'"
**Cause:** `order_book.py` not in repo root  
**Fix:** `cp $SRC/order_book.py ./`

### "Diff shows conflicts in portfolio_manager.py"
**Cause:** Your version has diverged from the uploaded version  
**Fix:** Manually apply the 5 changes listed above instead of overwriting

---

## 📦 Optional: Create Patch File

If you prefer using git patches:

```bash
cd /path/to/outputs/phase2_complete

# Initialize git (if not already)
git init
git add .
git commit -m "Phase 1+2: Complete hardening"

# Create patch
git format-patch HEAD~1 --stdout > ../quant_pm_hardening.patch

# Apply in your repo
cd /path/to/your/quant_pm
git apply ../quant_pm_hardening.patch
```

---

## 🎯 Minimal Application (If You Just Want Tests)

If you only want to add tests without modifying portfolio_manager.py:

```bash
# Copy only test files
cp -r $SRC/tests ./

# Copy test dependencies
cp -r $SRC/utils ./
cp $SRC/order_book.py ./

# Copy mocks
# (already in tests/ from above)

# Run tests
pytest tests/ -v
```

**Note:** Some tests will fail without the portfolio_manager.py updates (order tracking, reconciliation).

---

## 📝 Files You DON'T Need to Copy

These are already in your repo or generated at runtime:

- ❌ `portfolio_manager.log` (generated)
- ❌ `portfolio_state.json` (generated)
- ❌ `order_book.jsonl` (generated)
- ❌ `audit/logs/*.jsonl` (generated)
- ❌ `reporting/**/*.json` (generated)
- ❌ `models/saved/*.pkl` (generated by training)
- ❌ `__pycache__/` (Python cache)
- ❌ `.pytest_cache/` (pytest cache)

---

**After copying, commit in small logical chunks as per the Git Commit Plan in FINAL_SUMMARY.md**
