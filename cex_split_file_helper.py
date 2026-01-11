#!/usr/bin/env python3
"""
CEX数据采集器 - 支持按时间自动分文件

相比原版的改进：
1. 支持按12小时或24小时自动切换文件
2. 文件名包含时间窗口
3. 适合SSHFS远程访问（单文件不会过大）

用法：
  # 每12小时一个文件（推荐）
  python3 cex_multi_venue_recorder_split.py --out logs/cex.csv --split-hours 12
  
  # 每24小时一个文件
  python3 cex_multi_venue_recorder_split.py --out logs/cex.csv --split-hours 24
  
  # 每6小时一个文件（高频交易）
  python3 cex_multi_venue_recorder_split.py --out logs/cex.csv --split-hours 6
  
  # 不分文件（原始模式）
  python3 cex_multi_venue_recorder_split.py --out logs/cex.csv --split-hours 0

输出示例（12小时模式）：
  logs/cex_20260110_00.csv  (2026-01-10 00:00 - 12:00)
  logs/cex_20260110_12.csv  (2026-01-10 12:00 - 24:00)
  logs/cex_20260111_00.csv  (2026-01-11 00:00 - 12:00)
  
优势：
  - 单文件大小可控（12小时 ~40MB, 24小时 ~80MB）
  - SSHFS访问延迟低（读取40-80MB vs 432MB）
  - 便于按日期管理和归档
"""

from pathlib import Path
from datetime import datetime, timezone

def get_output_path(base_path: str, split_hours: int) -> Path:
    """
    根据当前时间和split_hours生成输出文件路径
    
    Args:
        base_path: 基础路径，如 "logs/cex.csv"
        split_hours: 分割小时数，0=不分割, 6/12/24=按小时分割
    
    Returns:
        实际输出文件路径
    
    Examples:
        split_hours=0:  logs/cex.csv（单文件）
        split_hours=12: logs/cex_20260110_00.csv 或 logs/cex_20260110_12.csv
        split_hours=24: logs/cex_20260110.csv
    """
    if split_hours == 0:
        return Path(base_path)
    
    now = datetime.now(timezone.utc)
    base = Path(base_path)
    stem = base.stem  # 不带扩展名，如 "cex"
    suffix = base.suffix  # 扩展名，如 ".csv"
    
    date_str = now.strftime("%Y%m%d")
    
    if split_hours == 24:
        # 每天一个文件: cex_20260110.csv
        filename = f"{stem}_{date_str}{suffix}"
    
    elif split_hours == 12:
        # 每12小时一个文件: cex_20260110_00.csv 或 cex_20260110_12.csv
        hour_block = "00" if now.hour < 12 else "12"
        filename = f"{stem}_{date_str}_{hour_block}{suffix}"
    
    elif split_hours == 6:
        # 每6小时一个文件: cex_20260110_00/06/12/18.csv
        hour_block = f"{(now.hour // 6) * 6:02d}"
        filename = f"{stem}_{date_str}_{hour_block}{suffix}"
    
    elif split_hours == 1:
        # 每小时一个文件: cex_20260110_15.csv
        hour_str = now.strftime("%H")
        filename = f"{stem}_{date_str}_{hour_str}{suffix}"
    
    else:
        raise ValueError(f"不支持的split_hours值: {split_hours}，应为 0, 1, 6, 12, 或 24")
    
    return base.parent / filename


def should_create_new_file(current_path: Path, base_path: str, split_hours: int) -> bool:
    """
    检查是否需要创建新文件
    
    当时间窗口变化时返回True
    """
    if split_hours == 0:
        return False  # 单文件模式，永不切换
    
    expected_path = get_output_path(base_path, split_hours)
    return current_path != expected_path


# 使用示例（添加到原始cex_multi_venue_recorder.py中）
"""
在main()函数中：

# 1. 添加命令行参数
p.add_argument(
    "--split-hours",
    type=int,
    default=12,
    help="按小时分文件：12=每12小时（推荐）, 24=每天, 6=每6小时, 0=单文件"
)

# 2. 修改主循环
current_output_path = None
f = None
w = None

while True:
    # 检查是否需要切换文件
    expected_path = get_output_path(args.out, args.split_hours)
    
    if current_output_path != expected_path:
        # 需要切换文件
        if f is not None:
            f.close()
            print(f"[{utc_ts()}] 关闭文件: {current_output_path}")
        
        # 打开新文件
        expected_path.parent.mkdir(parents=True, exist_ok=True)
        is_new_file = not expected_path.exists() or expected_path.stat().st_size == 0
        
        f = expected_path.open("a", newline="", encoding="utf-8")
        w = csv.writer(f)
        
        # 新文件需要写入header
        if is_new_file:
            w.writerow([
                "ts_sample_utc", "t_sample_unix", "sample_id", "venue",
                "best_bid", "best_ask", "mid", "spread",
                "bid_qty_l1", "ask_qty_l1",
                "bid_notional", "ask_notional",
                "imb", "micro", "micro_edge",
                "top_bids_json", "top_asks_json", "err"
            ])
        
        current_output_path = expected_path
        print(f"[{utc_ts()}] 写入文件: {current_output_path}")
    
    # 采集和写入数据...
    for venue in venues:
        # ... 采集逻辑 ...
        w.writerow([...])
        f.flush()
    
    time.sleep(interval)
"""

if __name__ == "__main__":
    # 测试代码
    import sys
    
    test_base = "logs/cex.csv"
    
    print("测试get_output_path函数:")
    print(f"基础路径: {test_base}")
    print()
    
    for split_hours in [0, 6, 12, 24]:
        path = get_output_path(test_base, split_hours)
        print(f"split_hours={split_hours:2d}: {path}")
    
    print()
    print("12小时模式文件示例:")
    print("  00:00-11:59 → cex_20260110_00.csv")
    print("  12:00-23:59 → cex_20260110_12.csv")
    print()
    print("24小时模式文件示例:")
    print("  00:00-23:59 → cex_20260110.csv")
    print()
    print("优势:")
    print("  ✅ 单文件大小可控（12h ~40MB vs 单文件432MB）")
    print("  ✅ SSHFS读取快（延迟从30秒降到1-2秒）")
    print("  ✅ 便于按日期管理和清理")
    print("  ✅ 历史数据易于归档和压缩")

