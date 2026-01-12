# VPS更新指南 - 修复文件重复创建问题

## 📋 本次更新内容

### 1. **修复Polymarket采集器文件名问题** ✅
   - **问题**：重启服务会创建新文件（因为文件名包含启动时间戳）
   - **修复**：文件名只基于市场slug，重启会继续写入同一文件
   - **影响文件**：`polymarket_multi_market_recorder.py`

### 2. **添加systemd定时归档** ✅
   - **问题**：之前没有自动归档任务，数据一直在real_hot/累积
   - **新增**：systemd timer每天凌晨4点自动归档30天前数据
   - **新增文件**：
     - `systemd/archive-data.service`
     - `systemd/archive-data.timer`

### 3. **热数据保留时长改为30天** ✅
   - **之前**：7天
   - **现在**：30天
   - **影响文件**：`archive_old_data.py`, `deploy_vps.sh`

---

## 🚀 部署步骤

### 方法A：完整重新部署（推荐，确保一致性）

```bash
# 1. 在本地打包
cd /Users/jimmysun/Desktop/workspace/polymarket/collect_data
tar -czf update.tar.gz \
  polymarket_multi_market_recorder.py \
  archive_old_data.py \
  deploy_vps.sh \
  systemd/

# 2. 上传到VPS
scp update.tar.gz ubuntu@YOUR_VPS:/home/ubuntu/

# 3. 在VPS上执行
ssh ubuntu@YOUR_VPS

cd /home/ubuntu
tar -xzf update.tar.gz -C trader-data-collect/

# 4. 停止现有服务
systemctl --user stop polymarket-recorder.service cex-recorder.service

# 5. 重新安装systemd服务（包含新的timer）
cd trader-data-collect/systemd
./install_services.sh

# 6. 启动所有服务
systemctl --user start polymarket-recorder.service cex-recorder.service archive-data.timer

# 7. 验证
systemctl --user status polymarket-recorder.service
systemctl --user status archive-data.timer
systemctl --user list-timers
```

### 方法B：增量更新（快速）

```bash
# 1. 上传修改的文件
cd /Users/jimmysun/Desktop/workspace/polymarket/collect_data
scp polymarket_multi_market_recorder.py ubuntu@YOUR_VPS:/home/ubuntu/trader-data-collect/
scp archive_old_data.py ubuntu@YOUR_VPS:/home/ubuntu/trader-data-collect/
scp migrate_filenames.py ubuntu@YOUR_VPS:/home/ubuntu/trader-data-collect/
scp systemd/archive-data.service ubuntu@YOUR_VPS:/home/ubuntu/trader-data-collect/systemd/
scp systemd/archive-data.timer ubuntu@YOUR_VPS:/home/ubuntu/trader-data-collect/systemd/
scp systemd/install_services.sh ubuntu@YOUR_VPS:/home/ubuntu/trader-data-collect/systemd/

# 2. 在VPS上更新服务
ssh ubuntu@YOUR_VPS << 'EOF'
cd /home/ubuntu/trader-data-collect

# 停止服务
systemctl --user stop polymarket-recorder.service

# 【重要】迁移现有文件名（先干跑测试）
echo "=== 检查需要迁移的文件 ==="
./venv/bin/python3 migrate_filenames.py --dry-run
echo ""
read -p "确认要执行迁移吗？(y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo "=== 执行文件名迁移 ==="
    ./venv/bin/python3 migrate_filenames.py
fi

# 复制新的systemd文件
cp systemd/archive-data.service ~/.config/systemd/user/
cp systemd/archive-data.timer ~/.config/systemd/user/

# 重新加载
systemctl --user daemon-reload

# 启用并启动
systemctl --user enable archive-data.timer
systemctl --user start polymarket-recorder.service archive-data.timer

# 验证
echo "=== 服务状态 ==="
systemctl --user status polymarket-recorder.service --no-pager
echo ""
echo "=== 定时器状态 ==="
systemctl --user status archive-data.timer --no-pager
echo ""
echo "=== 所有定时器 ==="
systemctl --user list-timers --all
EOF
```

---

## ✅ 验证清单

### 0. 文件名迁移验证（新增）
```bash
ssh ubuntu@YOUR_VPS
cd /home/ubuntu/trader-data-collect

# 检查是否还有旧格式文件
ls -lh real_hot/*_2026*.jsonl 2>/dev/null

# 应该没有输出，如果有输出说明迁移未完成
```

### 1. Polymarket采集器正常运行
```bash
ssh ubuntu@YOUR_VPS
systemctl --user status polymarket-recorder.service
journalctl --user -u polymarket-recorder.service -n 20
```

**预期输出**：
- 状态：`Active: active (running)`
- 日志：`[btc_15m] bid=0.01 ask=0.99` 这样的输出

### 2. 文件名正确（无时间戳）
```bash
ssh ubuntu@YOUR_VPS
ls -lh /home/ubuntu/trader-data-collect/real_hot/*.jsonl | tail -5
```

**预期文件名格式**：
```
btc-updown-15m-1768xxx.jsonl          # ✅ 正确
eth-updown-15m-1768xxx.jsonl          # ✅ 正确
bitcoin-up-or-down-january-xx-xam-et.jsonl  # ✅ 正确

# 不应该再有这种：
btc-updown-15m-1768xxx_20260111_140523.jsonl  # ❌ 旧版本
```

### 3. 定时归档已启用
```bash
ssh ubuntu@YOUR_VPS
systemctl --user list-timers
```

**预期输出**：
```
NEXT                        LEFT       LAST PASSED UNIT               ACTIVATES
Sat 2026-01-11 04:00:00 UTC 8h left    n/a  n/a    archive-data.timer archive-data.service
```

### 4. 手动测试归档（干跑）
```bash
ssh ubuntu@YOUR_VPS
cd /home/ubuntu/trader-data-collect
./venv/bin/python3 archive_old_data.py --days 30 --dry-run
```

**预期输出**：
```
归档阈值: 30天前的数据
扫描文件数: xxx
归档文件数: xxx (30天前的)
这是模拟运行，未实际移动文件
```

---

## 🔍 故障排查

### 问题1：定时器未启动

```bash
# 检查timer状态
systemctl --user status archive-data.timer

# 如果是"inactive (dead)"，手动启动
systemctl --user enable archive-data.timer
systemctl --user start archive-data.timer
```

### 问题2：Polymarket服务启动失败

```bash
# 查看详细错误
journalctl --user -u polymarket-recorder.service -n 50

# 常见问题：
# - 路径错误：检查 /home/ubuntu/trader-data-collect/polymarket_multi_market_recorder.py
# - 虚拟环境：检查 /home/ubuntu/trader-data-collect/venv/bin/python3
```

### 问题3：归档脚本报错

```bash
# 手动运行测试
cd /home/ubuntu/trader-data-collect
./venv/bin/python3 archive_old_data.py --days 30 --dry-run

# 查看日志
tail -50 /home/ubuntu/trader-data-collect/logs/archive.log
```

### 问题4：文件名迁移失败

```bash
# 查看迁移脚本输出
cd /home/ubuntu/trader-data-collect
./venv/bin/python3 migrate_filenames.py --dry-run

# 如果迁移中断，旧文件会保留.old后缀
# 恢复旧文件（如果需要）：
for f in real_hot/*.old; do 
    mv "$f" "${f%.old}"
done
```

---

## 📊 更新后的系统行为

### 文件生命周期

```
数据采集 -> real_hot/ (最近30天)
              ↓ (每天4am自动归档)
         real_archive/YYYY-MM/ (30天前的数据)
              ↓ (本地需要时)
         本地real_cache/ (按需下载缓存)
```

### 服务重启行为

**之前**（有问题）：
```bash
重启 -> 创建新文件 btc-updown-15m-1768xxx_20260111_140523.jsonl
再重启 -> 又创建新文件 btc-updown-15m-1768xxx_20260111_150234.jsonl
结果：同一个窗口有多个文件 ❌
```

**现在**（修复后）：
```bash
启动 -> 创建/打开文件 btc-updown-15m-1768xxx.jsonl
重启 -> 继续写入同一文件 btc-updown-15m-1768xxx.jsonl (append模式)
结果：同一个窗口只有一个文件 ✅
```

### 文件名迁移工具

**`migrate_filenames.py` 功能**：
1. 扫描所有带时间戳的旧文件
2. 按market_slug分组
3. 合并同一窗口的多个文件
4. 保留旧文件（.old后缀）以防万一

**示例**：
```bash
# 迁移前
btc-updown-15m-1768110300_20260110_214626.jsonl  # 100行
btc-updown-15m-1768110300_20260110_220134.jsonl  # 50行

# 迁移后
btc-updown-15m-1768110300.jsonl  # 150行（合并）
btc-updown-15m-1768110300_20260110_214626.jsonl.old  # 备份
btc-updown-15m-1768110300_20260110_220134.jsonl.old  # 备份
```

---

## 💡 后续维护

### 查看定时归档是否执行

```bash
# 查看上次执行时间和下次执行时间
systemctl --user list-timers archive-data.timer

# 查看归档日志
journalctl --user -u archive-data.service -n 100

# 或者查看日志文件
tail -50 /home/ubuntu/trader-data-collect/logs/archive.log
```

### 手动触发归档（如需要）

```bash
# 立即执行一次归档
systemctl --user start archive-data.service

# 查看执行结果
journalctl --user -u archive-data.service -n 50
```

### 调整热数据时长（如需要）

```bash
# 编辑timer，改为15天
systemctl --user edit archive-data.service

# 添加：
[Service]
ExecStart=
ExecStart=/home/ubuntu/trader-data-collect/venv/bin/python3 /home/ubuntu/trader-data-collect/archive_old_data.py --days 15 ...

# 重新加载并重启
systemctl --user daemon-reload
systemctl --user restart archive-data.timer
```

---

## ✅ 更新完成检查

运行以下命令确认所有正常：

```bash
ssh ubuntu@YOUR_VPS "bash -s" << 'EOF'
echo "=== 系统状态检查 ==="
echo ""
echo "1. 采集服务状态:"
systemctl --user is-active polymarket-recorder.service cex-recorder.service
echo ""
echo "2. 定时器状态:"
systemctl --user is-active archive-data.timer
echo ""
echo "3. 下次归档时间:"
systemctl --user list-timers archive-data.timer --no-pager
echo ""
echo "4. 最新文件（检查文件名格式）:"
ls -lht /home/ubuntu/trader-data-collect/real_hot/*.jsonl | head -5
echo ""
echo "5. 数据统计:"
echo "  Polymarket文件数: $(ls /home/ubuntu/trader-data-collect/real_hot/*.jsonl 2>/dev/null | wc -l)"
echo "  CEX文件数: $(ls /home/ubuntu/trader-data-collect/real_hot/cex_*.csv 2>/dev/null | wc -l)"
echo "  热数据大小: $(du -sh /home/ubuntu/trader-data-collect/real_hot 2>/dev/null | cut -f1)"
echo ""
echo "✅ 检查完成！"
EOF
```

**预期所有服务都是 `active`，定时器显示下次执行时间为明天凌晨4点。**

---

**更新完成！系统现在会自动管理数据归档，且重启不会创建重复文件了。** 🎉

