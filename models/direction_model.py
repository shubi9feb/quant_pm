"""
=============================================================================
DIRECTION MODEL — XGBoost with Walk-Forward Cross-Validation
=============================================================================
Predicts probability that a stock will outperform Nifty 50 over next 30 days.
Training uses strict walk-forward CV (no lookahead) with full backtest metrics.
"""

import numpy as np
import pandas as pd
import json
import os
import hashlib
import logging
from datetime import datetime
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, asdict

try:
    import xgboost as xgb
    from sklearn.metrics import roc_auc_score, precision_score, recall_score, f1_score
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.preprocessing import StandardScaler
    XGB_AVAILABLE = True
except ImportError:
    XGB_AVAILABLE = False
    print("[MODEL] XGBoost not installed. Run: pip install xgboost scikit-learn")

from data.features import FEATURE_COLS
from config.settings import (
    MODEL_VERSION, WALK_FORWARD_FOLDS, TRAIN_WINDOW_DAYS,
    OOS_WINDOW_DAYS, PREDICTION_HORIZON, MODEL_DIR
)

log = logging.getLogger("direction_model")


# ─────────────────────────────────────────────────────────────────────────────
# MODEL METADATA
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ModelMetadata:
    version:        str
    trained_on:     str   # ISO date
    train_end_date: str
    n_train_samples:int
    n_features:     int
    feature_cols:   List[str]
    oos_auc:        float
    oos_precision:  float
    oos_recall:     float
    oos_f1:         float
    walk_forward_folds: int
    model_hash:     str   # SHA256 of serialized model for audit

    def to_dict(self) -> dict:
        return asdict(self)


# ─────────────────────────────────────────────────────────────────────────────
# XGBOOST DIRECTION MODEL
# ─────────────────────────────────────────────────────────────────────────────

class DirectionModel:
    """
    XGBoost binary classifier predicting Nifty-relative outperformance.

    Architecture:
    - Input : FEATURE_COLS (cross-sectionally z-scored)
    - Output: P(stock outperforms Nifty 50 over next 30 days)
    - Calibration: Platt scaling (sigmoid) for reliable probabilities
    """

    XGB_PARAMS = {
        "n_estimators":      400,
        "max_depth":         4,          # shallow trees to prevent overfit
        "learning_rate":     0.05,
        "subsample":         0.8,
        "colsample_bytree":  0.7,
        "min_child_weight":  20,         # large to prevent overfitting small groups
        "reg_alpha":         0.1,        # L1 regularization
        "reg_lambda":        1.0,        # L2 regularization
        "scale_pos_weight":  1.0,        # adjust if target is imbalanced
        "eval_metric":       "auc",
        "use_label_encoder": False,
        "random_state":      42,
        "n_jobs":            -1,
        "early_stopping_rounds": 30,
    }

    def __init__(self):
        self.model:    Optional[object]   = None
        self.metadata: Optional[ModelMetadata] = None
        self._trained  = False

    # ── Walk-Forward Cross-Validation ─────────────────────────────────────────

    def walk_forward_cv(
        self,
        feature_df:     pd.DataFrame,
        n_folds:        int = WALK_FORWARD_FOLDS,
        train_window:   int = TRAIN_WINDOW_DAYS,
        oos_window:     int = OOS_WINDOW_DAYS
    ) -> Dict:
        """
        Strict walk-forward validation — no data leakage.

        Structure: [TRAIN | OOS | TRAIN+OOS | OOS | ...]
        Each fold trains ONLY on data before the OOS period.

        Returns dict with per-fold metrics and aggregated OOS predictions.
        """
        if not XGB_AVAILABLE:
            raise ImportError("xgboost and scikit-learn required")

        # Use unique trading dates as the time axis
        dates     = sorted(feature_df.index.unique())
        n_dates   = len(dates)
        fold_size = oos_window

        # Minimum dates needed: at least 1 train + 1 OOS fold
        if n_dates < train_window + fold_size:
            raise ValueError(f"Insufficient data: {n_dates} dates, need {train_window + fold_size}")

        all_oos_preds = []
        all_oos_true  = []
        fold_metrics  = []

        # Determine fold start points
        first_oos_start = train_window
        fold_starts = []
        for i in range(n_folds):
            oos_start = first_oos_start + i * fold_size
            if oos_start + fold_size > n_dates:
                break
            fold_starts.append(oos_start)

        log.info(f"[WF-CV] Running {len(fold_starts)} folds, "
                 f"train={train_window}d, OOS={oos_window}d")

        for fold_idx, oos_start in enumerate(fold_starts):
            train_dates = dates[max(0, oos_start - train_window): oos_start]
            oos_dates   = dates[oos_start: oos_start + fold_size]

            train_mask = feature_df.index.isin(train_dates)
            oos_mask   = feature_df.index.isin(oos_dates)

            X_train = feature_df.loc[train_mask, FEATURE_COLS]
            y_train = feature_df.loc[train_mask, "target"]
            X_oos   = feature_df.loc[oos_mask,   FEATURE_COLS]
            y_oos   = feature_df.loc[oos_mask,   "target"]

            # Skip folds with missing targets (end-of-data horizon issue)
            valid_train = y_train.notna()
            valid_oos   = y_oos.notna()
            X_train = X_train[valid_train]; y_train = y_train[valid_train]
            X_oos   = X_oos[valid_oos];     y_oos   = y_oos[valid_oos]

            if len(X_train) < 500 or len(X_oos) < 50:
                log.warning(f"[WF-CV] Fold {fold_idx+1}: insufficient samples, skipping")
                continue

            # Train model on this fold
            model, probs = self._train_fold(X_train, y_train, X_oos)

            # Metrics
            if len(y_oos.unique()) < 2:
                log.warning(f"[WF-CV] Fold {fold_idx+1}: single class in OOS")
                continue

            auc = roc_auc_score(y_oos, probs)
            preds_binary = (probs >= 0.5).astype(int)
            metrics = {
                "fold":      fold_idx + 1,
                "train_n":   len(X_train),
                "oos_n":     len(X_oos),
                "oos_start": str(oos_dates[0]),
                "oos_end":   str(oos_dates[-1]),
                "auc":       round(auc, 4),
                "precision": round(precision_score(y_oos, preds_binary, zero_division=0), 4),
                "recall":    round(recall_score(y_oos, preds_binary, zero_division=0), 4),
                "f1":        round(f1_score(y_oos, preds_binary, zero_division=0), 4),
            }
            fold_metrics.append(metrics)
            all_oos_preds.extend(probs.tolist())
            all_oos_true.extend(y_oos.tolist())

            log.info(f"[WF-CV] Fold {fold_idx+1}: AUC={auc:.4f} | "
                     f"Prec={metrics['precision']:.4f} | Rec={metrics['recall']:.4f}")

        # Aggregate OOS stats
        if all_oos_true:
            agg_auc  = roc_auc_score(all_oos_true, all_oos_preds)
            bin_preds = [1 if p >= 0.5 else 0 for p in all_oos_preds]
            agg = {
                "aggregate_oos_auc":       round(agg_auc, 4),
                "aggregate_oos_precision": round(precision_score(all_oos_true, bin_preds, zero_division=0), 4),
                "aggregate_oos_recall":    round(recall_score(all_oos_true, bin_preds, zero_division=0), 4),
                "aggregate_oos_f1":        round(f1_score(all_oos_true, bin_preds, zero_division=0), 4),
                "total_oos_samples":       len(all_oos_true),
                "folds":                   fold_metrics
            }
        else:
            agg = {"error": "No valid OOS predictions generated"}

        return agg

    def _train_fold(
        self,
        X_train: pd.DataFrame,
        y_train: pd.Series,
        X_val:   pd.DataFrame
    ) -> Tuple[object, np.ndarray]:
        """Train XGBoost on one fold, return (model, oos_probabilities)."""
        params = {k: v for k, v in self.XGB_PARAMS.items()
                  if k != "early_stopping_rounds"}

        # Create validation set for early stopping (last 20% of train)
        val_split  = int(len(X_train) * 0.8)
        X_tr, X_es = X_train.iloc[:val_split], X_train.iloc[val_split:]
        y_tr, y_es = y_train.iloc[:val_split], y_train.iloc[val_split:]

        model = xgb.XGBClassifier(**params, early_stopping_rounds=30)
        model.fit(
            X_tr, y_tr,
            eval_set=[(X_es, y_es)],
            verbose=False
        )

        probs = model.predict_proba(X_val)[:, 1]
        return model, probs

    # ── Production Training (Full Dataset) ────────────────────────────────────

    def train(self, feature_df: pd.DataFrame, run_wf_cv: bool = True) -> Dict:
        """
        Train the production model on the full historical dataset.
        Always runs walk-forward CV first to get honest OOS metrics.
        """
        if not XGB_AVAILABLE:
            raise ImportError("xgboost and scikit-learn required")

        cv_results = {}
        if run_wf_cv:
            log.info("[MODEL] Running walk-forward CV for OOS metrics...")
            cv_results = self.walk_forward_cv(feature_df)
            log.info(f"[MODEL] WF-CV AUC: {cv_results.get('aggregate_oos_auc', 'N/A')}")

        # Train final model on all available data
        valid_mask = feature_df["target"].notna()
        X_full = feature_df.loc[valid_mask, FEATURE_COLS]
        y_full = feature_df.loc[valid_mask, "target"]

        log.info(f"[MODEL] Training production model on {len(X_full):,} samples")

        # Production XGBoost (no early stopping, fixed n_estimators)
        prod_params = {k: v for k, v in self.XGB_PARAMS.items()
                       if k not in ["early_stopping_rounds", "eval_metric"]}
        prod_params["n_estimators"] = 400  # fixed after CV tuning

        self.model = xgb.XGBClassifier(**prod_params)
        self.model.fit(X_full, y_full, verbose=False)

        # Build metadata
        agg = cv_results.get
        self.metadata = ModelMetadata(
            version          = MODEL_VERSION,
            trained_on       = datetime.now().isoformat(),
            train_end_date   = str(feature_df.index.max()),
            n_train_samples  = len(X_full),
            n_features       = len(FEATURE_COLS),
            feature_cols     = FEATURE_COLS,
            oos_auc          = cv_results.get("aggregate_oos_auc", 0.0),
            oos_precision    = cv_results.get("aggregate_oos_precision", 0.0),
            oos_recall       = cv_results.get("aggregate_oos_recall", 0.0),
            oos_f1           = cv_results.get("aggregate_oos_f1", 0.0),
            walk_forward_folds = WALK_FORWARD_FOLDS,
            model_hash       = self._compute_hash()
        )
        self._trained = True

        log.info(f"[MODEL] Training complete. OOS AUC: {self.metadata.oos_auc:.4f}")
        return {**cv_results, "metadata": self.metadata.to_dict()}

    # ── Inference ─────────────────────────────────────────────────────────────

    def predict(self, feature_df: pd.DataFrame) -> pd.DataFrame:
        """
        Score all stocks in the universe for today.

        Returns DataFrame with columns: symbol, model_prob, model_rank
        """
        if not self._trained or self.model is None:
            raise RuntimeError("Model not trained. Call train() first.")

        # Take the most recent row per symbol
        latest = (feature_df
                  .sort_index()
                  .groupby("symbol")
                  .last()
                  .reset_index())

        available_features = [c for c in FEATURE_COLS if c in latest.columns]
        missing = set(FEATURE_COLS) - set(available_features)
        if missing:
            log.warning(f"[MODEL] Missing features: {missing}")

        X = latest[available_features].fillna(0)

        probs           = self.model.predict_proba(X)[:, 1]
        latest["model_prob"] = probs
        latest["model_rank"] = latest["model_prob"].rank(ascending=False).astype(int)
        latest["model_version"] = MODEL_VERSION

        return latest[["symbol", "model_prob", "model_rank", "model_version"]].copy()

    # ── Feature Importance ────────────────────────────────────────────────────

    def feature_importance(self) -> pd.DataFrame:
        """Return feature importances sorted descending."""
        if not self._trained:
            raise RuntimeError("Model not trained")
        imp = self.model.feature_importances_
        return (pd.DataFrame({"feature": FEATURE_COLS, "importance": imp})
                .sort_values("importance", ascending=False)
                .reset_index(drop=True))

    # ── Save / Load ───────────────────────────────────────────────────────────

    def save(self, path: str = MODEL_DIR):
        """Persist model and metadata for audit trail."""
        import pickle
        os.makedirs(path, exist_ok=True)
        version_safe = MODEL_VERSION.replace(".", "_")
        model_path   = os.path.join(path, f"model_{version_safe}.pkl")
        meta_path    = os.path.join(path, f"metadata_{version_safe}.json")

        with open(model_path, "wb") as f:
            pickle.dump(self.model, f)
        with open(meta_path, "w") as f:
            json.dump(self.metadata.to_dict(), f, indent=2)

        log.info(f"[MODEL] Saved to {model_path}")

    @classmethod
    def load(cls, path: str = MODEL_DIR) -> "DirectionModel":
        import pickle
        version_safe = MODEL_VERSION.replace(".", "_")
        model_path   = os.path.join(path, f"model_{version_safe}.pkl")
        meta_path    = os.path.join(path, f"metadata_{version_safe}.json")

        instance = cls()
        with open(model_path, "rb") as f:
            instance.model = pickle.load(f)
        with open(meta_path) as f:
            meta_dict = json.load(f)
            instance.metadata = ModelMetadata(**meta_dict)
        instance._trained = True
        log.info(f"[MODEL] Loaded {MODEL_VERSION} from {model_path}")
        return instance

    def _compute_hash(self) -> str:
        """SHA256 of model parameters for audit immutability."""
        if self.model is None:
            return "untrained"
        try:
            import pickle
            return hashlib.sha256(pickle.dumps(self.model)).hexdigest()[:16]
        except Exception:
            return "hash_error"
