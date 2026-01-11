#!/usr/bin/env python3
"""
SSHFS挂载监控和自动修复脚本

功能：
1. 定期检查SSHFS挂载状态
2. 断线自动重连
3. 监控本地缓存大小
4. 统计热/冷数据访问频率
5. 可选webhook通知

用法：
  # 运行一次检查
  python3 monitor_sync.py --check
  
  # 持续监控（每分钟检查）
  python3 monitor_sync.py --daemon
  
  # 仅重新挂载
  python3 monitor_sync.py --remount
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Optional


class SSHFSMonitor:
    """SSHFS挂载监控器"""
    
    def __init__(
        self,
        mount_point: Optional[Path] = None,
        mount_script: Optional[Path] = None,
        webhook_url: Optional[str] = None,
    ):
        workspace = Path.home() / "Desktop" / "workspace" / "polymarket"
        self.mount_point = mount_point or (workspace / "real_hot")
        self.mount_script = mount_script or (Path.home() / ".local" / "bin" / "mount_polymarket.sh")
        self.webhook_url = webhook_url or os.environ.get("MONITOR_WEBHOOK_URL")
        
        self.cache_dir = workspace / "real_cache"
        self.log_file = workspace / "logs" / "monitor.log"
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
    
    def log(self, message: str, level: str = "INFO") -> None:
        """写日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{level}] {message}"
        print(log_line)
        
        with open(self.log_file, "a") as f:
            f.write(log_line + "\n")
    
    def send_notification(self, title: str, message: str) -> None:
        """发送webhook通知（可选）"""
        if not self.webhook_url:
            return
        
        try:
            import requests
            payload = {
                "title": title,
                "message": message,
                "timestamp": datetime.now().isoformat(),
            }
            requests.post(self.webhook_url, json=payload, timeout=5)
        except Exception as e:
            self.log(f"发送通知失败: {e}", "WARN")
    
    def is_mounted(self) -> bool:
        """检查挂载状态"""
        if not self.mount_point.exists():
            return False
        
        # 使用mount命令检查
        try:
            result = subprocess.run(
                ["mount"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(self.mount_point) in result.stdout
        except Exception as e:
            self.log(f"检查挂载状态出错: {e}", "ERROR")
            return False
    
    def can_access(self) -> bool:
        """检查是否可以访问挂载点"""
        try:
            # 尝试列出目录
            list(self.mount_point.iterdir())
            return True
        except Exception as e:
            self.log(f"无法访问挂载点: {e}", "WARN")
            return False
    
    def remount(self) -> bool:
        """重新挂载"""
        self.log("尝试重新挂载...")
        
        # 先卸载
        try:
            subprocess.run(
                ["umount", str(self.mount_point)],
                capture_output=True,
                timeout=10,
            )
        except Exception:
            pass  # 忽略卸载失败
        
        # 重新挂载
        if not self.mount_script.exists():
            self.log(f"挂载脚本不存在: {self.mount_script}", "ERROR")
            return False
        
        try:
            result = subprocess.run(
                [str(self.mount_script)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            
            if result.returncode == 0:
                self.log("重新挂载成功", "INFO")
                self.send_notification("SSHFS重新挂载", "挂载点已恢复")
                return True
            else:
                self.log(f"重新挂载失败: {result.stderr}", "ERROR")
                self.send_notification("SSHFS重新挂载失败", result.stderr)
                return False
                
        except Exception as e:
            self.log(f"执行挂载脚本出错: {e}", "ERROR")
            return False
    
    def check_and_fix(self) -> dict:
        """检查并修复挂载"""
        status = {
            "mounted": False,
            "accessible": False,
            "remounted": False,
            "timestamp": datetime.now().isoformat(),
        }
        
        # 1. 检查是否挂载
        status["mounted"] = self.is_mounted()
        
        if not status["mounted"]:
            self.log("挂载点未挂载", "WARN")
            status["remounted"] = self.remount()
            status["mounted"] = self.is_mounted()
        
        # 2. 检查是否可访问
        if status["mounted"]:
            status["accessible"] = self.can_access()
            
            if not status["accessible"]:
                self.log("挂载点不可访问，尝试重新挂载", "WARN")
                status["remounted"] = self.remount()
                status["accessible"] = self.can_access()
        
        # 3. 最终状态
        if status["mounted"] and status["accessible"]:
            self.log("挂载状态正常", "INFO")
        else:
            self.log("挂载异常！", "ERROR")
            self.send_notification("SSHFS挂载异常", "请检查网络连接和VPS状态")
        
        return status
    
    def get_cache_stats(self) -> dict:
        """获取缓存统计"""
        total_size = 0
        file_count = 0
        
        if self.cache_dir.exists():
            for file_path in self.cache_dir.rglob("*.jsonl*"):
                try:
                    total_size += file_path.stat().st_size
                    file_count += 1
                except Exception:
                    pass
        
        return {
            "cache_dir": str(self.cache_dir),
            "file_count": file_count,
            "total_size_mb": total_size / 1024 / 1024,
        }
    
    def cleanup_cache(self, keep_size_mb: int = 1024) -> dict:
        """清理缓存"""
        self.log(f"开始清理缓存（保留 {keep_size_mb}MB）...")
        
        if not self.cache_dir.exists():
            return {"cleaned_count": 0, "cleaned_size_mb": 0}
        
        # 获取所有文件及修改时间
        files = []
        total_size = 0
        
        for file_path in self.cache_dir.rglob("*.jsonl*"):
            try:
                stat = file_path.stat()
                files.append((file_path, stat.st_size, stat.st_mtime))
                total_size += stat.st_size
            except Exception:
                pass
        
        # 按修改时间排序（最旧的在前）
        files.sort(key=lambda x: x[2])
        
        keep_size_bytes = keep_size_mb * 1024 * 1024
        cleaned_count = 0
        cleaned_size = 0
        
        # 删除最旧的文件直到低于阈值
        for file_path, size, mtime in files:
            if total_size - cleaned_size <= keep_size_bytes:
                break
            
            try:
                file_path.unlink()
                cleaned_size += size
                cleaned_count += 1
            except Exception as e:
                self.log(f"删除文件失败 {file_path}: {e}", "WARN")
        
        self.log(f"清理完成: 删除 {cleaned_count} 个文件，释放 {cleaned_size / 1024 / 1024:.1f}MB")
        
        return {
            "cleaned_count": cleaned_count,
            "cleaned_size_mb": cleaned_size / 1024 / 1024,
        }
    
    def run_daemon(self, interval_seconds: int = 60) -> None:
        """持续监控模式"""
        self.log(f"启动监控守护进程（检查间隔: {interval_seconds}秒）...")
        
        while True:
            try:
                # 检查挂载
                status = self.check_and_fix()
                
                # 检查缓存（每10次检查一次）
                if int(time.time()) % (interval_seconds * 10) == 0:
                    cache_stats = self.get_cache_stats()
                    self.log(f"缓存统计: {cache_stats['file_count']} 文件, "
                            f"{cache_stats['total_size_mb']:.1f}MB")
                    
                    # 如果缓存超过1.2GB，清理到1GB
                    if cache_stats['total_size_mb'] > 1200:
                        self.cleanup_cache(keep_size_mb=1024)
                
                time.sleep(interval_seconds)
                
            except KeyboardInterrupt:
                self.log("监控守护进程已停止", "INFO")
                break
            except Exception as e:
                self.log(f"监控出错: {e}", "ERROR")
                time.sleep(interval_seconds)


def main():
    parser = argparse.ArgumentParser(description="SSHFS挂载监控和维护")
    
    parser.add_argument(
        "--check",
        action="store_true",
        help="运行一次检查并修复",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="持续监控模式",
    )
    parser.add_argument(
        "--remount",
        action="store_true",
        help="强制重新挂载",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="清理缓存",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="监控间隔（秒，默认60）",
    )
    
    args = parser.parse_args()
    
    monitor = SSHFSMonitor()
    
    if args.remount:
        success = monitor.remount()
        return 0 if success else 1
    
    elif args.cleanup:
        stats = monitor.cleanup_cache()
        print(f"清理完成: 删除 {stats['cleaned_count']} 个文件, "
              f"释放 {stats['cleaned_size_mb']:.1f}MB")
        return 0
    
    elif args.daemon:
        monitor.run_daemon(interval_seconds=args.interval)
        return 0
    
    elif args.check:
        status = monitor.check_and_fix()
        cache_stats = monitor.get_cache_stats()
        
        print()
        print("=== 挂载状态 ===")
        print(f"已挂载: {status['mounted']}")
        print(f"可访问: {status['accessible']}")
        if status['remounted']:
            print(f"已重新挂载: True")
        
        print()
        print("=== 缓存统计 ===")
        print(f"文件数: {cache_stats['file_count']}")
        print(f"总大小: {cache_stats['total_size_mb']:.2f} MB")
        
        return 0 if (status['mounted'] and status['accessible']) else 1
    
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    exit(main())

