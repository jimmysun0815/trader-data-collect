# Polymarket多市场数据采集系统 - 部署总结

## ✅ 完成的工作

### 1. 创建的文件

| 文件 | 说明 |
|------|------|
| `polymarket_multi_market_recorder.py` | Polymarket多市场采集器（BTC/ETH 15分钟+1小时） |
| `cex_multi_asset_recorder.py` | CEX多资产采集器（BTC/ETH order book，12小时切分） |
| `test_multi_markets.py` | API测试脚本 |
| `test_recorders.sh` | 采集器测试脚本 |
| `MULTI_MARKET_GUIDE.md` | 多市场采集完整指南 |
| `requirements.txt` | Python依赖（已更新） |
| `backup_hot_data.sh` | 本地备份脚本（可选） |
| `SSHFS_MECHANISM.md` | SSHFS工作机制说明 |

### 2. 更新的文件

| 文件 | 更新内容 |
|------|---------|
| `deploy_vps.sh` | 添加新采集脚本的部署说明 |

---

## 📊 支持的市场

### Polymarket (4个市场)
- ✅ BTC 15分钟市场: `btc-updown-15m-{timestamp}`
- ✅ ETH 15分钟市场: `eth-updown-15m-{timestamp}`
- ✅ BTC 1小时市场: `bitcoin-up-or-down-{date}-{hour}pm-et`
- ✅ ETH 1小时市场: `ethereum-up-or-down-{date}-{hour}pm-et`

### CEX (2个资产 × 5个venue = 10个数据源)
**BTC**:
- ✅ Binance Spot (BTCUSDT)
- ✅ OKX Spot (BTC-USDT)
- ✅ OKX Swap (BTC-USDT-SWAP)
- ✅ Bybit Spot (BTCUSDT)
- ✅ Bybit Linear (BTCUSDT)

**ETH**:
- ✅ Binance Spot (ETHUSDT)
- ✅ OKX Spot (ETH-USDT)
- ✅ OKX Swap (ETH-USDT-SWAP)
- ✅ Bybit Spot (ETHUSDT)
- ✅ Bybit Linear (ETHUSDT)

---

## 🧪 测试结果

### API测试 (test_multi_markets.py)
```
✓ ETH 15分钟市场
✓ BTC 15分钟市场
✓ BTC 1小时市场
✓ ETH 1小时市场
✓ ETH CEX Orderbook
```

### 采集器测试 (test_recorders.sh)
```
✓ Polymarket采集器运行正常
✓ CEX采集器运行正常
✓ 文件自动创建
✓ 数据格式正确
```

**生成的测试文件**:
- `real_hot/btc-updown-15m-*.jsonl`
- `real_hot/eth-updown-15m-*.jsonl`
- `real_hot/bitcoin-up-or-down-*.jsonl`
- `real_hot/ethereum-up-or-down-*.jsonl`
- `real_hot/cex_btc_*_00-12.csv`
- `real_hot/cex_eth_*_00-12.csv`

---

## 📁 输出文件格式

### Polymarket数据

**文件命名**: `{market_slug}_{timestamp}.jsonl`

**示例**: `btc-updown-15m-1768102200_20260110_193559.jsonl`

**数据格式** (每行一个JSON对象):
```json
{
  "timestamp": 1768102563000,
  "market_key": "btc_15m",
  "market_slug": "btc-updown-15m-1768102200",
  "question": "Bitcoin Up or Down - January 10, 9:30PM-9:45PM ET",
  "tokens": [
    {
      "outcome": "Yes",
      "token_id": "xxxxx",
      "orderbook": {
        "bids": [[price, size], ...],
        "asks": [[price, size], ...]
      }
    }
  ]
}
```

### CEX数据

**文件命名**: `cex_{asset}_{date}_{session}.csv`

**示例**: 
- `cex_btc_20260110_00-12.csv` (0:00-12:00 UTC)
- `cex_btc_20260110_12-24.csv` (12:00-24:00 UTC)

**数据格式** (CSV):
```
ts_sample_utc, t_sample_unix, sample_id, venue,
best_bid, best_ask, mid, spread,
bid_qty_l1, ask_qty_l1,
bid_notional, ask_notional, imb, micro, micro_edge,
err
```

**12小时切分**:
- ✅ 每个文件 ~100-200MB
- ✅ SSHFS友好（避免单个大文件）
- ✅ 方便按时段分析

---

## 🚀 快速开始

### 本地测试

```bash
cd /path/to/polymarket/collect_data

# 1. 创建虚拟环境（如果还没有）
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# 2. 测试API
./venv/bin/python3 test_multi_markets.py

# 3. 测试采集器（运行5秒后自动停止）
./test_recorders.sh

# 4. 查看生成的文件
ls -lh ../real_hot/
```

### 部署到VPS

```bash
# 1. 上传文件到VPS
scp polymarket_multi_market_recorder.py user@vps:~/polymarket/scripts/
scp cex_multi_asset_recorder.py user@vps:~/polymarket/scripts/
scp archive_old_data.py user@vps:~/polymarket/scripts/
scp requirements.txt user@vps:~/polymarket/
scp deploy_vps.sh user@vps:~/polymarket/

# 2. SSH到VPS
ssh user@vps

# 3. 运行部署脚本
cd ~/polymarket
chmod +x deploy_vps.sh
./deploy_vps.sh

# 4. 安装依赖
source ~/polymarket/venv/bin/activate
pip install -r requirements.txt

# 5. 启动采集器
cd ~/polymarket/scripts

# Polymarket多市场采集
nohup python3 polymarket_multi_market_recorder.py > ../logs/poly_multi.log 2>&1 &

# CEX多资产采集
nohup python3 cex_multi_asset_recorder.py > ../logs/cex_multi.log 2>&1 &

# 6. 检查运行状态
ps aux | grep recorder
tail -f ../logs/poly_multi.log
tail -f ../logs/cex_multi.log
```

---

## 📈 性能特点

### SSHFS访问性能

| 数据类型 | 文件大小 | 首次读取 | 后续读取 | 切换频率 |
|---------|---------|---------|---------|---------|
| Polymarket | 100-500KB | 500ms-1s | <50ms | 每15分钟 |
| CEX | 100-200MB | 5-10s | <500ms | 每12小时 |

### 实时交易友好度

- ✅ **Polymarket**: 每15分钟只有一次1秒延迟，完全可接受
- ✅ **CEX**: 每12小时切换一次，对交易影响极小
- ✅ **内核缓存**: 同一窗口内重复读取几乎无延迟

### 存储估算

| 数据类型 | 每天 | 7天(热数据) | 30天(归档) |
|---------|------|------------|-----------|
| Polymarket (4市场) | 40-200MB | 280MB-1.4GB | 1.2-6GB |
| CEX (BTC+ETH) | 432MB | ~3GB | ~13GB |
| **总计** | ~500MB | ~4.5GB | ~19GB |

---

## 🔧 虚拟环境说明

### 独立的venv

在 `collect_data/` 目录中创建了独立的虚拟环境：

```
collect_data/
├── venv/              ← Python虚拟环境
│   ├── bin/
│   │   ├── python3
│   │   ├── pip
│   │   └── activate
│   └── lib/
├── requirements.txt   ← 依赖列表
└── *.py              ← 采集脚本
```

**优点**:
- ✅ 依赖隔离，不影响主项目
- ✅ 方便部署（直接scp整个文件夹）
- ✅ 明确的依赖管理

**使用方法**:
```bash
# 激活虚拟环境
source venv/bin/activate

# 或直接使用
./venv/bin/python3 script.py
```

---

## 📚 文档索引

| 文档 | 说明 |
|------|------|
| `MULTI_MARKET_GUIDE.md` | **多市场采集完整指南** (新) |
| `SYSTEM_SUMMARY.md` | 整个数据收集系统总结 |
| `REMOTE_DATA_SETUP.md` | 远程数据访问设置 |
| `CEX_DATA_ACCESS.md` | CEX数据访问说明 |
| `SSHFS_MECHANISM.md` | SSHFS工作机制 (新) |
| `GETTING_STARTED.md` | 5分钟快速开始 |
| `QUICK_REFERENCE.md` | 常用命令速查 |

---

## ⚠️ 重要提醒

### 关于SSHFS

- ❌ **不会真正下载文件到本地**: SSHFS只是一个"传送门"
- ✅ **系统缓存**: 最近读过的数据在内存中
- ❌ **重启后失效**: 电脑重启需要重新挂载SSHFS
- ✅ **自动重连**: `monitor_sync.py` 会自动检测并重新挂载

### 如需本地备份

```bash
# 使用提供的备份脚本
./collect_data/backup_hot_data.sh 3  # 保留最近3天

# 或添加到crontab每天自动备份
0 3 * * * cd ~/Desktop/workspace/polymarket && ./collect_data/backup_hot_data.sh 3
```

---

## 🎯 下一步

1. **本地测试通过** ✅
2. **准备部署到VPS**:
   - 配置VPS SSH访问
   - 上传采集脚本
   - 运行deploy_vps.sh
   - 启动采集器
3. **配置本地SSHFS**:
   - 运行setup_sshfs_mount.sh
   - 设置LaunchAgent自动挂载
   - 启动monitor_sync.py
4. **开始使用**:
   - 交易机器人通过SSHFS读取real_hot/
   - 数据分析通过data_accessor.py访问所有数据
   - 定期检查采集状态和磁盘使用

---

## 📞 支持

如有问题：
1. 查看 `MULTI_MARKET_GUIDE.md` 的故障排查章节
2. 检查日志文件
3. 运行测试脚本验证API和采集器

祝使用愉快！🎉

