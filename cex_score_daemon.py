from __future__ import annotations

import argparse
import csv
import json
import socket
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cex_scorer import AdaptiveScoreNormalizer, _needs_warmup, _normalize_ts


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
    args = ap.parse_args()

    symbol = str(args.symbol).strip().lower()
    window = str(args.window).strip().lower()
    hot_dir = Path(str(args.hot_dir)).expanduser()
    sleep_s = max(0.1, float(args.sleep_s))

    output_path = Path(args.output) if args.output else hot_dir / f"cex_score_{symbol}_{window}.jsonl"
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

    venues = ["binance_spot", "okx_spot", "okx_swap", "bybit_spot", "bybit_linear"]
    weights = [1.0, 1.0, 2.0, 2.0, 3.0]

    broadcaster = TcpBroadcaster(args.host, int(args.port))
    broadcaster.start()

    last_save_s = 0.0
    reader: IncrementalCexTailReader | None = None

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

            signal = reader.poll_latest_complete()
            if signal is None:
                time.sleep(sleep_s)
                continue

            sample_id, t_sample, raw_score = signal
            normalizer.update(raw_score, t_sample)
            z_score, stats = normalizer.normalize(raw_score, t_sample)
            is_normalized = bool(stats.get("is_normalized"))
            z_eff = float(z_score) if is_normalized else 0.0

            payload = {
                "ts": float(_normalize_ts(t_sample)),
                "sample_id": int(sample_id),
                "raw_score": float(raw_score),
                "z_score": float(z_score),
                "z_eff": float(z_eff),
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
