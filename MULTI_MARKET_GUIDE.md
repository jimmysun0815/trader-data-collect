# 多市场数据采集说明

## 概述

系统现已支持同时采集多个Polymarket市场和CEX资产的实时数据。

## 支持的市场

### Polymarket市场

| 市场 | 类型 | Slug格式 | 更新频率 |
|------|------|---------|---------|
| BTC 15分钟 | 短期 | `btc-updown-15m-{timestamp}` | 每15分钟切换 |
| ETH 15分钟 | 短期 | `eth-updown-15m-{timestamp}` | 每15分钟切换 |
| BTC 1小时 | 中期 | `bitcoin-up-or-down-{date}-{hour}pm-et` | 每小时切换 |
| ETH 1小时 | 中期 | `ethereum-up-or-down-{date}-{hour}pm-et` | 每小时切换 |

### CEX资产

| 资产 | 交易所 | 交易对 |
|------|--------|--------|
| BTC | Binance Spot | BTCUSDT |
| BTC | OKX Spot | BTC-USDT |
| BTC | OKX Swap | BTC-USDT-SWAP |
| BTC | Bybit Spot | BTCUSDT |
| BTC | Bybit Linear | BTCUSDT |
| ETH | Binance Spot | ETHUSDT |
| ETH | OKX Spot | ETH-USDT |
| ETH | OKX Swap | ETH-USDT-SWAP |
| ETH | Bybit Spot | ETHUSDT |
| ETH | Bybit Linear | ETHUSDT |

---

## 采集脚本

### 1. `polymarket_multi_market_recorder.py`

**功能**: 同时采集BTC和ETH的15分钟和1小时Polymarket市场

**输出格式**: JSONL文件，每个市场每15分钟一个文件

**输出路径**: `../real_hot/{market_slug}_{timestamp}.jsonl`

**文件示例**:
```
btc-updown-15m-1768102200_20260110_193559.jsonl
eth-updown-15m-1768102200_20260110_193600.jsonl
bitcoin-up-or-down-january-10-10pm-et_20260110_193600.jsonl
ethereum-up-or-down-january-10-10pm-et_20260110_193600.jsonl
```

**数据格式**:
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
    },
    {
      "outcome": "No",
      "token_id": "yyyyy",
      "orderbook": {...}
    }
  ]
}
```

**使用方法**:
```bash
# 本地测试
cd collect_data
./venv/bin/python3 polymarket_multi_market_recorder.py

# VPS后台运行
cd ~/polymarket/scripts
nohup python3 polymarket_multi_market_recorder.py > ../logs/poly_multi.log 2>&1 &
```

---

### 2. `cex_multi_asset_recorder.py`

**功能**: 同时采集BTC和ETH的多交易所order book数据

**输出格式**: CSV文件，每12小时自动切分

**输出路径**: `../real_hot/cex_{asset}_{date}_{session}.csv`

**文件示例**:
```
cex_btc_20260110_00-12.csv    # BTC数据，0:00-12:00 UTC
cex_btc_20260110_12-24.csv    # BTC数据，12:00-24:00 UTC
cex_eth_20260110_00-12.csv    # ETH数据，0:00-12:00 UTC
cex_eth_20260110_12-24.csv    # ETH数据，12:00-24:00 UTC
```

**数据格式** (CSV表头):
```
ts_sample_utc, t_sample_unix, sample_id, venue,
best_bid, best_ask, mid, spread,
bid_qty_l1, ask_qty_l1,
bid_notional, ask_notional, imb, micro, micro_edge,
err
```

**使用方法**:
```bash
# 本地测试
cd collect_data
./venv/bin/python3 cex_multi_asset_recorder.py

# VPS后台运行
cd ~/polymarket/scripts
nohup python3 cex_multi_asset_recorder.py > ../logs/cex_multi.log 2>&1 &
```

---

## 文件大小估算

### Polymarket数据

- **单个市场单个15分钟窗口**: ~100-500KB
- **4个市场同时运行**: ~400KB-2MB / 15分钟
- **每天数据量**: ~40-200MB
- **7天热数据**: ~280MB-1.4GB

### CEX数据

- **单个资产每秒5个venue**: ~500字节/行 × 5行 = 2.5KB/秒
- **单个资产12小时**: ~2.5KB × 3600s × 12h = ~108MB
- **BTC+ETH 12小时**: ~216MB
- **每天数据量**: ~432MB
- **7天热数据**: ~3GB

---

## SSHFS访问性能

### Polymarket数据（小文件）

| 操作 | 延迟 | 说明 |
|------|------|------|
| 首次读取新文件 | 500ms-1s | SSHFS网络延迟 |
| 再次读取同文件 | <50ms | 内核缓存生效 |
| 列出目录 | 100-300ms | 元数据缓存 |

**适合实时交易**: ✅ 每15分钟只有1次1秒延迟

### CEX数据（12小时切分）

| 文件大小 | 首次读取 | 再次读取 |
|---------|---------|---------|
| 0-50MB | 1-3s | <100ms |
| 50-100MB | 3-5s | <200ms |
| 100-200MB | 5-10s | <500ms |

**适合实时交易**: ✅ 每12小时切换一次，延迟可接受

---

## 部署到VPS

### 1. 准备工作

```bash
# 在本地collect_data目录
cd /path/to/polymarket/collect_data

# 确保虚拟环境已创建
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 2. 上传文件到VPS

```bash
# 上传采集脚本
scp polymarket_multi_market_recorder.py user@vps:~/polymarket/scripts/
scp cex_multi_asset_recorder.py user@vps:~/polymarket/scripts/
scp archive_old_data.py user@vps:~/polymarket/scripts/

# 上传部署脚本
scp deploy_vps.sh user@vps:~/polymarket/
```

### 3. 在VPS上运行部署脚本

```bash
ssh user@vps
cd ~/polymarket
chmod +x deploy_vps.sh
./deploy_vps.sh
```

### 4. 启动采集器

```bash
cd ~/polymarket/scripts
source ~/polymarket/venv/bin/activate

# 启动Polymarket多市场采集
nohup python3 polymarket_multi_market_recorder.py > ../logs/poly_multi.log 2>&1 &

# 启动CEX多资产采集
nohup python3 cex_multi_asset_recorder.py > ../logs/cex_multi.log 2>&1 &

# 检查进程
ps aux | grep recorder
```

### 5. 查看日志

```bash
# 实时查看日志
tail -f ~/polymarket/logs/poly_multi.log
tail -f ~/polymarket/logs/cex_multi.log

# 查看生成的文件
ls -lh ~/polymarket/real_hot/*.jsonl | tail -20
ls -lh ~/polymarket/real_hot/cex_*.csv
```

---

## 监控和维护

### 检查采集状态

```bash
# 检查进程是否运行
ps aux | grep recorder

# 检查最新文件
ls -lt ~/polymarket/real_hot/ | head -10

# 检查磁盘使用
du -sh ~/polymarket/real_hot
du -sh ~/polymarket/real_archive
```

### 重启采集器

```bash
# 停止所有采集器
pkill -f recorder

# 重新启动
cd ~/polymarket/scripts
nohup python3 polymarket_multi_market_recorder.py > ../logs/poly_multi.log 2>&1 &
nohup python3 cex_multi_asset_recorder.py > ../logs/cex_multi.log 2>&1 &
```

### 手动归档旧数据

```bash
cd ~/polymarket/scripts
python3 archive_old_data.py --dry-run  # 预览
python3 archive_old_data.py            # 实际执行
```

---

## 故障排查

### 问题1: Polymarket市场找不到

**症状**: 日志显示 `Market not found: xxx`

**原因**: 
- 1小时市场的命名规则可能变化
- 市场尚未创建

**解决**: 
```bash
# 手动测试API
cd collect_data
./venv/bin/python3 test_multi_markets.py
```

### 问题2: CEX API请求失败

**症状**: CSV中出现大量err列

**原因**:
- 网络问题
- API rate limit
- 交易所维护

**解决**:
```bash
# 检查日志详情
tail -100 ~/polymarket/logs/cex_multi.log

# 测试网络
curl -I https://api.binance.com/api/v3/depth?symbol=BTCUSDT
```

### 问题3: 文件未切分

**症状**: CEX文件一直增长超过12小时

**原因**: 时区或逻辑问题

**解决**:
```bash
# 检查系统时间
date -u  # 应该显示UTC时间

# 检查文件修改时间
ls -lh ~/polymarket/real_hot/cex_*.csv
```

---

## 性能优化建议

### 1. 减少采集频率

如果网络带宽有限：

```python
# 修改 cex_multi_asset_recorder.py
hz = 0.5  # 从1.0改为0.5，每2秒采集一次
```

### 2. 减少order book深度

如果只需要top 10档：

```python
# 修改采集脚本
limit = 10  # 从200改为10
```

### 3. 定期清理日志

```bash
# 添加到crontab
0 2 * * 0 find ~/polymarket/logs -name "*.log" -mtime +7 -delete
```

---

## 数据访问示例

### 读取Polymarket数据

```python
import json
from pathlib import Path

# 读取某个窗口的数据
data_file = Path("real_hot/btc-updown-15m-1768102200_20260110_193559.jsonl")

ticks = []
with open(data_file) as f:
    for line in f:
        tick = json.loads(line)
        ticks.append(tick)

print(f"加载了 {len(ticks)} 个tick")

# 提取Yes token的最优买卖价
yes_token = ticks[0]["tokens"][0]
best_bid = yes_token["orderbook"]["bids"][0][0]
best_ask = yes_token["orderbook"]["asks"][0][0]
print(f"Best bid: {best_bid}, Best ask: {best_ask}")
```

### 读取CEX数据

```python
import pandas as pd

# 读取12小时的CEX数据
df = pd.read_csv("real_hot/cex_btc_20260110_00-12.csv")

# 过滤某个交易所
binance = df[df["venue"] == "binance_spot"]

# 计算平均spread
avg_spread = binance["spread"].mean()
print(f"Binance平均spread: {avg_spread:.2f}")
```

---

## 总结

✅ **4个Polymarket市场**: BTC/ETH 15分钟 + 1小时
✅ **2个CEX资产**: BTC/ETH order book，每个5个venue
✅ **自动文件管理**: Polymarket 15分钟切分，CEX 12小时切分
✅ **SSHFS友好**: 小文件策略，延迟可控
✅ **完整部署流程**: 一键部署到VPS

有问题请查看主文档 `SYSTEM_SUMMARY.md` 或联系开发者。

