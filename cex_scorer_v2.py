from __future__ import annotationsimport json
import math
import os
import pickle
import time
import urllib.parse
import urllib.request
import statistics
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optionaltry:
    from scipy.stats import spearmanr as _spearmanr
    from scipy.stats import trim_mean as _trim_mean
except ImportError:
    _spearmanr = None  # type: ignore[assignment]
    _trim_mean = None  # type: ignore[assignment]_NORMALIZER_CACHE: dict[str, AdaptiveScoreNormalizer] = {}
_LOGGED_NORMALIZER: set[str] = set()
_LOGGED_VOL_FALLBACK: set[int] = set()  # id(normalizer) 已打过 vol fallback 日志，防刷屏
_LOGGED_CSV: set[str] = set()
_LOGGED_MID_EMPTY: set[str] = set()  # cache_key 已打过 current_mid 为空且 use_vol_norm 的日志，防刷屏
_WARMUP_DONE: set[str] = set()
_CHAINLINK_CACHE: dict[str, dict[str, Any]] = {}
_EWMA_STATE: dict[str, float] = {}  # cache_key -> ewma_raw
_EWMA_LOCKS: dict[str, threading.Lock] = {}  # cache_key -> Lock，多线程下读写 EWMA 与 pkl 时加锁def _ewma_lock(cache_key: str) -> threading.Lock:
    """按 cache_key 懒加载 Lock，用于 EWMA 读写与 pkl 的线程安全。"""
    if cache_key not in _EWMA_LOCKS:
        _EWMA_LOCKS[cache_key] = threading.Lock()
    return _EWMA_LOCKS[cache_key]def _load_ewma_state(ewma_file: Path) -> Optional[float]:
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
        return Nonedef _save_ewma_state(ewma_file: Path, ewma_raw: float) -> None:
    """将 EWMA 状态写入 pkl。"""
    try:
        ewma_file.parent.mkdir(parents=True, exist_ok=True)
        state = {"ewma_raw": float(ewma_raw), "saved_at": time.time()}
        with ewma_file.open("wb") as f:
            pickle.dump(state, f)
    except Exception:
        pass  # 保存失败不影响返回结果def _read_csv_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8", errors="replace") as f:
        line = f.readline().strip("\n")
    import csvreturn next(csv.reader([line]))def _read_tail_rows(path: Path, *, tail_bytes: int) -> list[dict[str, str]]:
    import csvheader = _read_csv_header(path)
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
return rowsdef _iter_complete_signals_from_rows(
    rows: list[dict[str, str]],
    *,
    venues: list[str],
    weights: list[float],
    min_abs_score: float = 0.0,
) -> list[tuple[float, float]]:
    need = set(venues)
    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        venue = (row.get("venue") or "").strip()
        if venue not in need:
            continue
        if (row.get("err") or "").strip():
            continue
        sid_s = (row.get("sample_id") or "").strip()
        t_s = (row.get("t_sample_unix") or "").strip()
        imb_s = (row.get("imb") or "").strip()
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
    return outdef _mid_series_from_rows(
    rows: list[dict[str, str]],
    mid_venue: str,
) -> list[tuple[float, float]]:
    """
    从 rows 中提取指定 venue 的 (t, mid) 序列，按 sample_id 去重（每 sample 取一条）。
    用于波动率计算。
    """
    out: list[tuple[float, float]] = []
    seen_sid: set[int] = set()
    for row in rows:
        venue = (row.get("venue") or "").strip()
        if venue != mid_venue:
            continue
        if (row.get("err") or "").strip():
            continue
        sid_s = (row.get("sample_id") or "").strip()
        t_s = (row.get("t_sample_unix") or "").strip()
        mid_s = (row.get("mid") or "").strip()
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
    return outdef apply_volatility_filter(
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
    """
    当当前窗口波动率高于历史阈值时，对 raw_score 做衰减或置零。
    - 波动率：std(mid) 或 ATR(mid)；阈值 = 历史滚动 window 波动率中位数 * multiplier。
    - 若 current_vol > threshold：extreme 时返回 0，否则 raw_score * decay_factor。
    """
    mid_series = _mid_series_from_rows(rows, mid_venue)
    if len(mid_series) < 2:
        return raw_score
    now_ts = mid_series[-1][0] if mid_series else 0.0
    cutoff_hist = now_ts - hist_window_sec
    # 只用历史窗口内的点
    in_hist = [(t, m) for t, m in mid_series if t >= cutoff_hist]
    if len(in_hist) < 2:
        return raw_score
    mids = [m for _, m in in_hist]
    # 当前窗口：最近 window_sec
    cutoff_cur = now_ts - window_sec
    cur_mids = [m for t, m in in_hist if t >= cutoff_cur]
    if len(cur_mids) < 2:
        return raw_score
    if use_atr:
        # ATR on mid: TR = |mid - prev_mid|, ATR = EMA(TR)
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
        # 历史 ATR：滚动窗口内 EMA(TR)，取中位数
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
        # 历史：滚动 window_sec 的 std 的中位数
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
    return raw_scoredef _venue_series_from_rows(
    rows: list[dict[str, str]],
    venues: list[str],
) -> dict[str, list[tuple[float, float, float]]]:
    """
    从 rows 中按 venue 提取 (t, imb, mid) 序列，每 venue 一个列表，按 t 升序。
    用于动态权重计算（需再补 delta_mid：backtest 用未来，live 用 delayed）。
    """
    out: dict[str, list[tuple[float, float, float]]] = {v: [] for v in venues}
    for row in rows:
        venue = (row.get("venue") or "").strip()
        if venue not in out:
            continue
        if (row.get("err") or "").strip():
            continue
        t_s = (row.get("t_sample_unix") or "").strip()
        imb_s = (row.get("imb") or "").strip()
        mid_s = (row.get("mid") or "").strip()
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
    return outdef compute_dynamic_weights(
    venue_data: dict[str, list[tuple[float, float, Optional[float]]]],
    *,
    window_sec: float = 60.0,
    k: float = 0.7,
    min_samples: int = 10,
    use_spearman: bool = True,
    base_weights: Optional[dict[str, float]] = None,
) -> dict[str, float]:
    """
    根据各 venue 最近 window_sec 内 (imb, delta_mid) 的置信度（Spearman 或 sign-match）
    计算权重：weight_v = base_v * (1 + k * (conf_v - median_conf))，clip [0.5, 1.5]。
    venue_data[v] = [(t, imb, delta_mid), ...]，delta_mid 为 None 的样本不参与 corr。
    若所有 venue 置信度都 NaN/不足，fallback 为 equal weights 或 base_weights。
    """
    need = list(venue_data.keys())
    if not need:
        return {}
    confidences: dict[str, float] = {}
    for venue in need:
        series = venue_data.get(venue) or []
        # 取最近 window_sec 内且 delta_mid 非 None 的点
        cutoff = 0.0
        if series:
            cutoff = series[-1][0] - window_sec
        pairs = [(imb, dm) for t, imb, dm in series if t >= cutoff and dm is not None]
        if len(pairs) < min_samples:
            confidences[venue] = 0.5  # 默认中性
            continue
        imbs = [p[0] for p in pairs]
        deltas = [p[1] for p in pairs]
        if use_spearman and _spearmanr is not None:
            try:
                corr, _ = _spearmanr(imbs, deltas)
                conf = float(corr) if not (corr != corr) else 0.0  # NaN -> 0
            except Exception:
                conf = 0.0
        else:
            # Sign 一致率
            match = sum(1 for a, b in zip(imbs, deltas) if (a > 0 and b > 0) or (a < 0 and b < 0) or (a == 0 and b == 0))
            conf = match / len(pairs) if pairs else 0.0
        confidences[venue] = conf
    vals = [confidences[v] for v in need]
    median_conf = statistics.median(vals) if vals else 0.0
    # Fallback: 若全部为默认中性或不可用，用 equal 或 base_weights
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
    return weightsdef compute_weighted_score_at_t(
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
    return total / total_wdef load_recent_signals(
    path: Path,
    venues: list[str],
    weights: list[float],
    *,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    tail_bytes: int = 16384,
) -> list[tuple[float, float]]:
    """
    从CSV文件加载最近时间窗口内的所有完整信号。Args:
    path: CSV文件路径
    venues: 交易所列表
    weights: 对应的权重列表
    time_window_sec: 滚动窗口秒数，默认30.0
    min_abs_score: 弱信号过滤阈值，默认0.1
    tail_bytes: 读取CSV尾部的字节数，默认16384

Returns:
    按时间升序排序的 (timestamp, score) 列表
"""
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
return sorted(recent, key=lambda x: x[0])  # 按 t 升序def load_recent_signals_with_dynamic_weights(
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
    """
    使用动态 venue 权重加载最近时间窗口内的信号：每 t 用历史 (imb, delta_mid) 算权重，再加权聚合。
    Live 下 delta_mid 用 delayed 近似（t+horizon 用当前最新 mid）。
    """
    rows = _read_tail_rows(path, tail_bytes=tail_bytes)
    venue_series = _venue_series_from_rows(rows, venues)
    need = set(venues)
    # now_ts = 数据中最大 t
    now_ts = 0.0
    for v in venues:
        for t, _, _ in venue_series.get(v) or []:
            now_ts = max(now_ts, float(t))
    if now_ts <= 0:
        now_ts = time.time()
    # 每个 venue 的 latest mid（用于 delayed delta_mid）
    latest_mid: dict[str, float] = {}
    for v in venues:
        ser = venue_series.get(v) or []
        if ser:
            latest_mid[v] = ser[-1][2]  # mid at last t
        else:
            latest_mid[v] = 0.0
    # 构建 (t, imb, delta_mid)，live 下 delta_mid = latest_mid - mid 当 t+horizon <= now_ts
    venue_data: dict[str, list[tuple[float, float, Optional[float]]]] = {}
    for v in venues:
        ser = venue_series.get(v) or []
        out: list[tuple[float, float, Optional[float]]] = []
        for t, imb, mid in ser:
            if t + dw_horizon_sec <= now_ts:
                delta_mid = latest_mid[v] - mid
            else:
                delta_mid = None
            out.append((t, imb, delta_mid))
        venue_data[v] = out
    # 完整 sample (t, {venue: imb})
    groups: dict[int, dict[str, Any]] = {}
    for row in rows:
        venue = (row.get("venue") or "").strip()
        if venue not in need or (row.get("err") or "").strip():
            continue
        sid_s = (row.get("sample_id") or "").strip()
        t_s = (row.get("t_sample_unix") or "").strip()
        imb_s = (row.get("imb") or "").strip()
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
        weights_map = compute_dynamic_weights(
            venue_data_at_t,
            window_sec=dw_window_sec,
            k=dw_k,
            min_samples=dw_min_samples,
            use_spearman=dw_use_spearman,
            base_weights=base_weights,
        )
        score_t = compute_weighted_score_at_t(imbs, weights_map)
        if abs(score_t) >= float(min_abs_score):
            out_signals.append((t, score_t))
    return sorted(out_signals, key=lambda x: x[0])def compute_raw_score_at_time(
    rows: list[dict[str, str]],
    as_of_ts: float,
    venues: list[str],
    weights: list[float],
    *,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    decay_rate: float = 0.15,
    ewma_alpha: float = 0.2,
    ewma_state: Optional[float] = None,
) -> tuple[float, Optional[float]]:
    """
    回测用：在截至 as_of_ts 的数据上，用与 score_cex 相同的滚动窗口 + 时间衰减 + EWMA 逻辑计算 raw_score。
    调用方需保证 rows 仅包含 t_sample_unix <= as_of_ts 的 row，并按时间顺序依次调用并传入上一时刻的 ewma_state。Returns:
    (raw_score, next_ewma_state)
"""
all_signals = _iter_complete_signals_from_rows(
    rows, venues=venues, weights=weights, min_abs_score=min_abs_score
)
cutoff_ts = as_of_ts - float(time_window_sec)
recent = [
    (t, s)
    for t, s in all_signals
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
return (float(raw_score), float(raw_score))def compute_raw_score_from_signals(
    signals: list[tuple[float, float]],
    as_of_ts: float,
    *,
    time_window_sec: float = 30.0,
    min_abs_score: float = 0.1,
    decay_rate: float = 0.15,
    ewma_alpha: float = 0.2,
    ewma_state: Optional[float] = None,
) -> tuple[float, Optional[float]]:
    """
    回测用（高性能）：对已计算好的 (t, score) 信号列表做时间窗口过滤 + 时间衰减 + EWMA。
    调用方需保证 signals 中 t <= as_of_ts，且按时间顺序依次调用并传入上一时刻的 ewma_state。Returns:
    (raw_score, next_ewma_state)
"""
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
return (float(raw_score), float(raw_score))_TS_WARNED: set[float] = set()  # 异常时间戳只告警一次def _normalize_ts(ts: float) -> float:
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
            print(f"[cex] _normalize_ts: 异常时间戳范围 ts={ts} -> {t}s (可能数据源错误)", flush=True)
    return float(t)def _needs_warmup(normalizer: AdaptiveScoreNormalizer, *, now_ts: float) -> bool:
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
    return Falsedef _prev_cex_slice_path(p: Path) -> Optional[Path]:
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
        return Nonedef _warmup_normalizer_from_csv(
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
    Warmup normalizer from CSV file.Args:
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
    print("[cex] warmup: tail 不够覆盖 2h，改用全量扫描", flush=True)
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
print(f"[cex] warmup: 已补齐样本 {used} 条 (lookback_s={int(lookback_seconds)}, cutoff={cutoff:.0f}, warmup_end={warmup_end:.0f}, last_ts={last_ts_str})", flush=True)def _warmup_normalizer_recursive(
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
    先尝试当前文件，如果不够就往前找上一个文件，直到满足 warmup 要求或达到最大文件数。Args:
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
    print(f"[cex] warmup: 当前文件失败 {type(e).__name__}: {e}", flush=True)

# 检查是否还需要更多数据
files_checked = 1
prev_csv = _prev_cex_slice_path(current_csv)
while _needs_warmup(normalizer, now_ts=now_ts) and files_checked < max_files:
    if prev_csv is None or not prev_csv.exists():
        break
    try:
        print(f"[cex] warmup: 数据仍不足，继续从上一个文件加载: {prev_csv.name}", flush=True)
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
        print(f"[cex] warmup: 上一个文件失败 {type(e).__name__}: {e}", flush=True)
        break

if _needs_warmup(normalizer, now_ts=now_ts):
    print(f"[cex] warmup: 警告：已检查 {files_checked} 个文件，normalizer history仍不足，z_eff 可能为0直到样本补齐", flush=True)
else:
    print(f"[cex] warmup: 完成，已从 {files_checked} 个文件加载数据", flush=True)@dataclass(frozen=True)
class CexScoreResult:
    """
    对外依旧只需要 float score，但为了 runner/日志调试，这里保留可选 meta。
    """score: float
meta: dict[str, Any]class AdaptiveScoreNormalizer:
    """
    基于历史分布的 Z-score 标准化器（trimmed mean + MAD + 可选波动率归一化 + soft clip）。动态维护过去 N 秒的 score 历史，用截尾均值和 MAD 计算稳健 Z-score，
可选按相对波动率缩放，并用 tanh 做软截断。
假设每 symbol 单线程调用（同一 cache_key 串行）。

Args:
    lookback_seconds: 回溯窗口（秒），默认 7200（2 小时）
    min_samples: 最小样本数，低于此值返回原始 score，默认 50
    winsorize_prop: 截尾比例（两端各去掉该比例，用于 trimmed mean），默认 0.01
    use_vol_norm: 是否按相对波动率归一化
    vol_window_sec: 波动率历史窗口（秒）
    clip_limit: soft clip 上下限（tanh 饱和）

注意：内部使用 deque，非线程安全；假设每 symbol 单线程调用，多线程时需在调用方加锁。
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
    self.history: deque[tuple[float, float]] = deque()  # (ts, raw_score)
    self.vol_history: deque[tuple[float, float]] = deque()  # (ts, mid_price) for vol

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
    """
    标准化 score 为 Z-score（trimmed mean + MAD；可选相对波动率缩放；soft clip）。

    Returns:
        (z_score, stats_dict)，含 mean, mad/scale, n_samples, is_normalized, vol_factor 等。
    """
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
    # Trimmed mean（截尾均值，两端各去掉 winsorize_prop）
    if _trim_mean is not None and 0 < self.winsorize_prop < 0.5:
        mean = float(_trim_mean(scores, self.winsorize_prop))
    else:
        mean = sum(scores) / len(scores)
    deviations = [abs(s - mean) for s in scores]
    mad = statistics.median(deviations) * 1.4826  # robust scale (MAD -> approx std)

    if mad < 1e-9:
        z_score = 0.0
    else:
        z_score = (float(score) - mean) / mad

    # 相对波动率归一化：stdev(mids)/median(mids)，尺度无关；mids 异常时 fallback 到 vol_factor=1.0
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
            print("[cex] normalizer vol_norm: mids 无波动或 med≈0，使用 vol_factor=1.0", flush=True)

    # Soft clip（tanh 平滑饱和）
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
    with open(path, "wb") as f:
        pickle.dump(state, f)

@classmethod
def load_state(cls, path: Path) -> Optional["AdaptiveScoreNormalizer"]:
    """从文件恢复 normalizer；旧 pkl 缺省字段使用默认值。"""
    if not path.exists():
        return None
    try:
        with open(path, "rb") as f:
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
        print(f"[cex] normalizer load_state 失败: {e!r}", flush=True)
        return Nonedef _chainlink_cache_path(cache_dir: Path, feed_id: str, time_range: str) -> Path:
    safe_feed = feed_id.replace("0x", "")[-12:]
    return cache_dir / f"chainlink_stream_{safe_feed}_{time_range}.json"def _load_chainlink_cache(path: Path, *, max_age_s: float) -> Optional[list[dict[str, Any]]]:
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
    return Nonedef _save_chainlink_cache(path: Path, nodes: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump({"fetched_at": time.time(), "nodes": nodes}, f)
    except Exception:
        returndef _extract_chainlink_nodes(payload: dict[str, Any]) -> list[dict[str, Any]]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    for _, val in data.items():
        if isinstance(val, dict):
            nodes = val.get("nodes")
            if isinstance(nodes, list):
                return nodes
    return []def _fetch_chainlink_history(
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
    return []def _parse_chainlink_nodes(nodes: list[dict[str, Any]]) -> list[tuple[float, float]]:
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
    return outdef _recent_chainlink_offsets(nodes: list[dict[str, Any]], *, n_windows: int) -> list[float]:
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
    return offsets[-int(n_windows) :]class SignalOptimizer:
    def __init__(
        self,
        *,
        T: float = 15.0,
        lambda_base: float = 0.2,
        sigma: float = 5.0,
        multiplier: float = 1.0,
        min_mu: float = 8.0,
        max_mu: float = 60.0,
        N_windows: int = 20,
    ) -> None:
        self.T = float(T)
        self.lambda_base = float(lambda_base)
        self.sigma = float(sigma)
        self.multiplier = float(multiplier)
        self.min_mu = float(min_mu)
        self.max_mu = float(max_mu)
        self.N_windows = int(N_windows)
        self.historical_offsets: list[float] = []def set_historical_offsets(self, offsets: list[float]) -> None:
    trimmed = [abs(float(x)) for x in offsets]
    if self.N_windows > 0:
        trimmed = trimmed[-int(self.N_windows) :]
    self.historical_offsets = trimmed

def compute_dynamic_mu(self) -> float:
    if not self.historical_offsets:
        return 20.0
    base_mu = float(statistics.median(self.historical_offsets))
    final_mu = max(self.min_mu, min(self.max_mu, self.multiplier * base_mu))
    return float(final_mu)

def dynamic_decay(self, elapsed_time: float, cum_change: float) -> float:
    abs_delta = abs(float(cum_change))
    mu = self.compute_dynamic_mu()
    try:
        g_delta = 1.0 / (1.0 + math.exp(-(abs_delta - mu) / float(self.sigma)))
    except OverflowError:
        g_delta = 1.0 if abs_delta > mu else 0.0
    effective_lambda = float(self.lambda_base) * float(g_delta)
    return math.exp(-effective_lambda * float(elapsed_time))def score_cex(
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
) -> float | CexScoreResult:
    """
    CEX 打分器：输入 live CSV + 参数（权重等）→ 输出单一 float score。说明：
- raw_score 计算采用滚动窗口聚合：从"最新一个 sample"改为"最近时间窗口（15-30秒）内所有完整 sample 的聚合"。
- 聚合方式：时间衰减加权平均（强调最新 sample）+ EWMA 平滑（连续调用时进一步平滑）。
- 通过 min_abs_score 过滤弱信号，减少噪声。
- 最终通过 Z-score 标准化和动态衰减得到 z_eff。

Args:
    csv_path: CEX数据CSV文件路径
    venues: 交易所列表
    weights: 对应的权重列表
    weights_by_venue: venue -> weight的字典（优先级高于venues/weights）
    tail_bytes: 读取CSV尾部的字节数，默认16384（足够覆盖30-60秒历史）
    use_normalization: 是否使用Z-score标准化，默认True（推荐）
    lookback_seconds: 标准化回溯窗口（秒），默认7200（2小时）
    normalizer_cache_dir: cache目录，默认为workspace/.cache
    symbol: 交易品种（用于cache文件命名），默认"btc"
    return_meta: 返回 CexScoreResult（含 z_eff/extra_factor 及聚合统计）
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
    time_window_sec: 滚动窗口秒数，默认30.0（15-30秒适合高频预测）
    min_abs_score: 弱信号过滤阈值，默认0.1（0.1-0.3减少噪声10-20%）
    decay_rate: 时间衰减率（每秒），默认0.15（半衰期≈4-5s，可调0.1-0.2）
    ewma_alpha: EWMA 衰减系数，默认0.2（半衰期≈3-4s，可调0.1-0.3）
    use_volatility_filter: 是否启用波动率过滤（高波动时衰减/置零 raw_score）
    vol_window_sec: 波动率滚动窗口（秒）
    vol_hist_window_sec: 历史波动率窗口（秒）
    vol_multiplier: 阈值 = 历史波动率中位数 * multiplier
    vol_decay_factor: 高波动时 raw_score *= decay_factor（若未置零）
    vol_use_atr: 使用 ATR 而非 std 计算波动率
    vol_extreme_zero: 高波动时直接置零（否则衰减）
    mid_venue: 用于波动率的 mid 来源 venue（默认首 venue）
    use_dynamic_weights: 是否使用动态 venue 权重（基于近期 imb vs delta_mid 置信度）
    dw_window_sec: 动态权重窗口（秒）
    dw_horizon_sec: delta_mid 预测 horizon（秒）
    dw_k: 权重调整敏感度
    dw_min_samples: 计算 corr 最少样本数
    dw_use_spearman: 用 Spearman（否则 sign-match）
    dw_base_weights: 各 venue 基础权重（可选）

Returns:
    标准化后的score（如果use_normalization=True），否则返回原始score。
    当 return_meta=True 时，返回 CexScoreResult，包含：
    - z_eff: 最终有效分数
    - z_score: 标准化后的分数
    - extra_factor: 动态衰减因子
    - n_signals: 聚合使用的信号数量
    - time_window_sec: 时间窗口大小
    - decay_rate: 时间衰减率
    - ewma_alpha: EWMA 系数
    - signal_span_sec: 信号时间跨度
"""
t0 = time.perf_counter()
p = Path(csv_path)

# 如果指定路径不存在，尝试从real_hot/自动查找当前12小时分片
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

# 提前计算 cache_key 与 ewma 持久化路径（与 normalizer 同目录）
cache_dir = Path(normalizer_cache_dir) if normalizer_cache_dir else Path(".cache")
cache_file = cache_dir / f"cex_normalizer_{symbol}.pkl"
ewma_file = cache_dir / f"cex_ewma_{symbol}.pkl"
cache_key = str(cache_file.resolve())

# 使用滚动窗口聚合：加载最近时间窗口内的所有完整信号（固定权重或动态权重）
t1 = time.perf_counter()
if use_dynamic_weights:
    dw_tail_bytes = max(tail_bytes, 65536)  # 至少 64KB 以覆盖 ~75s
    recent_signals = load_recent_signals_with_dynamic_weights(
        p,
        venues=venues2,
        time_window_sec=float(time_window_sec),
        min_abs_score=float(min_abs_score),
        tail_bytes=int(dw_tail_bytes),
        dw_window_sec=float(dw_window_sec),
        dw_horizon_sec=float(dw_horizon_sec),
        dw_k=float(dw_k),
        dw_min_samples=int(dw_min_samples),
        dw_use_spearman=bool(dw_use_spearman),
        base_weights=dw_base_weights,
    )
else:
    recent_signals = load_recent_signals(
        p,
        venues=venues2,
        weights=weights2,
        time_window_sec=float(time_window_sec),
        min_abs_score=float(min_abs_score),
        tail_bytes=int(tail_bytes),
    )
t2 = time.perf_counter()

n_signals = len(recent_signals)
if not recent_signals:
    print(
        f"[cex] timing path={p} load_signals_s={t2 - t1:.3f} total_s={t2 - t0:.3f} n_signals=0 time_window_sec={time_window_sec:.1f}",
        flush=True,
    )
    return 0.0

# 时间基准：统一用信号最大 t（与数据时间一致，避免 CSV 写入延迟导致 age_sec 异常）
now_ts = _normalize_ts(recent_signals[-1][0])
# 时间衰减加权平均
weighted_sum = 0.0
total_weight = 0.0
decay_rate_val = float(decay_rate)

for t, s in recent_signals:  # 已升序，最新在后
    t_norm = _normalize_ts(float(t))
    age_sec = now_ts - t_norm
    weight = math.exp(-decay_rate_val * age_sec)  # 最新 weight ≈1，老的衰减
    weighted_sum += weight * float(s)
    total_weight += weight

current_raw = weighted_sum / total_weight if total_weight > 0 else 0.0

# EWMA 平滑（跨调用连续性，按 cache_key 区分；持久化到 pkl，多进程/多实例安全；同进程多线程加锁）
global _EWMA_STATE
lock = _ewma_lock(cache_key)
with lock:
    if cache_key not in _EWMA_STATE:
        loaded = _load_ewma_state(ewma_file)
        _EWMA_STATE[cache_key] = float(loaded) if loaded is not None else current_raw
    ewma_raw = _EWMA_STATE[cache_key]
    ewma_raw = float(ewma_alpha) * current_raw + (1.0 - float(ewma_alpha)) * ewma_raw
    _EWMA_STATE[cache_key] = ewma_raw
    _save_ewma_state(ewma_file, ewma_raw)
raw_score = _EWMA_STATE[cache_key]
if os.environ.get("CEX_DEBUG_RAW_SCORE") == "1":
    print(f"[raw_score] {raw_score:.6f}", flush=True)

# 需要 mid/vol 时只读一次 tail，供波动率过滤与 normalizer current_mid 复用
vol_rows: list[dict[str, str]] = []
mid_series: list[tuple[float, float]] = []
mid_venue_val = mid_venue if mid_venue is not None else (venues2[0] if venues2 else "binance_spot")
need_vol_or_mid = use_volatility_filter or use_normalization
if need_vol_or_mid:
    vol_tail_bytes = max(tail_bytes, 256 * 1024)  # 至少 256KB 以覆盖 hist_window
    vol_rows = _read_tail_rows(p, tail_bytes=vol_tail_bytes)
    mid_series = _mid_series_from_rows(vol_rows, mid_venue_val) if vol_rows else []

# 波动率过滤：高波动时衰减或置零 raw_score（复用上面 vol_rows）
if use_volatility_filter:
    raw_score = apply_volatility_filter(
        vol_rows,
        raw_score,
        mid_venue_val,
        window_sec=vol_window_sec,
        hist_window_sec=vol_hist_window_sec,
        multiplier=vol_multiplier,
        decay_factor=vol_decay_factor,
        use_atr=vol_use_atr,
        vol_extreme_zero=vol_extreme_zero,
    )

# 如果不使用标准化，直接返回原始score
if not use_normalization:
    if return_meta:
        return CexScoreResult(
            score=float(raw_score),
            meta={"z_score": float(raw_score), "extra_factor": 1.0, "z_eff": float(raw_score)},
        )
    return float(raw_score)

# 使用标准化（cache_dir、cache_file、cache_key 已在前面计算）
csv_key = str(p.resolve()) if p.exists() else str(p)
if csv_key not in _LOGGED_CSV:
    print(
        f"[cex] 使用CSV={csv_key} tail_bytes={int(tail_bytes)} lookback_s={int(lookback_seconds)} normalize={bool(use_normalization)}",
        flush=True,
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
        normalizer = AdaptiveScoreNormalizer(
            lookback_seconds=int(lookback_seconds),
            min_samples=50,
        )
        created_new = True
    _NORMALIZER_CACHE[cache_key] = normalizer
if cache_key not in _LOGGED_NORMALIZER:
    try:
        n_samples = len(normalizer.history)
    except Exception:
        n_samples = 0
    print(
        f"[cex] normalizer_cache={cache_key} reused_mem={bool(reused_from_memory)} loaded_disk={bool(loaded_from_disk)} "
        f"created_new={bool(created_new)} n_samples={int(n_samples)} lookback_s={int(lookback_seconds)} "
        f"min_samples={int(normalizer.min_samples)}",
        flush=True,
    )
    if created_new:
        print("[cex] normalizer 初始化为空，样本不足时将暂停交易（不会使用 raw_score）", flush=True)
    _LOGGED_NORMALIZER.add(cache_key)

# 当前时间戳（与时间衰减一致，用信号最大 t）
timestamp = now_ts

# 确保近 2 小时缓存已补齐（永远不使用 raw）
warmup_key = f"{cache_key}:{int(lookback_seconds)}"
if warmup_key not in _WARMUP_DONE and _needs_warmup(normalizer, now_ts=float(timestamp)):
    print("[cex] warmup: 检测到缓存不足，开始补齐近 2 小时数据 ...", flush=True)
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
        print(f"[cex] warmup: 失败 {type(e).__name__}:{e}", flush=True)
    _WARMUP_DONE.add(warmup_key)

# 先标准化再更新（避免 look-ahead）；若 normalizer 启用 vol 归一化则传入当前 mid（复用 mid_series）
current_mid: Optional[float] = None
if normalizer.use_vol_norm and mid_series:
    current_mid = mid_series[-1][1]
if normalizer.use_vol_norm and current_mid is None and cache_key not in _LOGGED_MID_EMPTY:
    _LOGGED_MID_EMPTY.add(cache_key)
    print("[cex] normalizer use_vol_norm=True 但 current_mid 为空，vol_history 未更新", flush=True)
normalized_score, stats = normalizer.normalize(raw_score, timestamp)
normalizer.update(raw_score, timestamp, mid_price=current_mid)
t3 = time.perf_counter()

# 记录聚合统计信息
if n_signals > 0:
    oldest_signal_ts = _normalize_ts(recent_signals[0][0])
    newest_signal_ts = _normalize_ts(recent_signals[-1][0])
    signal_span_sec = newest_signal_ts - oldest_signal_ts if newest_signal_ts > oldest_signal_ts else 0.0
else:
    signal_span_sec = 0.0

# 若样本仍不足，返回 0（避免 raw）
if not bool(stats.get("is_normalized")):
    print(f"[cex] warn: 样本不足(n={int(stats.get('n_samples') or 0)}), 暂不交易", flush=True)
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
if elapsed_time_min is not None and cum_change is not None:
    nodes = _fetch_chainlink_history(
        feed_id=str(chainlink_feed_id),
        time_range=str(chainlink_time_range),
        cache_dir=chainlink_cache_dir or normalizer_cache_dir,
        max_age_s=float(chainlink_cache_max_age_s),
    )
    offsets = _recent_chainlink_offsets(nodes, n_windows=int(decay_N_windows))
    offsets_n = len(offsets)
    optimizer = SignalOptimizer(
        T=float(decay_T),
        lambda_base=float(decay_lambda_base),
        sigma=float(decay_sigma),
        multiplier=float(decay_multiplier),
        min_mu=float(decay_min_mu),
        max_mu=float(decay_max_mu),
        N_windows=int(decay_N_windows),
    )
    optimizer.set_historical_offsets(offsets)
    mu_val = optimizer.compute_dynamic_mu()
    extra_factor = optimizer.dynamic_decay(float(elapsed_time_min), float(cum_change))
z_eff = float(normalized_score) * float(extra_factor)

if return_meta:
    meta = dict(stats or {})
    meta.update(
        {
            "z_score": float(normalized_score),
            "extra_factor": float(extra_factor),
            "z_eff": float(z_eff),
            "mu": float(mu_val) if mu_val is not None else None,
            "offsets_n": int(offsets_n),
            "n_signals": int(n_signals),
            "time_window_sec": float(time_window_sec),
            "decay_rate": float(decay_rate),
            "ewma_alpha": float(ewma_alpha),
            "signal_span_sec": float(signal_span_sec),
        }
    )
    print(
        f"[cex] timing path={p} load_signals_s={t2 - t1:.3f} "
        f"normalize_s={t3 - t2:.3f} save_s={t4 - t3:.3f} total_s={t4 - t0:.3f} "
        f"n_signals={n_signals} time_window_sec={time_window_sec:.1f} signal_span_sec={signal_span_sec:.1f}",
        flush=True,
    )
    return CexScoreResult(score=float(normalized_score), meta=meta)

print(
    f"[cex] timing path={p} load_signals_s={t2 - t1:.3f} "
    f"normalize_s={t3 - t2:.3f} save_s={t4 - t3:.3f} total_s={t4 - t0:.3f} "
    f"n_signals={n_signals} time_window_sec={time_window_sec:.1f}",
    flush=True,
)
return float(normalized_score)def ensure_cex_warmup(
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
            return Falseif weights_by_venue is not None:
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

print(f"[cex] warmup: start cache={cache_key}", flush=True)
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
    print("[cex] warmup: reset history for full backfill", flush=True)
    try:
        prev = _prev_cex_slice_path(p)
        if prev is not None:
            print(f"[cex] warmup: try prev slice {prev}", flush=True)
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
        print(f"[cex] warmup: 失败 {type(e).__name__}:{e}", flush=True)

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
print(
    f"[cex] warmup: done ok={bool(ok)} n_samples={int(n_samples)} span_s={span_s:.1f} now={now_s:.1f} oldest={oldest:.1f} newest={newest:.1f}",
    flush=True,
)
return bool(ok)def _auto_detect_cex_slice(symbol: str) -> Path:
    """
    自动检测当前UTC时间对应的12小时CEX数据分片。Args:
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

