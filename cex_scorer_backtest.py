"""
Backtest framework for cex_scorer_v4 SignalOptimizer.

- backtest_optimizer: walk-forward evaluation with TimeSeriesSplit.
- mock_ohlcv: synthetic OHLCV for testing without real data.
- Cache backtest results (<24h skip); regime fingerprint (volatility) forces re-run if >30% change.
"""

from __future__ import annotations

import bisect
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# High-acc UTC hours for backtest only (live 不限制): 00:00–08:00, 12:00–14:00, 19:00–21:00, 23:00
HIGH_ACC_UTC_HOURS: frozenset[int] = frozenset({0, 1, 2, 3, 4, 5, 6, 7, 12, 13, 19, 20, 23})


def _is_high_acc_utc(dt: pd.Timestamp) -> bool:
    """True if dt (UTC) hour is in high-acc window. For backtest filtering only."""
    if hasattr(dt, "hour"):
        return dt.hour in HIGH_ACC_UTC_HOURS
    return pd.Timestamp(dt, tz="UTC").hour in HIGH_ACC_UTC_HOURS


def _poly_path_is_high_acc(path: Path) -> bool:
    """True if Poly JSONL filename ts (window start) falls in high-acc UTC hours. For backtest: only read these files."""
    path = Path(path)
    parts = path.stem.split("-")
    if not parts:
        return False
    try:
        ts = int(float(parts[-1]))
    except (ValueError, TypeError):
        return False
    if ts > 1e12:
        ts = ts // 1000
    dt = pd.Timestamp(ts, unit="s", tz="UTC")
    return dt.hour in HIGH_ACC_UTC_HOURS


try:
    from sklearn.model_selection import TimeSeriesSplit
except ImportError:
    TimeSeriesSplit = None  # type: ignore[misc, assignment]

# Import from cex_scorer_v4
try:
    from cex_scorer_v4 import (
        SignalOptimizer,
        AdaptiveScoreNormalizer,
        _iter_complete_signals_from_rows,
        v3_compute_raw_score_from_signals,
    )
except ImportError:
    SignalOptimizer = None  # type: ignore[misc, assignment]
    AdaptiveScoreNormalizer = None  # type: ignore[misc, assignment]
    _iter_complete_signals_from_rows = None  # type: ignore[misc, assignment]
    v3_compute_raw_score_from_signals = None  # type: ignore[misc, assignment]


def mock_ohlcv(
    n_days: int = 30,
    freq: str = "1h",
    *,
    seed: Optional[int] = None,
) -> pd.DataFrame:
    """
    Generate synthetic OHLCV for testing when no real data is available.

    Args:
        n_days: Number of days of data.
        freq: Bar frequency, e.g. '1h', '15m'.
        seed: Random seed for reproducibility.

    Returns:
        DataFrame with DatetimeIndex and columns: open, high, low, close, volume.
        Also has 'timestamp' column (Unix seconds) for compatibility.
    """
    if seed is not None:
        np.random.seed(seed)
    if freq == "1h":
        bars_per_day = 24
    elif freq == "15m":
        bars_per_day = 96
    else:
        bars_per_day = 24
    n_bars = n_days * bars_per_day
    start_ts = pd.Timestamp("2020-01-01", tz="UTC").value // 10**9
    if freq == "1h":
        step_s = 3600
    elif freq == "15m":
        step_s = 900
    else:
        step_s = 3600
    timestamps = start_ts + np.arange(n_bars) * step_s
    # Random walk in log-space for close
    log_ret = np.random.randn(n_bars) * 0.01
    close = 50000.0 * np.exp(np.cumsum(log_ret))
    open_ = np.roll(close, 1)
    open_[0] = 50000.0
    high = np.maximum(open_, close) * (1 + np.abs(np.random.randn(n_bars) * 0.002))
    low = np.minimum(open_, close) * (1 - np.abs(np.random.randn(n_bars) * 0.002))
    volume = np.random.lognormal(10, 1, size=n_bars)
    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )
    df = df.set_index(pd.to_datetime(df["timestamp"], unit="s", utc=True))
    return df


def _up_mid_from_orderbook(ob: dict[str, Any], path: Optional[Path] = None) -> float:
    """
    Up outcome mid from orderbook. Single-side empty is valid (market resolved).
    Only when both bids and asks are empty do we fallback to 0.5 and warn.
    """
    bids = ob.get("bids") or []
    asks = ob.get("asks") or []
    # Explicit max/min with fallback to ensure robustness against unsorted data
    prices_bid = [float(p[0]) for p in bids if p and len(p) >= 1]
    prices_ask = [float(p[0]) for p in asks if p and len(p) >= 1]
    best_bid = max(prices_bid) if prices_bid else None
    best_ask = min(prices_ask) if prices_ask else None
    
    if best_bid is not None and best_ask is not None:
        return (best_bid + best_ask) / 2.0
    if best_bid is not None:
        return float(best_bid)
    if best_ask is not None:
        return float(best_ask)
    
    logger.warning(
        "Invalid Up orderbook (both sides empty), fallback mid=0.5",
        extra={"path": str(path) if path else None},
    )
    return 0.5


def load_poly_jsonls(paths: list[Path]) -> pd.DataFrame:
    """
    Load Polymarket btc-updown-15m JSONL files into a DataFrame with timestamp and close (Up mid).

    - ts from filename stem: last segment; if >1e12 treat as ms and divide by 1000.
    - Each file: use last line only as the 15min window representative.
    - Up mid: best_bid = max(bid prices), best_ask = min(ask prices); single-side empty is valid.
    """
    rows: list[dict[str, Any]] = []
    for p in sorted(Path(x) for x in paths):
        if not p.exists():
            continue
        stem = p.stem
        parts = stem.split("-")
        if not parts:
            continue
        try:
            ts = int(float(parts[-1]))
        except (ValueError, TypeError):
            continue
        if ts > 1e12:
            ts = ts // 1000
        last_line: Optional[str] = None
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line:
                    last_line = line
        if not last_line:
            continue
        try:
            obj = json.loads(last_line)
        except Exception:
            continue
        tokens = obj.get("tokens")
        if not isinstance(tokens, list):
            continue
        up_token = None
        for t in tokens:
            if isinstance(t, dict) and str(t.get("outcome") or "").strip() == "Up":
                up_token = t
                break
        if not up_token:
            continue
        ob = up_token.get("orderbook") or {}
        up_mid = _up_mid_from_orderbook(ob, path=p)
        rows.append({"timestamp": ts, "close": up_mid})
    if not rows:
        return pd.DataFrame(columns=["timestamp", "close"])
    df = pd.DataFrame(rows)
    df = df.drop_duplicates(subset=["timestamp"], keep="last").sort_values("timestamp").reset_index(drop=True)
    df = df.set_index(pd.to_datetime(df["timestamp"], unit="s", utc=True))
    return df[["close"]]


def load_cex_full_rows(
    paths: list[Path],
    columns: Optional[list[str]] = None,
) -> list[dict[str, Any]]:
    """
    Load CEX CSV files, returning all rows with required columns for signal generation.
    Default columns: t_sample_unix, sample_id, venue, imb.
    Returns list of dict sorted by t_sample_unix.
    """
    if columns is None:
        columns = ["t_sample_unix", "sample_id", "venue", "imb"]
    all_rows: list[dict[str, Any]] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, nrows=0)
            header = list(df.columns)
        except Exception:
            continue
        # Check required columns exist
        if not all(col in header for col in columns):
            logger.warning("CEX file %s missing required columns, skipping", p.name)
            continue
        try:
            chunk = pd.read_csv(p, usecols=columns)
        except Exception:
            continue
        for _, row in chunk.iterrows():
            row_dict = {col: row.get(col) for col in columns}
            # Convert to proper types
            if "t_sample_unix" in row_dict:
                try:
                    row_dict["t_sample_unix"] = float(row_dict["t_sample_unix"])
                except (ValueError, TypeError):
                    continue
            if "venue" in row_dict:
                row_dict["venue"] = str(row_dict["venue"] or "").strip()
            all_rows.append(row_dict)
    # Sort by t_sample_unix
    all_rows.sort(key=lambda r: r.get("t_sample_unix", 0))
    return all_rows


def load_cex_mid_csvs(
    paths: list[Path],
    mid_venue: str = "binance_spot",
) -> pd.DataFrame:
    """
    Load CEX CSV files, keep one mid per sample_id for mid_venue. Fallback to first available venue if mid_venue empty.
    """
    all_rows: list[dict[str, Any]] = []
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        try:
            df = pd.read_csv(p, nrows=0)
            header = list(df.columns)
        except Exception:
            continue
        if "venue" not in header or "mid" not in header or "t_sample_unix" not in header or "sample_id" not in header:
            continue
        try:
            chunk = pd.read_csv(p, usecols=["t_sample_unix", "sample_id", "venue", "mid"])
        except Exception:
            continue
        for _, row in chunk.iterrows():
            all_rows.append({
                "t_sample_unix": row.get("t_sample_unix"),
                "sample_id": row.get("sample_id"),
                "venue": str(row.get("venue") or "").strip(),
                "mid": row.get("mid"),
            })
    if not all_rows:
        return pd.DataFrame(columns=["timestamp", "close"])
    raw = pd.DataFrame(all_rows)
    raw["mid"] = pd.to_numeric(raw["mid"], errors="coerce")
    raw["t_sample_unix"] = pd.to_numeric(raw["t_sample_unix"], errors="coerce")
    raw = raw.dropna(subset=["t_sample_unix", "mid"])
    venue_df = raw[raw["venue"] == mid_venue]
    if venue_df.empty:
        available = raw["venue"].dropna().unique().tolist()
        fallback = available[0] if available else None
        if fallback:
            logger.warning("mid_venue %s has no data, fallback to %s", mid_venue, fallback)
            venue_df = raw[raw["venue"] == fallback]
    if venue_df.empty:
        return pd.DataFrame(columns=["timestamp", "close"])
    last_per_sample = venue_df.sort_values("t_sample_unix").groupby("sample_id", sort=False).last().reset_index()
    out = last_per_sample[["t_sample_unix", "mid"]].rename(columns={"t_sample_unix": "timestamp", "mid": "close"})
    out = out.sort_values("timestamp").reset_index(drop=True)
    out = out.set_index(pd.to_datetime(out["timestamp"], unit="s", utc=True))
    return out[["close"]]


def mock_poly(
    n_days: int = 10,
    freq: str = "15m",
    *,
    seed: Optional[int] = None,
) -> pd.DataFrame:
    """Synthetic Poly-like probability series for testing. close in [0.01, 0.99]."""
    if seed is not None:
        np.random.seed(seed)
    n_bars = n_days * 96  # 15min
    # Smoother random walk for probability
    close = 0.5 + np.cumsum(np.random.randn(n_bars) * 0.001)
    close = np.clip(close, 0.01, 0.99)
    ts_index = pd.date_range(start="2026-01-01", periods=n_bars, freq="15min", tz="UTC")
    timestamp = ts_index.astype("int64") // 10**9
    df = pd.DataFrame({"timestamp": timestamp, "close": close})
    df = df.set_index(ts_index)
    return df


def _get_bucket_mid(cex_10s: pd.Series, bucket_unix: int) -> Optional[float]:
    """Get CEX mid for 10s bucket; use nearest available bucket <= bucket_unix if exact miss."""
    if bucket_unix in cex_10s.index:
        return float(cex_10s.loc[bucket_unix])
    idx = cex_10s.index[cex_10s.index <= bucket_unix]
    if len(idx) == 0:
        return None
    return float(cex_10s.loc[idx[-1]])


# 梯度输出用阈值列表（信号量纲约 ±4）
THRESH_GRADIENT = [1.0, 2.0, 3.0]


def backtest_zeff_full(
    cex_csv_paths: list[Path],
    poly_jsonl_paths: list[Path],
    *,
    mid_venue: str = "binance_spot",
    thresh: float = 0.1,
    thresh_gradient: Optional[list[float]] = None,
    window_size: int = 24,
    min_train_size: int = 48,
    n_splits: Optional[int] = None,
    optimizer_params: Optional[dict[str, Any]] = None,
    # Parameters for real score calculation
    venues: Optional[list[str]] = None,
    weights: Optional[list[float]] = None,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    decay_rate: float = 0.15,
    ewma_alpha: float = 0.2,
    lookback_seconds: int = 7200,
    min_samples: int = 50,
) -> dict[str, Any]:
    """
    Walk-forward backtest: zeff vs Poly 15min, raw zscore vs BTC 10s.
    Uses REAL CEX data to compute raw_score, z_score, zeff at each timestep.
    按 thresh_gradient（默认 [1.0, 2.0, 3.0]）输出 raw_score、zscore、zeff 的准确率、击中次数、占比、覆盖率。
    """
    if thresh_gradient is None:
        thresh_gradient = THRESH_GRADIENT
    
    # Default venues/weights if not provided
    if venues is None:
        venues = ["binance_spot", "okx_spot", "okx_swap", "bybit_spot", "bybit_linear"]
    if weights is None:
        weights = [1.0, 1.0, 2.0, 2.0, 3.0]
    
    _empty_summary = {
        "hit_rate_raw_btc": 0.0,
        "hit_rate_zeff_poly": 0.0,
        "hit_rate_raw_btc_thresh": thresh,
        "hit_rate_zeff_poly_thresh": thresh,
        "num_predicted_raw_btc": 0,
        "num_predicted_zeff_poly": 0,
        "coverage_rate_raw_btc": 0.0,
        "coverage_rate_zeff_poly": 0.0,
        "n_folds": 0,
        "thresh_gradient_results": [],
    }
    
    if SignalOptimizer is None or TimeSeriesSplit is None:
        logger.warning("SignalOptimizer or TimeSeriesSplit missing")
        return {"all_results": [], "summary": _empty_summary}
    if _iter_complete_signals_from_rows is None or v3_compute_raw_score_from_signals is None:
        logger.warning("CEX scorer v4 functions missing")
        return {"all_results": [], "summary": _empty_summary}
    if AdaptiveScoreNormalizer is None:
        logger.warning("AdaptiveScoreNormalizer missing")
        return {"all_results": [], "summary": _empty_summary}
    
    poly_paths_filtered = [Path(p) for p in poly_jsonl_paths if _poly_path_is_high_acc(Path(p))]
    if len(poly_paths_filtered) < len(poly_jsonl_paths):
        logger.info(
            "High-acc filter: loading only %d / %d Poly JSONL (UTC 00–08, 12–14, 19–21, 23)",
            len(poly_paths_filtered),
            len(poly_jsonl_paths),
        )
    
    # Load data
    cex_df = load_cex_mid_csvs(cex_csv_paths, mid_venue=mid_venue)
    cex_rows = load_cex_full_rows(cex_csv_paths)
    poly_df = load_poly_jsonls(poly_paths_filtered)
    
    if cex_df.empty or poly_df.empty or not cex_rows:
        logger.warning("CEX or Poly data empty")
        return {"all_results": [], "summary": _empty_summary}
    
    logger.info("Loaded %d CEX rows, %d CEX mid samples, %d Poly samples", 
                len(cex_rows), len(cex_df), len(poly_df))
    
    cex_df = cex_df.copy()
    cex_df["ts"] = cex_df.index.astype("int64") // 10**9
    cex_10s = cex_df.assign(bucket=(cex_df["ts"] // 10) * 10).groupby("bucket")["close"].mean()
    
    if cex_10s.empty:
        logger.warning("CEX 10s buckets empty")
        return {"all_results": [], "summary": _empty_summary}
    
    poly_close = poly_df["close"]
    poly_15min_change = poly_close.shift(-1) - poly_close
    
    # Build merge_df
    rows: list[dict[str, Any]] = []
    for i in range(len(poly_df)):
        ts_unix = int(pd.Timestamp(poly_df.index[i]).timestamp())
        bucket0 = (ts_unix // 10) * 10
        bucket_next = math.ceil((ts_unix + 1e-6) / 10) * 10
        btc_mid = _get_bucket_mid(cex_10s, bucket0)
        btc_mid_next = _get_bucket_mid(cex_10s, bucket_next)
        if btc_mid is None:
            continue
        if btc_mid_next is not None and btc_mid != 0:
            btc_10s_change = (btc_mid_next - btc_mid) / btc_mid
        else:
            btc_10s_change = np.nan
        poly_c = float(poly_close.iloc[i])
        poly_chg = poly_15min_change.iloc[i] if i < len(poly_15min_change) else np.nan
        rows.append({
            "timestamp": ts_unix,
            "poly_close": poly_c,
            "btc_mid": float(btc_mid),
            "btc_10s_change": btc_10s_change,
            "poly_15min_change": poly_chg,
        })
    
    if not rows:
        return {"all_results": [], "summary": _empty_summary}
    
    merge_df = pd.DataFrame(rows)
    merge_df["datetime"] = pd.to_datetime(merge_df["timestamp"], unit="s", utc=True)
    merge_df = merge_df.set_index("datetime")
    use_15min_sign_for_btc = merge_df["btc_10s_change"].isna()
    if use_15min_sign_for_btc.any():
        merge_df.loc[use_15min_sign_for_btc, "btc_10s_change"] = np.sign(
            merge_df.loc[use_15min_sign_for_btc, "poly_15min_change"]
        )
    data = merge_df[["poly_close", "btc_mid", "btc_10s_change", "poly_15min_change"]].dropna(how="all")
    
    if len(data) < window_size + 1:
        logger.warning("Insufficient merged data after high-acc filter for backtest (need >= %d rows)", window_size + 1)
        return {"all_results": [], "summary": _empty_summary}
    
    if len(data) < min_train_size + window_size:
        n_train = len(data) - window_size
        splits = [(np.arange(n_train), np.arange(n_train, len(data)))]
    else:
        if n_splits is None:
            n_splits = min(5, (len(data) - min_train_size) // max(1, window_size))
        n_splits = max(2, n_splits)
        tscv = TimeSeriesSplit(n_splits=n_splits, max_train_size=None)
        splits = list(tscv.split(data))
    
    params = dict(optimizer_params or {})
    params["conservative_scaling_enabled"] = False
    all_results: list[dict[str, Any]] = []
    
    for fold, (train_idx, test_idx) in enumerate(splits):
        train_df = data.iloc[train_idx]
        test_df = data.iloc[test_idx]
        train_btc = train_df["btc_mid"].astype(float)
        train_offsets = [abs(train_btc.iloc[j + 1] - train_btc.iloc[j]) for j in range(len(train_btc) - 1)]
        
        if not train_offsets:
            empty_by_thresh = {
                float(t): {
                    "raw_btc": {"hit_rate": 0.0, "num_hits": 0, "ratio": 0.0, "coverage_rate": 0.0},
                    "zscore_btc": {"hit_rate": 0.0, "num_hits": 0, "ratio": 0.0, "coverage_rate": 0.0},
                    "zeff_poly": {"hit_rate": 0.0, "num_hits": 0, "ratio": 0.0, "coverage_rate": 0.0},
                }
                for t in thresh_gradient
            }
            all_results.append({
                "fold": fold,
                "hit_rate_raw_btc": 0.0,
                "hit_rate_zeff_poly": 0.0,
                "num_predicted_raw_btc": 0,
                "num_predicted_zeff_poly": 0,
                "coverage_rate_raw_btc": 0.0,
                "coverage_rate_zeff_poly": 0.0,
                "n": 0,
                "by_thresh": empty_by_thresh,
            })
            continue
        
        opt = SignalOptimizer(**params)
        opt.set_historical_offsets(train_offsets)
        
        # Initialize normalizer and EWMA state
        normalizer = AdaptiveScoreNormalizer(
            lookback_seconds=lookback_seconds,
            min_samples=min_samples,
            winsorize_prop=0.01,
            use_vol_norm=False,
            clip_limit=3.0,
        )
        ewma_state: Optional[float] = None
        
        # Warm-up: process train_df to update normalizer and EWMA
        train_timestamps = [int(pd.Timestamp(idx).timestamp()) for idx in train_df.index]
        cex_ptr = 0
        
        for t in train_timestamps:
            # Advance CEX pointer to include all rows <= t
            while cex_ptr < len(cex_rows) and cex_rows[cex_ptr].get("t_sample_unix", float("inf")) <= t:
                cex_ptr += 1
            
            # Compute raw_score from CEX rows up to this point
            signals = _iter_complete_signals_from_rows(
                cex_rows[:cex_ptr],
                venues=venues,
                weights=weights,
                min_abs_score=min_abs_score,
            )
            
            raw_score, ewma_state = v3_compute_raw_score_from_signals(
                signals,
                float(t),
                time_window_sec=time_window_sec,
                min_abs_score=min_abs_score,
                decay_rate=decay_rate,
                ewma_alpha=ewma_alpha,
                ewma_state=ewma_state,
            )
            
            # Update normalizer (don't need z_score during warmup)
            # Pass allow_disk_cache=False to prevent disk writes during warmup
            normalizer.update(raw_score, float(t))
        
        logger.info("Fold %d: warmup complete, normalizer has %d samples, ewma_state=%.4f",
                    fold, len(normalizer.history), ewma_state or 0.0)
        
        # Test: compute raw, z_score, zeff for each test row
        test_btc = test_df["btc_mid"].astype(float)
        btc_10s = test_df["btc_10s_change"].fillna(0).values
        poly_chg = test_df["poly_15min_change"].fillna(0).values
        
        t0 = test_df.index[0]
        t0_ts = float(pd.Timestamp(t0).timestamp())
        prev_btc = float(train_btc.iloc[-1])
        cum_change = 0.0
        
        raw_scores_list: list[float] = []
        z_scores_list: list[float] = []
        zeff_list: list[float] = []
        
        test_timestamps = [int(pd.Timestamp(idx).timestamp()) for idx in test_df.index]
        
        for j, t in enumerate(test_timestamps):
            # Advance CEX pointer
            while cex_ptr < len(cex_rows) and cex_rows[cex_ptr].get("t_sample_unix", float("inf")) <= t:
                cex_ptr += 1
            
            # Compute raw_score
            signals = _iter_complete_signals_from_rows(
                cex_rows[:cex_ptr],
                venues=venues,
                weights=weights,
                min_abs_score=min_abs_score,
            )
            
            raw_score, ewma_state = v3_compute_raw_score_from_signals(
                signals,
                float(t),
                time_window_sec=time_window_sec,
                min_abs_score=min_abs_score,
                decay_rate=decay_rate,
                ewma_alpha=ewma_alpha,
                ewma_state=ewma_state,
            )
            
            # Compute z_score
            z_score, _ = normalizer.normalize(raw_score, float(t))
            normalizer.update(raw_score, float(t))
            
            # Compute zeff
            elapsed_min = (t - t0_ts) / 60.0
            decay = float(opt.dynamic_decay(elapsed_min, cum_change, return_meta=False))
            zeff = z_score * decay
            
            raw_scores_list.append(raw_score)
            z_scores_list.append(z_score)
            zeff_list.append(zeff)
            
            # Update cum_change
            cur_btc = float(test_btc.iloc[j])
            cum_change += abs(cur_btc - prev_btc)
            prev_btc = cur_btc
        
        raw_scores = np.array(raw_scores_list)
        z_scores = np.array(z_scores_list)
        zeff_arr = np.array(zeff_list)
        n_test = len(test_df)
        
        # Compute metrics by threshold
        by_thresh: dict[float, dict[str, Any]] = {}
        for t in thresh_gradient:
            mask_raw = np.abs(raw_scores) > t
            mask_zscore = np.abs(z_scores) > t
            mask_zeff = np.abs(zeff_arr) > t
            
            num_pred_raw = int(mask_raw.sum())
            num_pred_zscore = int(mask_zscore.sum())
            num_pred_zeff = int(mask_zeff.sum())
            
            # raw_btc: raw_score vs btc_10s
            if num_pred_raw == 0:
                raw_btc_hit_rate, raw_btc_hits, raw_btc_ratio, raw_btc_coverage = 0.0, 0, 0.0, 0.0
            else:
                raw_btc_correct = np.sum(np.sign(raw_scores)[mask_raw] == np.sign(btc_10s)[mask_raw])
                raw_btc_hit_rate = float(raw_btc_correct / num_pred_raw)
                raw_btc_hits = int(raw_btc_correct)
                raw_btc_ratio = num_pred_raw / n_test
                raw_btc_coverage = raw_btc_ratio
            
            # zscore_btc: z_score vs btc_10s
            if num_pred_zscore == 0:
                zscore_btc_hit_rate, zscore_btc_hits, zscore_btc_ratio, zscore_btc_coverage = 0.0, 0, 0.0, 0.0
            else:
                zscore_btc_correct = np.sum(np.sign(z_scores)[mask_zscore] == np.sign(btc_10s)[mask_zscore])
                zscore_btc_hit_rate = float(zscore_btc_correct / num_pred_zscore)
                zscore_btc_hits = int(zscore_btc_correct)
                zscore_btc_ratio = num_pred_zscore / n_test
                zscore_btc_coverage = zscore_btc_ratio
            
            # zeff_poly: zeff vs poly_15min
            if num_pred_zeff == 0:
                zeff_poly_hit_rate, zeff_poly_hits, zeff_poly_ratio, zeff_poly_coverage = 0.0, 0, 0.0, 0.0
            else:
                zeff_poly_correct = np.sum(np.sign(zeff_arr)[mask_zeff] == np.sign(poly_chg)[mask_zeff])
                zeff_poly_hit_rate = float(zeff_poly_correct / num_pred_zeff)
                zeff_poly_hits = int(zeff_poly_correct)
                zeff_poly_ratio = num_pred_zeff / n_test
                zeff_poly_coverage = zeff_poly_ratio
            
            by_thresh[float(t)] = {
                "raw_btc": {
                    "hit_rate": raw_btc_hit_rate,
                    "num_hits": raw_btc_hits,
                    "ratio": raw_btc_ratio,
                    "coverage_rate": raw_btc_coverage,
                },
                "zscore_btc": {
                    "hit_rate": zscore_btc_hit_rate,
                    "num_hits": zscore_btc_hits,
                    "ratio": zscore_btc_ratio,
                    "coverage_rate": zscore_btc_coverage,
                },
                "zeff_poly": {
                    "hit_rate": zeff_poly_hit_rate,
                    "num_hits": zeff_poly_hits,
                    "ratio": zeff_poly_ratio,
                    "coverage_rate": zeff_poly_coverage,
                },
            }
        
        # Legacy single thresh metrics
        mask_raw_legacy = np.abs(raw_scores) > thresh
        mask_zeff_legacy = np.abs(zeff_arr) > thresh
        num_raw = int(mask_raw_legacy.sum())
        num_zeff = int(mask_zeff_legacy.sum())
        
        if num_raw == 0:
            hit_rate_raw_btc = 0.0
        else:
            hit_rate_raw_btc = float(np.mean(np.sign(raw_scores)[mask_raw_legacy] == np.sign(btc_10s)[mask_raw_legacy]))
        
        if num_zeff == 0:
            hit_rate_zeff_poly = 0.0
        else:
            hit_rate_zeff_poly = float(np.mean(np.sign(zeff_arr)[mask_zeff_legacy] == np.sign(poly_chg)[mask_zeff_legacy]))
        
        all_results.append({
            "fold": fold,
            "hit_rate_raw_btc": hit_rate_raw_btc,
            "hit_rate_zeff_poly": hit_rate_zeff_poly,
            "num_predicted_raw_btc": num_raw,
            "num_predicted_zeff_poly": num_zeff,
            "coverage_rate_raw_btc": num_raw / n_test if n_test > 0 else 0.0,
            "coverage_rate_zeff_poly": num_zeff / n_test if n_test > 0 else 0.0,
            "n": n_test,
            "mean_abs_raw": float(np.mean(np.abs(raw_scores))),
            "mean_abs_zscore": float(np.mean(np.abs(z_scores))),
            "mean_abs_zeff": float(np.mean(np.abs(zeff_arr))),
            "by_thresh": by_thresh,
        })
    
    # Compute summary
    summary = {
        "hit_rate_raw_btc": float(np.mean([r["hit_rate_raw_btc"] for r in all_results])) if all_results else 0.0,
        "hit_rate_zeff_poly": float(np.mean([r["hit_rate_zeff_poly"] for r in all_results])) if all_results else 0.0,
        "hit_rate_raw_btc_thresh": thresh,
        "hit_rate_zeff_poly_thresh": thresh,
        "num_predicted_raw_btc": sum(r["num_predicted_raw_btc"] for r in all_results),
        "num_predicted_zeff_poly": sum(r["num_predicted_zeff_poly"] for r in all_results),
        "coverage_rate_raw_btc": float(np.mean([r["coverage_rate_raw_btc"] for r in all_results])) if all_results else 0.0,
        "coverage_rate_zeff_poly": float(np.mean([r["coverage_rate_zeff_poly"] for r in all_results])) if all_results else 0.0,
        "n_folds": len(all_results),
        "thresh_gradient_results": [],
    }
    
    if all_results:
        summary["mean_abs_raw"] = float(np.mean([r["mean_abs_raw"] for r in all_results]))
        summary["mean_abs_zscore"] = float(np.mean([r["mean_abs_zscore"] for r in all_results]))
        summary["mean_abs_zeff"] = float(np.mean([r["mean_abs_zeff"] for r in all_results]))
        total_n = sum(r["n"] for r in all_results)
        
        for t in thresh_gradient:
            raw_btc_hr = np.mean([r["by_thresh"][t]["raw_btc"]["hit_rate"] for r in all_results])
            raw_btc_hits = sum(r["by_thresh"][t]["raw_btc"]["num_hits"] for r in all_results)
            raw_btc_pred = sum(r["n"] * r["by_thresh"][t]["raw_btc"]["ratio"] for r in all_results)
            raw_btc_ratio = raw_btc_pred / total_n if total_n else 0.0
            raw_btc_coverage = np.mean([r["by_thresh"][t]["raw_btc"]["coverage_rate"] for r in all_results])
            
            zscore_btc_hr = np.mean([r["by_thresh"][t]["zscore_btc"]["hit_rate"] for r in all_results])
            zscore_btc_hits = sum(r["by_thresh"][t]["zscore_btc"]["num_hits"] for r in all_results)
            zscore_btc_pred = sum(r["n"] * r["by_thresh"][t]["zscore_btc"]["ratio"] for r in all_results)
            zscore_btc_ratio = zscore_btc_pred / total_n if total_n else 0.0
            zscore_btc_coverage = np.mean([r["by_thresh"][t]["zscore_btc"]["coverage_rate"] for r in all_results])
            
            zeff_poly_hr = np.mean([r["by_thresh"][t]["zeff_poly"]["hit_rate"] for r in all_results])
            zeff_poly_hits = sum(r["by_thresh"][t]["zeff_poly"]["num_hits"] for r in all_results)
            zeff_poly_pred = sum(r["n"] * r["by_thresh"][t]["zeff_poly"]["ratio"] for r in all_results)
            zeff_poly_ratio = zeff_poly_pred / total_n if total_n else 0.0
            zeff_poly_coverage = np.mean([r["by_thresh"][t]["zeff_poly"]["coverage_rate"] for r in all_results])
            
            summary["thresh_gradient_results"].append({
                "thresh": float(t),
                "raw_btc": {
                    "hit_rate": float(raw_btc_hr),
                    "num_hits": raw_btc_hits,
                    "ratio": float(raw_btc_ratio),
                    "coverage_rate": float(raw_btc_coverage),
                },
                "zscore_btc": {
                    "hit_rate": float(zscore_btc_hr),
                    "num_hits": zscore_btc_hits,
                    "ratio": float(zscore_btc_ratio),
                    "coverage_rate": float(zscore_btc_coverage),
                },
                "zeff_poly": {
                    "hit_rate": float(zeff_poly_hr),
                    "num_hits": zeff_poly_hits,
                    "ratio": float(zeff_poly_ratio),
                    "coverage_rate": float(zeff_poly_coverage),
                },
            })
    
    return {"all_results": all_results, "summary": summary}


def backtest_raw_zscore_per_second(
    cex_csv_paths: list[Path],
    *,
    mid_venue: str = "binance_spot",
    thresh_gradient: Optional[list[float]] = None,
    high_acc_only: bool = True,
    venues: Optional[list[str]] = None,
    weights: Optional[list[float]] = None,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    decay_rate: float = 0.15,
    ewma_alpha: float = 0.2,
    lookback_seconds: int = 7200,
    min_samples: int = 50,
) -> dict[str, Any]:
    """
    仅测试 raw 与 zscore：在每次 CEX 有完整样本的时刻计算一次，与「该时刻 5 秒后」的 BTC 价格比较
    （用当前桶与下一 10s 桶线性插值得到 5s 后价格），按阈值输出准确率（raw_btc / zscore_btc）。
    不涉及 Poly、zeff；评估时间点 = 所有有完整信号的 CEX 样本时间戳。
    """
    if thresh_gradient is None:
        thresh_gradient = THRESH_GRADIENT
    if venues is None:
        venues = ["binance_spot", "okx_spot", "okx_swap", "bybit_spot", "bybit_linear"]
    if weights is None:
        weights = [1.0, 1.0, 2.0, 2.0, 3.0]

    empty_summary = {
        "hit_rate_raw_btc": 0.0,
        "hit_rate_zscore_btc": 0.0,
        "num_predicted_raw_btc": 0,
        "num_predicted_zscore_btc": 0,
        "coverage_rate_raw_btc": 0.0,
        "coverage_rate_zscore_btc": 0.0,
        "n_eval": 0,
        "thresh_gradient_results": [],
    }
    if _iter_complete_signals_from_rows is None or v3_compute_raw_score_from_signals is None:
        logger.warning("CEX scorer v4 functions missing for per-second backtest")
        return {"all_results": [], "summary": empty_summary}
    if AdaptiveScoreNormalizer is None:
        logger.warning("AdaptiveScoreNormalizer missing")
        return {"all_results": [], "summary": empty_summary}

    cex_df = load_cex_mid_csvs(cex_csv_paths, mid_venue=mid_venue)
    cex_rows = load_cex_full_rows(cex_csv_paths)
    if cex_df.empty or not cex_rows:
        logger.warning("CEX data empty")
        return {"all_results": [], "summary": empty_summary}

    cex_df = cex_df.copy()
    cex_df["ts"] = cex_df.index.astype("int64") // 10**9
    cex_10s = cex_df.assign(bucket=(cex_df["ts"] // 10) * 10).groupby("bucket")["close"].mean()
    if cex_10s.empty:
        logger.warning("CEX 10s buckets empty")
        return {"all_results": [], "summary": empty_summary}

    all_signals = _iter_complete_signals_from_rows(
        cex_rows,
        venues=venues,
        weights=weights,
        min_abs_score=0.0,
    )
    if not all_signals:
        logger.warning("No complete CEX signals")
        return {"all_results": [], "summary": empty_summary}

    # Precompute sorted t for O(log n) window slice per eval (avoid O(n) list comp each time)
    all_signals_ts = [tt for tt, _ in all_signals]

    min_bucket = int(cex_10s.index.min())
    max_bucket = int(cex_10s.index.max())
    min_eval_ts = min_bucket + lookback_seconds
    max_eval_ts = max_bucket - 10
    if min_eval_ts >= max_eval_ts:
        logger.warning("Insufficient CEX range for per-second eval (need warmup + 10s future)")
        return {"all_results": [], "summary": empty_summary}

    eval_times = sorted(
        t for t in set(int(round(tt)) for tt, _ in all_signals)
        if min_eval_ts <= t <= max_eval_ts
    )
    if high_acc_only:
        n_before_filter = len(eval_times)
        eval_times = [t for t in eval_times if _is_high_acc_utc(pd.Timestamp(t, unit="s", tz="UTC"))]
        if n_before_filter > 0:
            logger.info(
                "High-acc filter: %d / %d eval timestamps (UTC 00–08, 12–14, 19–21, 23)",
                len(eval_times),
                n_before_filter,
            )
    else:
        logger.info("High-acc filter disabled, using all %d eval timestamps", len(eval_times))
    if not eval_times:
        logger.warning("No eval timestamps in range" + (" after high-acc filter" if high_acc_only else ""))
        return {"all_results": [], "summary": empty_summary}

    normalizer = AdaptiveScoreNormalizer(
        lookback_seconds=lookback_seconds,
        min_samples=min_samples,
        winsorize_prop=0.01,
        use_vol_norm=False,
        clip_limit=3.0,
    )
    ewma_state: Optional[float] = None

    warmup_times = sorted(
        t for t in set(int(round(tt)) for tt, _ in all_signals)
        if t < min_eval_ts
    )
    for t in warmup_times:
        lo = bisect.bisect_left(all_signals_ts, t - time_window_sec)
        hi = bisect.bisect_right(all_signals_ts, t)
        recent = all_signals[lo:hi]
        raw_score, ewma_state = v3_compute_raw_score_from_signals(
            recent,
            float(t),
            time_window_sec=time_window_sec,
            min_abs_score=min_abs_score,
            decay_rate=decay_rate,
            ewma_alpha=ewma_alpha,
            ewma_state=ewma_state,
        )
        normalizer.update(raw_score, float(t))

    raw_scores_list: list[float] = []
    z_scores_list: list[float] = []
    btc_chg_list: list[float] = []

    for t in eval_times:
        lo = bisect.bisect_left(all_signals_ts, t - time_window_sec)
        hi = bisect.bisect_right(all_signals_ts, t)
        recent = all_signals[lo:hi]
        raw_score, ewma_state = v3_compute_raw_score_from_signals(
            recent,
            float(t),
            time_window_sec=time_window_sec,
            min_abs_score=min_abs_score,
            decay_rate=decay_rate,
            ewma_alpha=ewma_alpha,
            ewma_state=ewma_state,
        )
        z_score, _ = normalizer.normalize(raw_score, float(t))
        normalizer.update(raw_score, float(t))

        bucket_t = (t // 10) * 10
        bucket_t10 = ((t + 10) // 10) * 10
        mid_t = _get_bucket_mid(cex_10s, bucket_t)
        mid_t10 = _get_bucket_mid(cex_10s, bucket_t10)
        if mid_t is None or mid_t10 is None or mid_t == 0:
            continue
        # 5 秒后价格：当前桶与下一 10s 桶线性插值
        price_5s = mid_t + (mid_t10 - mid_t) * (5.0 / 10.0)
        btc_5s_change = (price_5s - mid_t) / mid_t
        raw_scores_list.append(raw_score)
        z_scores_list.append(z_score)
        btc_chg_list.append(btc_5s_change)

    raw_scores = np.array(raw_scores_list)
    z_scores = np.array(z_scores_list)
    btc_chg = np.array(btc_chg_list)
    n_eval = len(btc_chg_list)
    if n_eval == 0:
        return {"all_results": [], "summary": empty_summary}

    by_thresh: dict[float, dict[str, Any]] = {}
    for th in thresh_gradient:
        mask_raw = np.abs(raw_scores) > th
        mask_z = np.abs(z_scores) > th
        n_raw = int(mask_raw.sum())
        n_z = int(mask_z.sum())
        if n_raw == 0:
            raw_hr, raw_hits, raw_cov = 0.0, 0, 0.0
        else:
            raw_correct = np.sum(np.sign(raw_scores)[mask_raw] == np.sign(btc_chg)[mask_raw])
            raw_hr = float(raw_correct / n_raw)
            raw_hits = int(raw_correct)
            raw_cov = n_raw / n_eval
        if n_z == 0:
            z_hr, z_hits, z_cov = 0.0, 0, 0.0
        else:
            z_correct = np.sum(np.sign(z_scores)[mask_z] == np.sign(btc_chg)[mask_z])
            z_hr = float(z_correct / n_z)
            z_hits = int(z_correct)
            z_cov = n_z / n_eval
        by_thresh[float(th)] = {
            "raw_btc": {"hit_rate": raw_hr, "num_hits": raw_hits, "ratio": raw_cov, "coverage_rate": raw_cov},
            "zscore_btc": {"hit_rate": z_hr, "num_hits": z_hits, "ratio": z_cov, "coverage_rate": z_cov},
        }

    n_raw_01 = int((np.abs(raw_scores) > 0.1).sum())
    n_z_01 = int((np.abs(z_scores) > 0.1).sum())
    hit_raw_01 = float(np.mean(np.sign(raw_scores)[np.abs(raw_scores) > 0.1] == np.sign(btc_chg)[np.abs(raw_scores) > 0.1])) if n_raw_01 else 0.0
    hit_z_01 = float(np.mean(np.sign(z_scores)[np.abs(z_scores) > 0.1] == np.sign(btc_chg)[np.abs(z_scores) > 0.1])) if n_z_01 else 0.0

    thresh_gradient_results = []
    for th in thresh_gradient:
        rb = by_thresh[th]["raw_btc"]
        zb = by_thresh[th]["zscore_btc"]
        thresh_gradient_results.append({
            "thresh": float(th),
            "raw_btc": {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in rb.items()},
            "zscore_btc": {k: (float(v) if isinstance(v, (np.floating, float)) else v) for k, v in zb.items()},
        })

    summary = {
        "hit_rate_raw_btc": hit_raw_01,
        "hit_rate_zscore_btc": hit_z_01,
        "hit_rate_raw_btc_thresh": 0.1,
        "hit_rate_zscore_btc_thresh": 0.1,
        "num_predicted_raw_btc": n_raw_01,
        "num_predicted_zscore_btc": n_z_01,
        "coverage_rate_raw_btc": n_raw_01 / n_eval if n_eval else 0.0,
        "coverage_rate_zscore_btc": n_z_01 / n_eval if n_eval else 0.0,
        "n_eval": n_eval,
        "thresh_gradient_results": thresh_gradient_results,
        "mean_abs_raw": float(np.mean(np.abs(raw_scores))),
        "mean_abs_zscore": float(np.mean(np.abs(z_scores))),
    }
    return {"all_results": [{"by_thresh": by_thresh, "n_eval": n_eval}], "summary": summary}


def _single_fold_metrics(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    optimizer_params: dict[str, Any],
    z_score_series: Optional[np.ndarray],
) -> dict[str, Any]:
    """Compute metrics for one fold: Sharpe (annualized), hit_rate, mean_return, std_return."""
    if SignalOptimizer is None:
        return {"sharpe": 0.0, "hit_rate": 0.0, "mean_return": 0.0, "std_return": 0.0, "n": 0}
    train_close = train_df["close"].astype(float)
    test_close = test_df["close"].astype(float)
    train_changes = train_close.pct_change().dropna()
    if len(train_changes) < 2:
        return {"sharpe": 0.0, "hit_rate": 0.0, "mean_return": 0.0, "std_return": 0.0, "n": 0}
    std_train = float(train_changes.std())
    if std_train <= 0:
        std_train = 1e-9
    # Offsets from train: absolute close changes
    train_offsets = [abs(train_close.iloc[i + 1] - train_close.iloc[i]) for i in range(len(train_close) - 1)]
    if not train_offsets:
        return {"sharpe": 0.0, "hit_rate": 0.0, "mean_return": 0.0, "std_return": 0.0, "n": 0}
    params = dict(optimizer_params)
    params.setdefault("conservative_scaling_enabled", False)
    opt = SignalOptimizer(**params)
    opt.set_historical_offsets(train_offsets)
    # z_score for test: use provided series or constant
    if z_score_series is not None and len(z_score_series) >= len(test_df):
        z_scores = np.asarray(z_score_series[: len(test_df)], dtype=float)
    else:
        z_scores = np.ones(len(test_df), dtype=float)
    # First test bar: no prior close for actual_change
    actual_changes = test_close.pct_change().fillna(0).values
    returns_list: list[float] = []
    hits_list: list[bool] = []
    t0 = test_df.index[0]
    if hasattr(t0, "timestamp"):
        t0_ts = float(t0.timestamp())
    else:
        t0_ts = float(pd.Timestamp(t0).timestamp())
    prev_close = float(train_close.iloc[-1])
    cum_change = 0.0
    for i in range(len(test_df)):
        close_i = float(test_close.iloc[i])
        actual_change = (close_i - prev_close) / prev_close if prev_close else 0.0
        row_time = test_df.index[i]
        if hasattr(row_time, "timestamp"):
            row_ts = float(row_time.timestamp())
        else:
            row_ts = float(pd.Timestamp(row_time).timestamp())
        elapsed_min = (row_ts - t0_ts) / 60.0
        out = opt.dynamic_decay(elapsed_min, cum_change, return_meta=False)
        decay = float(out)
        z_eff = float(z_scores[i]) * decay
        normalized_change = actual_change / std_train
        ret = z_eff * normalized_change
        returns_list.append(ret)
        hits_list.append(np.sign(z_eff) == np.sign(actual_change))
        cum_change += abs(close_i - prev_close)
        prev_close = close_i
    returns = np.array(returns_list)
    hits = np.array(hits_list)
    n = len(returns)
    if n == 0:
        return {"sharpe": 0.0, "hit_rate": 0.0, "mean_return": 0.0, "std_return": 0.0, "n": 0}
    mean_ret = float(returns.mean())
    std_ret = float(returns.std())
    if std_ret <= 0:
        sharpe = 0.0
    else:
        # Annualize assuming 1h bars -> 24*365 bars/year
        sharpe = mean_ret / std_ret * np.sqrt(24 * 365)
    hit_rate = float(hits.mean())
    return {
        "sharpe": sharpe,
        "hit_rate": hit_rate,
        "mean_return": mean_ret,
        "std_return": std_ret,
        "n": n,
    }


def backtest_optimizer(
    ohlcv_df: pd.DataFrame,
    *,
    window_size: int = 24,
    min_train_size: int = 48,
    n_splits: Optional[int] = None,
    optimizer_params: Optional[dict[str, Any]] = None,
    z_score_series: Optional[np.ndarray] = None,
) -> dict[str, Any]:
    """
    Walk-forward backtest of SignalOptimizer on OHLCV.

    - Ensures time index on ohlcv_df (sets from 'timestamp' if needed).
    - n_splits = min(5, (len(data) - min_train_size) // window_size) by default.
    - z_score_series=None: use constant z=1.0.
    - returns = z_eff * normalized_change (normalized by std of train changes).
    - hit_rate = mean(sign(z_eff) == sign(actual_change)).
    - conservative_scaling_enabled is forced False for backtest.

    Returns:
        all_results: list of per-fold metrics; summary: mean sharpe, mean hit_rate, etc.
    """
    if SignalOptimizer is None or TimeSeriesSplit is None:
        logger.warning("SignalOptimizer or TimeSeriesSplit missing")
        return {"all_results": [], "summary": {}}
    df = ohlcv_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex) and "timestamp" in df.columns:
        df = df.set_index(pd.to_datetime(df["timestamp"], utc=True))
    if "close" not in df.columns:
        logger.warning("ohlcv_df has no 'close' column")
        return {"all_results": [], "summary": {}}
    data = df[["close"]].dropna()
    if len(data) < min_train_size + window_size:
        logger.warning("Insufficient data for backtest")
        return {"all_results": [], "summary": {}}
    if n_splits is None:
        n_splits = min(5, (len(data) - min_train_size) // max(1, window_size))
    n_splits = max(1, n_splits)
    tscv = TimeSeriesSplit(n_splits=n_splits, max_train_size=None)
    params = dict(optimizer_params or {})
    params["conservative_scaling_enabled"] = False
    all_results: list[dict[str, Any]] = []
    for fold, (train_idx, test_idx) in enumerate(tscv.split(data)):
        train_df = data.iloc[train_idx]
        test_df = data.iloc[test_idx]
        fold_metrics = _single_fold_metrics(
            train_df=train_df,
            test_df=test_df,
            optimizer_params=params,
            z_score_series=z_score_series,
        )
        fold_metrics["fold"] = fold
        all_results.append(fold_metrics)
    sharpes = [r["sharpe"] for r in all_results]
    hit_rates = [r["hit_rate"] for r in all_results]
    summary = {
        "mean_sharpe": float(np.mean(sharpes)) if sharpes else 0.0,
        "mean_hit_rate": float(np.mean(hit_rates)) if hit_rates else 0.0,
        "n_folds": len(all_results),
    }
    return {"all_results": all_results, "summary": summary}


def cache_backtest_results(
    cache_path: Path,
    best_params: dict[str, Any],
    regime_volatility: float,
    *,
    max_age_hours: float = 24.0,
) -> None:
    """Write backtest cache: best_params and regime fingerprint (volatility)."""
    try:
        cache_path = Path(cache_path)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "best_params": best_params,
            "regime_volatility": regime_volatility,
            "written_at": time.time(),
        }
        with cache_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
    except Exception as e:
        logger.warning("cache_backtest_results write failed: %s", e)


def load_cached_backtest(
    cache_path: Path,
    current_volatility: float,
    *,
    max_age_hours: float = 24.0,
    volatility_tolerance: float = 0.3,
) -> Optional[dict[str, Any]]:
    """
    Load cached best_params if age < max_age_hours and regime similar (vol within 30%).
    If current_volatility differs from cached regime_volatility by > 30%, return None to force re-run.
    """
    try:
        cache_path = Path(cache_path)
        if not cache_path.exists():
            return None
        with cache_path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        written_at = float(payload.get("written_at", 0))
        if (time.time() - written_at) / 3600.0 > max_age_hours:
            return None
        cached_vol = float(payload.get("regime_volatility", 0))
        if cached_vol <= 0:
            return payload
        if abs(current_volatility - cached_vol) / max(cached_vol, 1e-9) > volatility_tolerance:
            return None
        return payload
    except Exception as e:
        logger.warning("load_cached_backtest failed: %s", e)
        return None


def run_backtest_with_cache(
    ohlcv_df: pd.DataFrame,
    cache_path: Path,
    *,
    window_size: int = 24,
    min_train_size: int = 48,
    optimizer_params: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Run backtest only when BACKTEST_MODE is set and cache miss or regime change.
    Regime fingerprint = std(returns) of full series (or last 24h); if current vs cache > 30%, re-run.
    """
    if not os.environ.get("BACKTEST_MODE"):
        return {"skipped": True, "reason": "BACKTEST_MODE not set"}
    df = ohlcv_df.copy()
    if not isinstance(df.index, pd.DatetimeIndex) and "timestamp" in df.columns:
        df = df.set_index(pd.to_datetime(df["timestamp"], utc=True))
    if "close" not in df.columns:
        return {"skipped": True, "reason": "no close column"}
    returns = df["close"].astype(float).pct_change().dropna()
    current_vol = float(returns.std()) if len(returns) else 0.0
    cached = load_cached_backtest(cache_path, current_vol, max_age_hours=24.0, volatility_tolerance=0.3)
    if cached is not None:
        return {"skipped": True, "reason": "cache_hit", "best_params": cached.get("best_params")}
    result = backtest_optimizer(
        ohlcv_df,
        window_size=window_size,
        min_train_size=min_train_size,
        optimizer_params=optimizer_params,
    )
    best_params = optimizer_params or {}
    summary = result.get("summary", {})
    cache_backtest_results(cache_path, best_params, current_vol, max_age_hours=24.0)
    result["best_params"] = best_params
    result["regime_volatility"] = current_vol
    return result


def _grid_search_one(
    args: tuple[
        pd.DataFrame,
        dict[str, Any],
        int,
        int,
    ],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Single task for grid search: (ohlcv_df, params, window_size, min_train_size)."""
    ohlcv_df, params, window_size, min_train_size = args
    res = backtest_optimizer(
        ohlcv_df,
        window_size=window_size,
        min_train_size=min_train_size,
        optimizer_params=params,
    )
    return (params, res)


def grid_search_backtest(
    ohlcv_df: pd.DataFrame,
    param_grid: list[dict[str, Any]],
    *,
    window_size: int = 24,
    min_train_size: int = 48,
    metric: str = "mean_sharpe",
    n_jobs: int = 1,
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    Run backtest for each param combo; optionally use multiprocessing.Pool (n_jobs>1).
    Returns list of (params, result) sorted by metric (e.g. mean_sharpe descending).
    """
    args_list: list[tuple[pd.DataFrame, dict[str, Any], int, int]] = [
        (ohlcv_df, p, window_size, min_train_size)
        for p in param_grid
    ]
    if n_jobs <= 1:
        results = [_grid_search_one(a) for a in args_list]
    else:
        from multiprocessing import Pool

        with Pool(processes=min(n_jobs, len(param_grid))) as pool:
            results = pool.map(_grid_search_one, args_list)
    key = "summary"
    subkey = metric

    def sort_key(item: tuple[dict[str, Any], dict[str, Any]]) -> float:
        s = (item[1].get(key) or {}).get(subkey)
        return float(s) if s is not None else -1e9

    results.sort(key=sort_key, reverse=True)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Test with mock data
    df = mock_ohlcv(n_days=14, freq="1h", seed=42)
    logger.info("mock_ohlcv rows=%d columns=%s", len(df), list(df.columns))
    res = backtest_optimizer(
        df,
        window_size=24,
        min_train_size=48,
    )
    logger.info("backtest summary: %s", res.get("summary"))
    for r in res.get("all_results", []):
        logger.info("  fold %s: sharpe=%.4f hit_rate=%.4f", r.get("fold"), r.get("sharpe"), r.get("hit_rate"))
