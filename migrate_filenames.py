#!/usr/bin/env python3
"""
文件名迁移脚本 - 将带启动时间戳的文件重命名并合并

旧格式: btc-updown-15m-1768110300_20260110_214626.jsonl
新格式: btc-updown-15m-1768110300.jsonl

功能：
1. 扫描real_hot/目录下所有.jsonl和.csv文件
2. 识别带启动时间戳的旧格式文件
3. 按market_slug分组
4. 合并同一窗口的多个文件到新文件
5. 保留原文件（添加.old后缀）以防万一

用法：
  python3 migrate_filenames.py [--dry-run] [--hot-dir PATH]
"""

import argparse
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Optional


def parse_filename(filename: str) -> Optional[tuple[str, str, str]]:
    """
    解析文件名，提取market_slug和文件类型
    
    返回: (market_slug, timestamp_suffix, extension) or None
    
    示例:
      btc-updown-15m-1768110300_20260110_214626.jsonl
      -> ("btc-updown-15m-1768110300", "20260110_214626", "jsonl")
      
      bitcoin-up-or-down-january-11-12am-et_20260110_214823.jsonl
      -> ("bitcoin-up-or-down-january-11-12am-et", "20260110_214823", "jsonl")
      
      cex_btc_20260111_00-12.csv (新格式，不需要迁移)
      -> None
    """
    # 匹配旧格式: market_slug_YYYYMMDD_HHMMSS.ext
    pattern = r'^(.+?)_(\d{8}_\d{6})\.(jsonl|csv)$'
    match = re.match(pattern, filename)
    
    if match:
        market_slug = match.group(1)
        timestamp_suffix = match.group(2)
        extension = match.group(3)
        
        # 排除CEX文件（已经是正确格式）
        # CEX格式: cex_btc_20260111_00-12.csv
        if market_slug.startswith('cex_'):
            # 检查是否是CEX的12小时分割格式
            if re.match(r'^cex_[a-z]+_\d{8}$', market_slug):
                # 这是正常的CEX文件，不需要迁移
                return None
        
        return (market_slug, timestamp_suffix, extension)
    
    return None


def merge_jsonl_files(input_files: list[Path], output_file: Path):
    """合并多个JSONL文件"""
    print(f"    合并 {len(input_files)} 个文件 -> {output_file.name}")
    
    total_lines = 0
    with open(output_file, 'w') as outf:
        for input_file in sorted(input_files):
            with open(input_file, 'r') as inf:
                lines = inf.readlines()
                outf.writelines(lines)
                total_lines += len(lines)
    
    print(f"      写入 {total_lines} 行")


def merge_csv_files(input_files: list[Path], output_file: Path):
    """合并多个CSV文件（跳过重复的header）"""
    print(f"    合并 {len(input_files)} 个文件 -> {output_file.name}")
    
    total_lines = 0
    header_written = False
    
    with open(output_file, 'w') as outf:
        for input_file in sorted(input_files):
            with open(input_file, 'r') as inf:
                lines = inf.readlines()
                
                if not lines:
                    continue
                
                # 第一个文件写入header
                if not header_written:
                    outf.writelines(lines)
                    total_lines += len(lines)
                    header_written = True
                else:
                    # 后续文件跳过header（假设第一行是header）
                    if len(lines) > 1:
                        outf.writelines(lines[1:])
                        total_lines += len(lines) - 1
    
    print(f"      写入 {total_lines} 行")


def migrate_files(hot_dir: Path, dry_run: bool = False):
    """迁移文件名"""
    print(f"扫描目录: {hot_dir}")
    print(f"模式: {'模拟运行' if dry_run else '实际执行'}")
    print()
    
    # 扫描所有文件
    all_files = list(hot_dir.glob("*.jsonl")) + list(hot_dir.glob("*.csv"))
    print(f"发现 {len(all_files)} 个文件")
    print()
    
    # 按market_slug分组
    files_by_slug = defaultdict(list)
    skipped_files = []
    
    for file_path in all_files:
        parsed = parse_filename(file_path.name)
        if parsed:
            market_slug, timestamp_suffix, extension = parsed
            files_by_slug[(market_slug, extension)].append(file_path)
        else:
            skipped_files.append(file_path.name)
    
    print(f"需要迁移的文件组: {len(files_by_slug)}")
    print(f"已是新格式（跳过）: {len(skipped_files)}")
    print()
    
    if skipped_files:
        print("跳过的文件（已是新格式）:")
        for name in sorted(skipped_files)[:10]:
            print(f"  - {name}")
        if len(skipped_files) > 10:
            print(f"  ... 还有 {len(skipped_files) - 10} 个")
        print()
    
    if not files_by_slug:
        print("✅ 没有需要迁移的文件！")
        return
    
    # 处理每组文件
    print("=" * 70)
    print("开始迁移...")
    print("=" * 70)
    print()
    
    migrated_count = 0
    for (market_slug, extension), input_files in sorted(files_by_slug.items()):
        new_filename = f"{market_slug}.{extension}"
        output_file = hot_dir / new_filename
        
        print(f"[{migrated_count + 1}/{len(files_by_slug)}] 处理: {market_slug}.{extension}")
        print(f"  发现 {len(input_files)} 个旧文件:")
        for f in input_files:
            print(f"    - {f.name}")
        
        if dry_run:
            print(f"  [模拟] 将合并到: {new_filename}")
            print()
            migrated_count += 1
            continue
        
        # 如果新文件已存在，先备份
        if output_file.exists():
            backup_file = hot_dir / f"{new_filename}.backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            print(f"    目标文件已存在，备份到: {backup_file.name}")
            shutil.move(str(output_file), str(backup_file))
        
        # 合并文件
        try:
            if extension == 'jsonl':
                merge_jsonl_files(input_files, output_file)
            elif extension == 'csv':
                merge_csv_files(input_files, output_file)
            
            # 重命名旧文件（添加.old后缀）
            for old_file in input_files:
                old_backup = hot_dir / f"{old_file.name}.old"
                print(f"    备份: {old_file.name} -> {old_backup.name}")
                shutil.move(str(old_file), str(old_backup))
            
            print(f"  ✅ 完成: {new_filename}")
            print()
            migrated_count += 1
            
        except Exception as e:
            print(f"  ❌ 错误: {e}")
            print()
    
    print("=" * 70)
    print(f"迁移完成: {migrated_count}/{len(files_by_slug)} 组文件")
    print("=" * 70)
    print()
    
    if not dry_run:
        print("注意:")
        print("- 旧文件已重命名为 .old 后缀，请验证新文件正常后再删除")
        print("- 删除旧文件命令: rm /path/to/real_hot/*.old")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="迁移Polymarket数据文件名（移除启动时间戳）"
    )
    parser.add_argument(
        "--hot-dir",
        type=Path,
        default=Path("/home/ubuntu/trader-data-collect/real_hot"),
        help="热数据目录（默认: /home/ubuntu/trader-data-collect/real_hot）",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅模拟运行，不实际修改文件",
    )
    
    args = parser.parse_args()
    
    print("=" * 70)
    print("Polymarket数据文件名迁移工具")
    print("=" * 70)
    print()
    
    if not args.hot_dir.exists():
        print(f"❌ 错误: 目录不存在: {args.hot_dir}")
        return 1
    
    try:
        migrate_files(args.hot_dir, args.dry_run)
        
        if args.dry_run:
            print("提示: 移除 --dry-run 参数以执行实际迁移")
        
        return 0
        
    except Exception as e:
        print(f"❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())

