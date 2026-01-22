from __future__ import annotations

import argparse
import csv
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cex_scorer import (
    AdaptiveScoreNormalizer,
    SignalOptimizer,
    _needs_warmup,
    _normalize_ts,
)


class IncrementalCexTailReader:
    def __init__(self, csv_path: Path, *, venues: list[str], weights: list[float]) -> None:
        self.csv_path = Path(csv_path)
        self.venues = list(venues)
        self.weights = list(weights)
        self.header = self._read_header(self.csv_path)
        self.file_pos = self.csv_path.stat().st_size
        self.buffer = ""
        self.groups: dict[int, dict[str, Any]] = {}
        self.last_sample_id = -1

    @staticmethod
    def _read_header(path: Path) -> list[str]:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            line = f.readline().strip("\n")
        return next(csv.reader([line]))

    def reset_for_new_file(self, csv_path: Path) -> None:
        self.csv_path = Path(csv_path)
        self.header = self._read_header(self.csv_path)
        self.file_pos = self.csv_path.stat().st_size
        self.buffer = ""
        self.groups.clear()
        self.last_sample_id = -1

    def poll_latest_complete(self) -> tuple[int, float, float] | None:
        size = self.csv_path.stat().st_size
        if size < self.file_pos:
            # file rotated/truncated
            self.reset_for_new_file(self.csv_path)
            return None
        if size == self.file_pos:
            return None
        with self.csv_path.open("rb") as f:
            f.seek(self.file_pos)
            chunk = f.read()
            self.file_pos = f.tell()
        text = (self.buffer + chunk.decode("utf-8", errors="replace"))
        lines = text.splitlines()
        if text and not text.endswith("\n"):
            self.buffer = lines.pop() if lines else text
        else:
            self.buffer = ""

        if not lines:
            return None

        need = set(self.venues)
        for ln in lines:
            if not ln or ln.startswith("ts_sample_utc,"):
                continue
            try:
                row = next(csv.DictReader([",".join(self.header), ln]))
            except Exception:
                continue
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
            g = self.groups.setdefault(sid, {"t": t, "imbs": {}})
            g["t"] = t
            imbs = g.get("imbs")
            if isinstance(imbs, dict):
                imbs[venue] = imb

        # keep groups from growing without bound
        if len(self.groups) > 2000:
            cutoff = max(self.last_sample_id - 500, 0)
            for sid in list(self.groups.keys()):
                if sid < cutoff:
                    self.groups.pop(sid, None)

        # find latest complete sample
        best_sid = None
        best_t = 0.0
        best_score = 0.0
        for sid, g in self.groups.items():
            if sid <= self.last_sample_id:
                continue
            imbs = g.get("imbs") if isinstance(g, dict) else None
            if not isinstance(imbs, dict):
                continue
            if not all(v in imbs for v in self.venues):
                continue
            score = 0.0
            for i, v in enumerate(self.venues):
                score += float(self.weights[i]) * float(imbs[v])
            t = float(g.get("t") or 0.0)
            if best_sid is None or sid > best_sid:
                best_sid = sid
                best_t = t
                best_score = score

        if best_sid is None:
            return None

        self.last_sample_id = int(best_sid)
        for sid in list(self.groups.keys()):
            if sid < self.last_sample_id:
                self.groups.pop(sid, None)
        return int(best_sid), float(best_t), float(best_score)


class TcpBroadcaster:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = int(port)
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self.host, self.port))
        self._sock.listen(5)
        self._clients: list[socket.socket] = []
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def _accept_loop(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
                conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                with self._lock:
                    self._clients.append(conn)
            except Exception:
                time.sleep(0.2)

    def publish(self, line: str) -> None:
        data = (line + "\n").encode("utf-8")
        stale: list[socket.socket] = []
        with self._lock:
            for c in self._clients:
                try:
                    c.sendall(data)
                except Exception:
                    stale.append(c)
            if stale:
                for c in stale:
                    try:
                        c.close()
                    except Exception:
                        pass
                self._clients = [c for c in self._clients if c not in stale]


def _current_slice_path(hot_dir: Path, symbol: str, now_ts: float | None = None) -> Path:
    now_utc = datetime.fromtimestamp(now_ts or time.time(), tz=timezone.utc)
    day = now_utc.strftime("%Y%m%d")
    label = "00-12" if now_utc.hour < 12 else "12-24"
    return hot_dir / f"cex_{symbol}_{day}_{label}.csv"


def _window_start_ts(ts: float, window: str) -> float:
    if window == "1h":
        size = 3600
    else:
        size = 900
    return float(int(ts // size) * size)


def _score_output_path(*, csv_path: Path, hot_dir: Path, symbol: str, window: str, explicit: str) -> Path:
    if str(explicit).strip():
        return Path(explicit)
    # Match the 12h slice naming: cex_{symbol}_YYYYMMDD_00-12.csv
    name = csv_path.name
    parts = name.split("_")
    day = parts[2] if len(parts) >= 4 else ""
    label = parts[3].replace(".csv", "") if len(parts) >= 4 else ""
    if day and label:
        return hot_dir / f"cex_score_{symbol}_{day}_{label}_{window}.jsonl"
    return hot_dir / f"cex_score_{symbol}_{window}.jsonl"


def _fetch_binance_kline_price(
    *,
    ts_ms: int,
    use_open: bool,
    timeout_s: float = 5.0,
) -> float | None:
    kline_start = int(ts_ms // 60000) * 60000
    kline_end = int(kline_start + 60000)
    params = urllib.parse.urlencode(
        {
            "symbol": "BTCUSDT",
            "interval": "1m",
            "startTime": int(kline_start),
            "endTime": int(kline_end),
            "limit": 1,
        }
    )
    url = f"https://api.binance.com/api/v3/klines?{params}"
    req = urllib.request.Request(url, headers={"User-Agent": "polymarket-bot/cex_score_daemon"})
    try:
        with urllib.request.urlopen(req, timeout=float(timeout_s)) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    if not isinstance(data, list) or not data or not isinstance(data[0], list):
        return None
    candle = data[0]
    try:
        return float(candle[1] if use_open else candle[4])
    except Exception:
        return None


def _fetch_binance_price_with_retry(
    *,
    ts_ms: int,
    use_open: bool,
    max_retries: int = 10,
    sleep_s: float = 1.0,
) -> float:
    for _ in range(max(1, int(max_retries))):
        price = _fetch_binance_kline_price(ts_ms=ts_ms, use_open=use_open)
        if price is not None:
            return float(price)
        time.sleep(float(sleep_s))
    raise RuntimeError("binance price unavailable after retries")


def _binance_cum_change_with_retry(
    *,
    window_start_ts: float,
    window_end_ts: float,
    max_retries: int = 10,
    sleep_s: float = 1.0,
) -> float:
    for _ in range(max(1, int(max_retries))):
        start_price = _fetch_binance_price_with_retry(
            ts_ms=int(window_start_ts * 1000),
            use_open=True,
            max_retries=1,
            sleep_s=sleep_s,
        )
        end_price = _fetch_binance_price_with_retry(
            ts_ms=int(window_end_ts * 1000),
            use_open=False,
            max_retries=1,
            sleep_s=sleep_s,
        )
        diff = abs(float(end_price) - float(start_price))
        if diff > 0.0:
            return float(diff)
        time.sleep(float(sleep_s))
    raise RuntimeError("binance cum_change unavailable after retries")


def main() -> int:
    ap = argparse.ArgumentParser(description="CEX score daemon (incremental, file + TCP).")
    ap.add_argument("--symbol", type=str, default="btc")
    ap.add_argument("--window", type=str, default="15m", help="window tag (15m/1h)")
    ap.add_argument("--hot-dir", type=str, default="real_hot")
    ap.add_argument("--output", type=str, default="")
    ap.add_argument("--host", type=str, default="0.0.0.0")
    ap.add_argument("--port", type=int, default=9001)
    ap.add_argument("--sleep-s", type=float, default=1.0)
    ap.add_argument("--lookback-s", type=int, default=7200)
    ap.add_argument("--min-samples", type=int, default=100)
    ap.add_argument("--chainlink-feed-id", type=str, default="0x00039d9e45394f473ab1f050a1b963e6b05351e52d71e507509ada0c95ed75b8")
    ap.add_argument("--chainlink-time-range", type=str, default="1W")
    ap.add_argument("--chainlink-cache-dir", type=str, default="")
    ap.add_argument("--chainlink-cache-max-age-s", type=float, default=300.0)
    ap.add_argument("--decay-T", type=float, default=0.0)
    ap.add_argument("--decay-lambda-base", type=float, default=0.22)
    ap.add_argument("--decay-sigma", type=float, default=11.0)
    ap.add_argument("--decay-multiplier", type=float, default=0.6)
    ap.add_argument("--decay-min-mu", type=float, default=8.0)
    ap.add_argument("--decay-max-mu", type=float, default=60.0)
    ap.add_argument("--decay-N-windows", type=int, default=20)
    args = ap.parse_args()

    symbol = str(args.symbol).strip().lower()
    window = str(args.window).strip().lower()
    hot_dir = Path(str(args.hot_dir)).expanduser()
    sleep_s = max(0.1, float(args.sleep_s))
    decay_T = float(args.decay_T) if float(args.decay_T) > 0 else (60.0 if window == "1h" else 15.0)
    chainlink_cache_dir = Path(args.chainlink_cache_dir) if args.chainlink_cache_dir else None

    output_path = _score_output_path(
        csv_path=_current_slice_path(hot_dir, symbol),
        hot_dir=hot_dir,
        symbol=symbol,
        window=window,
        explicit=args.output,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    cache_dir = Path(".cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"cex_normalizer_{symbol}_{window}.pkl"

    normalizer = AdaptiveScoreNormalizer.load_state(cache_file)
    if normalizer is None:
        normalizer = AdaptiveScoreNormalizer(
            lookback_seconds=int(args.lookback_s),
            min_samples=int(args.min_samples),
        )

    if _needs_warmup(normalizer, now_ts=time.time()):
        print("[cex-score] warn: normalizer history不足，z_eff 可能为0直到样本补齐", flush=True)

    # log decay parameters for debugging
    print(
        f"[cex-score] decay params: T={decay_T:.1f} lambda_base={args.decay_lambda_base:.4f} "
        f"sigma={args.decay_sigma:.1f} multiplier={args.decay_multiplier:.2f} "
        f"min_mu={args.decay_min_mu:.1f} max_mu={args.decay_max_mu:.1f} N_windows={args.decay_N_windows}",
        flush=True,
    )

    venues = ["binance_spot", "okx_spot", "okx_swap", "bybit_spot", "bybit_linear"]
    weights = [1.0, 1.0, 2.0, 2.0, 3.0]

    broadcaster = TcpBroadcaster(args.host, int(args.port))
    broadcaster.start()

    last_save_s = 0.0
    reader: IncrementalCexTailReader | None = None
    offsets_history: deque[float] = deque(maxlen=int(args.decay_N_windows))
    current_window_start: float | None = None
    window_start_price: float | None = None
    last_cum_change: float | None = None

    print(
        f"[cex-score] start symbol={symbol} window={window} hot_dir={hot_dir} "
        f"output={output_path} port={int(args.port)}",
        flush=True,
    )

    while True:
        try:
            csv_path = _current_slice_path(hot_dir, symbol)
            if not csv_path.exists():
                time.sleep(sleep_s)
                continue

            if reader is None:
                reader = IncrementalCexTailReader(csv_path, venues=venues, weights=weights)
            elif reader.csv_path != csv_path:
                reader.reset_for_new_file(csv_path)
                output_path = _score_output_path(
                    csv_path=csv_path,
                    hot_dir=hot_dir,
                    symbol=symbol,
                    window=window,
                    explicit=args.output,
                )
                output_path.parent.mkdir(parents=True, exist_ok=True)
                print(f"[cex-score] rotate output={output_path}", flush=True)

            signal = reader.poll_latest_complete()
            if signal is None:
                time.sleep(sleep_s)
                continue

            sample_id, t_sample, raw_score = signal
            normalizer.update(raw_score, t_sample)
            z_score, stats = normalizer.normalize(raw_score, t_sample)
            is_normalized = bool(stats.get("is_normalized"))

            extra_factor = None
            mu_val = None
            offsets_n = 0
            binance_ok = True
            if is_normalized:
                try:
                    window_start = _window_start_ts(float(t_sample), window)
                    if current_window_start is None or window_start != current_window_start:
                        if last_cum_change is not None:
                            offsets_history.append(float(last_cum_change))
                        current_window_start = float(window_start)
                        window_start_price = _fetch_binance_price_with_retry(
                            ts_ms=int(current_window_start * 1000),
                            use_open=True,
                            max_retries=10,
                            sleep_s=1.0,
                        )
                    if window_start_price is None:
                        raise RuntimeError("binance window_start_price unavailable")
                    current_price = _fetch_binance_price_with_retry(
                        ts_ms=int(float(t_sample) * 1000),
                        use_open=False,
                        max_retries=10,
                        sleep_s=1.0,
                    )
                    cum_change = abs(float(current_price) - float(window_start_price))
                    if cum_change <= 0.0:
                        raise RuntimeError("binance cum_change=0 (invalid)")
                    last_cum_change = float(cum_change)
                    offsets = list(offsets_history)
                    offsets_n = len(offsets)
                    elapsed_time_min = max(0.0, (float(t_sample) - float(window_start)) / 60.0)
                    optimizer = SignalOptimizer(
                        T=float(decay_T),
                        lambda_base=float(args.decay_lambda_base),
                        sigma=float(args.decay_sigma),
                        multiplier=float(args.decay_multiplier),
                        min_mu=float(args.decay_min_mu),
                        max_mu=float(args.decay_max_mu),
                        N_windows=int(args.decay_N_windows),
                    )
                    optimizer.set_historical_offsets(offsets)
                    mu_val = optimizer.compute_dynamic_mu()
                    extra_factor = optimizer.dynamic_decay(float(elapsed_time_min), float(cum_change))
                except Exception as exc:
                    binance_ok = False
                    if int(sample_id) % 100 == 0:
                        print(f"[cex-score] warn: binance price unavailable: {exc}", flush=True)

            z_eff = None
            if is_normalized and binance_ok and extra_factor is not None:
                z_eff = float(z_score) * float(extra_factor)

            payload = {
                "ts": float(_normalize_ts(t_sample)),
                "sample_id": int(sample_id),
                "raw_score": float(raw_score),
                "z_score": float(z_score),
                "z_eff": float(z_eff) if z_eff is not None else None,
                "symbol": symbol,
                "window": window,
            }
            line = json.dumps(payload, ensure_ascii=True)
            with output_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

            broadcaster.publish(line)

            now_s = time.time()
            if now_s - last_save_s >= 5.0:
                try:
                    normalizer.save_state(cache_file)
                except Exception:
                    pass
                last_save_s = now_s
        except KeyboardInterrupt:
            print("[cex-score] exit", flush=True)
            return 0
        except Exception as exc:
            print(f"[cex-score] error: {type(exc).__name__}: {exc}", flush=True)
            time.sleep(sleep_s)


if __name__ == "__main__":
    raise SystemExit(main())
