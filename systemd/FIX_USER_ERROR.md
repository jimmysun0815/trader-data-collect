# 修复Systemd服务错误

## 问题原因

错误信息：
```
Failed to determine supplementary groups: Operation not permitted
status=216/GROUP
```

**原因**: 用户级systemd服务（`systemctl --user`）不能使用 `User=` 指令。

- ❌ 用户级服务：不能指定 `User=`
- ✅ 系统级服务：可以指定 `User=`

## 解决方案

已移除服务文件中的 `User=ubuntu` 行。

用户级服务会自动以运行 `systemctl --user` 的用户身份运行。

---

## 在VPS上更新服务

### 方法1: 重新上传并安装（推荐）

```bash
# 1. 在本地重新上传systemd目录
scp -r systemd/ ubuntu@<your-vps-ip>:/home/ubuntu/trader-data-collect/

# 2. SSH到VPS
ssh ubuntu@<your-vps-ip>

# 3. 重新安装服务
cd /home/ubuntu/trader-data-collect/systemd
./install_services.sh

# 4. 启动服务
systemctl --user start polymarket-recorder.service cex-recorder.service

# 5. 查看状态
systemctl --user status polymarket-recorder.service
```

### 方法2: 手动编辑现有服务

```bash
# SSH到VPS
ssh ubuntu@<your-vps-ip>

# 编辑polymarket服务
nano ~/.config/systemd/user/polymarket-recorder.service

# 找到并删除这一行：
# User=ubuntu

# 编辑cex服务
nano ~/.config/systemd/user/cex-recorder.service

# 找到并删除这一行：
# User=ubuntu

# 重新加载systemd
systemctl --user daemon-reload

# 重启服务
systemctl --user restart polymarket-recorder.service cex-recorder.service

# 查看状态
systemctl --user status polymarket-recorder.service
```

---

## 验证修复

```bash
# 查看服务状态（应该是running）
systemctl --user status polymarket-recorder.service

# 查看日志（应该没有GROUP错误）
journalctl --user -u polymarket-recorder.service -n 50

# 检查进程
ps aux | grep recorder

# 查看生成的文件
ls -lh /home/ubuntu/trader-data-collect/real_hot/
```

---

## 正确的服务文件

### polymarket-recorder.service

```ini
[Unit]
Description=Polymarket Multi-Market Data Recorder
After=network.target
Wants=network-online.target

[Service]
Type=simple
# 注意：没有User=这一行！
WorkingDirectory=/home/ubuntu/trader-data-collect/scripts
ExecStart=/home/ubuntu/trader-data-collect/venv/bin/python3 /home/ubuntu/trader-data-collect/scripts/polymarket_multi_market_recorder.py

Restart=always
RestartSec=10

StandardOutput=journal
StandardError=journal
SyslogIdentifier=polymarket-recorder

MemoryLimit=512M
CPUQuota=50%

Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
```

### cex-recorder.service

```ini
[Unit]
Description=CEX Multi-Asset Order Book Recorder
After=network.target
Wants=network-online.target

[Service]
Type=simple
# 注意：没有User=这一行！
WorkingDirectory=/home/ubuntu/trader-data-collect/scripts
ExecStart=/home/ubuntu/trader-data-collect/venv/bin/python3 /home/ubuntu/trader-data-collect/scripts/cex_multi_asset_recorder.py

Restart=always
RestartSec=10

StandardOutput=journal
StandardError=journal
SyslogIdentifier=cex-recorder

MemoryLimit=512M
CPUQuota=50%

Environment="PYTHONUNBUFFERED=1"

[Install]
WantedBy=multi-user.target
```

---

## 关键知识点

### 用户级 vs 系统级服务

| 类型 | 命令 | 位置 | User字段 |
|------|------|------|---------|
| **用户级** | `systemctl --user` | `~/.config/systemd/user/` | ❌ 不能用 |
| **系统级** | `systemctl` (需要sudo) | `/etc/systemd/system/` | ✅ 可以用 |

### 为什么我们用用户级服务？

✅ **优点**:
- 不需要sudo权限
- 自动以当前用户运行
- 日志独立管理
- 适合个人项目

❌ **限制**:
- 不能指定User
- 需要启用linger才能开机自启

---

## 故障排查

如果服务仍然失败：

```bash
# 1. 检查Python路径
ls -l /home/ubuntu/trader-data-collect/venv/bin/python3

# 2. 检查脚本路径
ls -l /home/ubuntu/trader-data-collect/scripts/polymarket_multi_market_recorder.py

# 3. 手动测试运行
cd /home/ubuntu/trader-data-collect/scripts
../venv/bin/python3 polymarket_multi_market_recorder.py
# 按Ctrl+C停止

# 4. 检查Python依赖
../venv/bin/pip list | grep requests

# 5. 查看完整日志
journalctl --user -u polymarket-recorder.service --no-pager
```

---

现在服务文件已修复，请按照上面的步骤重新部署！

