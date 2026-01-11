"""
Polymarket数据访问代理层

功能：
1. 统一访问热数据（SSHFS挂载的real_hot/）和冷数据（归档的real_archive/）
2. 自动路由数据请求到正确的存储位置
3. 按需拉取冷数据到本地缓存
4. LRU缓存管理（限制本地缓存大小）
5. 智能预取相邻窗口数据

用法示例：
    from data_accessor import DataAccessor
    
    # 初始化（自动检测路径）
    accessor = DataAccessor()
    
    # 获取单个窗口数据文件路径
    path = accessor.get_window_jsonl(1767507300)
    with open(path) as f:
        for line in f:
            data = json.loads(line)
    
    # 列出所有可用窗口
    windows = accessor.list_all_windows()
    
    # 批量获取窗口（自动预取）
    for ws in windows:
        path = accessor.get_window_jsonl(ws)
        # 处理数据...

或者使用便捷函数自动设置全局路径映射：
    from data_accessor import setup_data_paths
    setup_data_paths()
    
    # 之后所有对 real/ 的访问会自动路由到 real_hot + 缓存
"""

from __future__ import annotations

import glob as _builtin_glob
import json
import os
import re
import subprocess
import time
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional


class DataAccessor:
    """数据访问代理"""
    
    def __init__(
        self,
        hot_dir: Optional[Path] = None,
        cache_dir: Optional[Path] = None,
        vps_user: Optional[str] = None,
        vps_host: Optional[str] = None,
        vps_archive_path: Optional[str] = None,
        cache_max_size_mb: int = 1024,
    ):
        """
        初始化数据访问器
        
        参数：
            hot_dir: 本地热数据目录（SSHFS挂载点），默认 ~/Desktop/workspace/polymarket/real_hot
            cache_dir: 本地冷数据缓存目录，默认 ~/Desktop/workspace/polymarket/real_cache
            vps_user: VPS用户名（从环境变量VPS_USER读取）
            vps_host: VPS地址（从环境变量VPS_HOST读取）
            vps_archive_path: VPS归档目录路径（默认 ~/polymarket/real_archive）
            cache_max_size_mb: 本地缓存最大大小（MB）
        """
        # 设置目录
        workspace = Path.home() / "Desktop" / "workspace" / "polymarket"
        self.hot_dir = hot_dir or (workspace / "real_hot")
        self.cache_dir = cache_dir or (workspace / "real_cache")
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        
        # VPS配置
        self.vps_user = vps_user or os.environ.get("VPS_USER")
        self.vps_host = vps_host or os.environ.get("VPS_HOST")
        self.vps_archive_path = vps_archive_path or "~/polymarket/real_archive"
        
        # 缓存配置
        self.cache_max_size_bytes = cache_max_size_mb * 1024 * 1024
        self.cache_access_log: OrderedDict[str, float] = OrderedDict()  # filename -> last_access_time
        
        # 预取配置
        self.prefetch_enabled = True
        self.prefetch_count = 3  # 预取相邻窗口数量
        
    def parse_window_start_from_filename(self, filename: str) -> Optional[int]:
        """从文件名提取window_start时间戳"""
        m = re.match(r"btc-updown-15m-(\d+)_", filename)
        if m:
            try:
                return int(m.group(1))
            except Exception:
                return None
        return None
    
    def get_archive_subpath(self, window_start: int) -> str:
        """计算归档子路径（年月目录）"""
        dt = datetime.fromtimestamp(window_start, tz=timezone.utc)
        return dt.strftime("%Y-%m")
    
    def get_window_jsonl(self, window_start: int, prefetch: bool = True) -> Path:
        """
        获取指定窗口的JSONL文件路径
        
        返回本地可访问的文件路径（热数据/缓存/新拉取）
        """
        # 1. 先查热数据（SSHFS挂载）
        pattern = f"btc-updown-15m-{window_start}_*.jsonl"
        hot_matches = list(self.hot_dir.glob(pattern))
        if hot_matches:
            hot_path = hot_matches[0]
            # 更新访问日志
            self._update_access_log(hot_path.name)
            
            # 触发预取
            if prefetch and self.prefetch_enabled:
                self._prefetch_neighbors(window_start)
            
            return hot_path
        
        # 2. 查本地缓存
        year_month = self.get_archive_subpath(window_start)
        cache_month_dir = self.cache_dir / year_month
        
        if cache_month_dir.exists():
            cache_matches = list(cache_month_dir.glob(pattern))
            if cache_matches:
                cache_path = cache_matches[0]
                # 更新访问日志
                self._update_access_log(cache_path.name)
                
                # 触发预取
                if prefetch and self.prefetch_enabled:
                    self._prefetch_neighbors(window_start)
                
                return cache_path
        
        # 3. 从VPS拉取到缓存
        print(f"  🌐 从VPS拉取冷数据: window_start={window_start}")
        fetched_path = self._fetch_from_vps(window_start, year_month)
        
        if fetched_path:
            # 触发预取
            if prefetch and self.prefetch_enabled:
                self._prefetch_neighbors(window_start)
            
            return fetched_path
        
        raise FileNotFoundError(
            f"无法找到窗口数据: window_start={window_start}, "
            f"已查找: 热数据({self.hot_dir}), 缓存({cache_month_dir}), VPS归档"
        )
    
    def _fetch_from_vps(self, window_start: int, year_month: str) -> Optional[Path]:
        """从VPS拉取归档文件到本地缓存"""
        if not self.vps_user or not self.vps_host:
            print(f"  ⚠️  未配置VPS连接信息（VPS_USER, VPS_HOST），无法拉取冷数据")
            return None
        
        # 创建缓存目录
        cache_month_dir = self.cache_dir / year_month
        cache_month_dir.mkdir(parents=True, exist_ok=True)
        
        # 构建远程路径pattern
        pattern = f"btc-updown-15m-{window_start}_*.jsonl*"
        remote_path = f"{self.vps_archive_path}/{year_month}/{pattern}"
        
        try:
            # 先列出远程文件
            remote_spec = f"{self.vps_user}@{self.vps_host}:{remote_path}"
            result = subprocess.run(
                ["ssh", f"{self.vps_user}@{self.vps_host}", f"ls {remote_path}"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            
            if result.returncode != 0:
                print(f"  ⚠️  远程文件不存在: {remote_path}")
                return None
            
            remote_files = result.stdout.strip().split("\n")
            if not remote_files or not remote_files[0]:
                print(f"  ⚠️  远程文件列表为空")
                return None
            
            # 拉取第一个匹配的文件
            remote_file = remote_files[0]
            filename = Path(remote_file).name
            local_path = cache_month_dir / filename
            
            # 使用scp拉取
            remote_full = f"{self.vps_user}@{self.vps_host}:{remote_file}"
            result = subprocess.run(
                ["scp", "-q", remote_full, str(local_path)],
                capture_output=True,
                timeout=60,
            )
            
            if result.returncode != 0:
                print(f"  ❌ SCP拉取失败: {result.stderr.decode()}")
                return None
            
            # 如果是压缩文件，解压
            if filename.endswith(".gz"):
                print(f"  📦 解压文件: {filename}")
                import gzip
                import shutil
                
                uncompressed_path = cache_month_dir / filename[:-3]
                with gzip.open(local_path, "rb") as f_in:
                    with open(uncompressed_path, "wb") as f_out:
                        shutil.copyfileobj(f_in, f_out)
                
                # 删除压缩文件
                local_path.unlink()
                local_path = uncompressed_path
            
            print(f"  ✓ 已拉取到缓存: {local_path.name}")
            
            # 更新访问日志
            self._update_access_log(local_path.name)
            
            # 检查缓存大小，必要时清理
            self._cleanup_cache_if_needed()
            
            return local_path
            
        except subprocess.TimeoutExpired:
            print(f"  ⚠️  SSH/SCP超时")
            return None
        except Exception as e:
            print(f"  ❌ 拉取出错: {e}")
            return None
    
    def _prefetch_neighbors(self, window_start: int) -> None:
        """预取相邻窗口（后台异步，不阻塞）"""
        # 15分钟窗口，每个窗口900秒
        window_size = 900
        
        for i in range(1, self.prefetch_count + 1):
            next_window = window_start + i * window_size
            try:
                # 检查是否已在热数据或缓存中
                pattern = f"btc-updown-15m-{next_window}_*.jsonl"
                if list(self.hot_dir.glob(pattern)):
                    continue
                
                year_month = self.get_archive_subpath(next_window)
                cache_month_dir = self.cache_dir / year_month
                if cache_month_dir.exists() and list(cache_month_dir.glob(pattern)):
                    continue
                
                # 后台拉取（不打印详细日志）
                self._fetch_from_vps(next_window, year_month)
                
            except Exception:
                # 预取失败不影响主流程
                pass
    
    def _update_access_log(self, filename: str) -> None:
        """更新文件访问日志（LRU）"""
        # 移除旧记录（如果存在）
        if filename in self.cache_access_log:
            del self.cache_access_log[filename]
        
        # 添加新记录到末尾
        self.cache_access_log[filename] = time.time()
    
    def _cleanup_cache_if_needed(self) -> None:
        """检查缓存大小，超限时清理最旧的文件"""
        # 计算缓存总大小
        total_size = 0
        file_sizes = {}
        
        for file_path in self.cache_dir.rglob("btc-updown-15m-*.jsonl*"):
            size = file_path.stat().st_size
            total_size += size
            file_sizes[file_path.name] = (file_path, size)
        
        if total_size <= self.cache_max_size_bytes:
            return  # 未超限
        
        print(f"  🧹 缓存超限 ({total_size / 1024 / 1024:.1f}MB > {self.cache_max_size_bytes / 1024 / 1024:.1f}MB)，开始清理...")
        
        # 按访问时间排序（最旧的在前）
        sorted_files = sorted(
            self.cache_access_log.items(),
            key=lambda x: x[1],  # 按访问时间排序
        )
        
        cleaned_size = 0
        cleaned_count = 0
        
        for filename, _ in sorted_files:
            if total_size - cleaned_size <= self.cache_max_size_bytes:
                break
            
            if filename in file_sizes:
                file_path, size = file_sizes[filename]
                try:
                    file_path.unlink()
                    cleaned_size += size
                    cleaned_count += 1
                    del self.cache_access_log[filename]
                except Exception as e:
                    print(f"  ⚠️  删除文件失败 {filename}: {e}")
        
        print(f"  ✓ 清理完成: 删除{cleaned_count}个文件，释放{cleaned_size / 1024 / 1024:.1f}MB")
    
    def list_all_windows(self, use_cache: bool = True) -> list[int]:
        """
        列出所有可用的窗口时间戳
        
        返回排序后的window_start列表
        """
        windows = set()
        
        # 1. 扫描热数据
        if self.hot_dir.exists():
            for file_path in self.hot_dir.glob("btc-updown-15m-*.jsonl"):
                ws = self.parse_window_start_from_filename(file_path.name)
                if ws:
                    windows.add(ws)
        
        # 2. 扫描本地缓存
        if use_cache and self.cache_dir.exists():
            for file_path in self.cache_dir.rglob("btc-updown-15m-*.jsonl*"):
                ws = self.parse_window_start_from_filename(file_path.name)
                if ws:
                    windows.add(ws)
        
        # 3. 可选：列出VPS所有文件（需要SSH连接，较慢）
        # 暂时不实现，需要时可以添加
        
        return sorted(windows)
    
    def get_cache_stats(self) -> dict:
        """获取缓存统计信息"""
        total_size = 0
        file_count = 0
        
        for file_path in self.cache_dir.rglob("btc-updown-15m-*.jsonl*"):
            total_size += file_path.stat().st_size
            file_count += 1
        
        return {
            "cache_dir": str(self.cache_dir),
            "file_count": file_count,
            "total_size_mb": total_size / 1024 / 1024,
            "max_size_mb": self.cache_max_size_bytes / 1024 / 1024,
            "usage_percent": (total_size / self.cache_max_size_bytes * 100) if self.cache_max_size_bytes > 0 else 0,
        }


# 全局单例
_global_accessor: Optional[DataAccessor] = None


def get_accessor() -> DataAccessor:
    """获取全局数据访问器实例"""
    global _global_accessor
    if _global_accessor is None:
        _global_accessor = DataAccessor()
    return _global_accessor


def setup_data_paths() -> None:
    """
    设置全局路径映射，让现有代码无需修改即可使用数据代理
    
    调用此函数后，所有对 real/ 的访问会自动路由到 real_hot + 缓存
    """
    accessor = get_accessor()
    
    # 创建 real 符号链接指向 real_hot（如果不存在）
    workspace = Path.home() / "Desktop" / "workspace" / "polymarket"
    real_link = workspace / "real"
    
    # 如果 real 是目录，重命名为 real_backup
    if real_link.exists() and real_link.is_dir() and not real_link.is_symlink():
        backup_path = workspace / f"real_backup_{int(time.time())}"
        print(f"  ⚠️  将原有 real/ 目录备份到: {backup_path}")
        real_link.rename(backup_path)
    
    # 创建符号链接
    if not real_link.exists():
        real_link.symlink_to(accessor.hot_dir)
        print(f"  ✓ 创建符号链接: real/ -> {accessor.hot_dir}")
    
    print(f"  ✓ 数据路径已配置")
    print(f"     热数据: {accessor.hot_dir}")
    print(f"     缓存: {accessor.cache_dir}")


# 便捷函数
def get_window_jsonl(window_start: int) -> Path:
    """便捷函数：获取窗口JSONL文件路径"""
    return get_accessor().get_window_jsonl(window_start)


def list_all_windows() -> list[int]:
    """便捷函数：列出所有窗口"""
    return get_accessor().list_all_windows()


if __name__ == "__main__":
    # 测试代码
    import sys
    
    accessor = DataAccessor()
    
    print("数据访问器配置:")
    print(f"  热数据目录: {accessor.hot_dir}")
    print(f"  缓存目录: {accessor.cache_dir}")
    print(f"  VPS: {accessor.vps_user}@{accessor.vps_host}")
    print()
    
    # 列出可用窗口
    print("扫描可用窗口...")
    windows = accessor.list_all_windows()
    print(f"  找到 {len(windows)} 个窗口")
    
    if windows:
        print(f"  最早: {windows[0]} ({datetime.fromtimestamp(windows[0], tz=timezone.utc)})")
        print(f"  最新: {windows[-1]} ({datetime.fromtimestamp(windows[-1], tz=timezone.utc)})")
    
    # 缓存统计
    print()
    stats = accessor.get_cache_stats()
    print("缓存统计:")
    print(f"  文件数: {stats['file_count']}")
    print(f"  总大小: {stats['total_size_mb']:.2f} MB")
    print(f"  使用率: {stats['usage_percent']:.1f}%")
    
    # 测试访问
    if len(sys.argv) > 1:
        test_window = int(sys.argv[1])
        print()
        print(f"测试访问窗口: {test_window}")
        try:
            path = accessor.get_window_jsonl(test_window)
            print(f"  ✓ 文件路径: {path}")
            print(f"  文件大小: {path.stat().st_size / 1024:.1f} KB")
        except FileNotFoundError as e:
            print(f"  ❌ {e}")

