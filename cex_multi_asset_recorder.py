#!/usr/bin/env python3
"""
CEX多资产Order Book采集器 - 支持BTC + ETH

功能：
- 同时采集BTC和ETH的多交易所order book
- 每12小时自动切分文件
- 输出CSV格式，每行一个venue的快照

输出路径：../real_hot/cex_{asset}_{date}_{session}.csv
例如：cex_btc_20260110_00-12.csv, cex_btc_20260110_12-24.csv
"""

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests


def utc_ts() -> str:
    """返回UTC时间戳字符串"""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = int(round((len(values) - 1) * float(q)))
    return float(values[idx])


def http_get_json(url: str, *, params: Optional[dict[str, Any]] = None, timeout_s: float = 5.0) -> Any:
    """HTTP GET请求并返回JSON"""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; polymarket-recorder/1.0)",
        "Accept": "application/json",
    }
    r = requests.get(url, params=params, timeout=timeout_s, headers=headers, allow_redirects=False)
    
    if 300 <= r.status_code < 400:
        raise RuntimeError(f"HTTP {r.status_code} redirect")
    
    r.raise_for_status()
    return r.json()


BINANCE_ENDPOINTS = [
    "https://api.binance.com",
    "https://api1.binance.com",
    "https://api2.binance.com",
]


class BinanceEndpointPool:
    def __init__(self, endpoints: list[str], *, rotate_threshold: int = 101) -> None:
        self._endpoints = [e.rstrip("/") for e in endpoints if e.strip()]
        self._rotate_threshold = int(rotate_threshold)
        self._index = 0
        self._errors: dict[str, int] = {e: 0 for e in self._endpoints}
        self._lock = Lock()

    def current_base(self) -> str:
        with self._lock:
            if not self._endpoints:
                return "https://api.binance.com"
            return self._endpoints[self._index % len(self._endpoints)]

    def record_success(self) -> None:
        with self._lock:
            if not self._endpoints:
                return
            base = self._endpoints[self._index % len(self._endpoints)]
            self._errors[base] = 0

    def record_error(self) -> None:
        with self._lock:
            if not self._endpoints:
                return
            base = self._endpoints[self._index % len(self._endpoints)]
            self._errors[base] = int(self._errors.get(base, 0)) + 1
            if self._errors[base] >= self._rotate_threshold:
                self._errors[base] = 0
                self._index = (self._index + 1) % len(self._endpoints)


_BINANCE_POOL = BinanceEndpointPool(BINANCE_ENDPOINTS, rotate_threshold=101)


@dataclass(frozen=True)
class VenueBook:
    """交易所order book快照"""
    venue: str
    best_bid: float
    best_ask: float
    mid: float
    spread: float
    bid_qty_l1: float
    ask_qty_l1: float
    bids: list[tuple[float, float]]
    asks: list[tuple[float, float]]


def _best_levels(bids: list[tuple[float, float]], asks: list[tuple[float, float]]) -> tuple[float, float, float, float]:
    """提取最优买卖价和数量"""
    bb, bq = max(bids, key=lambda x: x[0])
    ba, aq = min(asks, key=lambda x: x[0])
    return bb, bq, ba, aq


def _sum_notional_within_bps(levels: list[tuple[float, float]], *, mid: float, side: str, band_bps: float) -> float:
    """计算在band_bps范围内的名义价值"""
    if mid <= 0:
        return 0.0
    band = band_bps / 1e4
    if side == "bid":
        lo = mid * (1.0 - band)
        return sum(p * q for p, q in levels if p >= lo)
    if side == "ask":
        hi = mid * (1.0 + band)
        return sum(p * q for p, q in levels if p <= hi)
    raise ValueError("side must be bid|ask")


def _imbalance(bid_notional: float, ask_notional: float) -> float:
    """计算订单簿不平衡度"""
    denom = bid_notional + ask_notional
    return ((bid_notional - ask_notional) / denom) if denom > 0 else 0.0


def _microprice(best_bid: float, best_ask: float, bid_qty: float, ask_qty: float) -> float:
    """计算微观价格"""
    denom = bid_qty + ask_qty
    if denom <= 0:
        return (best_bid + best_ask) / 2.0
    return (best_bid * ask_qty + best_ask * bid_qty) / denom


def fetch_binance_depth(symbol: str, limit: int, timeout_s: float, venue: str) -> VenueBook:
    """获取Binance现货depth"""
    base = _BINANCE_POOL.current_base()
    try:
        j = http_get_json(
            f"{base}/api/v3/depth",
            params={"symbol": symbol, "limit": str(limit)},
            timeout_s=timeout_s
        )
        _BINANCE_POOL.record_success()
    except requests.HTTPError as exc:
        status = getattr(exc.response, "status_code", None)
        if status in (418, 429):
            _BINANCE_POOL.record_error()
        raise
    bids = [(float(p), float(q)) for p, q in j.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in j.get("asks", [])]
    
    if not bids or not asks:
        raise RuntimeError(f"{venue}: empty book")
    
    bb, bq, ba, aq = _best_levels(bids, asks)
    mid = (bb + ba) / 2.0
    return VenueBook(
        venue=venue, best_bid=bb, best_ask=ba, mid=mid, spread=ba - bb,
        bid_qty_l1=bq, ask_qty_l1=aq, bids=bids, asks=asks
    )


def fetch_okx_books(inst_id: str, sz: int, timeout_s: float, venue: str) -> VenueBook:
    """获取OKX order book"""
    j = http_get_json(
        "https://www.okx.com/api/v5/market/books",
        params={"instId": inst_id, "sz": str(sz)},
        timeout_s=timeout_s
    )
    data = j.get("data", [])
    if not data:
        raise RuntimeError(f"{venue}: empty data")
    
    ob = data[0]
    bids = [(float(p), float(q)) for p, q, *_ in ob.get("bids", [])]
    asks = [(float(p), float(q)) for p, q, *_ in ob.get("asks", [])]
    
    if not bids or not asks:
        raise RuntimeError(f"{venue}: empty book")
    
    bb, bq, ba, aq = _best_levels(bids, asks)
    mid = (bb + ba) / 2.0
    return VenueBook(
        venue=venue, best_bid=bb, best_ask=ba, mid=mid, spread=ba - bb,
        bid_qty_l1=bq, ask_qty_l1=aq, bids=bids, asks=asks
    )


def fetch_bybit_books(category: str, symbol: str, limit: int, timeout_s: float, venue: str) -> VenueBook:
    """获取Bybit order book"""
    j = http_get_json(
        "https://api.bybit.com/v5/market/orderbook",
        params={"category": category, "symbol": symbol, "limit": str(limit)},
        timeout_s=timeout_s
    )
    result = j.get("result", {})
    bids = [(float(p), float(q)) for p, q in result.get("b", [])]
    asks = [(float(p), float(q)) for p, q in result.get("a", [])]
    
    if not bids or not asks:
        raise RuntimeError(f"{venue}: empty book")
    
    bb, bq, ba, aq = _best_levels(bids, asks)
    mid = (bb + ba) / 2.0
    return VenueBook(
        venue=venue, best_bid=bb, best_ask=ba, mid=mid, spread=ba - bb,
        bid_qty_l1=bq, ask_qty_l1=aq, bids=bids, asks=asks
    )


def compute_features(book: VenueBook, band_bps: float) -> dict[str, float]:
    """计算order book特征"""
    bid_notional = _sum_notional_within_bps(book.bids, mid=book.mid, side="bid", band_bps=band_bps)
    ask_notional = _sum_notional_within_bps(book.asks, mid=book.mid, side="ask", band_bps=band_bps)
    imb = _imbalance(bid_notional, ask_notional)
    micro = _microprice(book.best_bid, book.best_ask, book.bid_qty_l1, book.ask_qty_l1)
    
    return {
        "bid_notional": bid_notional,
        "ask_notional": ask_notional,
        "imb": imb,
        "micro": micro,
        "micro_edge": micro - book.mid,
    }


# 资产配置
ASSETS = {
    "btc": {
        "binance_symbol": "BTCUSDT",
        "okx_spot": "BTC-USDT",
        "okx_swap": "BTC-USDT-SWAP",
        "bybit_symbol": "BTCUSDT",
    },
    "eth": {
        "binance_symbol": "ETHUSDT",
        "okx_spot": "ETH-USDT",
        "okx_swap": "ETH-USDT-SWAP",
        "bybit_symbol": "ETHUSDT",
    }
}

VENUES = ["binance_spot", "okx_spot", "okx_swap", "bybit_spot", "bybit_linear"]


def get_12h_session() -> str:
    """
    获取当前12小时时段标识
    返回: "00-12" 或 "12-24"
    """
    hour = datetime.now(timezone.utc).hour
    return "00-12" if hour < 12 else "12-24"


def get_output_file(asset: str, output_dir: Path) -> tuple[Path, str]:
    """
    获取当前应该写入的文件路径
    返回: (文件路径, 时段标识)
    """
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    session = get_12h_session()
    filename = f"cex_{asset}_{date_str}_{session}.csv"
    return output_dir / filename, session


class AssetRecorder:
    """单个资产的采集器"""
    
    def __init__(self, asset: str, output_dir: Path, band_bps: float, limit: int, timeout_s: float):
        self.asset = asset
        self.output_dir = output_dir
        self.band_bps = band_bps
        self.limit = limit
        self.timeout_s = timeout_s
        self.config = ASSETS[asset]
        
        self.current_file = None
        self.current_writer = None
        self.current_session = None
        self.sample_id = 0
        
        # 确保输出目录存在
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def _check_rotate_file(self):
        """检查是否需要切换文件"""
        file_path, session = get_output_file(self.asset, self.output_dir)
        
        if self.current_session != session:
            # 需要切换文件
            if self.current_file:
                self.current_file.close()
                print(f"[{self.asset}] Rotated to new file: {file_path}", file=sys.stderr)
            
            # 打开新文件
            is_new = not file_path.exists()
            self.current_file = open(file_path, "a", newline="", encoding="utf-8")
            self.current_writer = csv.writer(self.current_file)
            self.current_session = session
            
            # 写入header
            if is_new:
                self.current_writer.writerow([
                    "ts_sample_utc", "t_sample_unix", "sample_id", "venue",
                    "best_bid", "best_ask", "mid", "spread",
                    "bid_qty_l1", "ask_qty_l1",
                    "bid_notional", "ask_notional", "imb", "micro", "micro_edge",
                    "err"
                ])
                self.current_file.flush()
    
    def _fetch_venue(self, venue: str) -> tuple[str, Optional[VenueBook], str]:
        """获取单个venue的数据（用于并行）
        返回: (venue名称, book数据或None, 错误信息)
        """
        try:
            if venue == "binance_spot":
                book = fetch_binance_depth(
                    self.config["binance_symbol"],
                    self.limit,
                    self.timeout_s,
                    venue
                )
            elif venue == "okx_spot":
                book = fetch_okx_books(
                    self.config["okx_spot"],
                    self.limit,
                    self.timeout_s,
                    venue
                )
            elif venue == "okx_swap":
                book = fetch_okx_books(
                    self.config["okx_swap"],
                    self.limit,
                    self.timeout_s,
                    venue
                )
            elif venue == "bybit_spot":
                book = fetch_bybit_books(
                    "spot",
                    self.config["bybit_symbol"],
                    self.limit,
                    self.timeout_s,
                    venue
                )
            elif venue == "bybit_linear":
                book = fetch_bybit_books(
                    "linear",
                    self.config["bybit_symbol"],
                    self.limit,
                    self.timeout_s,
                    venue
                )
            else:
                return venue, None, "Unknown venue"
            
            return venue, book, ""
            
        except Exception as e:
            msg = str(e).replace("\n", " ")
            if len(msg) > 240:
                msg = msg[:240] + "…"
            return venue, None, f"{type(e).__name__}: {msg}"
    
    def collect_tick(self, *, enable_write: bool = True) -> dict[str, Any]:
        """采集一个tick的数据（并行请求所有venue）"""
        if enable_write:
            self._check_rotate_file()
        
        t0 = time.time()
        ts = utc_ts()
        self.sample_id += 1
        
        ok = 0
        total = 0
        err_rows = 0
        # 并行请求所有venue（最多5个线程）
        with ThreadPoolExecutor(max_workers=len(VENUES)) as executor:
            # 提交所有请求
            futures = {
                executor.submit(self._fetch_venue, venue): venue 
                for venue in VENUES
            }
            
            # 收集结果并写入CSV
            for future in as_completed(futures):
                venue, book, err = future.result()
                total += 1

                if book and not err:
                    ok += 1
                    if enable_write:
                        # 成功 - 写入完整数据
                        feats = compute_features(book, self.band_bps)
                        self.current_writer.writerow([
                            ts, f"{t0:.6f}", self.sample_id, venue,
                            book.best_bid, book.best_ask, book.mid, book.spread,
                            book.bid_qty_l1, book.ask_qty_l1,
                            feats["bid_notional"], feats["ask_notional"],
                            feats["imb"], feats["micro"], feats["micro_edge"],
                            ""
                        ])
                else:
                    err_rows += 1
                    if enable_write:
                        # 失败 - 写入错误行
                        self.current_writer.writerow([
                            ts, f"{t0:.6f}", self.sample_id, venue,
                            "", "", "", "", "", "",
                            "", "", "", "", "",
                            err
                        ])
        
        # 刷新到磁盘
        if enable_write and self.current_file:
            self.current_file.flush()
        elapsed = time.time() - t0
        return {
            "asset": self.asset,
            "elapsed_s": float(elapsed),
            "ok": int(ok),
            "total": int(total),
            "err": int(err_rows),
        }
    
    def close(self):
        """关闭文件"""
        if self.current_file:
            self.current_file.close()


def _run_benchmark(
    *,
    recorders: dict[str, AssetRecorder],
    executor: ThreadPoolExecutor,
    hz: float,
    duration_s: float,
    log_path: Path,
) -> dict[str, float]:
    interval = 1.0 / hz if hz > 0 else 0.0
    latencies: list[float] = []
    ok_rates: list[float] = []
    total_ticks = 0
    ok_ticks = 0
    total_rows = 0
    ok_rows = 0

    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as f:
        f.write("ts_utc,elapsed_s,ok,total,ok_rate\n")
        start = time.time()
        while time.time() - start < duration_s:
            t0 = time.time()
            futures = {
                executor.submit(recorder.collect_tick, enable_write=False): asset
                for asset, recorder in recorders.items()
            }
            ok = 0
            total = 0
            for future in as_completed(futures):
                try:
                    res = future.result()
                except Exception:
                    continue
                ok += int(res.get("ok") or 0)
                total += int(res.get("total") or 0)
            elapsed = time.time() - t0
            ok_rate = (float(ok) / float(total)) if total > 0 else 0.0
            f.write(f"{utc_ts()},{elapsed:.6f},{ok},{total},{ok_rate:.4f}\n")
            f.flush()
            latencies.append(float(elapsed))
            ok_rates.append(float(ok_rate))
            total_ticks += 1
            if ok_rate >= 0.9:
                ok_ticks += 1
            total_rows += total
            ok_rows += ok

            to_sleep = interval - elapsed
            if to_sleep > 0:
                time.sleep(to_sleep)

        summary = {
            "ticks": float(total_ticks),
            "ok_tick_ratio": float(ok_ticks / total_ticks) if total_ticks else 0.0,
            "ok_row_ratio": float(ok_rows / total_rows) if total_rows else 0.0,
            "p50_s": _quantile(latencies, 0.5),
            "p90_s": _quantile(latencies, 0.9),
            "p99_s": _quantile(latencies, 0.99),
            "avg_ok_rate": sum(ok_rates) / len(ok_rates) if ok_rates else 0.0,
        }
        f.write(
            "summary,"
            f"ticks={int(summary['ticks'])},"
            f"ok_tick_ratio={summary['ok_tick_ratio']:.4f},"
            f"ok_row_ratio={summary['ok_row_ratio']:.4f},"
            f"p50_s={summary['p50_s']:.4f},"
            f"p90_s={summary['p90_s']:.4f},"
            f"p99_s={summary['p99_s']:.4f},"
            f"avg_ok_rate={summary['avg_ok_rate']:.4f}\n"
        )
    return summary


def main():
    """主循环"""
    import argparse

    ap = argparse.ArgumentParser(description="CEX multi-asset recorder")
    ap.add_argument("--output-dir", type=str, default=str(Path(__file__).parent / "real_hot"))
    ap.add_argument("--hz", type=float, default=1.0, help="target frequency")
    ap.add_argument("--band-bps", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--timeout-s", type=float, default=1.0)
    ap.add_argument("--test-seconds", type=float, default=0.0)
    ap.add_argument("--log-dir", type=str, default=str(Path(__file__).parent / "log"))
    args = ap.parse_args()

    output_dir = Path(args.output_dir)
    hz = float(args.hz)
    band_bps = float(args.band_bps)
    limit = int(args.limit)
    timeout_s = float(args.timeout_s)
    test_seconds = float(args.test_seconds)
    log_dir = Path(args.log_dir)

    print(f"[INFO] CEX多资产采集器启动", file=sys.stderr)
    print(f"[INFO] 输出目录: {output_dir}", file=sys.stderr)
    print(f"[INFO] 资产: {', '.join(ASSETS.keys())}", file=sys.stderr)
    print(f"[INFO] 采集频率: {hz} Hz", file=sys.stderr)
    print(f"[INFO] 文件切分: 每12小时", file=sys.stderr)
    print(f"[INFO] timeout_s: {timeout_s}", file=sys.stderr)

    # 创建每个资产的采集器
    recorders = {
        asset: AssetRecorder(asset, output_dir, band_bps, limit, timeout_s)
        for asset in ASSETS.keys()
    }

    interval = 1.0 / hz if hz > 0 else 0.0

    try:
        with ThreadPoolExecutor(max_workers=len(recorders)) as executor:
            if test_seconds > 0:
                log_dir.mkdir(parents=True, exist_ok=True)
                log_path = log_dir / f"cex_benchmark_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.log"
                summary = _run_benchmark(
                    recorders=recorders,
                    executor=executor,
                    hz=hz if hz > 0 else 1.0,
                    duration_s=test_seconds,
                    log_path=log_path,
                )
                print(f"[INFO] benchmark_log: {log_path}", file=sys.stderr)
                print(f"[INFO] benchmark_summary: {summary}", file=sys.stderr)
                return 0

            while True:
                t0 = time.time()
                futures = {
                    executor.submit(recorder.collect_tick, enable_write=True): asset
                    for asset, recorder in recorders.items()
                }
                for future in as_completed(futures):
                    asset = futures[future]
                    try:
                        future.result()
                    except Exception as e:
                        print(f"[ERROR] {asset} tick failed: {e}", file=sys.stderr)

                # 控制采集频率
                dt = time.time() - t0
                to_sleep = interval - dt
                if to_sleep > 0:
                    time.sleep(to_sleep)

    except KeyboardInterrupt:
        print("\n[INFO] Shutting down...", file=sys.stderr)
    finally:
        # 关闭所有文件
        for asset, recorder in recorders.items():
            recorder.close()
            print(f"[INFO] Closed recorder for {asset}", file=sys.stderr)

    print("[INFO] Recorder stopped", file=sys.stderr)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())

