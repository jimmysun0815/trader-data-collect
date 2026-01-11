"""
示例：如何将现有分析脚本迁移到远程数据访问系统

这个脚本展示了三种迁移方式，从最简单到最灵活
"""

# ============================================================
# 方式1：一行代码自动补丁（推荐）
# ============================================================

# 在你的脚本开头添加这两行：
from data_path_compat import auto_patch
auto_patch()

# 之后所有代码保持不变！
import glob
import json

print("方式1：自动补丁")
print("=" * 60)

# 现有代码无需任何修改
files = glob.glob("real/btc-updown-15m-*.jsonl")
print(f"找到 {len(files)} 个窗口文件")

if files:
    # 读取第一个文件
    with open(files[0]) as f:
        first_line = f.readline()
        data = json.loads(first_line)
        print(f"示例数据: {data.get('type', 'unknown')}")

print()

# ============================================================
# 方式2：使用数据访问器API（更灵活）
# ============================================================

from data_accessor import DataAccessor

print("方式2：数据访问器API")
print("=" * 60)

accessor = DataAccessor()

# 列出所有窗口
all_windows = accessor.list_all_windows()
print(f"总共 {len(all_windows)} 个窗口")

if all_windows:
    print(f"最早: {all_windows[0]}")
    print(f"最新: {all_windows[-1]}")
    
    # 获取特定窗口
    window_path = accessor.get_window_jsonl(all_windows[-1])
    print(f"最新窗口文件: {window_path}")
    
    # 查看缓存状态
    stats = accessor.get_cache_stats()
    print(f"缓存: {stats['file_count']} 文件, {stats['total_size_mb']:.2f}MB")

print()

# ============================================================
# 方式3：仅补丁glob，手动控制文件访问
# ============================================================

from data_path_compat import patch_glob

print("方式3：部分补丁")
print("=" * 60)

patch_glob()

# glob自动路由，但open需要手动处理
files = glob.glob("real/btc-updown-15m-*.jsonl")
print(f"找到 {len(files)} 个文件")

# 对于特定的冷数据访问，可以显式使用accessor
if len(all_windows) > 100:
    old_window = all_windows[50]
    print(f"访问历史窗口: {old_window}")
    old_path = accessor.get_window_jsonl(old_window)
    print(f"路径: {old_path}")

print()

# ============================================================
# 实际使用示例：批量处理窗口
# ============================================================

print("实际使用示例：批量处理")
print("=" * 60)

# 重新初始化accessor以使用自动补丁
from data_path_compat import auto_patch
auto_patch()

# 现在可以像以前一样使用glob
recent_files = sorted(glob.glob("real/btc-updown-15m-*.jsonl"))[-10:]  # 最近10个

print(f"处理最近 {len(recent_files)} 个窗口...")

for i, file_path in enumerate(recent_files, 1):
    try:
        with open(file_path) as f:
            line_count = sum(1 for _ in f)
        print(f"  {i}. {Path(file_path).name}: {line_count} 行")
    except Exception as e:
        print(f"  {i}. {Path(file_path).name}: 错误 - {e}")

print()
print("✓ 所有方式都可以正常工作！")
print()
print("推荐使用方式1（auto_patch），代码改动最小")

