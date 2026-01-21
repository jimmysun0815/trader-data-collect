from __future__ import annotations

import json
import math
import pickle
import time
import urllib.parse
import urllib.request
import statistics
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

_NORMALIZER_CACHE: dict[str, AdaptiveScoreNormalizer] = {}
_LOGGED_NORMALIZER: set[str] = set()
_LOGGED_CSV: set[str] = set()
_WARMUP_DONE: set[str] = set()
_CHAINLINK_CACHE: dict[str, dict[str, Any]] = {}


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
        g = groups.setdefault(sid, {"t": t, "imbs": {}})
        g["t"] = t
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


def _normalize_ts(ts: float) -> float:
    t = float(ts)
    # Normalize ms/us timestamps to seconds.
    while t > 1e11:
        t /= 1000.0
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
) -> None:
    now_s = _normalize_ts(float(now_ts))
    cutoff = float(now_s) - float(lookback_seconds)
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
    used = 0
    newest_ts: float | None = None
    for t, s in signals:
        t_s = _normalize_ts(float(t))
        if t_s + 1e-9 < cutoff:
            continue
        if last_ts is not None and t_s <= float(last_ts) + 1e-9:
            continue
        normalizer.update(float(s), float(t_s))
        used += 1
        newest_ts = t_s if newest_ts is None else max(newest_ts, t_s)
    normalizer._cleanup(float(now_s))
    print(f"[cex] warmup: 已补齐样本 {used} 条 (lookback_s={int(lookback_seconds)})", flush=True)


@dataclass(frozen=True)
class CexScoreResult:
    """
    对外依旧只需要 float score，但为了 runner/日志调试，这里保留可选 meta。
    """

    score: float
    meta: dict[str, Any]


class AdaptiveScoreNormalizer:
    """
    基于历史分布的Z-score标准化器。
    
    动态维护过去N秒的score历史，实时计算均值和标准差，
    将新的score标准化为Z-score形式。
    
    Args:
        lookback_seconds: 回溯窗口（秒），默认7200（2小时）
        min_samples: 最小样本数，低于此值返回原始score，默认100
    """
    
    def __init__(self, lookback_seconds: int = 7200, min_samples: int = 100):
        self.lookback_seconds = float(lookback_seconds)
        self.min_samples = int(min_samples)
        self.history: deque[tuple[float, float]] = deque()  # [(timestamp, score), ...]
    
    def update(self, score: float, timestamp: Optional[float] = None) -> None:
        """
        添加新的score样本到历史记录。
        
        Args:
            score: 原始score值
            timestamp: Unix时间戳，默认使用当前时间
        """
        ts = _normalize_ts(float(timestamp) if timestamp is not None else time.time())
        self.history.append((float(ts), float(score)))
        self._cleanup(ts)
    
    def _cleanup(self, current_time: float) -> None:
        """删除超出lookback窗口的旧数据"""
        cutoff = float(current_time) - self.lookback_seconds
        while self.history and self.history[0][0] < cutoff:
            self.history.popleft()
    
    def normalize(self, score: float, timestamp: Optional[float] = None) -> tuple[float, dict[str, Any]]:
        """
        标准化score为Z-score。
        
        Args:
            score: 要标准化的原始score
            timestamp: Unix时间戳，默认使用当前时间
        
        Returns:
            (z_score, stats_dict)
            - z_score: 标准化后的值，如果样本不足则返回原始score
            - stats_dict: 包含 mean, std, n_samples, is_normalized 等统计信息
        """
        ts = _normalize_ts(float(timestamp) if timestamp is not None else time.time())
        self._cleanup(ts)
        
        # 样本不足，返回原始值
        if len(self.history) < self.min_samples:
            return float(score), {
                'mean': 0.0,
                'std': 0.0,
                'n_samples': len(self.history),
                'is_normalized': False,
                'z_score': float(score),
                'raw_score': float(score),
            }
        
        # 计算均值和标准差
        scores = [s for _, s in self.history]
        mean = sum(scores) / len(scores)
        variance = sum((s - mean) ** 2 for s in scores) / len(scores)
        std = variance ** 0.5
        
        # 避免除零
        if std < 1e-9:
            z_score = 0.0
        else:
            z_score = (float(score) - mean) / std
        
        return float(z_score), {
            'mean': float(mean),
            'std': float(std),
            'n_samples': len(self.history),
            'is_normalized': True,
            'z_score': float(z_score),
            'raw_score': float(score),
        }
    
    def save_state(self, path: Path) -> None:
        """
        持久化normalizer状态到文件。
        
        Args:
            path: 保存路径（.pkl文件）
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        state = {
            'lookback_seconds': self.lookback_seconds,
            'min_samples': self.min_samples,
            'history': list(self.history),  # deque -> list for pickle
            'saved_at': time.time(),
        }
        with open(path, 'wb') as f:
            pickle.dump(state, f)
    
    @classmethod
    def load_state(cls, path: Path) -> Optional['AdaptiveScoreNormalizer']:
        """
        从文件恢复normalizer状态。
        
        Args:
            path: 加载路径（.pkl文件）
        
        Returns:
            恢复的normalizer实例，如果文件不存在返回None
        """
        if not path.exists():
            return None
        
        try:
            with open(path, 'rb') as f:
                state = pickle.load(f)
            
            normalizer = cls(
                lookback_seconds=int(state['lookback_seconds']),
                min_samples=int(state['min_samples'])
            )
            hist = []
            for t, s in list(state.get("history") or []):
                try:
                    hist.append((_normalize_ts(float(t)), float(s)))
                except Exception:
                    continue
            normalizer.history = deque(hist)

            # 清理过期数据
            normalizer._cleanup(_normalize_ts(time.time()))
            
            return normalizer
        except Exception:
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


class SignalOptimizer:
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
        self.historical_offsets: list[float] = []

    def set_historical_offsets(self, offsets: list[float]) -> None:
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
        return math.exp(-effective_lambda * float(elapsed_time))


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
    
    Returns:
        标准化后的score（如果use_normalization=True），否则返回原始score。
        当 return_meta=True 时，返回 CexScoreResult，包含 z_eff 与 extra_factor。
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

    # 复用旧脚本的"最后一个 sample_id 且 venues 齐全"的取样逻辑
    try:
        from polymarket_live_one_trade import load_latest_complete_cex_signal  # type: ignore
    except Exception:
        try:
            from trade.polymarket_live_run_current_window import load_latest_complete_cex_signal  # type: ignore
        except Exception:
            return 0.0

    t1 = time.perf_counter()
    sig = load_latest_complete_cex_signal(
        p,
        venues=venues2,
        weights=weights2,
        min_abs_score=0.0,
        tail_bytes=int(tail_bytes),
    )
    t2 = time.perf_counter()
    if sig is None:
        print(
            f"[cex] timing path={p} load_signal_s={t2 - t1:.3f} total_s={t2 - t0:.3f}",
            flush=True,
        )
        return 0.0
    
    try:
        raw_score = float(sig.score)
    except Exception:
        return 0.0
    
    # 如果不使用标准化，直接返回原始score
    if not use_normalization:
        if return_meta:
            return CexScoreResult(
                score=float(raw_score),
                meta={"z_score": float(raw_score), "extra_factor": 1.0, "z_eff": float(raw_score)},
            )
        return float(raw_score)
    
    # 使用标准化
    cache_dir = Path(normalizer_cache_dir) if normalizer_cache_dir else Path(".cache")
    cache_file = cache_dir / f"cex_normalizer_{symbol}.pkl"

    csv_key = str(p.resolve()) if p.exists() else str(p)
    if csv_key not in _LOGGED_CSV:
        print(
            f"[cex] 使用CSV={csv_key} tail_bytes={int(tail_bytes)} lookback_s={int(lookback_seconds)} normalize={bool(use_normalization)}",
            flush=True,
        )
        _LOGGED_CSV.add(csv_key)
    
    cache_key = str(cache_file.resolve())
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
                min_samples=100
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
            f"min_samples={int(getattr(normalizer,'min_samples',100))}",
            flush=True,
        )
        if created_new:
            print("[cex] normalizer 初始化为空，样本不足时将暂停交易（不会使用 raw_score）", flush=True)
        _LOGGED_NORMALIZER.add(cache_key)
    
    # 获取当前时间戳
    try:
        timestamp = float(sig.t)
    except Exception:
        timestamp = time.time()
    
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

    # 更新历史并标准化
    normalizer.update(raw_score, timestamp)
    normalized_score, stats = normalizer.normalize(raw_score, timestamp)
    t3 = time.perf_counter()

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
            }
        )
        print(
            f"[cex] timing path={p} load_signal_s={t2 - t1:.3f} "
            f"normalize_s={t3 - t2:.3f} save_s={t4 - t3:.3f} total_s={t4 - t0:.3f}",
            flush=True,
        )
        return CexScoreResult(score=float(normalized_score), meta=meta)

    print(
        f"[cex] timing path={p} load_signal_s={t2 - t1:.3f} "
        f"normalize_s={t3 - t2:.3f} save_s={t4 - t3:.3f} total_s={t4 - t0:.3f}",
        flush=True,
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

    print(f"[cex] warmup: start cache={cache_key}", flush=True)
    normalizer = _NORMALIZER_CACHE.get(cache_key)
    if normalizer is None:
        normalizer = AdaptiveScoreNormalizer.load_state(cache_file)
        if normalizer is None:
            normalizer = AdaptiveScoreNormalizer(
                lookback_seconds=int(lookback_seconds),
                min_samples=100,
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

