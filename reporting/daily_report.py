"""
=============================================================================
DAILY REPORT GENERATOR — JSON Report with P&L, Decisions, Orders, Model
=============================================================================
Produces the mandatory daily JSON report after each trading session.
Designed to be consumed by dashboards, risk teams, or regulatory systems.
"""

import json
import os
import logging
from datetime import datetime, date
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict, field

from config.settings import (
    CAPITAL, MODEL_VERSION, PAPER_MODE,
    GOLD_HEDGE_WEIGHT, CASH_BUFFER, MAX_POSITIONS, REPORT_DIR
)

log = logging.getLogger("reporting")

os.makedirs(REPORT_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# PORTFOLIO SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class PositionSnapshot:
    symbol:           str
    quantity:         int
    avg_cost:         float
    current_price:    float
    market_value:     float
    unrealised_pnl:   float
    unrealised_pct:   float
    current_stop:     float
    gain_from_entry:  float
    days_held:        int
    stop_status:      str      # "ORIGINAL" | "BREAKEVEN" | "TRAILING"
    model_prob_entry: float
    weight_pct:       float

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


@dataclass
class TradeRecord:
    symbol:       str
    action:       str      # "ENTRY" | "EXIT" | "STOP_HIT" | "TRAIL_UPDATE"
    quantity:     int
    price:        float
    value:        float
    reason:       str
    model_prob:   float
    stop_price:   float
    cost_inr:     float    # transaction costs
    client_order_id: str
    timestamp:    str
    realised_pnl: float = 0.0   # realised P&L from this trade (exits only)

    def to_dict(self) -> dict:
        return {k: (round(v, 4) if isinstance(v, float) else v)
                for k, v in asdict(self).items()}


# ─────────────────────────────────────────────────────────────────────────────
# DAILY REPORT BUILDER
# ─────────────────────────────────────────────────────────────────────────────

class DailyReportBuilder:
    """
    Assembles the complete daily JSON report.

    Report structure:
    {
      "report_date":       "YYYY-MM-DD",
      "model_version":     "xgb_v1.0.0",
      "paper_mode":        true,
      "regime":            {...},
      "portfolio_summary": {...},
      "positions":         [...],
      "decisions":         {...},
      "orders_placed":     [...],
      "risk_status":       {...},
      "top_signals":       [...],
      "performance":       {...},
      "audit_hash":        "...",
    }
    """

    def __init__(self, report_date: Optional[str] = None):
        self.report_date   = report_date or date.today().isoformat()
        self._report: Dict = {}

    def build(
        self,
        regime_state:         Any,
        portfolio_nav:        float,
        positions:            List[PositionSnapshot],
        entries_today:        List[TradeRecord],
        exits_today:          List[TradeRecord],
        rejected_signals:     List[Dict],
        orders_placed:        List[Dict],
        top_signals:          List[Dict],
        drawdown_state:       str,
        current_drawdown:     float,
        peak_nav:             float,
        realised_pnl_today:   float,
        realised_pnl_total:   float,
        last_audit_hash:      str = "",
        fundamental_scores:   List[Dict] = None,
    ) -> Dict:
        """
        Assemble complete daily report dictionary.
        """

        # ── Portfolio Summary ────────────────────────────────────────────────
        cash_allocated   = sum(p.market_value for p in positions)
        gold_value       = portfolio_nav * GOLD_HEDGE_WEIGHT
        cash_value       = portfolio_nav - cash_allocated - gold_value
        equity_positions = len(positions)
        total_unrealised = sum(p.unrealised_pnl for p in positions)
        total_pnl        = realised_pnl_total + total_unrealised

        portfolio_summary = {
            "nav":                    round(portfolio_nav, 2),
            "starting_capital":       CAPITAL,
            "total_pnl":              round(total_pnl, 2),
            "total_pnl_pct":          round(total_pnl / CAPITAL * 100, 3),
            "realised_pnl_today":     round(realised_pnl_today, 2),
            "realised_pnl_total":     round(realised_pnl_total, 2),
            "unrealised_pnl":         round(total_unrealised, 2),
            "equity_value":           round(cash_allocated, 2),
            "equity_pct":             round(cash_allocated / (portfolio_nav + 1e-10) * 100, 2),
            "gold_hedge_value":       round(gold_value, 2),
            "cash_value":             round(cash_value, 2),
            "cash_pct":               round(cash_value / (portfolio_nav + 1e-10) * 100, 2),
            "open_positions":         equity_positions,
            "max_positions":          MAX_POSITIONS,
            "capacity_utilised_pct":  round(equity_positions / MAX_POSITIONS * 100, 1),
        }

        # ── Decisions Summary ────────────────────────────────────────────────
        decisions = {
            "entries_today":    len(entries_today),
            "exits_today":      len(exits_today),
            "rejected_today":   len(rejected_signals),
            "entries":          [t.to_dict() for t in entries_today],
            "exits":            [t.to_dict() for t in exits_today],
            "rejected_signals": rejected_signals,
        }

        # ── Risk Status ──────────────────────────────────────────────────────
        risk_status = {
            "drawdown_state":    drawdown_state,
            "current_drawdown":  round(current_drawdown * 100, 3),
            "peak_nav":          round(peak_nav, 2),
            "new_buys_halved":   drawdown_state == "REDUCED_BUYS",
            "cash_mode":         drawdown_state == "CASH_MODE",
            "regime":            regime_state.regime.value if hasattr(regime_state, 'regime') else regime_state,
            "allow_new_longs":   regime_state.allow_new_longs if hasattr(regime_state, 'allow_new_longs') else True,
            "allocation_mult":   regime_state.allocation_mult if hasattr(regime_state, 'allocation_mult') else 1.0,
        }

        # ── Assemble Full Report ─────────────────────────────────────────────
        self._report = {
            "report_date":         self.report_date,
            "generated_at":        datetime.now().isoformat(),
            "model_version":       MODEL_VERSION,
            "paper_mode":          PAPER_MODE,
            "regime":              regime_state.to_dict() if hasattr(regime_state, 'to_dict') else {},
            "portfolio_summary":   portfolio_summary,
            "positions":           [p.to_dict() for p in positions],
            "decisions":           decisions,
            "orders_placed":       orders_placed,
            "risk_status":         risk_status,
            "top_signals":         top_signals[:10],  # top 10 model signals
            "performance":         self._compute_performance_metrics(
                                       total_pnl, portfolio_nav, current_drawdown,
                                       entries_today, exits_today),
            "fundamental_scores":  fundamental_scores or [],
            "last_audit_hash":     last_audit_hash,
        }

        return self._report

    def _compute_performance_metrics(
        self,
        total_pnl:    float,
        nav:          float,
        drawdown:     float,
        entries:      List,
        exits:        List
    ) -> Dict:
        """Compute key performance metrics included in report."""
        win_exits = [e for e in exits if hasattr(e, 'unrealised_pnl') and e.unrealised_pnl > 0]
        return {
            "total_return_pct":   round(total_pnl / CAPITAL * 100, 3),
            "current_drawdown":   round(drawdown * 100, 3),
            "win_rate_exits_today": (
                round(len(win_exits) / len(exits) * 100, 1) if exits else "N/A"
            ),
            "positions_profitable": sum(1 for p in [] if hasattr(p, 'unrealised_pnl') and p.unrealised_pnl > 0),
        }

    def save(self, report: Dict = None, subdir: str = "") -> str:
        """
        Write daily report to JSON file.
        Returns file path.
        """
        report    = report or self._report
        save_dir  = os.path.join(REPORT_DIR, subdir) if subdir else REPORT_DIR
        os.makedirs(save_dir, exist_ok=True)

        filename  = f"daily_report_{self.report_date}.json"
        path      = os.path.join(save_dir, filename)

        with open(path, "w") as f:
            json.dump(report, f, indent=2, default=str)

        log.info(f"[REPORT] Daily report saved: {path}")
        return path

    def print_summary(self, report: Dict = None):
        """Print human-readable summary to console/logs."""
        r = report or self._report
        ps = r.get("portfolio_summary", {})
        rs = r.get("risk_status", {})
        d  = r.get("decisions", {})

        print(f"""
╔══════════════════════════════════════════════════════════════════╗
║          DAILY PORTFOLIO REPORT — {r.get('report_date', 'N/A')}              ║
║          Model: {r.get('model_version', 'N/A')} | {'📄 PAPER' if r.get('paper_mode') else '🔴 LIVE'}           ║
╠══════════════════════════════════════════════════════════════════╣
║  NAV:         ₹{ps.get('nav', 0):>12,.2f}   Total P&L: ₹{ps.get('total_pnl', 0):>10,.2f}  ║
║  Positions:   {ps.get('open_positions', 0)}/{ps.get('max_positions', 8)}              Unrealised:₹{ps.get('unrealised_pnl', 0):>10,.2f}  ║
║  Equity:      ₹{ps.get('equity_value', 0):>12,.2f}   Cash:      ₹{ps.get('cash_value', 0):>10,.2f}  ║
╠══════════════════════════════════════════════════════════════════╣
║  Regime:      {rs.get('regime', 'N/A'):<20}  DrawdownState: {rs.get('drawdown_state', 'N/A'):<10}║
║  Drawdown:    {rs.get('current_drawdown', 0):.2f}%          AllowNewLongs: {rs.get('allow_new_longs', True)}      ║
╠══════════════════════════════════════════════════════════════════╣
║  Entries:     {d.get('entries_today', 0):<5}  Exits: {d.get('exits_today', 0):<5}  Rejected: {d.get('rejected_today', 0):<5}        ║
╚══════════════════════════════════════════════════════════════════╝""")


# ─────────────────────────────────────────────────────────────────────────────
# PERFORMANCE TRACKER (multi-day)
# ─────────────────────────────────────────────────────────────────────────────

class PerformanceTracker:
    """
    Tracks daily NAV, computes Sharpe, Max Drawdown, Calmar, Win Rate.
    Used to determine paper-to-live eligibility.
    """

    def __init__(self, starting_nav: float = CAPITAL, risk_free_rate: float = 0.065):
        """
        Args:
            starting_nav:    initial capital
            risk_free_rate:  annualized risk-free rate (India ~6.5% repo rate)
        """
        self.starting_nav   = starting_nav
        self.risk_free_rate = risk_free_rate
        self._nav_history:  List[Dict] = []
        self._trade_log:    List[Dict] = []

    def record_nav(self, nav: float, trade_date: str):
        self._nav_history.append({"date": trade_date, "nav": nav})

    def record_trade(self, trade: Dict):
        self._trade_log.append(trade)

    def compute_metrics(self) -> Dict:
        """Compute full performance metrics from NAV history."""
        import numpy as np

        if len(self._nav_history) < 2:
            return {"error": "Insufficient history"}

        navs  = [r["nav"] for r in self._nav_history]
        dates = [r["date"] for r in self._nav_history]

        # Daily returns
        returns    = np.diff(navs) / np.array(navs[:-1])
        ann_factor = 252

        # Sharpe Ratio
        daily_rf   = (1 + self.risk_free_rate) ** (1/252) - 1
        excess_ret = returns - daily_rf
        sharpe     = (np.mean(excess_ret) / (np.std(excess_ret) + 1e-10)) * np.sqrt(ann_factor)

        # Sortino Ratio
        downside   = excess_ret[excess_ret < 0]
        sortino    = (np.mean(excess_ret) / (np.std(downside) + 1e-10)) * np.sqrt(ann_factor) if len(downside) > 0 else 0

        # Max Drawdown
        nav_arr    = np.array(navs)
        peak       = np.maximum.accumulate(nav_arr)
        dd_series  = (nav_arr - peak) / (peak + 1e-10)
        max_dd     = float(np.min(dd_series))

        # Calmar Ratio
        total_return = (navs[-1] - self.starting_nav) / self.starting_nav
        days_elapsed = len(navs)
        ann_return   = (1 + total_return) ** (ann_factor / days_elapsed) - 1
        calmar        = ann_return / (abs(max_dd) + 1e-10)

        # Trade stats
        profitable = [t for t in self._trade_log if t.get("pnl", 0) > 0]
        win_rate   = len(profitable) / len(self._trade_log) if self._trade_log else 0
        avg_win    = np.mean([t["pnl"] for t in profitable]) if profitable else 0
        losers     = [t for t in self._trade_log if t.get("pnl", 0) < 0]
        avg_loss   = np.mean([t["pnl"] for t in losers]) if losers else 0
        expectancy = (win_rate * avg_win) + ((1 - win_rate) * avg_loss)

        # Go-live eligibility check
        from config.settings import GO_LIVE_SHARPE_MIN, GO_LIVE_MAX_DD, PAPER_TRADE_MONTHS
        days_of_paper = days_elapsed
        months_of_paper = days_of_paper / 21  # ~21 trading days/month
        eligible = (
            sharpe   >= GO_LIVE_SHARPE_MIN and
            abs(max_dd) <= GO_LIVE_MAX_DD and
            months_of_paper >= PAPER_TRADE_MONTHS
        )

        return {
            "nav_start":          self.starting_nav,
            "nav_current":        navs[-1],
            "total_return_pct":   round(total_return * 100, 3),
            "annualised_return":  round(ann_return * 100, 3),
            "sharpe_ratio":       round(float(sharpe), 4),
            "sortino_ratio":      round(float(sortino), 4),
            "calmar_ratio":       round(float(calmar), 4),
            "max_drawdown_pct":   round(abs(max_dd) * 100, 3),
            "win_rate_pct":       round(win_rate * 100, 2),
            "avg_win_inr":        round(avg_win, 2),
            "avg_loss_inr":       round(avg_loss, 2),
            "expectancy_inr":     round(float(expectancy), 2),
            "total_trades":       len(self._trade_log),
            "trading_days":       days_elapsed,
            "paper_months":       round(months_of_paper, 1),
            "go_live_eligible":   eligible,
            "go_live_checks": {
                "sharpe_ok":     sharpe >= GO_LIVE_SHARPE_MIN,
                "maxdd_ok":      abs(max_dd) <= GO_LIVE_MAX_DD,
                "duration_ok":   months_of_paper >= PAPER_TRADE_MONTHS,
                "required_sharpe": GO_LIVE_SHARPE_MIN,
                "required_maxdd":  GO_LIVE_MAX_DD,
                "required_months": PAPER_TRADE_MONTHS
            }
        }

    def print_performance(self):
        m = self.compute_metrics()
        print(f"""
╔══════════════════════════════════════════════════════════╗
║             PERFORMANCE SUMMARY                          ║
╠══════════════════════════════════════════════════════════╣
║  Total Return:    {m.get('total_return_pct', 0):>8.2f}%                         ║
║  Ann. Return:     {m.get('annualised_return', 0):>8.2f}%                         ║
║  Sharpe Ratio:    {m.get('sharpe_ratio', 0):>8.4f}                          ║
║  Sortino Ratio:   {m.get('sortino_ratio', 0):>8.4f}                          ║
║  Max Drawdown:   -{m.get('max_drawdown_pct', 0):>8.2f}%                         ║
║  Calmar Ratio:    {m.get('calmar_ratio', 0):>8.4f}                          ║
║  Win Rate:        {m.get('win_rate_pct', 0):>8.2f}%                         ║
║  Expectancy:     ₹{m.get('expectancy_inr', 0):>8.2f}                          ║
║  Total Trades:    {m.get('total_trades', 0):>8}                          ║
╠══════════════════════════════════════════════════════════╣
║  Go-Live Eligible: {'✅ YES' if m.get('go_live_eligible') else '❌ NO (see checks below)'}                   ║
║  Paper Months:    {m.get('paper_months', 0):>6.1f} / {m.get('go_live_checks', {}).get('required_months', 6)}                        ║
╚══════════════════════════════════════════════════════════╝""")
