# Polymarket远程数据系统 - 快速参考

## 🚀 核心概念

**Polymarket数据**（SSHFS挂载）→ 最近7天，实时访问  
**CEX数据**（定期同步）→ CSV文件，rsync同步  
**冷数据**（按需拉取）→ 历史数据，自动缓存  
**零修改**（auto_patch）→ 现有代码几乎不用改

## 📋 常用命令

### 基本操作
```bash
# 挂载热数据
~/.local/bin/mount_polymarket.sh

# 卸载
~/.local/bin/unmount_polymarket.sh

# 系统诊断
python3 diagnose_remote_setup.py

# 监控状态
python3 monitor_sync.py --check
```

### 数据管理
```bash
# 查看缓存
python3 data_accessor.py

# 清理缓存
python3 monitor_sync.py --cleanup

# 测试访问窗口
python3 data_accessor.py 1767507300

# 同步CEX数据
./sync_cex_data.sh
```

### VPS维护
```bash
# 登录VPS
ssh $VPS_USER@$VPS_HOST

# 手动归档（测试）
python3 ~/polymarket/scripts/archive_old_data.py --dry-run

# 查看归档日志
tail -f ~/polymarket/logs/archive.log

# 查看CEX数据大小
ls -lh ~/polymarket/logs/cex_multi_venue_books.csv

# 查看采集进程
ps aux | grep python3
```

## 💻 代码集成

### 最简单（1行）
```python
from data_path_compat import auto_patch; auto_patch()
```

### 标准方式（2行）
```python
from data_path_compat import auto_patch
auto_patch()

# 之后所有代码保持不变
import glob
files = glob.glob("real/btc-updown-15m-*.jsonl")
```

### 使用API
```python
from data_accessor import DataAccessor

accessor = DataAccessor()
windows = accessor.list_all_windows()
path = accessor.get_window_jsonl(windows[-1])
```

## 🔧 故障排查

| 问题 | 解决方案 |
|------|---------|
| 挂载失败 | `./setup_sshfs_mount.sh` |
| 找不到文件 | 检查 `$VPS_USER` `$VPS_HOST` |
| 无法访问冷数据 | `ssh $VPS_USER@$VPS_HOST ls ~/polymarket/real_archive` |
| 缓存太大 | `python3 monitor_sync.py --cleanup` |
| 脚本变慢 | 首次访问冷数据需等待，之后会快 |

## 📁 目录结构

```
本地:
  real_hot/                    ← SSHFS挂载（Polymarket热数据）
  real_cache/                  ← 自动缓存（Polymarket冷数据）
  real/                        ← 符号链接 → real_hot/
  real/cex_multi_venue_books.csv ← CEX数据（同步）

VPS:
  real_hot/                    ← Polymarket采集输出（最近7天）
  real_archive/                ← 按月归档（历史数据）
  logs/cex_multi_venue_books.csv ← CEX数据（追加）
  logs/poly.log, cex.log       ← 采集日志
```

## 📊 性能指标

- 热数据访问: < 50ms
- 冷数据首次: 200-500ms
- 冷数据缓存: < 10ms
- 本地空间: < 5GB（vs 原来 50GB+）

## 🔗 文档链接

- 完整设置: `REMOTE_DATA_SETUP.md`
- 迁移指南: `MIGRATION_GUIDE.md`
- 示例代码: `example_migration.py`
- 诊断工具: `diagnose_remote_setup.py`

## ⚙️ 环境变量

添加到 `~/.zshrc`:
```bash
export VPS_USER="your_username"
export VPS_HOST="your_vps_ip"
```

## 🎯 首次部署流程

1. **VPS端**: `./deploy_vps.sh`
2. **上传脚本**: `scp polymarket_btc15m_record.py cex_multi_venue_recorder.py ...`
3. **启动采集**: 在VPS上用nohup启动两个采集脚本
4. **本地端**: `./setup_sshfs_mount.sh`
5. **测试**: `python3 diagnose_remote_setup.py`
6. **同步CEX**: `./sync_cex_data.sh`
7. **迁移脚本**: 添加 `auto_patch()`
8. **监控**: `python3 monitor_sync.py --daemon &`

---

**提示**: 运行 `python3 diagnose_remote_setup.py` 可自动检查所有配置

