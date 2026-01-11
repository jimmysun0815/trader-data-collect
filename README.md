# Polymarket数据远程收集系统

这个文件夹包含了在VPS上运行数据收集（Polymarket + CEX），本地通过SSHFS实时访问的完整系统。

## 🎯 支持的数据源

1. **Polymarket数据** - BTC/ETH 15分钟涨跌预测市场
   - 格式: JSONL（按窗口）
   - 存储: `real_hot/` → 7天后归档到 `real_archive/`
   - 脚本: `polymarket_btc15m_record.py`

2. **CEX数据** - 多交易所订单簿
   - 格式: CSV（时间序列）
   - 存储: `logs/cex_multi_venue_books.csv`
   - 脚本: `cex_multi_venue_recorder.py`
   - 支持: Binance, OKX, Bybit (Spot + Perp/Swap)

## 📁 VPS目录结构

```
~/polymarket/
├── real_hot/              ← Polymarket热数据（最近7天）
├── real_archive/          ← Polymarket冷数据归档（按月）
├── logs/                  ← CEX数据和日志
│   ├── cex_multi_venue_books.csv  ← CEX订单簿数据
│   ├── poly.log          ← Polymarket采集日志
│   ├── cex.log           ← CEX采集日志
│   └── archive.log       ← 归档日志
├── scripts/               ← 数据采集脚本
│   ├── polymarket_btc15m_record.py
│   ├── cex_multi_venue_recorder.py
│   └── archive_old_data.py
└── venv/                  ← Python虚拟环境
```

### 核心脚本 (8个)
- `deploy_vps.sh` - VPS自动化部署脚本
- `archive_old_data.py` - 数据自动归档（7天后按月归档）
- `setup_sshfs_mount.sh` - SSHFS挂载配置脚本
- `data_accessor.py` - 数据访问代理（热/冷路由、缓存管理）
- `data_path_compat.py` - 路径兼容层（monkey patch）
- `monitor_sync.py` - 挂载监控和自动修复
- `diagnose_remote_setup.py` - 系统诊断工具
- `example_migration.py` - 迁移示例代码

### 文档 (5个)
- `GETTING_STARTED.md` - 5分钟快速入门
- `REMOTE_DATA_SETUP.md` - 完整设置文档
- `MIGRATION_GUIDE.md` - 脚本迁移指南
- `QUICK_REFERENCE.md` - 快速参考卡片
- `IMPLEMENTATION_SUMMARY.md` - 实施总结

### 工具脚本 (2个)
- `check_deployment.sh` - 部署检查清单
- `list_files.py` - 文件清单生成器

## 🚀 快速开始

### 1. 检查准备情况
```bash
cd collect_data
./check_deployment.sh
```

### 2. 查看快速入门
```bash
cat GETTING_STARTED.md
```

### 3. VPS部署
```bash
# 上传到VPS
scp deploy_vps.sh archive_old_data.py your_user@your_vps:/tmp/

# 在VPS上运行
ssh your_user@your_vps
cd /tmp
./deploy_vps.sh
```

### 4. 启动数据采集

**复制采集脚本到VPS**:
```bash
# 从本地上传
scp polymarket_btc15m_record.py cex_multi_venue_recorder.py \
    $VPS_USER@$VPS_HOST:~/polymarket/scripts/
```

**在VPS上启动**:
```bash
cd ~/polymarket/scripts
source ~/polymarket/venv/bin/activate

# 启动Polymarket数据采集（后台）
nohup python3 polymarket_btc15m_record.py \
    --output ~/polymarket/real_hot \
    > ~/polymarket/logs/poly.log 2>&1 &

# 启动CEX数据采集（后台）
nohup python3 cex_multi_venue_recorder.py \
    --out ~/polymarket/logs/cex_multi_venue_books.csv \
    --hz 1.0 \
    > ~/polymarket/logs/cex.log 2>&1 &

# 查看进程
ps aux | grep python3

# 查看日志
tail -f ~/polymarket/logs/poly.log
tail -f ~/polymarket/logs/cex.log
```

### 4. 本地配置
```bash
# 设置环境变量
export VPS_USER="your_username"
export VPS_HOST="your_vps_ip"

# 运行配置脚本
./setup_sshfs_mount.sh
```

**配置完成后，本地可以访问**:
- `~/Desktop/workspace/polymarket/real_hot/` - Polymarket热数据（SSHFS）
- VPS上的 `logs/cex_multi_venue_books.csv` - 需要手动同步或访问

### 5. 验证系统
```bash
python3 diagnose_remote_setup.py
```

## 💡 核心特性

- ✅ **零延迟访问热数据** - SSHFS挂载，<50ms延迟
- ✅ **智能冷热数据路由** - 自动判断热数据/缓存/VPS拉取
- ✅ **本地空间节省90%+** - <5GB vs 原来50GB+
- ✅ **代码最小改动** - 只需添加2行代码
- ✅ **自动化程度高** - 归档、缓存、挂载全自动
- ✅ **完整诊断工具** - 一键检查所有配置

## 📖 使用示例

在你的分析脚本中添加：

```python
# 方式1: 使用路径兼容层（推荐）
import sys
sys.path.insert(0, '/path/to/collect_data')
from data_path_compat import auto_patch
auto_patch()

# 之后所有代码保持不变
import glob
files = glob.glob("real/btc-updown-15m-*.jsonl")

# 方式2: 直接使用数据访问器API
sys.path.insert(0, '/path/to/collect_data')
from data_accessor import DataAccessor

accessor = DataAccessor()
windows = accessor.list_all_windows()
path = accessor.get_window_jsonl(windows[-1])
```

## 📊 系统架构

```
VPS服务器
├── real_hot/          ← 数据采集输出（最近7天）
├── real_archive/      ← 自动归档（按月）
└── scripts/           ← 采集和归档脚本

        ↓ SSHFS + SSH

本地Mac
├── real_hot/          ← SSHFS挂载点（实时访问）
├── real_cache/        ← 冷数据缓存（LRU管理）
└── collect_data/      ← 本文件夹（工具和文档）
```

## 📚 详细文档

- **快速入门**: `GETTING_STARTED.md` (10-15分钟完成首次配置)
- **完整文档**: `REMOTE_DATA_SETUP.md` (架构、性能、维护)
- **迁移指南**: `MIGRATION_GUIDE.md` (如何迁移现有脚本)
- **快速参考**: `QUICK_REFERENCE.md` (常用命令速查)

## 🔧 常用命令

```bash
# 系统诊断
python3 diagnose_remote_setup.py

# 检查挂载
mount | grep polymarket

# 清理缓存
python3 monitor_sync.py --cleanup

# 查看文件清单
python3 list_files.py
```

## ⚠️ 重要提示

1. **环境变量**: 必须设置 `VPS_USER` 和 `VPS_HOST`
2. **SSH密钥**: 需要配置免密登录
3. **macFUSE**: Mac上需要安装macFUSE才能使用SSHFS
4. **首次访问**: 历史数据首次访问会从VPS拉取，有200-500ms延迟

## 🆘 获取帮助

- 运行诊断: `python3 diagnose_remote_setup.py`
- 查看日志: `tail -f ~/Desktop/workspace/polymarket/logs/monitor.log`
- 测试SSH: `ssh $VPS_USER@$VPS_HOST "echo OK"`

---

**系统版本**: v1.0  
**创建时间**: 2026-01-10  
**兼容性**: macOS 12.0+ (Apple Silicon & Intel)

