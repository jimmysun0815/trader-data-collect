"""
CEX数据按时间分文件收集器

新增功能：
- 支持按12小时/24小时自动切换文件
- 文件名包含时间窗口，便于管理
- 适合SSHFS远程访问（单文件不会过大）

输出文件格式：
- cex_multi_venue_books_20260110_00.csv  (00:00-12:00)
- cex_multi_venue_books_20260110_12.csv  (12:00-24:00)

或24小时模式：
- cex_multi_venue_books_20260110.csv  (00:00-24:00)
"""

import argparse
from pathlib import Path
from datetime import datetime, timezone

# 在main()函数中添加这个参数
parser.add_argument(
    "--split-hours",
    type=int,
    default=0,
    help="按小时分文件：12=每12小时新文件, 24=每天新文件, 0=单文件追加（默认）"
)

def get_output_path(base_path: str, split_hours: int) -> Path:
    """
    根据当前时间和split_hours生成输出文件路径
    
    split_hours=0:  base_path（单文件追加）
    split_hours=12: base_path_20260110_00.csv 或 base_path_20260110_12.csv
    split_hours=24: base_path_20260110.csv
    """
    if split_hours == 0:
        return Path(base_path)
    
    now = datetime.now(timezone.utc)
    base = Path(base_path)
    stem = base.stem  # 不带扩展名
    suffix = base.suffix  # .csv
    
    if split_hours == 24:
        # 每天一个文件
        date_str = now.strftime("%Y%m%d")
        return base.parent / f"{stem}_{date_str}{suffix}"
    
    elif split_hours == 12:
        # 每12小时一个文件
        date_str = now.strftime("%Y%m%d")
        hour_block = "00" if now.hour < 12 else "12"
        return base.parent / f"{stem}_{date_str}_{hour_block}{suffix}"
    
    elif split_hours == 6:
        # 每6小时一个文件
        date_str = now.strftime("%Y%m%d")
        hour_block = f"{(now.hour // 6) * 6:02d}"
        return base.parent / f"{stem}_{date_str}_{hour_block}{suffix}"
    
    else:
        raise ValueError(f"不支持的split_hours值: {split_hours}，应为 0, 6, 12, 或 24")

# 使用示例：
# 在main()的循环中：
"""
while True:
    current_output = get_output_path(args.out, args.split_hours)
    
    # 如果文件路径变化，说明需要切换到新文件
    if current_output != last_output:
        f.close()
        f = open(current_output, "a", newline="")
        w = csv.writer(f)
        # 如果是新文件，写入header
        if not current_output.exists() or current_output.stat().st_size == 0:
            w.writerow(HEADER)
        last_output = current_output
    
    # 继续写入数据...
"""

