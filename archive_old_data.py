#!/usr/bin/env python3
"""
VPS自动归档脚本 - 将超过30天的数据从real_hot/迁移到real_archive/

功能：
1. 扫描real_hot/目录下的btc-updown-15m-*.jsonl文件
2. 识别超过30天的文件（基于文件名中的时间戳）
3. 按月归档到real_archive/YYYY-MM/目录
4. 可选压缩（gzip）
5. 记录归档日志

用法：
  python3 archive_old_data.py [--dry-run] [--compress] [--days 30]
"""

from __future__ import annotations

import argparse
import gzip
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def parse_window_start_from_filename(filename: str) -> Optional[int]:
    """
    从文件名提取window_start时间戳
    例如: btc-updown-15m-1767507300_1767507300_20260103_222417.jsonl -> 1767507300
    """
    m = re.match(r"btc-updown-15m-(\d+)_", filename)
    if m:
        try:
            return int(m.group(1))
        except Exception:
            return None
    return None


def get_archive_path(window_start: int, base_archive: Path) -> Path:
    """
    根据window_start计算归档路径
    例如: 1767507300 -> real_archive/2026-01/
    """
    dt = datetime.fromtimestamp(window_start, tz=timezone.utc)
    year_month = dt.strftime("%Y-%m")
    return base_archive / year_month


def compress_file(src: Path, dst: Path) -> None:
    """使用gzip压缩文件"""
    with open(src, "rb") as f_in:
        with gzip.open(dst, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)


def archive_old_files(
    hot_dir: Path,
    archive_dir: Path,
    days_threshold: int = 7,
    compress: bool = False,
    dry_run: bool = False,
) -> dict:
    """
    归档旧文件
    
    返回统计信息字典
    """
    now = time.time()
    threshold_seconds = days_threshold * 24 * 3600
    
    stats = {
        "scanned": 0,
        "archived": 0,
        "skipped": 0,
        "errors": 0,
        "size_before": 0,
        "size_after": 0,
    }
    
    # 扫描hot目录
    pattern = "btc-updown-15m-*.jsonl"
    files = sorted(hot_dir.glob(pattern))
    
    stats["scanned"] = len(files)
    
    for file_path in files:
        try:
            # 解析文件名获取时间戳
            window_start = parse_window_start_from_filename(file_path.name)
            if window_start is None:
                print(f"  ⚠️  跳过无法解析的文件: {file_path.name}")
                stats["skipped"] += 1
                continue
            
            # 检查是否超过阈值
            age_seconds = now - window_start
            if age_seconds < threshold_seconds:
                continue  # 还不够老，跳过
            
            age_days = age_seconds / 86400
            
            # 计算目标路径
            archive_month_dir = get_archive_path(window_start, archive_dir)
            
            if compress:
                target_filename = file_path.name + ".gz"
            else:
                target_filename = file_path.name
            
            target_path = archive_month_dir / target_filename
            
            # 获取文件大小
            file_size = file_path.stat().st_size
            stats["size_before"] += file_size
            
            if dry_run:
                print(f"  [DRY-RUN] 将归档: {file_path.name} -> {target_path}")
                print(f"            年龄: {age_days:.1f}天, 大小: {file_size/1024:.1f}KB")
                stats["archived"] += 1
                stats["size_after"] += file_size if not compress else int(file_size * 0.3)
                continue
            
            # 创建目标目录
            archive_month_dir.mkdir(parents=True, exist_ok=True)
            
            # 执行归档
            if compress:
                print(f"  📦 压缩归档: {file_path.name} -> {target_path.relative_to(archive_dir)}")
                compress_file(file_path, target_path)
            else:
                print(f"  📁 移动归档: {file_path.name} -> {target_path.relative_to(archive_dir)}")
                shutil.move(str(file_path), str(target_path))
            
            target_size = target_path.stat().st_size
            stats["size_after"] += target_size
            stats["archived"] += 1
            
            compression_ratio = (target_size / file_size * 100) if compress else 100
            print(f"      年龄: {age_days:.1f}天, 原始: {file_size/1024:.1f}KB, "
                  f"归档: {target_size/1024:.1f}KB ({compression_ratio:.1f}%)")
            
        except Exception as e:
            print(f"  ❌ 处理文件出错 {file_path.name}: {e}")
            stats["errors"] += 1
    
    return stats


def main():
    parser = argparse.ArgumentParser(
        description="归档Polymarket旧数据到按月目录"
    )
    parser.add_argument(
        "--hot-dir",
        type=Path,
        default=Path.home() / "polymarket" / "real_hot",
        help="热数据目录（默认: ~/polymarket/real_hot）",
    )
    parser.add_argument(
        "--archive-dir",
        type=Path,
        default=Path.home() / "polymarket" / "real_archive",
        help="归档目录（默认: ~/polymarket/real_archive）",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="归档阈值天数（默认: 30天）",
    )
    parser.add_argument(
        "--compress",
        action="store_true",
        help="使用gzip压缩归档文件",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅模拟运行，不实际移动文件",
    )
    
    args = parser.parse_args()
    
    print("=" * 60)
    print("Polymarket数据自动归档")
    print("=" * 60)
    print(f"热数据目录: {args.hot_dir}")
    print(f"归档目录: {args.archive_dir}")
    print(f"归档阈值: {args.days}天前的数据")
    print(f"压缩模式: {'开启' if args.compress else '关闭'}")
    print(f"运行模式: {'模拟运行（不实际操作）' if args.dry_run else '实际执行'}")
    print()
    
    # 检查目录
    if not args.hot_dir.exists():
        print(f"❌ 错误: 热数据目录不存在: {args.hot_dir}")
        return 1
    
    # 执行归档
    start_time = time.time()
    stats = archive_old_files(
        hot_dir=args.hot_dir,
        archive_dir=args.archive_dir,
        days_threshold=args.days,
        compress=args.compress,
        dry_run=args.dry_run,
    )
    elapsed = time.time() - start_time
    
    # 打印统计
    print()
    print("=" * 60)
    print("归档统计")
    print("=" * 60)
    print(f"扫描文件数: {stats['scanned']}")
    print(f"归档文件数: {stats['archived']}")
    print(f"跳过文件数: {stats['skipped']}")
    print(f"错误数: {stats['errors']}")
    print(f"原始大小: {stats['size_before'] / 1024 / 1024:.2f} MB")
    print(f"归档大小: {stats['size_after'] / 1024 / 1024:.2f} MB")
    if stats['size_before'] > 0:
        ratio = stats['size_after'] / stats['size_before'] * 100
        print(f"压缩比: {ratio:.1f}%")
    print(f"耗时: {elapsed:.2f}秒")
    print()
    
    if args.dry_run:
        print("ℹ️  这是模拟运行，未实际移动文件")
        print("   移除 --dry-run 参数以执行实际归档")
    
    return 0


if __name__ == "__main__":
    exit(main())

