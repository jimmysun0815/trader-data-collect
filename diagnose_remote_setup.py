#!/usr/bin/env python3
"""
远程数据访问系统 - 快速诊断工具

检查系统各组件是否正常工作
"""

import os
import subprocess
import sys
from pathlib import Path


def print_section(title: str):
    """打印分节标题"""
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def check_mark(condition: bool, message: str):
    """打印检查结果"""
    icon = "✓" if condition else "✗"
    color = "\033[92m" if condition else "\033[91m"
    reset = "\033[0m"
    print(f"{color}{icon}{reset} {message}")
    return condition


def main():
    print("Polymarket远程数据访问系统 - 诊断工具")
    
    all_ok = True
    
    # 1. 检查环境变量
    print_section("环境配置")
    
    vps_user = os.environ.get("VPS_USER")
    vps_host = os.environ.get("VPS_HOST")
    
    all_ok &= check_mark(bool(vps_user), f"VPS_USER: {vps_user or '未设置'}")
    all_ok &= check_mark(bool(vps_host), f"VPS_HOST: {vps_host or '未设置'}")
    
    # 2. 检查目录结构
    print_section("本地目录结构")
    
    workspace = Path.home() / "Desktop" / "workspace" / "polymarket"
    hot_dir = workspace / "real_hot"
    cache_dir = workspace / "real_cache"
    real_link = workspace / "real"
    
    all_ok &= check_mark(hot_dir.exists(), f"热数据目录: {hot_dir}")
    all_ok &= check_mark(cache_dir.exists(), f"缓存目录: {cache_dir}")
    
    if real_link.exists():
        if real_link.is_symlink():
            target = real_link.resolve()
            check_mark(True, f"real/ 符号链接 -> {target}")
        else:
            check_mark(False, f"real/ 是目录而非符号链接")
    else:
        check_mark(False, "real/ 不存在")
    
    # 3. 检查SSHFS挂载
    print_section("SSHFS挂载状态")
    
    try:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        is_mounted = str(hot_dir) in result.stdout
        all_ok &= check_mark(is_mounted, f"SSHFS挂载: {hot_dir}")
        
        if is_mounted:
            # 尝试访问
            try:
                files = list(hot_dir.glob("*.jsonl"))
                check_mark(True, f"可访问挂载点: 找到 {len(files)} 个文件")
            except Exception as e:
                check_mark(False, f"无法访问挂载点: {e}")
                all_ok = False
        
    except Exception as e:
        check_mark(False, f"检查挂载出错: {e}")
        all_ok = False
    
    # 4. 检查SSH连接
    print_section("VPS连接")
    
    if vps_user and vps_host:
        try:
            result = subprocess.run(
                ["ssh", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
                 f"{vps_user}@{vps_host}", "echo OK"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            ssh_ok = result.returncode == 0
            all_ok &= check_mark(ssh_ok, f"SSH连接: {vps_user}@{vps_host}")
            
            if ssh_ok:
                # 检查远程目录
                result = subprocess.run(
                    ["ssh", f"{vps_user}@{vps_host}",
                     "ls ~/polymarket/real_hot ~/polymarket/real_archive 2>/dev/null | wc -l"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                if result.returncode == 0:
                    file_count = result.stdout.strip()
                    check_mark(True, f"VPS目录可访问，包含文件数: {file_count}")
                
        except Exception as e:
            check_mark(False, f"SSH连接失败: {e}")
            all_ok = False
    else:
        check_mark(False, "跳过SSH检查（未配置VPS信息）")
        all_ok = False
    
    # 5. 检查Python模块
    print_section("Python模块")
    
    try:
        from data_accessor import DataAccessor
        check_mark(True, "data_accessor模块")
        
        from data_path_compat import auto_patch
        check_mark(True, "data_path_compat模块")
        
        # 测试数据访问器
        accessor = DataAccessor()
        windows = accessor.list_all_windows()
        check_mark(len(windows) > 0, f"数据访问器: 找到 {len(windows)} 个窗口")
        
        # 缓存统计
        stats = accessor.get_cache_stats()
        print(f"  缓存: {stats['file_count']} 文件, {stats['total_size_mb']:.2f}MB")
        
    except Exception as e:
        check_mark(False, f"Python模块测试失败: {e}")
        all_ok = False
    
    # 6. 检查监控脚本
    print_section("监控脚本")
    
    monitor_script = workspace / "monitor_sync.py"
    check_mark(monitor_script.exists(), f"监控脚本: {monitor_script}")
    
    mount_script = Path.home() / ".local" / "bin" / "mount_polymarket.sh"
    check_mark(mount_script.exists(), f"挂载脚本: {mount_script}")
    
    # 总结
    print_section("诊断总结")
    
    if all_ok:
        print("✅ 所有检查通过！系统运行正常")
        print()
        print("下一步:")
        print("  1. 在VPS上启动数据采集脚本")
        print("  2. 在分析脚本中添加 auto_patch()")
        print("  3. 运行监控: python3 monitor_sync.py --daemon")
        return 0
    else:
        print("⚠️  部分检查未通过，请修复上述问题")
        print()
        print("常见问题:")
        print("  - 环境变量未设置: 添加到 ~/.zshrc")
        print("  - SSHFS未挂载: 运行 ./setup_sshfs_mount.sh")
        print("  - SSH连接失败: 检查密钥配置和VPS状态")
        print()
        print("详细文档: REMOTE_DATA_SETUP.md")
        return 1


if __name__ == "__main__":
    try:
        exit(main())
    except KeyboardInterrupt:
        print("\n中断")
        exit(1)

