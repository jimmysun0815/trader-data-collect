# SSHFS数据传输机制说明

## 核心问题：SSHFS不会真正下载文件到本地

### SSHFS工作原理

```
┌─────────────────────────────────────────────────────────┐
│  你的Mac                                                 │
│                                                          │
│  ~/polymarket/real_hot/  ← SSHFS挂载点                  │
│         │                   (不是真正的本地文件)          │
│         │                                                │
│         └─SSH隧道──→  VPS:/root/polymarket/real_hot/    │
│                          (文件实际存储位置)               │
│                                                          │
│  内存/系统缓存：最近读过的数据块(重启后消失)              │
└─────────────────────────────────────────────────────────┘
```

### 运行12小时后的实际情况

| 时间 | 你读取的文件 | 本地磁盘 | VPS |
|------|-------------|----------|-----|
| 0h | - | 无 | 无 |
| 1h | 读取窗口1-4的.jsonl | 系统缓存中 | 有 |
| 6h | 读取窗口1-24的.jsonl | 部分在缓存 | 有 |
| 12h | 读取窗口1-48的.jsonl | 最近的在缓存 | 有 |
| **断开SSHFS** | ❌ 全部不可访问 | **无** | 有 |

**结论：你本地磁盘上没有真正的文件！**

---

## 当前设计中真正在本地的文件

### ✅ real_cache/ - 冷数据缓存

```python
# 只有当你通过 data_accessor.py 访问旧数据时
# 才会用rsync真正下载到本地

from data_accessor import DataAccessor
accessor = DataAccessor()

# 这会触发rsync，真正下载到 ~/polymarket/real_cache/
old_data = accessor.list_data_files(
    start_time=datetime(2025, 12, 1),
    end_time=datetime(2025, 12, 31)
)
```

### ❌ real_hot/ - SSHFS透明访问

```python
# 这不会下载文件，只是通过SSH读取
with open("~/polymarket/real_hot/btc-updown-15m-xxx.jsonl") as f:
    data = json.loads(f.read())  # ← 每次都从VPS读取
```

---

## 解决方案

### 选项1：定时备份脚本（推荐）

**我已创建 `backup_hot_data.sh`**

```bash
# 每天运行一次，真正下载最近3天的数据到本地
./collect_data/backup_hot_data.sh 3

# 添加到crontab（每天凌晨3点）
0 3 * * * cd ~/Desktop/workspace/polymarket && ./collect_data/backup_hot_data.sh 3
```

**效果：**
- ✅ 真正下载文件到 `~/polymarket/real_backup/`
- ✅ SSHFS断开也能访问这些备份
- ✅ 自动清理超过N天的旧备份

### 选项2：增强monitor_sync.py自动备份

可以让 `monitor_sync.py` 每小时自动备份最新数据：

```python
# monitor_sync.py 新增功能
def auto_backup_recent_data(hours=12):
    """自动备份最近N小时的数据到本地"""
    # 使用rsync增量同步
    # 只下载新文件，不重复下载
```

### 选项3：交易机器人自己缓存

```python
# 在你的交易机器人中
class DataCache:
    def __init__(self):
        self.cache = {}  # 内存中保存最近10个窗口
    
    def load_window(self, window_slug):
        if window_slug in self.cache:
            return self.cache[window_slug]  # 从内存读取
        
        # 从SSHFS读取（只读一次）
        data = load_from_sshfs(window_slug)
        self.cache[window_slug] = data
        
        # 可选：同时保存到本地磁盘
        save_to_local_disk(window_slug, data)
        
        return data
```

---

## 推荐配置

### 对于交易机器人（低延迟优先）

1. **主要数据源：SSHFS** （500ms首次延迟，后续极快）
2. **内存缓存：最近10个窗口** （机器人自己维护）
3. **本地备份：每天一次** （`backup_hot_data.sh`）
4. **监控：monitor_sync.py** （自动检测SSHFS状态）

### 对于数据分析（可靠性优先）

1. **优先使用 data_accessor.py** （自动管理缓存）
2. **定期全量备份** （rsync整个real_hot/）
3. **冷热分离** （旧数据走real_cache/）

---

## 性能对比

| 访问方式 | 首次延迟 | 后续访问 | 断网影响 | 占用磁盘 |
|---------|---------|---------|---------|---------|
| **SSHFS直接读** | 500ms-1s | <50ms(缓存) | ❌ 完全断开 | 0 |
| **本地备份** | 0ms | <10ms | ✅ 无影响 | 全部占用 |
| **内存缓存** | 首次500ms | <1ms | ❌ 完全断开 | 0(仅内存) |
| **data_accessor** | 首次5-10s | <50ms | ⚠️ 部分可用 | 按需占用 |

---

## 总结

### 🎯 回答你的问题

> "运行12小时后，本地是否有了这12小时的文件？"

**答案：❌ 没有！**

- SSHFS只是让你"看起来能访问"这些文件
- 实际上文件还在VPS上
- 本地只有系统缓存（重启后消失）
- **要真正在本地有文件，需要运行 `backup_hot_data.sh` 或 rsync**

### 💡 建议

1. **现在就运行一次备份**：`./collect_data/backup_hot_data.sh 3`
2. **添加到crontab**：每天自动备份
3. **交易机器人自己做内存缓存**：避免重复读取同一个窗口

需要我帮你配置自动备份cron任务吗？

