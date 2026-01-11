# 现有脚本迁移指南

## 概述

这个指南帮助你将现有的分析脚本迁移到远程数据访问系统，只需要**极少的代码修改**（通常只需添加2行）。

## 迁移前准备

1. 确保已完成VPS部署和SSHFS挂载配置
2. 确认 `real_hot/` 目录已正常挂载并有数据
3. 设置环境变量 `VPS_USER` 和 `VPS_HOST`

```bash
# 验证挂载
ls ~/Desktop/workspace/polymarket/real_hot/

# 验证环境变量
echo $VPS_USER
echo $VPS_HOST
```

## 迁移方法

### 方法1：自动补丁（推荐）✅

**适用场景**：所有使用 `glob.glob("real/...")` 或 `open("real/...")` 的脚本

**修改步骤**：只需在脚本开头添加2行

```python
# 在所有import之后、业务代码之前添加
from data_path_compat import auto_patch
auto_patch()

# 之后的所有代码保持不变
import glob
files = glob.glob("real/btc-updown-15m-*.jsonl")
```

**示例**：[`research/btc15m_strong_signal_enhanced_rule_search.py`](research/btc15m_strong_signal_enhanced_rule_search.py)

原始代码（第23行附近）：
```python
import argparse
import csv
import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
```

修改后：
```python
import argparse
import csv
import math
import re
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# 启用远程数据访问
from data_path_compat import auto_patch
auto_patch()
```

### 方法2：使用数据访问器API

**适用场景**：需要精细控制数据访问，或需要缓存统计等高级功能

**修改步骤**：

```python
from data_accessor import DataAccessor

accessor = DataAccessor()

# 1. 列出所有窗口
windows = accessor.list_all_windows()

# 2. 获取特定窗口的文件路径
for ws in windows:
    file_path = accessor.get_window_jsonl(ws)
    with open(file_path) as f:
        # 处理数据...
        pass

# 3. 查看缓存状态
stats = accessor.get_cache_stats()
print(f"缓存使用: {stats['total_size_mb']:.1f}MB")
```

### 方法3：不修改代码，使用符号链接

**适用场景**：完全不想修改代码，但只能访问热数据（最近7天）

**步骤**：

```bash
cd ~/Desktop/workspace/polymarket

# 备份原有real目录
mv real real_backup_$(date +%s)

# 创建符号链接
ln -s real_hot real

# 验证
ls -la real
```

**限制**：无法自动访问冷数据（需要手动从VPS拉取）

## 需要迁移的脚本清单

以下是项目中使用 `real/` 目录的主要脚本：

### 研究脚本（`research/`）

- [x] `btc15m_strong_signal_enhanced_rule_search.py` - 添加 auto_patch
- [ ] `btc15m_staged_entry_signal_eval.py` - 添加 auto_patch
- [ ] `btc15m_eval_dynamic_topk_train_test.py` - 添加 auto_patch
- [ ] `btc15m_eval_top_params_train_test.py` - 添加 auto_patch
- [ ] 其他 `btc15m_*.py` 脚本

### 根目录脚本

- [ ] `evaluate_top_models_on_history.py`
- [ ] `continuous_window_pnl_one_model.py`
- [ ] `continuous_window_pnl_top_models.py`
- [ ] `per_window_pnl_top_models.py`
- [ ] 其他读取 `real/` 的脚本

## 批量迁移脚本

使用以下脚本自动为所有Python文件添加 `auto_patch`：

```bash
#!/bin/bash
# 文件名: batch_migrate.sh

# 查找所有包含 'real/' 的Python脚本
grep -l "real/" research/*.py *.py 2>/dev/null | while read file; do
    # 检查是否已经包含auto_patch
    if ! grep -q "auto_patch" "$file"; then
        echo "迁移: $file"
        
        # 在第一个import后插入auto_patch
        # 这里使用简单的sed，实际使用时可能需要根据具体情况调整
        sed -i '' '/^from __future__ import/a\
\
# 启用远程数据访问\
from data_path_compat import auto_patch\
auto_patch()\
' "$file"
        
        echo "  ✓ 完成"
    else
        echo "跳过: $file (已迁移)"
    fi
done

echo "批量迁移完成"
```

## 测试迁移结果

运行以下命令测试迁移：

```bash
# 1. 测试数据访问器
python3 data_accessor.py

# 2. 测试迁移示例
python3 example_migration.py

# 3. 测试你的脚本（使用--dry-run等安全参数）
python3 research/btc15m_strong_signal_enhanced_rule_search.py --help
```

## 常见问题

### Q1: 脚本运行速度变慢了？

A: 首次访问历史数据时会从VPS拉取，有200-500ms延迟。后续访问会使用本地缓存，速度恢复正常。

解决方案：
- 预热缓存：运行 `python3 data_accessor.py` 让系统预加载常用数据
- 调整缓存大小：在代码中设置 `DataAccessor(cache_max_size_mb=2048)`

### Q2: FileNotFoundError: 找不到文件

A: 检查以下几点：

```bash
# 1. SSHFS是否已挂载
mount | grep polymarket

# 2. 环境变量是否设置
echo $VPS_USER $VPS_HOST

# 3. 测试SSH连接
ssh $VPS_USER@$VPS_HOST "ls ~/polymarket/real_hot"
```

### Q3: 某些CSV文件找不到（如 polymarket_btc15m_book.csv）

A: CSV文件通常不是按窗口存储的，需要特殊处理：

```python
from pathlib import Path

# 方式1：直接访问real_hot（推荐）
csv_path = Path.home() / "Desktop/workspace/polymarket/real_hot/polymarket_btc15m_book.csv"

# 方式2：使用符号链接
csv_path = Path("real/polymarket_btc15m_book.csv")

# 确保文件存在
if csv_path.exists():
    with open(csv_path) as f:
        # 处理CSV...
        pass
```

### Q4: 如何回退到原来的本地数据？

```bash
cd ~/Desktop/workspace/polymarket

# 删除符号链接
rm real

# 恢复备份
mv real_backup_XXXXXXXX real

# 或者直接指向本地数据
ln -s /path/to/your/old/real/data real
```

## 性能优化建议

### 1. 批量操作使用预取

```python
accessor = DataAccessor()
accessor.prefetch_enabled = True  # 默认开启
accessor.prefetch_count = 5  # 预取5个相邻窗口

# 按时间顺序处理时，后续窗口会自动预取
for ws in sorted_windows:
    path = accessor.get_window_jsonl(ws)
    # 处理...
```

### 2. 避免重复访问同一窗口

```python
# ❌ 不好：重复访问
for _ in range(10):
    path = get_window_jsonl(ws)
    process(path)

# ✅ 好：缓存路径
path = get_window_jsonl(ws)
for _ in range(10):
    process(path)
```

### 3. 定期清理缓存

```bash
# 手动清理
python3 monitor_sync.py --cleanup

# 或在代码中
from data_accessor import get_accessor
get_accessor()._cleanup_cache_if_needed()
```

## 监控和维护

### 运行监控脚本

```bash
# 一次性检查
python3 monitor_sync.py --check

# 持续监控（推荐在后台运行）
nohup python3 monitor_sync.py --daemon > logs/monitor_daemon.log 2>&1 &
```

### 配置LaunchAgent自动监控

```bash
# 创建LaunchAgent配置
cat > ~/Library/LaunchAgents/com.polymarket.monitor.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.polymarket.monitor</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/Users/YOUR_USER/Desktop/workspace/polymarket/monitor_sync.py</string>
        <string>--daemon</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOUR_USER/Desktop/workspace/polymarket/logs/monitor_daemon.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOUR_USER/Desktop/workspace/polymarket/logs/monitor_daemon_error.log</string>
</dict>
</plist>
EOF

# 加载
launchctl load ~/Library/LaunchAgents/com.polymarket.monitor.plist
```

## 下一步

1. 选择一个简单的脚本先迁移测试（推荐用 `auto_patch` 方式）
2. 验证结果正确后，批量迁移其他脚本
3. 配置监控脚本保证系统稳定运行
4. 定期检查VPS归档日志，确保旧数据正常归档

## 获取帮助

- 测试数据访问：`python3 data_accessor.py [window_start]`
- 查看示例：`python3 example_migration.py`
- 检查挂载：`python3 monitor_sync.py --check`
- 查看日志：`tail -f logs/monitor.log`

