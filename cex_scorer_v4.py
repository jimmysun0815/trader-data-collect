from __future__ import annotations

import json
import logging
import math
import pickle
import sys
import threading
import time
import urllib.parse
import urllib.request
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

try:
    from scipy.stats import trim_mean as _trim_mean
except ImportError:
    _trim_mean = None  # type: ignore[assignment]

try:
    from scipy.stats import bootstrap as _scipy_bootstrap
except ImportError:
    _scipy_bootstrap = None  # type: ignore[assignment]

try:
    from scipy.stats import spearmanr as _scipy_spearmanr
except ImportError:
    _scipy_spearmanr = None  # type: ignore[assignment]

_NORMALIZER_CACHE: dict[str, AdaptiveScoreNormalizer] = {}
_LOGGED_NORMALIZER: set[str] = set()
_LOGGED_CSV: set[str] = set()
_LOGGED_VOL_FALLBACK: set[int] = set()
_LOGGED_MID_EMPTY: set[str] = set()
_LOGGED_MID_SERIES_EMPTY: set[str] = set()
_WARMUP_DONE: set[str] = set()
_TS_WARNED: set[float] = set()  # 异常时间戳只告警一次
_CHAINLINK_CACHE: dict[str, dict[str, Any]] = {}
_EWMA_STATE: dict[str, float] = {}
_EWMA_LOCKS: dict[str, threading.Lock] = {}
_consecutive_failures: int = 0
_binance_failed_since: Optional[float] = None
_BINANCE_OFFSETS_CACHE: dict[str, tuple[float, list[float]]] = {}
_BINANCE_OHLCV_DEQUE: Optional[deque] = None
_BINANCE_DEQUE_MAXLEN: int = 48


def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        line = f.readline().strip("\n")
    import csv

    return next(csv.reader([line]))


def _read_tail_rows(path: Path, *, tail_bytes: int) -> list[dict[str, str]]:
    import csv

    header = _read_csv_header(path)
    with path.open("rb") as f:
        f.seek(0, 2)
        size = f.tell()
        start = max(0, size - int(tail_bytes))
        f.seek(start)
        text = f.read().decode("utf-8", errors="replace")
    lines = [ln for ln in text.splitlines() if ln.strip()]
    rows: list[dict[str, str]] = []
    for ln in lines:
        if ln.startswith("ts_sample_utc,") and "venue" in ln:
            continue
        try:
            row = next(csv.DictReader([",".join(header), ln]))
        except Exception:
            continue
        rows.append({k: (row.get(k) or "") for k in header})
    return rows


def _iter_complete_signals_from_rows(
    rows: list[dict[str, str]],
    *,
    venues: list[str],
    weights: list[float],
    min_abs_score: float = 0.0,
) -> list[tuple[float, float]]:
    need = set(venues)
    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        venue = str(row.get("venue") or "").strip()
        if venue not in need:
            continue
        if str(row.get("err") or "").strip():
            continue
        sid_s = str(row.get("sample_id") or "").strip()
        t_s = str(row.get("t_sample_unix") or "").strip()
        imb_s = str(row.get("imb") or "").strip()
        if not (sid_s and t_s and imb_s):
            continue
        try:
            sid = int(float(sid_s))
            t = float(t_s)
            imb = float(imb_s)
        except Exception:
            continue
        t_norm = _normalize_ts(t)
        g = groups.setdefault(sid, {"t": t_norm, "imbs": {}})
        g["t"] = t_norm
        g["imbs"][venue] = imb
    out: list[tuple[float, float]] = []
    for sid, g in groups.items():
        imbs = g.get("imbs") or {}
        if not all(v in imbs for v in venues):
            continue
        score = 0.0
        for i, v in enumerate(venues):
            score += float(weights[i]) * float(imbs[v])
        if abs(score) < float(min_abs_score):
            continue
        out.append((float(g["t"]), float(score)))
    out.sort(key=lambda x: x[0])
    return out


def _ewma_lock(cache_key: str) -> threading.Lock:
    """按 cache_key 懒加载 Lock，用于 EWMA 读写与 pkl 的线程安全。"""
    if cache_key not in _EWMA_LOCKS:
        _EWMA_LOCKS[cache_key] = threading.Lock()
    return _EWMA_LOCKS[cache_key]


def _load_ewma_state(ewma_file: Path) -> Optional[float]:
    """从 pkl 加载 EWMA 状态；文件不存在或损坏返回 None。"""
    if not ewma_file.exists():
        return None
    try:
        with ewma_file.open("rb") as f:
            state = pickle.load(f)
        ewma_raw = state.get("ewma_raw")
        if ewma_raw is None:
            return None
        return float(ewma_raw)
    except Exception:
        return None


def _save_ewma_state(ewma_file: Path, ewma_raw: float) -> None:
    """将 EWMA 状态写入 pkl。"""
    try:
        ewma_file.parent.mkdir(parents=True, exist_ok=True)
        state = {"ewma_raw": float(ewma_raw), "saved_at": time.time()}
        with ewma_file.open("wb") as f:
            pickle.dump(state, f)
    except Exception:
        pass


def cleanup_old_ewma(cache_dir: Path, max_files: int = 50) -> None:
    """删除过旧的 cex_normalizer_*_ewma.pkl，保留最新 max_files 个。"""
    try:
        cache_dir = Path(cache_dir)
        if not cache_dir.exists():
            return
        files = list(cache_dir.glob("cex_normalizer_*_ewma.pkl"))
        if len(files) <= max_files:
            return
        files.sort(key=lambda p: p.stat().st_mtime)
        for old in files[:-max_files]:
            try:
                old.unlink()
                logger.info("Cleaned old EWMA file: %s", old)
            except Exception:
                pass
    except Exception:
        pass


def v3_mid_series_from_rows(
    rows: list[dict[str, str]],
    mid_venue: str,
) -> list[tuple[float, float]]:
    """从 rows 中提取指定 venue 的 (t, mid) 序列，按 sample_id 去重。用于波动率计算。"""
    out: list[tuple[float, float]] = []
    seen_sid: set[int] = set()
    for row in rows:
        venue = str(row.get("venue") or "").strip()
        if venue != mid_venue:
            continue
        if str(row.get("err") or "").strip():
            continue
        sid_s = str(row.get("sample_id") or "").strip()
        t_s = str(row.get("t_sample_unix") or "").strip()
        mid_s = str(row.get("mid") or "").strip()
        if not (sid_s and t_s and mid_s):
            continue
        try:
            sid = int(float(sid_s))
            t = float(t_s)
            mid = float(mid_s)
        except Exception:
            continue
        if sid in seen_sid:
            continue
        seen_sid.add(sid)
        out.append((_normalize_ts(t), mid))
    out.sort(key=lambda x: x[0])
    return out


def v3_apply_volatility_filter(
    rows: list[dict[str, str]],
    raw_score: float,
    mid_venue: str,
    *,
    window_sec: float = 60.0,
    hist_window_sec: float = 300.0,
    multiplier: float = 1.5,
    decay_factor: float = 0.7,
    use_atr: bool = False,
    vol_extreme_zero: bool = False,
) -> float:
    """当当前窗口波动率高于历史阈值时，对 raw_score 做衰减或置零。"""
    mid_series = v3_mid_series_from_rows(rows, mid_venue)
    if len(mid_series) < 2:
        return raw_score
    now_ts = mid_series[-1][0] if mid_series else 0.0
    cutoff_hist = now_ts - hist_window_sec
    in_hist = [(t, m) for t, m in mid_series if t >= cutoff_hist]
    if len(in_hist) < 2:
        return raw_score
    mids = [m for _, m in in_hist]
    cutoff_cur = now_ts - window_sec
    cur_mids = [m for t, m in in_hist if t >= cutoff_cur]
    if len(cur_mids) < 2:
        return raw_score
    if use_atr:
        trs_cur = []
        for i in range(1, len(cur_mids)):
            trs_cur.append(abs(cur_mids[i] - cur_mids[i - 1]))
        if not trs_cur:
            return raw_score
        span = max(1, int(window_sec // 2))
        ema_tr = trs_cur[0]
        for i in range(1, len(trs_cur)):
            ema_tr = (2.0 / (span + 1)) * trs_cur[i] + (1.0 - 2.0 / (span + 1)) * ema_tr
        current_vol = ema_tr
        hist_vols = []
        w = max(2, int(window_sec))
        for start in range(len(mids) - w):
            window_mids = mids[start : start + w]
            if len(window_mids) < 2:
                continue
            trs_w = [abs(window_mids[i] - window_mids[i - 1]) for i in range(1, len(window_mids))]
            if not trs_w:
                continue
            ema_w = trs_w[0]
            for i in range(1, len(trs_w)):
                ema_w = (2.0 / (span + 1)) * trs_w[i] + (1.0 - 2.0 / (span + 1)) * ema_w
            hist_vols.append(ema_w)
        historical_vol = statistics.median(hist_vols) if hist_vols else current_vol
    else:
        current_vol = statistics.stdev(cur_mids)
        hist_vols = []
        n = len(mids)
        w = max(2, int(window_sec))
        for i in range(n - w + 1):
            slice_m = mids[i : i + w]
            if len(slice_m) >= 2:
                hist_vols.append(statistics.stdev(slice_m))
        historical_vol = statistics.median(hist_vols) if hist_vols else current_vol
    threshold = historical_vol * multiplier if historical_vol > 0 else 0.0
    if threshold <= 0:
        return raw_score
    if current_vol > threshold:
        if vol_extreme_zero:
            return 0.0
        return raw_score * decay_factor
    return raw_score


def v3_venue_series_from_rows(
    rows: list[dict[str, str]],
    venues: list[str],
) -> dict[str, list[tuple[float, float, float]]]:
    """从 rows 中按 venue 提取 (t, imb, mid) 序列，每 venue 一个列表，按 t 升序。"""
    out: dict[str, list[tuple[float, float, float]]] = {v: [] for v in venues}
    for row in rows:
        venue = str(row.get("venue") or "").strip()
        if venue not in out:
            continue
        if str(row.get("err") or "").strip():
            continue
        t_s = str(row.get("t_sample_unix") or "").strip()
        imb_s = str(row.get("imb") or "").strip()
        mid_s = str(row.get("mid") or "").strip()
        if not (t_s and imb_s and mid_s):
            continue
        try:
            t = float(t_s)
            imb = float(imb_s)
            mid = float(mid_s)
        except Exception:
            continue
        out[venue].append((_normalize_ts(t), imb, mid))
    for v in out:
        out[v] = sorted(out[v], key=lambda x: x[0])
    return out


def v3_compute_dynamic_weights(
    venue_data: dict[str, list[tuple[float, float, Optional[float]]]],
    *,
    window_sec: float = 60.0,
    k: float = 0.7,
    min_samples: int = 10,
    use_spearman: bool = True,
    base_weights: Optional[dict[str, float]] = None,
) -> dict[str, float]:
    """根据各 venue 最近 window_sec 内 (imb, delta_mid) 的置信度计算动态权重。"""
    need = list(venue_data.keys())
    if not need:
        return {}
    confidences: dict[str, float] = {}
    for venue in need:
        series = venue_data.get(venue) or []
        cutoff = 0.0
        if series:
            cutoff = series[-1][0] - window_sec
        pairs = [(imb, dm) for t, imb, dm in series if t >= cutoff and dm is not None]
        if len(pairs) < min_samples:
            confidences[venue] = 0.5
            continue
        imbs = [p[0] for p in pairs]
        deltas = [p[1] for p in pairs]
        if use_spearman and _scipy_spearmanr is not None:
            try:
                corr, _ = _scipy_spearmanr(imbs, deltas)
                conf = float(corr) if not (corr != corr) else 0.0
            except Exception:
                conf = 0.0
        else:
            match = sum(1 for a, b in zip(imbs, deltas) if (a > 0 and b > 0) or (a < 0 and b < 0) or (a == 0 and b == 0))
            conf = match / len(pairs) if pairs else 0.0
        confidences[venue] = conf
    vals = [confidences[v] for v in need]
    median_conf = statistics.median(vals) if vals else 0.0
    if base_weights is not None:
        default_weights = {v: float(base_weights.get(v, 1.0)) for v in need}
    else:
        default_weights = {v: 1.0 for v in need}
    weights = {}
    for venue in need:
        base_v = float(default_weights.get(venue, 1.0))
        adj = 1.0 + k * (confidences[venue] - median_conf)
        w = base_v * adj
        w = max(0.5, min(1.5, w))
        weights[venue] = w
    return weights


def v3_compute_weighted_score_at_t(
    venue_imbs: dict[str, float],
    weights: dict[str, float],
) -> float:
    """sum(weight_v * imb_v) / sum(weight_v)。"""
    total = 0.0
    total_w = 0.0
    for v, imb in venue_imbs.items():
        w = weights.get(v, 1.0)
        total += w * imb
        total_w += w
    if total_w <= 0:
        return 0.0
    return total / total_w


def v3_load_recent_signals(
    path: Path,
    venues: list[str],
    weights: list[float],
    *,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    tail_bytes: int = 16384,
) -> list[tuple[float, float]]:
    """从CSV文件加载最近时间窗口内的所有完整信号（固定权重）。"""
    rows = _read_tail_rows(path, tail_bytes=tail_bytes)
    all_signals = _iter_complete_signals_from_rows(
        rows, venues=venues, weights=weights, min_abs_score=min_abs_score
    )
    now_ts = time.time()
    cutoff_ts = now_ts - float(time_window_sec)
    recent = [
        (t, s)
        for t, s in all_signals
        if _normalize_ts(float(t)) >= cutoff_ts and abs(float(s)) >= float(min_abs_score)
    ]
    return sorted(recent, key=lambda x: x[0])


def v3_load_recent_signals_with_dynamic_weights(
    path: Path,
    venues: list[str],
    *,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    tail_bytes: int = 65536,
    dw_window_sec: float = 60.0,
    dw_horizon_sec: float = 15.0,
    dw_k: float = 0.7,
    dw_min_samples: int = 10,
    dw_use_spearman: bool = True,
    base_weights: Optional[dict[str, float]] = None,
) -> list[tuple[float, float]]:
    """使用动态 venue 权重加载最近时间窗口内的信号。"""
    rows = _read_tail_rows(path, tail_bytes=tail_bytes)
    venue_series = v3_venue_series_from_rows(rows, venues)
    need = set(venues)
    now_ts = 0.0
    for v in venues:
        for t, _, _ in venue_series.get(v) or []:
            now_ts = max(now_ts, float(t))
    if now_ts <= 0:
        now_ts = time.time()
    latest_mid: dict[str, float] = {}
    for v in venues:
        ser = venue_series.get(v) or []
        if ser:
            latest_mid[v] = ser[-1][2]
        else:
            latest_mid[v] = 0.0
    venue_data: dict[str, list[tuple[float, float, Optional[float]]]] = {}
    for v in venues:
        ser = venue_series.get(v) or []
        out_list: list[tuple[float, float, Optional[float]]] = []
        for t, imb, mid in ser:
            if t + dw_horizon_sec <= now_ts:
                delta_mid = latest_mid[v] - mid
            else:
                delta_mid = None
            out_list.append((t, imb, delta_mid))
        venue_data[v] = out_list
    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        venue = str(row.get("venue") or "").strip()
        if venue not in need or str(row.get("err") or "").strip():
            continue
        sid_s = str(row.get("sample_id") or "").strip()
        t_s = str(row.get("t_sample_unix") or "").strip()
        imb_s = str(row.get("imb") or "").strip()
        if not (sid_s and t_s and imb_s):
            continue
        try:
            sid = int(float(sid_s))
            t = float(t_s)
            imb = float(imb_s)
        except Exception:
            continue
        g = groups.setdefault(sid, {"t": t, "imbs": {}})
        g["t"] = t
        g["imbs"][venue] = imb
    cutoff_ts = now_ts - float(time_window_sec)
    complete = [
        (g["t"], g["imbs"].copy())
        for g in groups.values()
        if all(v in (g.get("imbs") or {}) for v in venues)
        and _normalize_ts(g["t"]) >= cutoff_ts
        and _normalize_ts(g["t"]) <= now_ts
    ]
    complete.sort(key=lambda x: x[0])
    out_signals: list[tuple[float, float]] = []
    for t, imbs in complete:
        window_start = t - dw_window_sec
        venue_data_at_t: dict[str, list[tuple[float, float, Optional[float]]]] = {}
        for v in venues:
            series = venue_data.get(v) or []
            pairs = [(ti, imb, dm) for ti, imb, dm in series if window_start <= ti <= t and dm is not None]
            venue_data_at_t[v] = pairs
        weights_map = v3_compute_dynamic_weights(
            venue_data_at_t,
            window_sec=dw_window_sec,
            k=dw_k,
            min_samples=dw_min_samples,
            use_spearman=dw_use_spearman,
            base_weights=base_weights,
        )
        score_t = v3_compute_weighted_score_at_t(imbs, weights_map)
        if abs(score_t) >= float(min_abs_score):
            out_signals.append((t, score_t))
    return sorted(out_signals, key=lambda x: x[0])


def v3_compute_raw_score_from_signals(
    signals: list[tuple[float, float]],
    as_of_ts: float,
    *,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    decay_rate: float = 0.15,
    ewma_alpha: float = 0.2,
    ewma_state: Optional[float] = None,
) -> tuple[float, float]:
    """对已计算好的 (t, score) 信号列表做时间窗口过滤 + 时间衰减 + EWMA。Returns (raw_score, next_ewma_state)。"""
    cutoff_ts = as_of_ts - float(time_window_sec)
    recent = [
        (t, s)
        for t, s in signals
        if _normalize_ts(float(t)) >= cutoff_ts and abs(float(s)) >= float(min_abs_score)
    ]
    recent = sorted(recent, key=lambda x: x[0])
    if not recent:
        next_state = ewma_state if ewma_state is not None else 0.0
        return (float(next_state), next_state)
    weighted_sum = 0.0
    total_weight = 0.0
    decay_rate_val = float(decay_rate)
    for t, s in recent:
        t_norm = _normalize_ts(float(t))
        age_sec = as_of_ts - t_norm
        w = math.exp(-decay_rate_val * age_sec)
        weighted_sum += w * float(s)
        total_weight += w
    current_raw = weighted_sum / total_weight if total_weight > 0 else 0.0
    if ewma_state is None:
        raw_score = current_raw
    else:
        raw_score = float(ewma_alpha) * current_raw + (1.0 - float(ewma_alpha)) * ewma_state
    return (float(raw_score), float(raw_score))


def _normalize_ts(ts: float) -> float:
    """将时间戳统一规范为秒：微秒(>1e15)、毫秒(>1e12) 一次转换；异常范围打一次告警。Unix 秒约 1e9。"""
    t = float(ts)
    if t > 1e15:
        t /= 1_000_000.0  # 微秒 -> 秒
    elif t > 1e12:
        t /= 1_000.0  # 毫秒 -> 秒
    # 否则视为已是秒（含 1e9 量级的 Unix 秒），不除
    if t < 1e8 or t > 1e15:
        key = round(t, 6)
        if key not in _TS_WARNED:
            _TS_WARNED.add(key)
            logger.warning("[cex] _normalize_ts: 异常时间戳范围 ts=%s -> %ss (可能数据源错误)", ts, t)
    return float(t)


def _needs_warmup(normalizer: AdaptiveScoreNormalizer, *, now_ts: float) -> bool:
    try:
        n = len(normalizer.history)
    except Exception:
        n = 0
    if n < int(normalizer.min_samples):
        return True
    try:
        oldest = float(normalizer.history[0][0]) if normalizer.history else 0.0
    except Exception:
        oldest = 0.0
    now_s = _normalize_ts(now_ts)
    oldest_s = _normalize_ts(oldest)
    if float(now_s) - float(oldest_s) < float(normalizer.lookback_seconds) * 0.8:
        return True
    return False


def _prev_cex_slice_path(p: Path) -> Optional[Path]:
    """
    Given cex_{symbol}_YYYYMMDD_00-12/12-24.csv, return previous 12h slice if exists.
    """
    try:
        name = p.name
        parts = name.split("_")
        if len(parts) < 4:
            return None
        symbol = parts[1]
        day = parts[2]
        label = parts[3].replace(".csv", "")
        if label not in ("00-12", "12-24"):
            return None
        d = datetime.strptime(day, "%Y%m%d").date()
        if label == "12-24":
            prev_day = d
            prev_label = "00-12"
        else:
            prev_day = d - timedelta(days=1)
            prev_label = "12-24"
        prev_name = f"cex_{symbol}_{prev_day.strftime('%Y%m%d')}_{prev_label}.csv"
        prev_path = p.parent / prev_name
        return prev_path if prev_path.exists() else None
    except Exception:
        return None


def _warmup_normalizer_from_csv(
    *,
    csv_path: Path,
    normalizer: AdaptiveScoreNormalizer,
    venues: list[str],
    weights: list[float],
    lookback_seconds: int,
    now_ts: float,
    warmup_end_ts: float | None = None,
) -> None:
    """
    Warmup normalizer from CSV file.
    
    Args:
        csv_path: CSV file path
        normalizer: Normalizer to warmup
        venues: Venue list
        weights: Weight list
        lookback_seconds: Lookback window in seconds
        now_ts: Current timestamp (used to calculate cutoff)
        warmup_end_ts: Optional end timestamp for warmup data. If None, uses now_ts.
                      This is useful when warmuping the current file in training,
                      where we want to include data up to start_t, not now_ts.
    """
    now_s = _normalize_ts(float(now_ts))
    cutoff = float(now_s) - float(lookback_seconds)
    # 如果指定了 warmup_end_ts，使用它作为上限；否则使用 now_ts
    warmup_end = float(warmup_end_ts) if warmup_end_ts is not None else float(now_s)
    warmup_end = _normalize_ts(warmup_end)
    try:
        last_ts = max((_normalize_ts(t) for t, _ in normalizer.history), default=None)
    except Exception:
        last_ts = None
    tail_bytes = 64_000_000
    rows = _read_tail_rows(csv_path, tail_bytes=tail_bytes)
    signals = _iter_complete_signals_from_rows(rows, venues=venues, weights=weights, min_abs_score=0.0)
    earliest = min((_normalize_ts(t) for t, _ in signals), default=float("inf"))
    use_full_scan = earliest > cutoff
    if use_full_scan:
        logger.info("[cex] warmup: tail 不够覆盖 2h，改用全量扫描")
        import csv

        header = _read_csv_header(csv_path)
        rows = []
        with csv_path.open("r", encoding="utf-8", errors="replace") as f:
            r = csv.DictReader(f, fieldnames=header)
            for row in r:
                if row.get("ts_sample_utc", "").startswith("ts_sample_utc,"):
                    continue
                rows.append({k: (row.get(k) or "") for k in header})
        signals = _iter_complete_signals_from_rows(rows, venues=venues, weights=weights, min_abs_score=0.0)
    # filter to last lookback window
    # 如果 last_ts 存在，说明之前已经加载过数据，现在需要加载 [cutoff, last_ts) 范围内的数据来填补空白
    # 如果 last_ts 不存在，说明是第一次加载，加载 [cutoff, warmup_end) 范围内的数据
    used = 0
    newest_ts: float | None = None
    for t, s in signals:
        t_s = _normalize_ts(float(t))
        if t_s + 1e-9 < cutoff:
            continue
        # 如果 last_ts 存在，只加载 [cutoff, last_ts) 范围内的数据（填补空白）
        # 如果 last_ts 不存在，加载 [cutoff, warmup_end) 范围内的数据
        if last_ts is not None:
            if t_s >= float(last_ts) - 1e-9:
                continue  # 跳过已经加载过的数据
        else:
            # 如果 warmup_end 指定了，包含 [cutoff, warmup_end] 范围的数据（包含 warmup_end 本身）
            # 这样训练开始时间 start_t 的数据也会被包含在 warmup 中
            if t_s > float(warmup_end) + 1e-9:
                continue  # 跳过 warmup_end 之后的数据（使用 > 而不是 >=，包含 warmup_end 本身）
        normalizer.update(float(s), float(t_s))
        used += 1
        newest_ts = t_s if newest_ts is None else max(newest_ts, t_s)
    normalizer._cleanup(float(now_s))
    last_ts_str = f"{last_ts:.0f}" if last_ts is not None else "None"
    logger.info(
        "[cex] warmup: 已补齐样本 %d 条 (lookback_s=%d, cutoff=%.0f, warmup_end=%.0f, last_ts=%s)",
        used, int(lookback_seconds), cutoff, warmup_end, last_ts_str,
    )


def _warmup_normalizer_recursive(
    *,
    current_csv: Path,
    normalizer: AdaptiveScoreNormalizer,
    venues: list[str],
    weights: list[float],
    lookback_seconds: int,
    now_ts: float,
    max_files: int = 10,
    warmup_end_ts: float | None = None,
) -> None:
    """
    递归往前查找多个文件来 warmup normalizer，直到有足够的数据。
    先尝试当前文件，如果不够就往前找上一个文件，直到满足 warmup 要求或达到最大文件数。
    
    Args:
        warmup_end_ts: Optional end timestamp for warmup data. When warmuping the current file,
                      this should be set to the training start time (start_t), so that data
                      in [cutoff, start_t) is included. For previous files, this should be None
                      to use now_ts as the limit.
    """
    if not current_csv.exists():
        return
    
    # 先尝试从当前文件 warmup（使用 warmup_end_ts 作为上限）
    try:
        _warmup_normalizer_from_csv(
            csv_path=current_csv,
            normalizer=normalizer,
            venues=venues,
            weights=weights,
            lookback_seconds=lookback_seconds,
            now_ts=now_ts,
            warmup_end_ts=warmup_end_ts,  # 当前文件使用 warmup_end_ts
        )
    except Exception as e:
        logger.warning("[cex] warmup: 当前文件失败 %s: %s", type(e).__name__, e)
    
    # 检查是否还需要更多数据
    files_checked = 1
    prev_csv = _prev_cex_slice_path(current_csv)
    while _needs_warmup(normalizer, now_ts=now_ts) and files_checked < max_files:
        if prev_csv is None or not prev_csv.exists():
            break
        try:
            logger.info("[cex] warmup: 数据仍不足，继续从上一个文件加载: %s", prev_csv.name)
            # 对于之前的文件，不使用 warmup_end_ts（使用 None，即 now_ts）
            _warmup_normalizer_from_csv(
                csv_path=prev_csv,
                normalizer=normalizer,
                venues=venues,
                weights=weights,
                lookback_seconds=lookback_seconds,
                now_ts=now_ts,
                warmup_end_ts=None,  # 之前的文件使用 now_ts 作为上限
            )
            files_checked += 1
            prev_csv = _prev_cex_slice_path(prev_csv)
        except Exception as e:
            logger.warning("[cex] warmup: 上一个文件失败 %s: %s", type(e).__name__, e)
            break
    
    if _needs_warmup(normalizer, now_ts=now_ts):
        logger.warning("[cex] warmup: 警告：已检查 %d 个文件，normalizer history仍不足，z_eff 可能为0直到样本补齐", files_checked)
    else:
        logger.info("[cex] warmup: 完成，已从 %d 个文件加载数据", files_checked)


@dataclass(frozen=True)
class CexScoreResult:
    """
    对外依旧只需要 float score，但为了 runner/日志调试，这里保留可选 meta。
    """

    score: float
    meta: dict[str, Any]


class AdaptiveScoreNormalizer:
    """
    基于历史分布的 Z-score 标准化器（trimmed mean + MAD + 可选波动率归一化 + soft clip）。
    动态维护过去 N 秒的 score 历史，用截尾均值和 MAD 计算稳健 Z-score，
    可选按相对波动率缩放，并用 tanh 做软截断。
    Args:
        lookback_seconds: 回溯窗口（秒），默认 7200（2 小时）
        min_samples: 最小样本数，低于此值返回原始 score，默认 50
        winsorize_prop: 截尾比例（两端各去掉该比例，用于 trimmed mean），默认 0.01
        use_vol_norm: 是否按相对波动率归一化
        vol_window_sec: 波动率历史窗口（秒）
        clip_limit: soft clip 上下限（tanh 饱和）
    """

    def __init__(
        self,
        lookback_seconds: int = 7200,
        min_samples: int = 50,
        winsorize_prop: float = 0.01,
        use_vol_norm: bool = False,
        vol_window_sec: float = 300.0,
        clip_limit: float = 3.0,
    ):
        self.lookback_seconds = float(lookback_seconds)
        self.min_samples = int(min_samples)
        self.winsorize_prop = float(winsorize_prop)
        self.use_vol_norm = bool(use_vol_norm)
        self.vol_window_sec = float(vol_window_sec)
        self.clip_limit = float(clip_limit)
        self.history: deque[tuple[float, float]] = deque()
        self.vol_history: deque[tuple[float, float]] = deque()

    def update(
        self,
        score: float,
        timestamp: Optional[float] = None,
        mid_price: Optional[float] = None,
    ) -> None:
        """添加新的 score 样本；若 use_vol_norm 且提供 mid_price 则同时更新 vol 历史。"""
        ts = _normalize_ts(float(timestamp) if timestamp is not None else time.time())
        self.history.append((ts, float(score)))
        if mid_price is not None and self.use_vol_norm:
            self.vol_history.append((ts, float(mid_price)))
        self._cleanup(ts)

    def _cleanup(self, current_time: float) -> None:
        """删除超出 lookback 的旧数据；若 use_vol_norm 则同时清理 vol_history。"""
        cutoff = current_time - self.lookback_seconds
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
        if self.use_vol_norm:
            vol_cutoff = current_time - self.vol_window_sec
            while self.vol_history and self.vol_history[0][0] < vol_cutoff:
                self.vol_history.popleft()

    def normalize(self, score: float, timestamp: Optional[float] = None) -> tuple[float, dict[str, Any]]:
        """标准化 score 为 Z-score（trimmed mean + MAD；可选相对波动率缩放；soft clip）。"""
        ts = _normalize_ts(float(timestamp) if timestamp is not None else time.time())
        self._cleanup(ts)

        if len(self.history) < self.min_samples:
            return float(score), {
                "mean": 0.0,
                "std": 0.0,
                "mad": 0.0,
                "n_samples": len(self.history),
                "is_normalized": False,
                "z_score": float(score),
                "raw_score": float(score),
                "vol_factor": 1.0,
            }

        scores = [s for _, s in self.history]
        if _trim_mean is not None and 0 < self.winsorize_prop < 0.5:
            mean = float(_trim_mean(scores, self.winsorize_prop))
        else:
            mean = sum(scores) / len(scores)
        deviations = [abs(s - mean) for s in scores]
        mad = statistics.median(deviations) * 1.4826

        if mad < 1e-9:
            z_score = 0.0
        else:
            z_score = (float(score) - mean) / mad

        vol_factor = 1.0
        vol_fallback = False
        if self.use_vol_norm and len(self.vol_history) >= 2:
            mids = [m for _, m in self.vol_history]
            med = statistics.median(mids)
            if med and med > 1e-9 and len(mids) >= 2:
                vol_abs = statistics.stdev(mids)
                vol_factor = vol_abs / med if vol_abs > 1e-9 else 1.0
            if vol_factor < 1e-9:
                vol_fallback = True
                vol_factor = 1.0
            z_score /= max(vol_factor, 1e-6)
            if vol_fallback and id(self) not in _LOGGED_VOL_FALLBACK:
                _LOGGED_VOL_FALLBACK.add(id(self))
                logger.warning("[cex] normalizer vol_norm: mids 无波动或 med≈0，使用 vol_factor=1.0")

        z_score = math.tanh(z_score / self.clip_limit) * self.clip_limit

        return float(z_score), {
            "mean": float(mean),
            "std": float(mad),
            "mad": float(mad),
            "n_samples": len(self.history),
            "is_normalized": True,
            "vol_factor": vol_factor,
            "z_score": float(z_score),
            "raw_score": float(score),
        }

    def save_state(self, path: Path) -> None:
        """持久化 normalizer 状态（含 history、vol_history 及所有构造参数）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "lookback_seconds": self.lookback_seconds,
            "min_samples": self.min_samples,
            "winsorize_prop": self.winsorize_prop,
            "use_vol_norm": self.use_vol_norm,
            "vol_window_sec": self.vol_window_sec,
            "clip_limit": self.clip_limit,
            "history": list(self.history),
            "vol_history": list(self.vol_history),
            "saved_at": time.time(),
        }
        with path.open("wb") as f:
            pickle.dump(state, f)

    @classmethod
    def load_state(cls, path: Path) -> Optional["AdaptiveScoreNormalizer"]:
        """从文件恢复 normalizer；旧 pkl 缺省字段使用默认值。"""
        if not path.exists():
            return None
        try:
            with path.open("rb") as f:
                state = pickle.load(f)
            s = state
            normalizer = cls(
                lookback_seconds=int(s.get("lookback_seconds", 7200)),
                min_samples=int(s.get("min_samples", 50)),
                winsorize_prop=float(s.get("winsorize_prop", 0.01)),
                use_vol_norm=bool(s.get("use_vol_norm", False)),
                vol_window_sec=float(s.get("vol_window_sec", 300.0)),
                clip_limit=float(s.get("clip_limit", 3.0)),
            )
            hist = []
            for t, score_val in list(s.get("history") or []):
                try:
                    hist.append((_normalize_ts(float(t)), float(score_val)))
                except Exception:
                    continue
            normalizer.history = deque(hist)
            vh = list(s.get("vol_history") or [])
            vol_hist = []
            for t, m in vh:
                try:
                    vol_hist.append((_normalize_ts(float(t)), float(m)))
                except Exception:
                    continue
            normalizer.vol_history = deque(vol_hist)
            normalizer._cleanup(_normalize_ts(time.time()))
            return normalizer
        except Exception as e:
            logger.warning("normalizer load_state 失败: %s", e)
            return None


def _chainlink_cache_path(cache_dir: Path, feed_id: str, time_range: str) -> Path:
    safe_feed = feed_id.replace("0x", "")[-12:]
    return cache_dir / f"chainlink_stream_{safe_feed}_{time_range}.json"


def _load_chainlink_cache(path: Path, *, max_age_s: float) -> Optional[list[dict[str, Any]]]:
    try:
        if not path.exists():
            return None
        with path.open("r", encoding="utf-8") as f:
            payload = json.load(f)
        fetched_at = float(payload.get("fetched_at") or 0.0)
        if (time.time() - fetched_at) > float(max_age_s):
            return None
        nodes = payload.get("nodes")
        if isinstance(nodes, list):
            return nodes
    except Exception:
        return None
    return None


def _save_chainlink_cache(path: Path, nodes: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "nodes": nodes}, f)
    except Exception:
        return


def _extract_chainlink_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    for _, val in data.items():
        if isinstance(val, dict):
            nodes = val.get("nodes")
            if isinstance(nodes, list):
                return nodes
    return []


def _fetch_chainlink_history(
    *,
    feed_id: str,
    time_range: str,
    cache_dir: Optional[Path],
    max_age_s: float = 300.0,
    timeout_s: float = 1.5,
) -> list[dict[str, Any]]:
    cache_key = f"{feed_id}:{time_range}"
    now = time.time()
    cached = _CHAINLINK_CACHE.get(cache_key)
    if cached and (now - float(cached.get("fetched_at") or 0.0) <= float(max_age_s)):
        nodes = cached.get("nodes")
        if isinstance(nodes, list):
            return nodes
    cache_root = Path(cache_dir) if cache_dir else Path(".cache")
    cache_path = _chainlink_cache_path(cache_root, feed_id, time_range)
    disk_nodes = _load_chainlink_cache(cache_path, max_age_s=max_age_s)
    if isinstance(disk_nodes, list):
        _CHAINLINK_CACHE[cache_key] = {"fetched_at": now, "nodes": disk_nodes}
        return disk_nodes
    url = "https://data.chain.link/api/historical-stream-data?" + urllib.parse.urlencode(
        {"feedId": feed_id, "timeRange": time_range}
    )
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return disk_nodes or []
    nodes = _extract_chainlink_nodes(payload)
    if isinstance(nodes, list):
        _save_chainlink_cache(cache_path, nodes)
        _CHAINLINK_CACHE[cache_key] = {"fetched_at": now, "nodes": nodes}
        return nodes
    return []


def _parse_chainlink_nodes(nodes: list[dict[str, Any]]) -> list[tuple[float, float]]:
    out: list[tuple[float, float]] = []
    for node in nodes:
        tb = node.get("timeBucket")
        mid = node.get("mid")
        if not tb or mid is None:
            continue
        try:
            ts = datetime.fromisoformat(str(tb).replace("Z", "+00:00")).timestamp()
            price = float(mid) / 1e18
        except Exception:
            continue
        out.append((float(ts), float(price)))
    out.sort(key=lambda x: x[0])
    return out


def _recent_chainlink_offsets(nodes: list[dict[str, Any]], *, n_windows: int) -> list[float]:
    points = _parse_chainlink_nodes(nodes)
    offsets: list[float] = []
    for i in range(len(points) - 1):
        t0, p0 = points[i]
        t1, p1 = points[i + 1]
        dt = float(t1 - t0)
        if dt < 600.0 or dt > 1200.0:
            continue
        offsets.append(abs(float(p1) - float(p0)))
    if n_windows <= 0:
        return offsets
    return offsets[-int(n_windows) :]


def _fetch_binance_ohlcv(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    since_ms: Optional[int] = None,
    limit: int = 24,
    n_hours: Optional[int] = None,
    cache_dir: Optional[Path] = None,
    max_age_s: float = 3600.0,
) -> list[list]:
    """Fetch OHLCV from Binance via ccxt. since_ms must be milliseconds. On first failure wait 5s and retry once."""
    try:
        import ccxt
    except ImportError:
        logger.warning("ccxt missing, fallback to Chainlink")
        return []
    limit = max(24, n_hours or limit)
    try:
        exchange = ccxt.binance({"enableRateLimit": True})
        rate_limit_ms = getattr(exchange, "rateLimit", 1000)
        time.sleep(rate_limit_ms / 1000.0)
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        if not ohlcv:
            time.sleep(5.0)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        return ohlcv if isinstance(ohlcv, list) else []
    except Exception as e:
        logger.warning("Binance fetch failed: %s", e)
        time.sleep(5.0)
        try:
            exchange = ccxt.binance({"enableRateLimit": True})
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
            return ohlcv if isinstance(ohlcv, list) else []
        except Exception as e2:
            logger.warning("Binance retry failed: %s", e2)
            return []


def _ohlcv_to_offsets(
    candles: list[list],
    n_windows: int,
    *,
    now_ts: Optional[float] = None,
) -> tuple[list[float], bool]:
    """
    Convert OHLCV to offsets. Filter only future bars (timestamp >= now).
    Allow ongoing bar [now-3600, now]; set includes_ongoing_bar=True if used.
    Returns (offsets, includes_ongoing_bar).
    """
    if len(candles) < 2:
        return [], False
    now = (now_ts or time.time()) * 1000.0
    closes: list[tuple[float, float]] = []
    for c in candles:
        if len(c) < 6:
            continue
        ts_ms = float(c[0])
        close = float(c[4])
        if ts_ms >= now:
            continue
        closes.append((ts_ms, close))
    closes.sort(key=lambda x: x[0])
    offsets: list[float] = []
    includes_ongoing = False
    for i in range(len(closes) - 1):
        t0, p0 = closes[i]
        t1, p1 = closes[i + 1]
        if t1 >= now:
            includes_ongoing = True
        offsets.append(abs(p1 - p0))
    if n_windows > 0 and offsets:
        offsets = offsets[-int(n_windows) :]
    return offsets, includes_ongoing


def _get_binance_offsets(
    *,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
    n_windows: int = 20,
    n_hours: Optional[int] = 48,
    cache_dir: Optional[Path] = None,
    max_age_s: float = 300.0,
) -> tuple[list[float], dict[str, Any]]:
    """Get offsets from Binance OHLCV with rolling deque and disk cache. Returns (offsets, meta)."""
    global _BINANCE_OHLCV_DEQUE, _BINANCE_DEQUE_MAXLEN
    meta: dict[str, Any] = {}
    maxlen = max(n_windows * 2, 48)
    _BINANCE_DEQUE_MAXLEN = maxlen
    limit = max(24, n_hours or 48)
    cache_path = Path(cache_dir) if cache_dir else Path(".cache")
    cache_file = cache_path / "binance_ohlcv_cache.pkl"
    now_ms = int(time.time() * 1000)
    if _BINANCE_OHLCV_DEQUE is None or len(_BINANCE_OHLCV_DEQUE) == 0:
        if cache_file.exists():
            try:
                with cache_file.open("rb") as f:
                    data = pickle.load(f)
                fetched_at = data.get("fetched_at", 0.0)
                if (time.time() - fetched_at) <= max_age_s:
                    rows = data.get("rows") or []
                    _BINANCE_OHLCV_DEQUE = deque(rows, maxlen=maxlen)
                    offsets, inc = _ohlcv_to_offsets(list(_BINANCE_OHLCV_DEQUE), n_windows, now_ts=time.time())
                    if inc:
                        meta["includes_ongoing_bar"] = True
                    return offsets, meta
            except Exception as e:
                logger.warning("Cache load failed: %s", e)
        ohlcv = _fetch_binance_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            since_ms=None,
            limit=limit,
            n_hours=n_hours,
            cache_dir=cache_path,
            max_age_s=max_age_s,
        )
        if not ohlcv:
            return [], meta
        _BINANCE_OHLCV_DEQUE = deque(ohlcv, maxlen=maxlen)
        try:
            cache_path.mkdir(parents=True, exist_ok=True)
            with cache_file.open("wb") as f:
                pickle.dump({"fetched_at": time.time(), "rows": list(_BINANCE_OHLCV_DEQUE)}, f)
        except Exception as e:
            logger.warning("Cache save failed: %s", e)
    else:
        last_ts_ms = int(_BINANCE_OHLCV_DEQUE[-1][0]) if _BINANCE_OHLCV_DEQUE else 0
        since_ms = last_ts_ms + 3600000 - 300000
        ohlcv = _fetch_binance_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            since_ms=since_ms,
            limit=2,
            n_hours=n_hours,
            cache_dir=cache_path,
            max_age_s=max_age_s,
        )
        for row in ohlcv or []:
            if len(row) >= 6 and (not _BINANCE_OHLCV_DEQUE or row[0] != _BINANCE_OHLCV_DEQUE[-1][0]):
                _BINANCE_OHLCV_DEQUE.append(row)
    offsets, includes_ongoing = _ohlcv_to_offsets(
        list(_BINANCE_OHLCV_DEQUE), n_windows, now_ts=time.time()
    )
    if includes_ongoing:
        meta["includes_ongoing_bar"] = True
    return offsets, meta


def _trimmed_mean_fallback(values: list[float], proportiontocut: float = 0.1) -> float:
    """Trimmed mean using scipy if available, else median."""
    if _trim_mean is not None and len(values) >= 3:
        try:
            return float(_trim_mean(values, proportiontocut))
        except Exception:
            pass
    logger.debug("SciPy unavailable, using median fallback")
    return float(statistics.median(values))


class SignalOptimizer:
    """
    Signal decay optimizer with EMA-adaptive mu and volatility.

    Fallback & Degradation Paths:
    - no scipy: use statistics.median for base_mu, no bootstrap CI
    - small batch (<3): skip all EMA update
    - small batch (<5): skip std update, still update ema_mu
    - std_batch > 3*ema_std: skip update (anomaly)
    - load_state version mismatch: partial recovery, clear historical_offsets
    - historical_offsets empty after load: restore from fallback_offsets if provided
    """

    def __init__(
        self,
        *,
        T: float = 15.0,
        lambda_base: float = 0.01,
        sigma: float = 5.0,
        multiplier: float = 1.0,
        min_mu: float = 8.0,
        max_mu: float = 60.0,
        N_windows: int = 20,
        ema_alpha_mu: float = 0.1,
        ema_alpha_lambda: float = 0.05,
        max_factor: float = 2.0,
        min_offsets: int = 5,
        default_mu: float = 20.0,
        threshold: float = 0.2,
        conservative_scaling_enabled: bool = True,
        fallback_offsets: Optional[list[float]] = None,
    ) -> None:
        self.T = float(T)
        self.lambda_base = float(lambda_base)
        self.sigma = float(sigma)
        self.multiplier = float(multiplier)
        self.min_mu = float(min_mu)
        self.max_mu = float(max_mu)
        self.N_windows = int(N_windows)
        self.ema_alpha_mu = float(ema_alpha_mu)
        self.ema_alpha_lambda = float(ema_alpha_lambda)
        self.max_factor = float(max_factor)
        self.min_offsets = int(min_offsets)
        self.default_mu = float(default_mu)
        self.threshold = float(threshold)
        self.conservative_scaling_enabled = bool(conservative_scaling_enabled)
        self.fallback_offsets = list(fallback_offsets) if fallback_offsets else None
        self.ema_mu: Optional[float] = None
        self.ema_std: Optional[float] = None
        self.historical_offsets: list[float] = []

    @classmethod
    def load_params_from_file(cls, path: Path) -> dict[str, Any]:
        """Load params from JSON or YAML for use as SignalOptimizer(**kwargs)."""
        path = Path(path)
        if not path.exists():
            return {}
        suf = path.suffix.lower()
        try:
            with path.open("r", encoding="utf-8") as f:
                raw = f.read()
            if suf in (".json",):
                return dict(json.loads(raw))
            if suf in (".yaml", ".yml"):
                try:
                    import yaml
                    return dict(yaml.safe_load(raw) or {})
                except ImportError:
                    logger.warning("PyYAML not installed, cannot load %s", path)
                    return {}
        except Exception as e:
            logger.warning("load_params_from_file failed: %s", e)
        return {}

    def set_historical_offsets(self, offsets: list[float]) -> None:
        trimmed = [abs(float(x)) for x in offsets]
        if self.N_windows > 0:
            trimmed = trimmed[-int(self.N_windows) :]
        self.historical_offsets = trimmed
        if len(trimmed) < self.min_offsets:
            logger.warning(
                "Offsets empty or too few (n=%d, min=%d), keeping last ema_mu=%s",
                len(trimmed),
                self.min_offsets,
                self.ema_mu,
            )
            return
        try:
            if len(trimmed) < 3:
                logger.warning("Batch critically small (<3), skipping EMA update entirely")
                return
            base_mu = _trimmed_mean_fallback(trimmed, 0.1)
            if _trim_mean is None:
                logger.info("SciPy unavailable, using median fallback")
            if self.ema_mu is None:
                self.ema_mu = base_mu
                self.ema_std = statistics.stdev(trimmed) if len(trimmed) >= 2 else (base_mu * 0.1)
                return
            if len(trimmed) < 5:
                self.ema_mu = self.ema_alpha_mu * base_mu + (1.0 - self.ema_alpha_mu) * self.ema_mu
                logger.info(
                    "Batch too small (<5), skipping std update, keeping ema_std=%.4f",
                    self.ema_std or 0.0,
                )
                return
            std_batch = statistics.stdev(trimmed)
            if self.ema_std is not None and std_batch > 3.0 * self.ema_std:
                logger.warning("Batch std > 3*ema_std, skipping update (anomaly)")
                return
            self.ema_mu = self.ema_alpha_mu * base_mu + (1.0 - self.ema_alpha_mu) * self.ema_mu
            self.ema_std = self.ema_alpha_lambda * std_batch + (1.0 - self.ema_alpha_lambda) * (self.ema_std or std_batch)
        except (ValueError, OverflowError) as e:
            logger.warning("set_historical_offsets error: %s", e)

    def compute_dynamic_mu(self) -> float:
        mu = self.ema_mu if self.ema_mu is not None else self.default_mu
        return max(self.min_mu, min(self.max_mu, self.multiplier * mu))

    def compute_mu_ci(
        self,
        offsets: Optional[list[float]] = None,
        n_resamples: int = 1000,
        confidence: float = 0.95,
    ) -> tuple[float, float]:
        """Bootstrap CI for mu. Uses winsorized offsets if len>=10; else wide CI."""
        use = offsets if offsets is not None else self.historical_offsets
        ema_mu = self.ema_mu if self.ema_mu is not None else self.default_mu
        if len(use) < 3:
            return (ema_mu * 0.9, ema_mu * 1.1)
        if len(use) < 10:
            return (ema_mu * 0.9, ema_mu * 1.1)
        try:
            import numpy as np
        except ImportError:
            return (ema_mu * 0.9, ema_mu * 1.1)
        arr = np.asarray(use, dtype=float)
        p1, p99 = np.percentile(arr, [1, 99])
        clipped = np.clip(arr, p1, p99)
        low = (1 - confidence) / 2 * 100
        high = (1 + confidence) / 2 * 100
        if _scipy_bootstrap is not None:
            try:
                r = _scipy_bootstrap(
                    (clipped,),
                    np.mean,
                    n_resamples=n_resamples,
                    confidence_level=confidence,
                    method="percentile",
                )
                return (float(r.confidence_interval.low), float(r.confidence_interval.high))
            except Exception:
                pass
        resamples = [
            float(np.mean(np.random.choice(clipped, size=len(clipped), replace=True)))
            for _ in range(n_resamples)
        ]
        ci = np.percentile(resamples, [low, high])
        return (float(ci[0]), float(ci[1]))

    def dynamic_decay(
        self,
        elapsed_time: float,
        cum_change: float,
        *,
        return_meta: bool = False,
    ) -> float | tuple[float, dict[str, Any]]:
        """
        Decay factor with volatility-adaptive lambda.
        sigma is fixed (not propagated to CI); may be extended later.
        """
        abs_delta = abs(float(cum_change))
        mu = self.compute_dynamic_mu()
        try:
            g_delta = 1.0 / (1.0 + math.exp(-(abs_delta - mu) / float(self.sigma)))
        except OverflowError:
            g_delta = 1.0 if abs_delta > mu else 0.0
        ema_mu = self.ema_mu if self.ema_mu is not None else self.default_mu
        ema_std = self.ema_std if self.ema_std is not None else (ema_mu * 0.1)
        vol_ratio = min(ema_std / max(ema_mu, 1e-9), self.max_factor)
        effective_lambda = float(self.lambda_base) * float(g_delta) * (1.0 + vol_ratio)
        decay = math.exp(-effective_lambda * float(elapsed_time))
        if not return_meta:
            return decay
        mu_ci = self.compute_mu_ci()
        sigma_fixed = self.sigma
        try:
            g_lo = 1.0 / (1.0 + math.exp(-(abs_delta - mu_ci[0]) / sigma_fixed))
            g_hi = 1.0 / (1.0 + math.exp(-(abs_delta - mu_ci[1]) / sigma_fixed))
        except OverflowError:
            g_lo = 1.0 if abs_delta > mu_ci[0] else 0.0
            g_hi = 1.0 if abs_delta > mu_ci[1] else 0.0
        lam_hi = self.lambda_base * g_lo * (1.0 + vol_ratio)
        lam_lo = self.lambda_base * g_hi * (1.0 + vol_ratio)
        decay_low = math.exp(-lam_hi * float(elapsed_time))
        decay_high = math.exp(-lam_lo * float(elapsed_time))
        meta = {
            "mu_ci": mu_ci,
            "decay_low": decay_low,
            "decay_high": decay_high,
        }
        return (decay, meta)

    def save_state(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            "version": 1,
            "T": self.T,
            "lambda_base": self.lambda_base,
            "sigma": self.sigma,
            "multiplier": self.multiplier,
            "min_mu": self.min_mu,
            "max_mu": self.max_mu,
            "N_windows": self.N_windows,
            "ema_alpha_mu": self.ema_alpha_mu,
            "ema_alpha_lambda": self.ema_alpha_lambda,
            "max_factor": self.max_factor,
            "min_offsets": self.min_offsets,
            "default_mu": self.default_mu,
            "ema_mu": self.ema_mu,
            "ema_std": self.ema_std,
            "historical_offsets": list(self.historical_offsets),
            "saved_at": time.time(),
        }
        with path.open("wb") as f:
            pickle.dump(state, f)

    @classmethod
    def load_state(
        cls,
        path: Path,
        *,
        fallback_offsets: Optional[list[float]] = None,
    ) -> Optional["SignalOptimizer"]:
        path = Path(path)
        if not path.exists():
            return None
        try:
            with path.open("rb") as f:
                state = pickle.load(f)
            loaded_version = state.get("version", 0)
            if loaded_version != 1:
                logger.warning(
                    "Version mismatch (loaded %d, expected 1), partial recovery or reset",
                    loaded_version,
                )
            kwargs = {
                "T": state.get("T", 15.0),
                "lambda_base": state.get("lambda_base", 0.01),
                "sigma": state.get("sigma", 5.0),
                "multiplier": state.get("multiplier", 1.0),
                "min_mu": state.get("min_mu", 8.0),
                "max_mu": state.get("max_mu", 60.0),
                "N_windows": state.get("N_windows", 20),
                "ema_alpha_mu": state.get("ema_alpha_mu", 0.1),
                "ema_alpha_lambda": state.get("ema_alpha_lambda", 0.05),
                "max_factor": state.get("max_factor", 2.0),
                "min_offsets": state.get("min_offsets", 5),
                "default_mu": state.get("default_mu", 20.0),
                "fallback_offsets": fallback_offsets,
            }
            opt = cls(**kwargs)
            opt.ema_mu = state.get("ema_mu")
            opt.ema_std = state.get("ema_std")
            hist = state.get("historical_offsets") or []
            if loaded_version != 1:
                opt.historical_offsets = []
            else:
                opt.historical_offsets = list(hist)
            if not opt.historical_offsets and (fallback_offsets or state.get("fallback_offsets")):
                fo = fallback_offsets or state.get("fallback_offsets") or []
                opt.historical_offsets = list(fo)
                logger.info("Restored historical_offsets from fallback")
            return opt
        except Exception as e:
            logger.warning("load_state failed: %s", e)
            return None


def score_cex(
    csv_path: Path,
    *,
    venues: Optional[list[str]] = None,
    weights: Optional[list[float]] = None,
    weights_by_venue: Optional[dict[str, float]] = None,
    tail_bytes: int = 16_384,
    use_normalization: bool = True,
    lookback_seconds: int = 7200,
    normalizer_cache_dir: Optional[Path] = None,
    symbol: str = "btc",
    return_meta: bool = False,
    elapsed_time_min: Optional[float] = None,
    cum_change: Optional[float] = None,
    chainlink_feed_id: str = "0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8",
    chainlink_time_range: str = "1W",
    chainlink_cache_dir: Optional[Path] = None,
    chainlink_cache_max_age_s: float = 300.0,
    decay_T: float = 15.0,
    decay_lambda_base: float = 0.22,
    decay_sigma: float = 11.0,
    decay_multiplier: float = 0.6,
    decay_min_mu: float = 8.0,
    decay_max_mu: float = 60.0,
    decay_N_windows: int = 20,
    decay_params_path: Optional[Path] = None,
    fallback_path: Optional[Path] = None,
    use_binance_offsets: bool = True,
    fallback_default_offsets: Optional[list[float]] = None,
    use_v3_raw_score: bool = True,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    decay_rate: float = 0.15,
    ewma_alpha: float = 0.2,
    use_volatility_filter: bool = False,
    vol_window_sec: float = 60.0,
    vol_hist_window_sec: float = 300.0,
    vol_multiplier: float = 1.5,
    vol_decay_factor: float = 0.7,
    vol_use_atr: bool = False,
    vol_extreme_zero: bool = False,
    mid_venue: Optional[str] = None,
    use_dynamic_weights: bool = False,
    dw_window_sec: float = 60.0,
    dw_horizon_sec: float = 15.0,
    dw_k: float = 0.7,
    dw_min_samples: int = 10,
    dw_use_spearman: bool = True,
    dw_base_weights: Optional[dict[str, float]] = None,
    use_vol_norm: bool = False,
    winsorize_prop: float = 0.01,
    clip_limit: float = 3.0,
    normalizer_min_samples: Optional[int] = None,
    min_samples: Optional[int] = None,
) -> float | CexScoreResult:
    """
    CEX 打分器壳：输入 live CSV + 参数（权重等）→ 输出单一 float score。

    说明：
    - 当前实现为了与旧脚本行为一致，默认复用 `trade/polymarket_live_run_current_window.py` 的尾部扫描逻辑。
    - 另一个 agent 可以把这里替换为更复杂的预测/聚合模型，但对外仍保持 `float` 输出。
    
    Args:
        csv_path: CEX数据CSV文件路径
        venues: 交易所列表
        weights: 对应的权重列表
        weights_by_venue: venue -> weight的字典（优先级高于venues/weights）
        tail_bytes: 读取CSV尾部的字节数
        use_normalization: 是否使用Z-score标准化，默认True（推荐）
        lookback_seconds: 标准化回溯窗口（秒），默认7200（2小时）
        normalizer_cache_dir: cache目录，默认为workspace/.cache
        symbol: 交易品种（用于cache文件命名），默认"btc"
        return_meta: 返回 CexScoreResult（含 z_eff/extra_factor）
        elapsed_time_min: 已过时间（分钟），用于动态衰减
        cum_change: 当前累计偏移（美元），用于动态衰减
        chainlink_feed_id: Chainlink feedId（用于历史偏移）
        chainlink_time_range: Chainlink timeRange（如 1W）
        chainlink_cache_dir: Chainlink 缓存目录
        chainlink_cache_max_age_s: Chainlink 缓存最大年龄（秒）
        decay_T: 窗口总时长（分钟）
        decay_lambda_base: 基础衰减率
        decay_sigma: 偏移过渡宽度
        decay_multiplier: mu 乘数
        decay_min_mu: mu 下限
        decay_max_mu: mu 上限
        decay_N_windows: 历史窗口数量
    
    use_v3_raw_score: 是否使用 v3 的 raw_score 聚合逻辑（滚动窗口 + 时间衰减 + EWMA + 波动率过滤 + 动态权重）。默认 True，确保与 v3 测试一致；设为 False 则回退 v4 原单 sample 逻辑（仅用最新完整信号）。
    time_window_sec, min_abs_score, decay_rate, ewma_alpha: v3 聚合参数。
    use_volatility_filter, vol_*: 波动率过滤参数。
    mid_venue: 用于波动率的 venue；默认首 venue。
    use_dynamic_weights, dw_*: 动态权重参数。
    use_vol_norm, winsorize_prop, clip_limit: normalizer v3 参数。
    normalizer_min_samples: 覆盖 v3 默认 50 时打警告。
    min_samples: 已废弃，请用 normalizer_min_samples；若传入则覆盖 normalizer_min_samples 并打 warning。

    Returns:
        标准化后的score（如果use_normalization=True），否则返回原始score。
        当 return_meta=True 时，返回 CexScoreResult，包含 z_eff、raw_*、z_* 等。
    """
    global _consecutive_failures, _binance_failed_since, _EWMA_STATE
    t0 = time.perf_counter()
    p = Path(csv_path)
    fallback_path = Path(fallback_path) if fallback_path is not None else Path(".cache/historical_btc.csv")
    if fallback_path and not fallback_path.parent.exists():
        fallback_path.parent.mkdir(parents=True, exist_ok=True)
    default_offsets = fallback_default_offsets if fallback_default_offsets is not None else [20.0] * 20

    if not p.exists():
        p = _auto_detect_cex_slice(symbol)
        if not p.exists():
            return 0.0

    if weights_by_venue is not None:
        venues2 = list(weights_by_venue.keys())
        weights2 = [float(weights_by_venue[v]) for v in venues2]
    else:
        venues2 = list(venues or ["binance_spot", "okx_spot", "okx_swap", "bybit_spot", "bybit_linear"])
        weights2 = list(weights or [1.0, 1.0, 2.0, 2.0, 3.0])
    if len(venues2) != len(weights2):
        return 0.0

    logger.info("[cex] v4 with v3 frontend (use_v3_raw_score=%s)", use_v3_raw_score)

    cache_dir = Path(normalizer_cache_dir) if normalizer_cache_dir else Path(".cache")
    cache_file = cache_dir / f"cex_normalizer_{symbol}.pkl"
    ewma_file = cache_dir / f"cex_normalizer_{symbol}_ewma.pkl"
    cache_key = str(cache_file.resolve())

    if use_v3_raw_score:
        logger.info("Using v3 raw_score aggregation logic (rolling window + EWMA)")
    else:
        logger.info("Using v4 legacy single-sample raw_score")

    n_signals = 0
    signal_span_sec = 0.0
    mid_venue_val = mid_venue if mid_venue else (venues2[0] if venues2 else None)
    if mid_venue_val is None and use_vol_norm:
        logger.warning("No mid_venue available for vol_norm, skipping vol_history update")
    vol_rows: Optional[list[dict[str, str]]] = None
    mid_series: Optional[list[tuple[float, float]]] = None
    now_ts = time.time()
    timestamp_else = time.time()

    if use_v3_raw_score:
        t1 = time.perf_counter()
        if use_dynamic_weights:
            recent_signals = v3_load_recent_signals_with_dynamic_weights(
                p,
                venues=venues2,
                time_window_sec=float(time_window_sec),
                min_abs_score=float(min_abs_score),
                tail_bytes=max(int(tail_bytes), 65536),
                dw_window_sec=float(dw_window_sec),
                dw_horizon_sec=float(dw_horizon_sec),
                dw_k=float(dw_k),
                dw_min_samples=int(dw_min_samples),
                dw_use_spearman=bool(dw_use_spearman),
                base_weights=dw_base_weights,
            )
        else:
            recent_signals = v3_load_recent_signals(
                p,
                venues=venues2,
                weights=weights2,
                time_window_sec=float(time_window_sec),
                min_abs_score=float(min_abs_score),
                tail_bytes=int(tail_bytes),
            )
        t2 = time.perf_counter()
        if not recent_signals:
            logger.info(
                "[cex] timing path=%s load_signals_s=%.3f total_s=%.3f n_signals=0",
                p, t2 - t1, t2 - t0,
            )
            return 0.0
        now_ts = _normalize_ts(recent_signals[-1][0])
        raw_score, _ = v3_compute_raw_score_from_signals(
            recent_signals,
            now_ts,
            time_window_sec=float(time_window_sec),
            min_abs_score=float(min_abs_score),
            decay_rate=float(decay_rate),
            ewma_alpha=float(ewma_alpha),
            ewma_state=None,
        )
        lock = _ewma_lock(cache_key)
        with lock:
            if cache_key not in _EWMA_STATE:
                loaded = _load_ewma_state(ewma_file)
                _EWMA_STATE[cache_key] = float(loaded) if loaded is not None else raw_score
            ewma_raw = _EWMA_STATE[cache_key]
            ewma_raw = float(ewma_alpha) * raw_score + (1.0 - float(ewma_alpha)) * ewma_raw
            _EWMA_STATE[cache_key] = ewma_raw
            _save_ewma_state(ewma_file, ewma_raw)
        raw_score = _EWMA_STATE[cache_key]
        n_signals = len(recent_signals)
        if n_signals >= 2:
            oldest_ts = _normalize_ts(recent_signals[0][0])
            newest_ts = _normalize_ts(recent_signals[-1][0])
            signal_span_sec = newest_ts - oldest_ts if newest_ts > oldest_ts else 0.0
        if use_volatility_filter or use_vol_norm:
            vol_tail_bytes = max(int(tail_bytes), 256 * 1024)
            vol_rows = _read_tail_rows(p, tail_bytes=vol_tail_bytes)
            if mid_venue_val:
                mid_series = v3_mid_series_from_rows(vol_rows, mid_venue_val)
        if use_volatility_filter and mid_venue_val and vol_rows is not None:
            raw_score = v3_apply_volatility_filter(
                vol_rows,
                raw_score,
                mid_venue_val,
                window_sec=float(vol_window_sec),
                hist_window_sec=float(vol_hist_window_sec),
                multiplier=float(vol_multiplier),
                decay_factor=float(vol_decay_factor),
                use_atr=vol_use_atr,
                vol_extreme_zero=vol_extreme_zero,
            )
    else:
        try:
            from polymarket_live_one_trade import load_latest_complete_cex_signal  # type: ignore
        except Exception:
            try:
                from trade.polymarket_live_run_current_window import load_latest_complete_cex_signal  # type: ignore
            except Exception:
                return 0.0
        t1 = time.perf_counter()
        sig = load_latest_complete_cex_signal(
            p, venues=venues2, weights=weights2, min_abs_score=0.0, tail_bytes=int(tail_bytes),
        )
        t2 = time.perf_counter()
        if sig is None:
            logger.info("[cex] timing path=%s load_signal_s=%.3f total_s=%.3f", p, t2 - t1, t2 - t0)
            return 0.0
        try:
            raw_score = float(sig.score)
        except Exception:
            return 0.0
        timestamp_else = float(sig.t) if (getattr(sig, "t", None) is not None and sig.t != "") else time.time()

    timestamp = now_ts if use_v3_raw_score else timestamp_else

    if not use_normalization:
        if return_meta:
            return CexScoreResult(
                score=float(raw_score),
                meta={"z_score": float(raw_score), "extra_factor": 1.0, "z_eff": float(raw_score)},
            )
        return float(raw_score)

    csv_key = str(p.resolve()) if p.exists() else str(p)
    if csv_key not in _LOGGED_CSV:
        logger.info(
            "[cex] 使用CSV=%s tail_bytes=%d lookback_s=%d normalize=%s",
            csv_key, int(tail_bytes), int(lookback_seconds), bool(use_normalization),
        )
        _LOGGED_CSV.add(csv_key)
    
    normalizer = _NORMALIZER_CACHE.get(cache_key)
    reused_from_memory = normalizer is not None
    loaded_from_disk = False
    created_new = False
    if normalizer is None:
        normalizer = AdaptiveScoreNormalizer.load_state(cache_file)
        if normalizer is not None:
            loaded_from_disk = True
        else:
            min_samples_val = 50
            if normalizer_min_samples is not None:
                min_samples_val = normalizer_min_samples
            elif min_samples is not None:
                min_samples_val = min_samples
                logger.warning(
                    "Using deprecated 'min_samples' kwarg, prefer 'normalizer_min_samples'",
                )
            if min_samples_val != 50:
                logger.warning(
                    "Overriding v3 default min_samples=50 with %d",
                    min_samples_val,
                )
            normalizer = AdaptiveScoreNormalizer(
                lookback_seconds=int(lookback_seconds),
                min_samples=min_samples_val,
                winsorize_prop=float(winsorize_prop),
                use_vol_norm=bool(use_vol_norm),
                vol_window_sec=300.0,
                clip_limit=float(clip_limit),
            )
            created_new = True
        _NORMALIZER_CACHE[cache_key] = normalizer
    if created_new:
        cleanup_old_ewma(cache_dir, max_files=50)
    if cache_key not in _LOGGED_NORMALIZER:
        try:
            n_samples = len(normalizer.history)
        except Exception:
            n_samples = 0
        logger.info(
            "[cex] normalizer_cache=%s reused_mem=%s loaded_disk=%s created_new=%s n_samples=%d lookback_s=%d min_samples=%d",
            cache_key, bool(reused_from_memory), bool(loaded_from_disk),
            bool(created_new), int(n_samples), int(lookback_seconds),
            int(getattr(normalizer, "min_samples", 50)),
        )
        if created_new:
            logger.info("[cex] normalizer 初始化为空，样本不足时将暂停交易（不会使用 raw_score）")
        _LOGGED_NORMALIZER.add(cache_key)

    warmup_key = f"{cache_key}:{int(lookback_seconds)}"
    if warmup_key not in _WARMUP_DONE and _needs_warmup(normalizer, now_ts=float(timestamp)):
        logger.info("[cex] warmup: 检测到缓存不足，开始补齐近 2 小时数据 ...")
        try:
            _warmup_normalizer_from_csv(
                csv_path=p,
                normalizer=normalizer,
                venues=venues2,
                weights=weights2,
                lookback_seconds=int(lookback_seconds),
                now_ts=float(timestamp),
            )
        except Exception as e:
            logger.warning("[cex] warmup: 失败 %s:%s", type(e).__name__, e)
        _WARMUP_DONE.add(warmup_key)

    current_mid: Optional[float] = None
    if use_vol_norm and mid_venue_val:
        if mid_series is not None:
            current_mid = mid_series[-1][1] if mid_series else None
        else:
            vol_tail_bytes = max(int(tail_bytes), 256 * 1024)
            vol_rows_mid = _read_tail_rows(p, tail_bytes=vol_tail_bytes)
            mid_series = v3_mid_series_from_rows(vol_rows_mid, mid_venue_val)
            if mid_series:
                current_mid = mid_series[-1][1]
        if current_mid is None and (mid_series is None or not mid_series):
            if cache_key not in _LOGGED_MID_SERIES_EMPTY:
                _LOGGED_MID_SERIES_EMPTY.add(cache_key)
                logger.warning("[cex] mid_series empty for vol_norm or filter")

    normalized_score, stats = normalizer.normalize(raw_score, timestamp)
    normalizer.update(raw_score, timestamp, mid_price=current_mid)
    t3 = time.perf_counter()

    # 若样本仍不足，返回 0（避免 raw）
    if not bool(stats.get("is_normalized")):
        logger.warning("[cex] warn: 样本不足(n=%d), 暂不交易", int(stats.get("n_samples") or 0))
        try:
            normalizer.save_state(cache_file)
        except Exception:
            pass
        if return_meta:
            return CexScoreResult(score=0.0, meta={"z_score": 0.0, "extra_factor": 0.0, "z_eff": 0.0})
        return 0.0
    
    # 保存状态
    try:
        normalizer.save_state(cache_file)
    except Exception:
        pass  # 保存失败不影响返回结果
    t4 = time.perf_counter()
    
    extra_factor = 1.0
    mu_val: Optional[float] = None
    offsets_n = 0
    binance_meta: dict[str, Any] = {}
    if elapsed_time_min is not None and cum_change is not None:
        try:
            use_binance_now = use_binance_offsets
            decay_kw: dict[str, Any] = {
                "T": float(decay_T),
                "lambda_base": float(decay_lambda_base),
                "sigma": float(decay_sigma),
                "multiplier": float(decay_multiplier),
                "min_mu": float(decay_min_mu),
                "max_mu": float(decay_max_mu),
                "N_windows": int(decay_N_windows),
            }
            if decay_params_path:
                loaded = SignalOptimizer.load_params_from_file(Path(decay_params_path))
                key_map = {
                    "decay_T": "T", "decay_lambda_base": "lambda_base", "decay_sigma": "sigma",
                    "decay_multiplier": "multiplier", "decay_min_mu": "min_mu",
                    "decay_max_mu": "max_mu", "decay_N_windows": "N_windows",
                }
                for k, v in loaded.items():
                    if k in key_map:
                        decay_kw[key_map[k]] = v
                    elif k in ("T", "lambda_base", "sigma", "multiplier", "min_mu", "max_mu", "N_windows",
                              "ema_alpha_mu", "ema_alpha_lambda", "max_factor", "min_offsets",
                              "default_mu", "threshold", "conservative_scaling_enabled"):
                        decay_kw[k] = v
            offsets: list[float] = []
            if _binance_failed_since is not None and (time.time() - _binance_failed_since) > 3600:
                logger.info("Binance cooldown expired, attempting retry")
            if use_binance_now and (_binance_failed_since is None or (time.time() - _binance_failed_since) > 3600):
                try:
                    offsets, binance_meta = _get_binance_offsets(
                        symbol="BTC/USDT",
                        timeframe="1h",
                        n_windows=int(decay_N_windows),
                        n_hours=48,
                        cache_dir=chainlink_cache_dir or normalizer_cache_dir,
                        max_age_s=float(chainlink_cache_max_age_s),
                    )
                except Exception as e:
                    logger.warning("Binance get offsets failed: %s", e)
                    offsets = []
                if not offsets:
                    use_binance_now = False
            if not use_binance_now or not offsets:
                nodes = _fetch_chainlink_history(
                    feed_id=str(chainlink_feed_id),
                    time_range=str(chainlink_time_range),
                    cache_dir=chainlink_cache_dir or normalizer_cache_dir,
                    max_age_s=float(chainlink_cache_max_age_s),
                )
                offsets = _recent_chainlink_offsets(nodes, n_windows=int(decay_N_windows))
            if not offsets and fallback_path and fallback_path.exists():
                try:
                    import pandas as pd
                    df = pd.read_csv(fallback_path, usecols=["timestamp", "close"])
                    closes = df["close"].astype(float).tolist()
                    offsets = [abs(closes[i + 1] - closes[i]) for i in range(len(closes) - 1)][-int(decay_N_windows):]
                except (FileNotFoundError, PermissionError, Exception) as e:
                    logger.error("Fallback CSV inaccessible, using hardcoded defaults: %s", e)
                    offsets = list(default_offsets)
            elif not offsets:
                offsets = list(default_offsets)
            offsets_n = len(offsets)
            fallback_offsets_list = list(default_offsets) if not offsets else None
            optimizer = SignalOptimizer(
                fallback_offsets=fallback_offsets_list,
                **{k: v for k, v in decay_kw.items() if k in (
                    "T", "lambda_base", "sigma", "multiplier", "min_mu", "max_mu", "N_windows",
                    "ema_alpha_mu", "ema_alpha_lambda", "max_factor", "min_offsets",
                    "default_mu", "threshold", "conservative_scaling_enabled",
                )},
            )
            optimizer.set_historical_offsets(offsets)
            mu_val = optimizer.compute_dynamic_mu()
            out = optimizer.dynamic_decay(
                float(elapsed_time_min), float(cum_change), return_meta=return_meta
            )
            if return_meta and isinstance(out, tuple):
                extra_factor, decay_meta = out
                z_eff_low = float(normalized_score) * decay_meta.get("decay_low", 1.0)
                z_eff_high = float(normalized_score) * decay_meta.get("decay_high", 1.0)
                ema_mu = optimizer.ema_mu or optimizer.default_mu
                mu_ci = decay_meta.get("mu_ci", (ema_mu * 0.9, ema_mu * 1.1))
                ci_width = (mu_ci[1] - mu_ci[0]) / max(ema_mu, 1e-9) if ema_mu else 0
                if optimizer.conservative_scaling_enabled and ci_width > optimizer.threshold:
                    extra_factor *= 0.8
                    binance_meta["conservative_scaling"] = True
                binance_meta["mu_ci"] = mu_ci
                binance_meta["z_eff_ci"] = (z_eff_low, z_eff_high)
            else:
                extra_factor = float(out)
            _consecutive_failures = 0
            _binance_failed_since = None
        except Exception as e:
            _consecutive_failures += 1
            if _consecutive_failures > 3:
                logger.error("Consecutive failures > 3 (score_cex decay)")
                sys.stderr.write("[cex] ERROR: Consecutive failures > 3\n")
                if use_binance_now:
                    _binance_failed_since = time.time()
            extra_factor = 1.0
            z_eff = 0.0
            if return_meta:
                meta = dict(stats or {})
                meta.update({
                    "z_score": float(normalized_score),
                    "extra_factor": 1.0,
                    "z_eff": 0.0,
                    "mu": None,
                    "offsets_n": 0,
                    "error": str(e) or type(e).__name__,
                })
                return CexScoreResult(score=0.0, meta=meta)
            return 0.0
    z_eff = float(normalized_score) * float(extra_factor)

    if return_meta:
        meta = dict(stats or {})
        meta.update({
            "raw_score": float(raw_score),
            "raw_n_signals": int(n_signals),
            "raw_time_window_sec": float(time_window_sec),
            "raw_decay_rate": float(decay_rate),
            "raw_ewma_alpha": float(ewma_alpha),
            "raw_signal_span_sec": float(signal_span_sec),
            "raw_use_volatility_filter": bool(use_volatility_filter),
            "raw_use_dynamic_weights": bool(use_dynamic_weights),
            "raw_normalizer_min_samples": int(getattr(normalizer, "min_samples", 50)),
            "raw_mid_venue_used": mid_venue_val,
            "z_score": float(normalized_score),
            "z_extra_factor": float(extra_factor),
            "z_eff": float(z_eff),
            "z_mu": float(mu_val) if mu_val is not None else None,
            "z_offsets_n": int(offsets_n),
        })
        meta.update(binance_meta)
        logger.info(
            "[cex] timing path=%s load_signal_s=%.3f normalize_s=%.3f save_s=%.3f total_s=%.3f n_signals=%s",
            p, t2 - t1, t3 - t2, t4 - t3, t4 - t0, n_signals,
        )
        return CexScoreResult(score=float(normalized_score), meta=meta)

    logger.info(
        "[cex] timing path=%s load_signal_s=%.3f normalize_s=%.3f save_s=%.3f total_s=%.3f",
        p, t2 - t1, t3 - t2, t4 - t3, t4 - t0,
    )
    return float(normalized_score)


def ensure_cex_warmup(
    csv_path: Path,
    *,
    venues: Optional[list[str]] = None,
    weights: Optional[list[float]] = None,
    weights_by_venue: Optional[dict[str, float]] = None,
    lookback_seconds: int = 7200,
    normalizer_cache_dir: Optional[Path] = None,
    symbol: str = "btc",
) -> bool:
    """
    确保 CEX normalizer 已补齐近 lookback_seconds 的历史。
    返回 True 表示已满足最小样本且覆盖窗口；False 表示仍不足。
    """
    p = Path(csv_path)
    if not p.exists():
        p = _auto_detect_cex_slice(symbol)
        if not p.exists():
            return False

    if weights_by_venue is not None:
        venues2 = list(weights_by_venue.keys())
        weights2 = [float(weights_by_venue[v]) for v in venues2]
    else:
        venues2 = list(venues or ["binance_spot", "okx_spot", "okx_swap", "bybit_spot", "bybit_linear"])
        weights2 = list(weights or [1.0, 1.0, 2.0, 2.0, 3.0])
    if len(venues2) != len(weights2):
        return False

    cache_dir = Path(normalizer_cache_dir) if normalizer_cache_dir else Path(".cache")
    cache_file = cache_dir / f"cex_normalizer_{symbol}.pkl"
    cache_key = str(cache_file.resolve())

    logger.info("[cex] warmup: start cache=%s", cache_key)
    normalizer = _NORMALIZER_CACHE.get(cache_key)
    if normalizer is None:
        normalizer = AdaptiveScoreNormalizer.load_state(cache_file)
        if normalizer is None:
            normalizer = AdaptiveScoreNormalizer(
                lookback_seconds=int(lookback_seconds),
                min_samples=50,
            )
        _NORMALIZER_CACHE[cache_key] = normalizer

    now_ts = time.time()
    if _needs_warmup(normalizer, now_ts=now_ts):
        # 强制从头补齐（避免历史不足导致永远不达标）
        try:
            normalizer.history = deque()
        except Exception:
            pass
        logger.info("[cex] warmup: reset history for full backfill")
        try:
            prev = _prev_cex_slice_path(p)
            if prev is not None:
                logger.info("[cex] warmup: try prev slice %s", prev)
                _warmup_normalizer_from_csv(
                    csv_path=prev,
                    normalizer=normalizer,
                    venues=venues2,
                    weights=weights2,
                    lookback_seconds=int(lookback_seconds),
                    now_ts=float(now_ts),
                )
            _warmup_normalizer_from_csv(
                csv_path=p,
                normalizer=normalizer,
                venues=venues2,
                weights=weights2,
                lookback_seconds=int(lookback_seconds),
                now_ts=float(now_ts),
            )
        except Exception as e:
            logger.warning("[cex] warmup: 失败 %s:%s", type(e).__name__, e)

    try:
        normalizer.save_state(cache_file)
    except Exception:
        pass

    ok = not _needs_warmup(normalizer, now_ts=time.time())
    try:
        n_samples = len(normalizer.history)
    except Exception:
        n_samples = 0
    try:
        oldest = _normalize_ts(float(normalizer.history[0][0])) if normalizer.history else 0.0
        newest = _normalize_ts(float(normalizer.history[-1][0])) if normalizer.history else 0.0
        span_s = float(newest - oldest) if (oldest and newest) else 0.0
    except Exception:
        oldest, newest, span_s = 0.0, 0.0, 0.0
    now_s = _normalize_ts(time.time())
    logger.info(
        "[cex] warmup: done ok=%s n_samples=%d span_s=%.1f now=%.1f oldest=%.1f newest=%.1f",
        bool(ok), int(n_samples), span_s, now_s, oldest, newest,
    )
    return bool(ok)


def _auto_detect_cex_slice(symbol: str) -> Path:
    """
    自动检测当前UTC时间对应的12小时CEX数据分片。
    
    Args:
        symbol: 交易品种（btc/eth等）
    
    Returns:
        当前分片的文件路径
    """
    # 尝试从data_sources导入
    try:
        from real_market.trade.data_sources.cex_hot_csv import CexHotCsvSource, DEFAULT_HOT_DIR
        source = CexHotCsvSource(hot_dir=DEFAULT_HOT_DIR, symbol=symbol)
        slice_info = source.pick_current_slice()
        return slice_info.path
    except Exception:
        pass
    
    # 降级方案：手动构造路径
    now_utc = datetime.now(timezone.utc)
    day = now_utc.strftime("%Y%m%d")
    label = "00-12" if now_utc.hour < 12 else "12-24"
    
    # 尝试几个可能的hot_dir位置
    possible_dirs = [
        Path("/Users/jimmysun/Desktop/workspace/polymarket/real_hot"),
        Path("real_hot"),
        Path("../real_hot"),
    ]
    
    for hot_dir in possible_dirs:
        p = hot_dir / f"cex_{symbol}_{day}_{label}.csv"
        if p.exists():
            return p
    
    # 返回默认路径（可能不存在）
    return Path(f"real_hot/cex_{symbol}_{day}_{label}.csv")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Optional for production: logger.addHandler(logging.FileHandler("cex.log"))

