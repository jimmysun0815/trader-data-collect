# 文件名迁移工具使用指南

## 📋 问题说明

之前的采集器代码在文件名中包含了启动时间戳，导致重启服务会创建新文件：

```
旧格式（有问题）:
btc-updown-15m-1768110300_20260110_214626.jsonl  ❌
btc-updown-15m-1768110300_20260110_220134.jsonl  ❌
-> 同一个窗口，但因为重启时间不同，创建了2个文件

新格式（正确）:
btc-updown-15m-1768110300.jsonl  ✅
-> 同一个窗口只有一个文件，重启会继续append
```

## 🔧 解决方案

**`migrate_filenames.py`** 脚本会：
1. 扫描所有旧格式文件
2. 按market_slug分组（去掉时间戳）
3. 合并同一窗口的多个文件
4. 保留旧文件（添加.old后缀）作为备份

## 🚀 使用方法

### 在VPS上运行

```bash
# 1. 上传脚本到VPS
cd /Users/jimmysun/Desktop/workspace/polymarket/collect_data
scp migrate_filenames.py ubuntu@YOUR_VPS:/home/ubuntu/trader-data-collect/

# 2. 在VPS上执行（先干跑测试）
ssh ubuntu@YOUR_VPS
cd /home/ubuntu/trader-data-collect

# 模拟运行，查看会做什么
./venv/bin/python3 migrate_filenames.py --dry-run

# 3. 确认无误后，执行实际迁移
./venv/bin/python3 migrate_filenames.py

# 4. 验证结果
ls -lh real_hot/*.jsonl | head -10
```

### 本地测试（可选）

```bash
# 在本地先测试脚本逻辑
cd /Users/jimmysun/Desktop/workspace/polymarket/collect_data
chmod +x test_migrate.sh
./test_migrate.sh
```

## 📊 输出示例

### 模拟运行（--dry-run）

```
=======================================================================
Polymarket数据文件名迁移工具
=======================================================================

扫描目录: /home/ubuntu/trader-data-collect/real_hot
模式: 模拟运行

发现 15 个文件

需要迁移的文件组: 4
已是新格式（跳过）: 3

跳过的文件（已是新格式）:
  - btc-updown-15m-1768111200.jsonl
  - cex_btc_20260111_00-12.csv
  - cex_eth_20260111_00-12.csv

=======================================================================
开始迁移...
=======================================================================

[1/4] 处理: btc-updown-15m-1768110300.jsonl
  发现 3 个旧文件:
    - btc-updown-15m-1768110300_20260110_214626.jsonl
    - btc-updown-15m-1768110300_20260110_220134.jsonl
    - btc-updown-15m-1768110300_20260110_223405.jsonl
  [模拟] 将合并到: btc-updown-15m-1768110300.jsonl

[2/4] 处理: eth-updown-15m-1768110300.jsonl
  发现 2 个旧文件:
    - eth-updown-15m-1768110300_20260110_214627.jsonl
    - eth-updown-15m-1768110300_20260110_220135.jsonl
  [模拟] 将合并到: eth-updown-15m-1768110300.jsonl

...

=======================================================================
迁移完成: 4/4 组文件
=======================================================================

提示: 移除 --dry-run 参数以执行实际迁移
```

### 实际运行

```
[1/4] 处理: btc-updown-15m-1768110300.jsonl
  发现 3 个旧文件:
    - btc-updown-15m-1768110300_20260110_214626.jsonl
    - btc-updown-15m-1768110300_20260110_220134.jsonl
    - btc-updown-15m-1768110300_20260110_223405.jsonl
    合并 3 个文件 -> btc-updown-15m-1768110300.jsonl
      写入 450 行
    备份: btc-updown-15m-1768110300_20260110_214626.jsonl -> ...old
    备份: btc-updown-15m-1768110300_20260110_220134.jsonl -> ...old
    备份: btc-updown-15m-1768110300_20260110_223405.jsonl -> ...old
  ✅ 完成: btc-updown-15m-1768110300.jsonl

...

注意:
- 旧文件已重命名为 .old 后缀，请验证新文件正常后再删除
- 删除旧文件命令: rm /path/to/real_hot/*.old
```

## ✅ 验证步骤

### 1. 检查新文件格式

```bash
# 查看新文件（不应有时间戳）
ls -lh /home/ubuntu/trader-data-collect/real_hot/*.jsonl

# 应该看到:
btc-updown-15m-1768110300.jsonl          ✅
eth-updown-15m-1768110300.jsonl          ✅
bitcoin-up-or-down-january-11-12am-et.jsonl  ✅

# 不应该看到:
btc-updown-15m-1768110300_20260110_214626.jsonl  ❌
```

### 2. 检查备份文件

```bash
# 查看.old备份文件
ls -lh /home/ubuntu/trader-data-collect/real_hot/*.old

# 应该看到所有旧文件都有.old后缀
btc-updown-15m-1768110300_20260110_214626.jsonl.old
btc-updown-15m-1768110300_20260110_220134.jsonl.old
...
```

### 3. 验证数据完整性

```bash
# 随机抽查一个新文件的行数
wc -l /home/ubuntu/trader-data-collect/real_hot/btc-updown-15m-1768110300.jsonl

# 对比原始文件的总行数
wc -l /home/ubuntu/trader-data-collect/real_hot/btc-updown-15m-1768110300_*.old

# 新文件行数 = 所有旧文件行数之和
```

### 4. 验证JSON格式

```bash
# 随机检查几行是否是有效的JSON
tail -5 /home/ubuntu/trader-data-collect/real_hot/btc-updown-15m-1768110300.jsonl | python3 -m json.tool
```

## 🗑️ 清理旧文件

**确认新文件正常后**，删除.old备份：

```bash
# 确认一切正常
cd /home/ubuntu/trader-data-collect/real_hot

# 查看将要删除的文件
ls -lh *.old

# 确认无误后删除
rm *.old

# 或者移动到临时备份目录（更安全）
mkdir -p /home/ubuntu/old_backups
mv *.old /home/ubuntu/old_backups/
```

## ⚠️ 注意事项

1. **先模拟运行** - 始终先用 `--dry-run` 查看会做什么
2. **停止服务** - 迁移前先停止采集服务，避免文件被占用
3. **保留备份** - 脚本会保留.old备份，验证后再删除
4. **数据完整性** - 迁移后验证新文件行数和内容
5. **磁盘空间** - 迁移过程中会临时占用双倍空间

## 🔄 恢复旧文件（如果需要）

如果迁移后发现问题，可以恢复：

```bash
cd /home/ubuntu/trader-data-collect/real_hot

# 删除新文件
rm btc-updown-15m-1768110300.jsonl

# 恢复旧文件
for f in btc-updown-15m-1768110300_*.old; do 
    mv "$f" "${f%.old}"
done
```

## 📚 相关文档

- **VPS_UPDATE_GUIDE.md** - 完整的VPS更新部署指南
- **test_migrate.sh** - 本地测试脚本

## 💡 FAQ

### Q: 迁移会丢失数据吗？
A: 不会。脚本会合并所有旧文件的内容到新文件，并保留.old备份。

### Q: CEX文件会被迁移吗？
A: 不会。CEX文件已经是正确格式（12小时分割），会被自动跳过。

### Q: 迁移需要多久？
A: 取决于文件数量和大小。通常几秒到几分钟。

### Q: 可以增量迁移吗？
A: 可以。多次运行脚本是安全的，已迁移的文件会被跳过。

### Q: 迁移失败怎么办？
A: 旧文件会保留.old后缀，可以手动恢复。参考"恢复旧文件"部分。

---

**迁移后，重启服务就不会再创建重复文件了！** ✅

