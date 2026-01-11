# CEX数据访问说明

CEX数据（`cex_multi_venue_books.csv`）采用CSV格式，适合时间序列存储，与Polymarket的按窗口JSONL不同。

## 📊 数据格式

### CEX订单簿数据
- **位置**: VPS上 `~/polymarket/logs/cex_multi_venue_books.csv`
- **格式**: CSV时间序列（追加模式）
- **频率**: 默认1Hz（可配置）
- **字段**:
  - `ts_sample_utc`, `t_sample_unix` - 采样时间
  - `venue` - 交易所（如 binance_spot, okx_swap）
  - `best_bid`, `best_ask`, `mid`, `spread` - 盘口价格
  - `bid_notional`, `ask_notional` - 订单簿深度
  - `imb`, `micro`, `micro_edge` - 微观结构指标

## 🔄 访问方式

### 方式1: 定期同步到本地（推荐）

CEX数据是单一CSV文件，定期同步到本地即可：

```bash
# 手动同步
rsync -avz --progress \
  $VPS_USER@$VPS_HOST:~/polymarket/logs/cex_multi_venue_books.csv \
  ~/Desktop/workspace/polymarket/real/

# 或使用scp
scp $VPS_USER@$VPS_HOST:~/polymarket/logs/cex_multi_venue_books.csv \
    ~/Desktop/workspace/polymarket/real/
```

### 方式2: 设置自动同步

创建定时任务每5分钟同步：

```bash
# 编辑crontab
crontab -e

# 添加
*/5 * * * * rsync -az $VPS_USER@$VPS_HOST:~/polymarket/logs/cex_multi_venue_books.csv ~/Desktop/workspace/polymarket/real/ 2>&1 | logger -t cex_sync
```

### 方式3: 直接通过SSH读取（小规模分析）

```python
import subprocess
import pandas as pd
from io import StringIO

# 读取远程CSV
cmd = f"ssh {vps_user}@{vps_host} 'cat ~/polymarket/logs/cex_multi_venue_books.csv'"
result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
df = pd.read_csv(StringIO(result.stdout))

# 或者只读取最近N行
cmd = f"ssh {vps_user}@{vps_host} 'tail -n 10000 ~/polymarket/logs/cex_multi_venue_books.csv'"
```

## 📈 数据增长

- **速度**: ~1KB/秒（1Hz采样，5个交易所）
- **每天**: ~80MB
- **每月**: ~2.4GB
- **建议**: 
  - 每周同步一次到本地
  - VPS上保留最近3个月数据
  - 旧数据可以压缩归档（gzip压缩率约90%）

## 🔧 管理命令

### 在VPS上

```bash
# 查看文件大小
ls -lh ~/polymarket/logs/cex_multi_venue_books.csv

# 查看最近10行
tail -n 10 ~/polymarket/logs/cex_multi_venue_books.csv

# 统计行数
wc -l ~/polymarket/logs/cex_multi_venue_books.csv

# 压缩旧数据（可选）
gzip -c ~/polymarket/logs/cex_multi_venue_books.csv > ~/polymarket/logs/cex_$(date +%Y%m%d).csv.gz
```

### 在本地

```python
# 读取CEX数据
import pandas as pd

df = pd.read_csv("real/cex_multi_venue_books.csv")
print(f"总行数: {len(df)}")
print(f"时间范围: {df['ts_sample_utc'].min()} - {df['ts_sample_utc'].max()}")
print(f"交易所: {df['venue'].unique()}")
```

## 💡 与Polymarket数据的区别

| 特性 | Polymarket数据 | CEX数据 |
|-----|---------------|---------|
| 格式 | JSONL（按窗口） | CSV（时间序列） |
| 切分 | 每个15分钟窗口一个文件 | 单一文件追加 |
| 热数据 | SSHFS实时挂载 | 定期同步 |
| 归档 | 自动按月归档 | 手动管理 |
| 访问 | 按需拉取单窗口 | 读取整个CSV或tail |
| 数据量 | 每天~500MB | 每天~80MB |

## 🚀 推荐实践

1. **实时分析**: 使用SSHFS挂载的Polymarket数据
2. **CEX数据**: 每小时/每天同步一次到本地
3. **历史回测**: Polymarket用代理层按需拉取，CEX直接读取本地CSV
4. **空间优化**: 本地保留最近1个月CEX数据，旧数据在VPS归档

## 📝 示例：联合使用两个数据源

```python
import pandas as pd
from pathlib import Path
import sys

# 使用Polymarket数据代理
sys.path.insert(0, '/path/to/collect_data')
from data_accessor import DataAccessor

# 获取Polymarket数据（自动热/冷路由）
accessor = DataAccessor()
windows = accessor.list_all_windows()

# 读取CEX数据
cex_df = pd.read_csv("real/cex_multi_venue_books.csv")
cex_df['t_unix'] = pd.to_datetime(cex_df['t_sample_unix'], unit='s')

# 联合分析
for ws in windows[-10:]:  # 最近10个窗口
    poly_path = accessor.get_window_jsonl(ws)
    
    # 获取该窗口对应的CEX数据
    window_start = pd.to_datetime(ws, unit='s')
    window_end = window_start + pd.Timedelta(minutes=15)
    
    cex_window = cex_df[
        (cex_df['t_unix'] >= window_start) & 
        (cex_df['t_unix'] < window_end)
    ]
    
    print(f"窗口 {ws}:")
    print(f"  Polymarket文件: {poly_path.name}")
    print(f"  CEX数据点: {len(cex_window)}")
```

---

**提示**: 对于简单的单个CSV文件，定期同步比SSHFS挂载更简单高效。

