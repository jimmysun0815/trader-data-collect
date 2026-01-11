"""
数据路径兼容层 - 让现有脚本无需修改即可使用数据代理

在你的分析脚本开头添加：
    from data_path_compat import patch_glob, patch_open
    patch_glob()
    patch_open()

或者更简单：
    from data_path_compat import auto_patch
    auto_patch()

之后所有的 glob.glob("real/...") 和 open("real/...") 会自动路由到数据代理层
"""

from __future__ import annotations

import glob as _builtin_glob
import builtins
from pathlib import Path
from typing import Any, Callable

from data_accessor import get_accessor

# 保存原始函数
_original_glob = _builtin_glob.glob
_original_iglob = _builtin_glob.iglob
_original_open = builtins.open


def _resolve_path(path_str: str) -> str:
    """
    解析路径，如果是 real/ 开头则转换到代理层
    
    支持的模式：
    - real/btc-updown-15m-*.jsonl
    - real/polymarket_btc15m_book.csv
    - real/cex_multi_venue_books.csv
    """
    if not isinstance(path_str, str):
        return path_str
    
    # 如果路径以 real/ 开头，尝试映射
    if path_str.startswith("real/"):
        accessor = get_accessor()
        
        # 如果是 glob pattern，返回 real_hot 路径
        if "*" in path_str or "?" in path_str:
            # 替换 real/ 为 real_hot/
            return str(accessor.hot_dir / path_str[5:])
        
        # 如果是具体文件，也映射到 real_hot
        # 注意：CSV文件通常在热数据中
        return str(accessor.hot_dir / path_str[5:])
    
    return path_str


def patched_glob(pathname: str, *, recursive: bool = False, **kwargs) -> list[str]:
    """glob.glob的补丁版本"""
    resolved = _resolve_path(pathname)
    return _original_glob(resolved, recursive=recursive, **kwargs)


def patched_iglob(pathname: str, *, recursive: bool = False, **kwargs):
    """glob.iglob的补丁版本"""
    resolved = _resolve_path(pathname)
    return _original_iglob(resolved, recursive=recursive, **kwargs)


def patched_open(file, mode='r', *args, **kwargs):
    """open的补丁版本"""
    resolved = _resolve_path(file) if isinstance(file, str) else file
    return _original_open(resolved, mode, *args, **kwargs)


def patch_glob():
    """替换全局的glob函数"""
    _builtin_glob.glob = patched_glob
    _builtin_glob.iglob = patched_iglob


def patch_open():
    """替换全局的open函数"""
    builtins.open = patched_open


def unpatch_all():
    """恢复原始函数"""
    _builtin_glob.glob = _original_glob
    _builtin_glob.iglob = _original_iglob
    builtins.open = _original_open


def auto_patch():
    """自动应用所有补丁"""
    patch_glob()
    patch_open()
    
    # 同时创建 real 符号链接
    from data_accessor import setup_data_paths
    setup_data_paths()


if __name__ == "__main__":
    # 测试代码
    print("测试数据路径兼容层")
    print()
    
    auto_patch()
    
    print("测试 glob.glob:")
    import glob
    files = glob.glob("real/btc-updown-15m-*.jsonl")
    print(f"  找到 {len(files)} 个文件")
    if files:
        print(f"  示例: {files[0]}")
    
    print()
    print("✓ 补丁已应用，现有脚本可以直接使用")

