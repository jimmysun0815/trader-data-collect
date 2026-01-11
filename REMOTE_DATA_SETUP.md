# Polymarket数据远程访问系统

## 概述

这套系统让你可以在VPS上运行数据收集，同时在本地Mac上像访问本地文件一样流畅地使用这些数据。

### 核心特性

- ✅ **零延迟访问热数据**：最近7天数据通过SSHFS实时挂载
- ✅ **本地自动清理**：只保留需要的数据，节省本地空间
- ✅ **透明访问冷数据**：历史数据按需自动拉取
- ✅ **代码零修改**：现有分析脚本无需改动
- ✅ **自动归档**：VPS自动按月归档旧数据

## 快速开始

### 1. VPS端部署

将部署脚本和必要文件上传到VPS：

```bash
# 在本地
scp deploy_vps.sh archive_old_data.py your_user@your_vps:/tmp/

# 在VPS上
ssh your_user@your_vps
cd /tmp
chmod +x deploy_vps.sh
./deploy_vps.sh
```

将数据采集脚本复制到VPS并修改输出路径：

```bash
# 复制脚本到VPS
scp polymarket_btc15m_record.py your_user@your_vps:~/polymarket/scripts/

# 在VPS上启动数据采集（修改输出路径为 real_hot/）
cd ~/polymarket/scripts
source ~/polymarket/venv/bin/activate
python3 polymarket_btc15m_record.py --output ~/polymarket/real_hot
```

### 2. 本地Mac配置

**配置环境变量**（添加到 `~/.zshrc` 或 `~/.bash_profile`）：

```bash
export VPS_USER="your_username"
export VPS_HOST="your_vps_ip"
```

**运行SSHFS挂载配置**：

```bash
cd ~/Desktop/workspace/polymarket
./setup_sshfs_mount.sh
```

按照提示完成SSH密钥配置和挂载。

### 3. 在分析脚本中使用

#### 方式1：自动补丁（推荐，无需修改代码）

在你的分析脚本开头添加一行：

```python
from data_path_compat import auto_patch
auto_patch()

# 之后所有代码保持不变
import glob
files = glob.glob("real/btc-updown-15m-*.jsonl")  # 自动路由到正确位置
```

#### 方式2：使用数据访问器API

```python
from data_accessor import DataAccessor

accessor = DataAccessor()

# 获取单个窗口数据
window_path = accessor.get_window_jsonl(1767507300)
with open(window_path) as f:
    data = json.load(f)

# 列出所有窗口
all_windows = accessor.list_all_windows()

# 查看缓存状态
stats = accessor.get_cache_stats()
print(f"缓存使用: {stats['total_size_mb']:.1f}MB / {stats['max_size_mb']:.1f}MB")
```

## 目录结构

### VPS端

```
~/polymarket/
├── real_hot/              # 热数据（最近7天）
│   └── btc-updown-15m-*.jsonl
├── real_archive/          # 冷数据归档（按月）
│   ├── 2026-01/
│   │   └── btc-updown-15m-*.jsonl.gz
│   └── 2026-02/
├── scripts/               # 数据采集脚本
│   ├── polymarket_btc15m_record.py
│   └── archive_old_data.py
├── logs/                  # 日志
│   └── archive.log
└── venv/                  # Python虚拟环境
```

### 本地Mac

```
~/Desktop/workspace/polymarket/
├── real_hot/              # SSHFS挂载点 -> VPS的real_hot/
├── real_cache/            # 冷数据本地缓存（自动管理）
│   ├── 2026-01/
│   └── 2026-02/
├── real/                  # 符号链接 -> real_hot/（兼容现有代码）
├── data_accessor.py       # 数据访问代理
├── data_path_compat.py    # 路径兼容层
├── setup_sshfs_mount.sh   # SSHFS配置脚本
└── research/              # 你的分析脚本
    └── btc15m_*.py
```

## 常用命令

### SSHFS管理

```bash
# 挂载
~/.local/bin/mount_polymarket.sh

# 卸载
~/.local/bin/unmount_polymarket.sh

# 查看挂载状态
mount | grep polymarket
```

### 数据管理

```bash
# 测试数据访问器
python3 data_accessor.py

# 测试访问特定窗口
python3 data_accessor.py 1767507300

# 查看缓存统计
python3 -c "from data_accessor import get_accessor; print(get_accessor().get_cache_stats())"
```

### VPS维护

```bash
# SSH到VPS
ssh $VPS_USER@$VPS_HOST

# 手动运行归档（测试）
cd ~/polymarket/scripts
python3 archive_old_data.py --dry-run

# 实际执行归档
python3 archive_old_data.py --compress

# 查看归档日志
tail -f ~/polymarket/logs/archive.log
```

## 性能说明

| 数据类型 | 访问延迟 | 说明 |
|---------|---------|------|
| 热数据（7天内） | < 50ms | SSHFS缓存命中，接近本地速度 |
| 冷数据（首次访问） | 200-500ms | SSH拉取单个文件（~500KB） |
| 冷数据（缓存命中） | < 10ms | 本地缓存读取 |
| 批量历史分析 | 初次慢，后续快 | 智能预取 + 本地缓存 |

## 数据量管理

- **VPS存储**: ~500MB/天 → ~15GB/月 → ~180GB/年
- **本地热数据**: 最多 ~3.5GB（7天）
- **本地冷缓存**: 上限 1GB（LRU自动清理）
- **总本地占用**: < 5GB（vs 原来全量 > 50GB）

## 故障排查

### SSHFS无法挂载

```bash
# 1. 检查SSH连接
ssh $VPS_USER@$VPS_HOST "echo 'OK'"

# 2. 检查macFUSE
brew list macfuse

# 3. 强制卸载后重新挂载
umount ~/Desktop/workspace/polymarket/real_hot
~/.local/bin/mount_polymarket.sh
```

### 无法访问冷数据

```bash
# 1. 检查环境变量
echo $VPS_USER
echo $VPS_HOST

# 2. 测试SSH免密登录
ssh -o BatchMode=yes $VPS_USER@$VPS_HOST "ls ~/polymarket/real_archive"

# 3. 检查VPS上的归档文件
ssh $VPS_USER@$VPS_HOST "find ~/polymarket/real_archive -name '*.jsonl*' | head"
```

### 缓存占用过大

```bash
# 手动清理缓存
rm -rf ~/Desktop/workspace/polymarket/real_cache/*

# 或者在Python中清理
python3 -c "from data_accessor import get_accessor; get_accessor()._cleanup_cache_if_needed()"
```

## 注意事项

1. **首次访问历史数据会较慢**：冷数据需要从VPS拉取，首次访问单个窗口约200-500ms
2. **网络波动**：SSHFS在网络断开时会有短暂不可用（最多15秒自动重连）
3. **VPS流量**：每次拉取冷数据约500KB，大批量历史分析会产生较多流量
4. **安全性**：建议配置SSH密钥仅允许访问数据目录（使用 `authorized_keys` 的 `command=` 限制）

## 高级配置

### 修改缓存大小限制

在代码中初始化时指定：

```python
from data_accessor import DataAccessor

accessor = DataAccessor(cache_max_size_mb=2048)  # 2GB缓存
```

### 禁用智能预取

```python
accessor = DataAccessor()
accessor.prefetch_enabled = False
```

### 修改归档阈值

```bash
# VPS上修改crontab
crontab -e

# 将命令改为（14天后归档）
0 4 * * * ~/polymarket/venv/bin/python3 ~/polymarket/scripts/archive_old_data.py --days 14 >> ~/polymarket/logs/archive.log 2>&1
```

## 更新日志

- 2026-01-10: 初始版本
  - VPS部署脚本
  - SSHFS自动挂载
  - 数据访问代理层
  - 路径兼容层
  - 自动归档系统

## 支持

遇到问题请查看：
1. 日志文件：`~/Library/Logs/polymarket_sshfs*.log`
2. VPS归档日志：`~/polymarket/logs/archive.log`
3. 测试数据访问：`python3 data_accessor.py`

