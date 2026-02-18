"""
=============================================================================
AUDIT SYSTEM — Immutable, Tamper-Evident Decision Logs
=============================================================================
Every decision (entry, exit, rejection, stop update, regime change) is:
1. Logged with full feature vector, model scores, and reason chain
2. Hash-chained (each record includes SHA256 of prior record → blockchain-style)
3. Written append-only to JSONL files (one per day)
4. Queryable but not modifiable after writing

Regulatory compliance: complete trail from signal → order → fill.
"""

import json
import hashlib
import os
import logging
from datetime import datetime, date
from typing import Any, Dict, Optional, List
from dataclasses import dataclass, asdict, field
from enum import Enum

log = logging.getLogger("audit")

AUDIT_DIR = "audit/logs"


# ─────────────────────────────────────────────────────────────────────────────
# RECORD TYPES
# ─────────────────────────────────────────────────────────────────────────────

class AuditEventType(Enum):
    SYSTEM_START      = "SYSTEM_START"
    REGIME_DETECTION  = "REGIME_DETECTION"
    MODEL_SCORE       = "MODEL_SCORE"
    ENTRY_APPROVED    = "ENTRY_APPROVED"
    ENTRY_REJECTED    = "ENTRY_REJECTED"
    ENTRY_ACCEPTED_NO_FILL = "ENTRY_ACCEPTED_NO_FILL"
    INSUFFICIENT_CASH_AFTER_FILL = "INSUFFICIENT_CASH_AFTER_FILL"
    ORDER_PLACED      = "ORDER_PLACED"
    ORDER_FILLED      = "ORDER_FILLED"
    ORDER_REJECTED    = "ORDER_REJECTED"
    EXIT_ORDER_REJECTED = "EXIT_ORDER_REJECTED"
    EXIT_ORDER_NO_FILL  = "EXIT_ORDER_NO_FILL"
    STOP_UPDATED      = "STOP_UPDATED"
    POSITION_CLOSED   = "POSITION_CLOSED"
    DRAWDOWN_ALERT    = "DRAWDOWN_ALERT"
    REBALANCE         = "REBALANCE"
    MODEL_RETRAINED   = "MODEL_RETRAINED"
    RISK_OVERRIDE     = "RISK_OVERRIDE"
    DAILY_SUMMARY     = "DAILY_SUMMARY"
    FUNDAMENTAL_SCORE = "FUNDAMENTAL_SCORE"
    RECONCILE_MISSING_POSITION = "RECONCILE_MISSING_POSITION"
    RECONCILE_STALE_POSITION   = "RECONCILE_STALE_POSITION"


@dataclass
class AuditRecord:
    """
    Single immutable audit record.
    prev_hash links to prior record for tamper detection.
    """
    seq_id:        int
    event_type:    str
    timestamp:     str
    date:          str
    symbol:        Optional[str]
    payload:       Dict[str, Any]
    model_version: str
    prev_hash:     str    # SHA256 of the serialized prior record
    record_hash:   str = field(default="", init=False)

    def __post_init__(self):
        self.record_hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """SHA256 of all fields except record_hash itself."""
        content = json.dumps({
            "seq_id":     self.seq_id,
            "event_type": self.event_type,
            "timestamp":  self.timestamp,
            "date":       self.date,
            "symbol":     self.symbol,
            "payload":    self.payload,
            "model_version": self.model_version,
            "prev_hash":  self.prev_hash
        }, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()

    def to_dict(self) -> dict:
        return {
            "seq_id":        self.seq_id,
            "event_type":    self.event_type,
            "timestamp":     self.timestamp,
            "date":          self.date,
            "symbol":        self.symbol,
            "payload":       self.payload,
            "model_version": self.model_version,
            "prev_hash":     self.prev_hash,
            "record_hash":   self.record_hash
        }

    def to_jsonl(self) -> str:
        return json.dumps(self.to_dict(), default=str) + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT WRITER
# ─────────────────────────────────────────────────────────────────────────────

class AuditWriter:
    """
    Append-only JSONL audit log writer with hash chaining.
    Thread-safe: file operations are synchronous.
    """

    def __init__(self, audit_dir: str = AUDIT_DIR, model_version: str = "unknown"):
        self.audit_dir     = audit_dir
        self.model_version = model_version
        self._seq_id       = 0
        self._prev_hash    = "GENESIS"   # hash of first record (chain anchor)
        self._current_file: Optional[str] = None
        os.makedirs(audit_dir, exist_ok=True)

        # Recover sequence from existing logs if restarting mid-day
        self._recover_state()

    @property
    def last_hash(self) -> str:
        """Return the hash of the last written audit record (for report inclusion)."""
        return self._prev_hash

    def _today_path(self) -> str:
        today = date.today().isoformat()
        return os.path.join(self.audit_dir, f"audit_{today}.jsonl")

    def _recover_state(self):
        """On startup, read today's log to get last seq_id and prev_hash."""
        path = self._today_path()
        if not os.path.exists(path):
            return
        try:
            with open(path, "r") as f:
                last_line = None
                for line in f:
                    stripped = line.strip()
                    if stripped:
                        last_line = stripped
            if last_line:
                last_record = json.loads(last_line)
                self._seq_id   = last_record["seq_id"] + 1
                self._prev_hash = last_record["record_hash"]
                log.info(f"[AUDIT] Recovered: seq_id={self._seq_id}, prev_hash={self._prev_hash[:8]}...")
        except Exception as e:
            log.warning(f"[AUDIT] State recovery failed: {e}")

    def write(
        self,
        event_type:    AuditEventType,
        payload:       Dict,
        symbol:        Optional[str] = None,
        timestamp:     Optional[str] = None
    ) -> AuditRecord:
        """Write a single audit record. Returns the record for reference."""
        ts = timestamp or datetime.now().isoformat()

        record = AuditRecord(
            seq_id        = self._seq_id,
            event_type    = event_type.value,
            timestamp     = ts,
            date          = ts[:10],
            symbol        = symbol,
            payload       = payload,
            model_version = self.model_version,
            prev_hash     = self._prev_hash
        )

        # Append to daily JSONL with fsync for crash safety
        path = self._today_path()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
        try:
            os.write(fd, record.to_jsonl().encode())
            os.fsync(fd)
        finally:
            os.close(fd)

        self._prev_hash = record.record_hash
        self._seq_id   += 1

        log.debug(f"[AUDIT] {event_type.value} | seq={record.seq_id} | hash={record.record_hash[:8]}")
        return record

    # ── Convenience writers ───────────────────────────────────────────────────

    def log_regime(self, regime_state) -> AuditRecord:
        return self.write(
            AuditEventType.REGIME_DETECTION,
            payload=regime_state.to_dict() if hasattr(regime_state, 'to_dict') else regime_state
        )

    def log_model_scores(self, scores: List[Dict]) -> AuditRecord:
        return self.write(
            AuditEventType.MODEL_SCORE,
            payload={"scores": scores, "count": len(scores)}
        )

    def log_entry_decision(self, decision) -> AuditRecord:
        evt = AuditEventType.ENTRY_APPROVED if decision.allowed else AuditEventType.ENTRY_REJECTED
        return self.write(
            evt,
            payload    = decision.to_dict() if hasattr(decision, 'to_dict') else decision,
            symbol     = decision.symbol if hasattr(decision, 'symbol') else None
        )

    def log_order(self, order, event: str = "PLACED") -> AuditRecord:
        evt_map = {
            "PLACED":   AuditEventType.ORDER_PLACED,
            "FILLED":   AuditEventType.ORDER_FILLED,
            "REJECTED": AuditEventType.ORDER_REJECTED
        }
        return self.write(
            evt_map.get(event, AuditEventType.ORDER_PLACED),
            payload = order.to_dict() if hasattr(order, 'to_dict') else order,
            symbol  = order.symbol if hasattr(order, 'symbol') else None
        )

    def log_stop_update(self, update) -> AuditRecord:
        return self.write(
            AuditEventType.STOP_UPDATED,
            payload = update.to_dict() if hasattr(update, 'to_dict') else update,
            symbol  = update.symbol if hasattr(update, 'symbol') else None
        )

    def log_daily_summary(self, summary: Dict) -> AuditRecord:
        return self.write(AuditEventType.DAILY_SUMMARY, payload=summary)

    def log_drawdown(self, drawdown: float, state: str, nav: float) -> AuditRecord:
        return self.write(
            AuditEventType.DRAWDOWN_ALERT,
            payload={"drawdown": drawdown, "state": state, "nav": nav}
        )

    def log_system_start(self, config: Dict) -> AuditRecord:
        return self.write(AuditEventType.SYSTEM_START, payload=config)


# ─────────────────────────────────────────────────────────────────────────────
# AUDIT VERIFIER
# ─────────────────────────────────────────────────────────────────────────────

class AuditVerifier:
    """
    Verifies the hash chain of an audit log file.
    Detects tampering or record deletion.
    """

    @staticmethod
    def verify_file(path: str) -> Dict:
        """
        Read a JSONL audit file and verify the complete hash chain.
        Returns {"valid": bool, "records": int, "first_break": int | None}
        """
        records    = []
        errors     = []
        prev_hash  = "GENESIS"

        if not os.path.exists(path):
            return {"valid": False, "error": f"File not found: {path}"}

        with open(path, "r") as f:
            for line_num, line in enumerate(f, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    rec = json.loads(stripped)
                    records.append(rec)

                    # Verify prev_hash linkage
                    if rec["prev_hash"] != prev_hash:
                        errors.append({
                            "line":     line_num,
                            "seq_id":   rec.get("seq_id"),
                            "issue":    "prev_hash mismatch",
                            "expected": prev_hash,
                            "found":    rec["prev_hash"]
                        })

                    # Recompute and verify record_hash
                    expected_hash = AuditRecord(
                        seq_id        = rec["seq_id"],
                        event_type    = rec["event_type"],
                        timestamp     = rec["timestamp"],
                        date          = rec["date"],
                        symbol        = rec["symbol"],
                        payload       = rec["payload"],
                        model_version = rec["model_version"],
                        prev_hash     = rec["prev_hash"]
                    ).record_hash

                    if rec["record_hash"] != expected_hash:
                        errors.append({
                            "line":     line_num,
                            "seq_id":   rec.get("seq_id"),
                            "issue":    "record_hash invalid (content tampered)",
                        })

                    prev_hash = rec["record_hash"]

                except json.JSONDecodeError as e:
                    errors.append({"line": line_num, "issue": f"JSON parse error: {e}"})

        return {
            "valid":        len(errors) == 0,
            "records":      len(records),
            "first_break":  errors[0]["line"] if errors else None,
            "errors":       errors,
            "file":         path
        }

    @staticmethod
    def verify_date(date_str: str, audit_dir: str = AUDIT_DIR) -> Dict:
        """Verify audit log for a specific date."""
        path = os.path.join(audit_dir, f"audit_{date_str}.jsonl")
        return AuditVerifier.verify_file(path)
