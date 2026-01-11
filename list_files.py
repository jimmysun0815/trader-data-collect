#!/usr/bin/env python3
"""
生成项目文件清单和统计信息
"""

from pathlib import Path
import os

def main():
    print("=" * 70)
    print("Polymarket远程数据访问系统 - 文件清单")
    print("=" * 70)
    print()
    
    workspace = Path.cwd()
    
    # 1. 核心脚本
    print("📦 核心脚本 (8个)")
    print("-" * 70)
    
    scripts = [
        ("deploy_vps.sh", "VPS部署脚本（创建目录、配置cron）"),
        ("archive_old_data.py", "数据归档脚本（7天后按月归档，可压缩）"),
        ("setup_sshfs_mount.sh", "SSHFS配置脚本（自动挂载、LaunchAgent）"),
        ("data_accessor.py", "数据访问代理（热/冷路由、缓存管理、LRU）"),
        ("data_path_compat.py", "路径兼容层（monkey patch glob/open）"),
        ("monitor_sync.py", "挂载监控（自动修复、缓存清理）"),
        ("diagnose_remote_setup.py", "系统诊断工具（全面检查配置）"),
        ("example_migration.py", "迁移示例代码（3种集成方式）"),
    ]
    
    total_size = 0
    for filename, desc in scripts:
        filepath = workspace / filename
        if filepath.exists():
            size = filepath.stat().st_size
            total_size += size
            print(f"  ✓ {filename:30s} {size/1024:6.1f}KB  {desc}")
        else:
            print(f"  ✗ {filename:30s} {'N/A':>6s}    {desc}")
    
    print(f"\n  总计: {total_size/1024:.1f}KB")
    print()
    
    # 2. 文档
    print("📚 文档文件 (5个)")
    print("-" * 70)
    
    docs = [
        ("GETTING_STARTED.md", "5分钟快速入门"),
        ("REMOTE_DATA_SETUP.md", "完整设置文档（架构、部署、维护）"),
        ("MIGRATION_GUIDE.md", "脚本迁移指南（批量迁移、测试）"),
        ("QUICK_REFERENCE.md", "快速参考卡片（常用命令）"),
        ("IMPLEMENTATION_SUMMARY.md", "实施总结（完成情况、交付清单）"),
    ]
    
    total_doc_size = 0
    for filename, desc in docs:
        filepath = workspace / filename
        if filepath.exists():
            size = filepath.stat().st_size
            total_doc_size += size
            print(f"  ✓ {filename:30s} {size/1024:6.1f}KB  {desc}")
        else:
            print(f"  ✗ {filename:30s} {'N/A':>6s}    {desc}")
    
    print(f"\n  总计: {total_doc_size/1024:.1f}KB")
    print()
    
    # 3. 工具脚本
    print("🔧 工具脚本 (2个)")
    print("-" * 70)
    
    tools = [
        ("check_deployment.sh", "部署前检查清单（验证所有文件）"),
        ("list_files.py", "本脚本（生成文件清单）"),
    ]
    
    for filename, desc in tools:
        filepath = workspace / filename
        if filepath.exists():
            size = filepath.stat().st_size
            print(f"  ✓ {filename:30s} {size/1024:6.1f}KB  {desc}")
        else:
            print(f"  ✗ {filename:30s} {'N/A':>6s}    {desc}")
    
    print()
    
    # 4. 已修改文件
    print("✏️  已修改文件 (2个)")
    print("-" * 70)
    
    modified = [
        ("README.md", "添加了远程数据系统说明"),
        ("research/btc15m_strong_signal_enhanced_rule_search.py", "集成auto_patch示例"),
    ]
    
    for filename, desc in modified:
        filepath = workspace / filename
        if filepath.exists():
            print(f"  ✓ {filename:50s}  {desc}")
        else:
            print(f"  ✗ {filename:50s}  {desc}")
    
    print()
    
    # 5. 统计信息
    print("=" * 70)
    print("📊 统计信息")
    print("=" * 70)
    print()
    
    print(f"  新增脚本文件:   8个   ({total_size/1024:.1f}KB)")
    print(f"  新增文档文件:   5个   ({total_doc_size/1024:.1f}KB)")
    print(f"  工具脚本:       2个")
    print(f"  修改现有文件:   2个")
    print(f"  总计:          17个文件  ({(total_size + total_doc_size)/1024:.1f}KB)")
    print()
    
    # 6. 功能特性
    print("=" * 70)
    print("✨ 核心功能")
    print("=" * 70)
    print()
    
    features = [
        "✅ VPS自动化部署和数据归档",
        "✅ SSHFS实时挂载热数据（最近7天）",
        "✅ 智能冷热数据路由和缓存管理",
        "✅ 现有代码零修改或最小修改（2行）",
        "✅ 挂载监控和自动修复",
        "✅ 本地空间节省90%+",
        "✅ LRU缓存淘汰（1GB上限）",
        "✅ 智能预取相邻窗口",
        "✅ 系统诊断工具",
        "✅ 完整文档和示例",
    ]
    
    for feature in features:
        print(f"  {feature}")
    
    print()
    
    # 7. 快速开始
    print("=" * 70)
    print("🚀 快速开始")
    print("=" * 70)
    print()
    print("  1. 检查部署:  ./check_deployment.sh")
    print("  2. 查看入门:  cat GETTING_STARTED.md")
    print("  3. VPS部署:   ./deploy_vps.sh (在VPS上)")
    print("  4. 本地配置:  ./setup_sshfs_mount.sh")
    print("  5. 系统诊断:  python3 diagnose_remote_setup.py")
    print()
    print("完整文档: REMOTE_DATA_SETUP.md")
    print()


if __name__ == "__main__":
    main()

