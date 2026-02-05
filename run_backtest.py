#!/usr/bin/env python3
"""
回测运行入口：支持「合成数据」和「真实 OHLCV CSV」两种方式。

用法示例：
  # 1. 用合成数据快速跑一遍（无需任何数据文件）
  python3 run_backtest.py

  # 2. 用本地 CSV 跑回测（CSV 需含 timestamp + close，或可解析为时间的列）
  python3 run_backtest.py --csv /path/to/ohlcv.csv

  # 3. 开启缓存逻辑（仅当 BACKTEST_MODE=1 时才跑，否则跳过）
  BACKTEST_MODE=1 python3 run_backtest.py --csv /path/to/ohlcv.csv --cache .cache/backtest_cache.json

  # 4. 仅传 CEX 文件：以 CEX 的 min_ts 为起点，默认 48 小时内的 Poly 15 分钟窗口（--duration-hours 可改）；CEX 不足时自动在 real_hot 下找后续 12h 分片补齐
  python3 run_backtest.py --cex-csvs real_hot/cex_btc_20260122_12-24.csv

  # 5. 将 zeff_full 回测结果写入 JSON 文件
  python3 run_backtest.py --cex-csvs real_hot/cex_btc_20260122_12-24.csv --out logs/backtest_zeff.json
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import pandas as pd

from cex_scorer_backtest import (
    backtest_optimizer,
    backtest_raw_zscore_per_second,
    backtest_zeff_full,
    mock_ohlcv,
    run_backtest_with_cache,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger(__name__)

# real_hot 固定为「脚本所在目录的上一级 / real_hot」，即 polymarket/real_hot
REAL_HOT_DIR = Path(__file__).resolve().parent.parent / "real_hot"

# zeff_full 回测：每窗口秒数；窗口数由 --duration-hours 决定（默认 48 小时）
POLY_WINDOW_SEC = 900


def _cex_time_range(path: Path) -> tuple[float, float] | None:
    """读取 CEX CSV 的 t_sample_unix 范围，返回 (min_ts, max_ts)，失败返回 None。"""
    path = Path(path)
    if not path.exists():
        return None
    try:
        cex_df = pd.read_csv(path, usecols=["t_sample_unix"])
    except Exception:
        return None
    if "t_sample_unix" not in cex_df.columns:
        return None
    ts = pd.to_numeric(cex_df["t_sample_unix"], errors="coerce").dropna()
    if ts.empty:
        return None
    if ts.max() > 1e12:
        ts = ts / 1000.0
    return float(ts.min()), float(ts.max())


def load_ohlcv_csv(path: Path) -> pd.DataFrame:
    """加载 OHLCV CSV，需包含 timestamp（或 time/open_time）和 close。"""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    df = pd.read_csv(path)
    # 常见列名兼容
    if "timestamp" not in df.columns and "time" in df.columns:
        df = df.rename(columns={"time": "timestamp"})
    if "timestamp" not in df.columns and "open_time" in df.columns:
        df = df.rename(columns={"open_time": "timestamp"})
    if "timestamp" not in df.columns:
        raise ValueError("CSV 需要 timestamp / time / open_time 列")
    if "close" not in df.columns:
        raise ValueError("CSV 需要 close 列")
    # 时间可能是毫秒
    ts = pd.to_numeric(df["timestamp"], errors="coerce")
    if ts.max() > 1e12:
        ts = ts / 1000.0
    df = df.assign(timestamp=ts)
    df = df.set_index(pd.to_datetime(df["timestamp"], unit="s", utc=True))
    return df[["close"]].dropna()


def main() -> None:
    parser = argparse.ArgumentParser(description="CEX SignalOptimizer 回测")
    parser.add_argument(
        "--csv",
        type=Path,
        default=None,
        help="OHLCV CSV 路径（含 timestamp + close）。不传则用合成数据。",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=None,
        help="回测缓存 JSON 路径。指定且设 BACKTEST_MODE=1 时启用缓存逻辑。",
    )
    parser.add_argument(
        "--window-size",
        type=int,
        default=24,
        help="每个 fold 测试窗口的 bar 数（默认 24，即 1h 频率下 24 小时）",
    )
    parser.add_argument(
        "--min-train",
        type=int,
        default=48,
        help="最少训练 bar 数（默认 48）",
    )
    parser.add_argument(
        "--mock-days",
        type=int,
        default=30,
        help="未指定 --csv 时，合成数据的天数",
    )
    parser.add_argument(
        "--duration-hours",
        type=float,
        default=48.0,
        help="从 CEX 起点起回测时长（小时）；用于计算需要的 Poly 15 分钟窗口数（默认 48）",
    )
    parser.add_argument(
        "--eval-mode",
        type=str,
        choices=["poly", "per_second"],
        default="poly",
        help="poly=按 15 分钟窗口与 Poly 对齐回测 zeff；per_second=仅 raw/zscore，每次 CEX 有数据算一次与 5s 后 BTC 比",
    )
    parser.add_argument(
        "--no-high-acc",
        action="store_true",
        help="per_second 模式下关闭 high-acc 过滤，使用全部小时（不限于 UTC 00–08, 12–14, 19–21, 23）",
    )
    parser.add_argument(
        "--cex-csvs",
        type=Path,
        nargs="+",
        default=None,
        help="CEX CSV 路径列表；仅传此项时自动按 CEX 的 t_sample_unix 范围匹配 real_hot 下 Poly JSONL 跑 zeff_full（poly 模式）或按秒回测（per_second 模式）",
    )
    parser.add_argument(
        "--poly-jsonls",
        type=Path,
        nargs="+",
        default=None,
        help="Poly JSONL 路径列表；若只传一个且含 * 则在 real_hot 下 glob 展开",
    )
    parser.add_argument(
        "--thresh",
        type=float,
        default=0.1,
        help="hit_rate 预测阈值（仅 |z|>thresh 时计入）",
    )
    parser.add_argument(
        "--mid-venue",
        type=str,
        default="binance_spot",
        help="CEX mid 所用 venue",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="zeff_full 回测结果输出路径（JSON）；不指定则只打日志",
    )
    args = parser.parse_args()

    if args.poly_jsonls and len(args.poly_jsonls) == 1 and "*" in str(args.poly_jsonls[0]):
        if REAL_HOT_DIR.exists():
            args.poly_jsonls = sorted(REAL_HOT_DIR.glob(Path(args.poly_jsonls[0]).name))
        else:
            args.poly_jsonls = sorted(Path(".").glob(str(args.poly_jsonls[0])))

    if args.cex_csvs and not args.poly_jsonls:
        # 用用户提供的 CEX 文件确定起始时间 cex_min_ts
        current_cex_paths: list[Path] = []
        cex_min_ts = None
        cex_max_ts = None
        for cex_path in args.cex_csvs:
            cex_path = Path(cex_path)
            if not cex_path.exists() and REAL_HOT_DIR.exists():
                cex_path = REAL_HOT_DIR / cex_path.name
            if not cex_path.exists():
                continue
            tr = _cex_time_range(cex_path)
            if tr is None:
                continue
            mn, mx = tr
            current_cex_paths.append(cex_path)
            if cex_min_ts is None:
                cex_min_ts, cex_max_ts = mn, mx
            else:
                cex_min_ts = min(cex_min_ts, mn)
                cex_max_ts = max(cex_max_ts, mx)
        if cex_min_ts is None or cex_max_ts is None:
            logger.warning("Could not read t_sample_unix from CEX file(s)")
        else:
            if args.eval_mode == "per_second":
                need_cex_max_ts = cex_min_ts + int(args.duration_hours * 3600)
            else:
                # 从 cex_min_ts 起按 duration_hours 需要的 Poly 15 分钟窗口数；CEX 需覆盖到最后一个窗口结束
                num_poly_windows = max(1, int(args.duration_hours * 3600 / POLY_WINDOW_SEC))
                need_poly_max_ts = cex_min_ts + num_poly_windows * POLY_WINDOW_SEC - 1
                need_cex_max_ts = need_poly_max_ts + POLY_WINDOW_SEC
            # 若当前 CEX 覆盖不足，从 REAL_HOT_DIR 按 12h 分片补齐（与 Poly 同目录，保证下一分片如 20260123_00-12 能被找到）
            if cex_max_ts < need_cex_max_ts and current_cex_paths and REAL_HOT_DIR.exists():
                search_dir = REAL_HOT_DIR.resolve()
                all_cex = sorted(search_dir.glob("cex_btc_*.csv"))
                candidates: list[tuple[Path, float, float]] = []
                for p in all_cex:
                    tr = _cex_time_range(p)
                    if tr is not None:
                        candidates.append((p.resolve(), tr[0], tr[1]))
                candidates.sort(key=lambda x: x[1])
                seen = {p.resolve() for p in current_cex_paths}
                covered_end = cex_max_ts
                n_added = 0
                # 分片间可能存在几秒空档（如上一片 max=1769126399、下一片 min=1769126401），允许 60 秒内视为衔接
                gap_ok_s = 60
                for p, fmin, fmax in candidates:
                    if p in seen:
                        continue
                    if fmin <= covered_end + gap_ok_s and fmax > covered_end:
                        current_cex_paths.append(p)
                        seen.add(p)
                        covered_end = max(covered_end, fmax)
                        n_added += 1
                        logger.info("CEX 扩展: 加入 %s (ts %d–%d)", p.name, int(fmin), int(fmax))
                        if covered_end >= need_cex_max_ts:
                            break
                current_cex_paths.sort(key=lambda p: _cex_time_range(p) or (0, 0))
                args.cex_csvs = current_cex_paths
                logger.info("CEX 扩展: 共 %d 个候选，新加 %d 个，合计 %d 个文件，覆盖到 ts=%d", len(candidates), n_added, len(current_cex_paths), int(covered_end))
                if covered_end < need_cex_max_ts:
                    logger.warning(
                        "CEX 数据仅覆盖到 %d，需要到 %d；将使用已有 CEX 与 Poly",
                        int(covered_end),
                        int(need_cex_max_ts),
                    )
            else:
                args.cex_csvs = sorted(current_cex_paths, key=lambda p: _cex_time_range(p) or (0, 0))
            if args.eval_mode == "per_second":
                logger.info("Per-second 回测: CEX %d 文件, 时长 %.1fh (仅 raw/zscore vs 5s 后 BTC)%s", len(args.cex_csvs), args.duration_hours, ", 全时段" if args.no_high_acc else "")
                res = backtest_raw_zscore_per_second(
                    list(args.cex_csvs),
                    mid_venue=args.mid_venue,
                    high_acc_only=not args.no_high_acc,
                )
                summary = res.get("summary", {})
                logger.info(
                    "回测汇总: hit_rate_raw_btc=%.4f hit_rate_zscore_btc=%.4f (thresh=0.1) n_eval=%s",
                    summary.get("hit_rate_raw_btc", 0),
                    summary.get("hit_rate_zscore_btc", 0),
                    summary.get("n_eval", 0),
                )
                logger.info(
                    "num_predicted_raw_btc=%s num_predicted_zscore_btc=%s coverage_raw=%.4f coverage_zscore=%.4f",
                    summary.get("num_predicted_raw_btc", 0),
                    summary.get("num_predicted_zscore_btc", 0),
                    summary.get("coverage_rate_raw_btc", 0),
                    summary.get("coverage_rate_zscore_btc", 0),
                )
                gradient_results = summary.get("thresh_gradient_results", [])
                if gradient_results:
                    logger.info("=" * 80)
                    logger.info("阈值梯度结果表 (raw_btc / zscore_btc vs 5s 后 BTC):")
                    logger.info("-" * 80)
                    logger.info("%-8s | %-35s | %-35s", "阈值", "raw_btc (准确率/击中/覆盖率)", "zscore_btc (准确率/击中/覆盖率)")
                    logger.info("-" * 80)
                    for row in gradient_results:
                        t = row.get("thresh", 0)
                        rb = row.get("raw_btc", {})
                        zb = row.get("zscore_btc", {})
                        logger.info(
                            "%-8.1f | %.4f / %4d / %.4f | %.4f / %4d / %.4f",
                            t,
                            rb.get("hit_rate", 0), rb.get("num_hits", 0), rb.get("coverage_rate", 0),
                            zb.get("hit_rate", 0), zb.get("num_hits", 0), zb.get("coverage_rate", 0),
                        )
                    logger.info("=" * 80)
                if args.out is not None:
                    out_path = Path(args.out)
                    out_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(out_path, "w", encoding="utf-8") as f:
                        json.dump(res, f, indent=2, ensure_ascii=False)
                    logger.info("回测结果已写入: %s", out_path)
                return
            # Poly: 仅取 cex_min_ts 起 duration_hours 对应窗口数内的 jsonl
            poly_dir = REAL_HOT_DIR
            poly_files = []
            for p in poly_dir.glob("btc-updown-15m-*.jsonl"):
                parts = p.stem.split("-")
                if not parts:
                    continue
                try:
                    pt = int(float(parts[-1]))
                except (ValueError, TypeError):
                    continue
                if pt > 1e12:
                    pt = pt // 1000
                if cex_min_ts <= pt <= need_poly_max_ts:
                    poly_files.append(p)
            args.poly_jsonls = sorted(poly_files)
            if not args.poly_jsonls:
                logger.warning(
                    "No matching Poly JSONL for range [%d, %d] (%d windows, %.1fh from cex_min_ts)",
                    int(cex_min_ts),
                    int(need_poly_max_ts),
                    num_poly_windows,
                    args.duration_hours,
                )
                return
            logger.info(
                "Auto matched %d Poly JSONL (%d windows, %.1fh from cex_min_ts), CEX %d file(s), range [%d, %d]",
                len(args.poly_jsonls),
                num_poly_windows,
                args.duration_hours,
                len(args.cex_csvs),
                int(cex_min_ts),
                int(need_poly_max_ts),
            )

    if args.cex_csvs and args.poly_jsonls:
        logger.info("Zeff full 回测: CEX %d 文件, Poly %d 文件, thresh=%.2f", len(args.cex_csvs), len(args.poly_jsonls), args.thresh)
        res = backtest_zeff_full(
            list(args.cex_csvs),
            list(args.poly_jsonls),
            mid_venue=args.mid_venue,
            thresh=args.thresh,
            window_size=args.window_size,
            min_train_size=args.min_train,
        )
        summary = res.get("summary", {})
        logger.info(
            "回测汇总: hit_rate_raw_btc=%.4f hit_rate_zeff_poly=%.4f thresh=%.2f n_folds=%s",
            summary.get("hit_rate_raw_btc", 0),
            summary.get("hit_rate_zeff_poly", 0),
            summary.get("hit_rate_raw_btc_thresh", args.thresh),
            summary.get("n_folds", 0),
        )
        logger.info(
            "num_predicted_raw_btc=%s num_predicted_zeff_poly=%s",
            summary.get("num_predicted_raw_btc", 0),
            summary.get("num_predicted_zeff_poly", 0),
        )
        logger.info(
            "coverage_rate_raw_btc=%.4f coverage_rate_zeff_poly=%.4f",
            summary.get("coverage_rate_raw_btc", 0),
            summary.get("coverage_rate_zeff_poly", 0),
        )
        for r in res.get("all_results", []):
            logger.info(
                "  fold %s: hit_rate_raw_btc=%.4f hit_rate_zeff_poly=%.4f n=%s",
                r.get("fold"), r.get("hit_rate_raw_btc"), r.get("hit_rate_zeff_poly"), r.get("n"),
            )
        # Print gradient table
        gradient_results = summary.get("thresh_gradient_results", [])
        if gradient_results:
            logger.info("=" * 120)
            logger.info("阈值梯度结果表:")
            logger.info("-" * 120)
            logger.info("%-8s | %-35s | %-35s | %-35s", "阈值", "raw_btc (准确率/击中/覆盖率)", "zscore_btc (准确率/击中/覆盖率)", "zeff_poly (准确率/击中/覆盖率)")
            logger.info("-" * 120)
            for row in gradient_results:
                t = row.get("thresh", 0)
                rb = row.get("raw_btc", {})
                zb = row.get("zscore_btc", {})
                ze = row.get("zeff_poly", {})
                logger.info(
                    "%-8.1f | %.4f / %4d / %.4f | %.4f / %4d / %.4f | %.4f / %4d / %.4f",
                    t,
                    rb.get("hit_rate", 0), rb.get("num_hits", 0), rb.get("coverage_rate", 0),
                    zb.get("hit_rate", 0), zb.get("num_hits", 0), zb.get("coverage_rate", 0),
                    ze.get("hit_rate", 0), ze.get("num_hits", 0), ze.get("coverage_rate", 0),
                )
            logger.info("=" * 120)
        if args.out is not None:
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(res, f, indent=2, ensure_ascii=False)
            logger.info("回测结果已写入: %s", out_path)
        return

    if args.csv is not None:
        logger.info("加载 CSV: %s", args.csv)
        ohlcv_df = load_ohlcv_csv(args.csv)
        logger.info("行数: %d", len(ohlcv_df))
    else:
        logger.info("使用合成数据 (%d 天)", args.mock_days)
        ohlcv_df = mock_ohlcv(n_days=args.mock_days, freq="1h", seed=42)

    if args.cache is not None:
        res = run_backtest_with_cache(
            ohlcv_df,
            args.cache,
            window_size=args.window_size,
            min_train_size=args.min_train,
        )
        if res.get("skipped"):
            logger.info("跳过回测: %s", res.get("reason"))
            if res.get("best_params"):
                logger.info("使用缓存 best_params: %s", res.get("best_params"))
            return
    else:
        res = backtest_optimizer(
            ohlcv_df,
            window_size=args.window_size,
            min_train_size=args.min_train,
        )

    summary = res.get("summary", {})
    logger.info("回测汇总: mean_sharpe=%.4f mean_hit_rate=%.4f n_folds=%s",
                summary.get("mean_sharpe", 0),
                summary.get("mean_hit_rate", 0),
                summary.get("n_folds", 0))
    for r in res.get("all_results", []):
        logger.info("  fold %s: sharpe=%.4f hit_rate=%.4f n=%s",
                    r.get("fold"), r.get("sharpe"), r.get("hit_rate"), r.get("n"))


if __name__ == "__main__":
    main()
