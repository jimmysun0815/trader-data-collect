# Systemd服务管理指南

## 概述

使用systemd管理Polymarket数据采集器，提供以下优势：

- ✅ **自动重启**: 进程崩溃自动重启
- ✅ **开机自启**: 服务器重启后自动启动
- ✅ **统一管理**: systemctl命令统一管理
- ✅ **日志集成**: journalctl查看日志
- ✅ **资源限制**: 内存、CPU配额控制

---

## 服务文件

### 1. `polymarket-recorder.service`

Polymarket多市场采集服务

**配置**:
- 工作目录: `~/polymarket/scripts`
- Python: `~/polymarket/venv/bin/python3`
- 自动重启: 是（延迟10秒）
- 资源限制: 内存512MB, CPU 50%

### 2. `cex-recorder.service`

CEX多资产order book采集服务

**配置**:
- 工作目录: `~/polymarket/scripts`
- Python: `~/polymarket/venv/bin/python3`
- 自动重启: 是（延迟10秒）
- 资源限制: 内存512MB, CPU 50%

### 3. `polymarket-recorders.target`

统一管理target（可选）

---

## 安装步骤

### 1. 上传服务文件到VPS

```bash
# 在本地
cd /path/to/polymarket/collect_data

# 创建systemd目录并上传
scp -r systemd/ user@vps:~/polymarket/
```

### 2. 在VPS上安装服务

```bash
# SSH到VPS
ssh user@vps

# 运行安装脚本
cd ~/polymarket/systemd
chmod +x install_services.sh
./install_services.sh
```

安装脚本会：
- 创建 `~/.config/systemd/user/` 目录
- 复制服务文件到systemd用户目录
- 重新加载systemd配置
- 启用开机自启

### 3. 启动服务

```bash
# 启动所有采集器
systemctl --user start polymarket-recorder.service cex-recorder.service

# 查看状态
systemctl --user status polymarket-recorder.service
systemctl --user status cex-recorder.service
```

---

## 常用命令

### 启动/停止/重启

```bash
# 启动单个服务
systemctl --user start polymarket-recorder.service

# 停止单个服务
systemctl --user stop polymarket-recorder.service

# 重启单个服务
systemctl --user restart polymarket-recorder.service

# 启动所有采集器
systemctl --user start polymarket-recorder.service cex-recorder.service

# 停止所有采集器
systemctl --user stop polymarket-recorder.service cex-recorder.service
```

### 查看状态

```bash
# 查看服务状态
systemctl --user status polymarket-recorder.service

# 查看所有polymarket相关服务
systemctl --user list-units 'polymarket-*' 'cex-*'

# 查看是否开机自启
systemctl --user is-enabled polymarket-recorder.service
```

### 日志查看

```bash
# 实时查看日志（类似tail -f）
journalctl --user -u polymarket-recorder.service -f

# 查看最近100行日志
journalctl --user -u polymarket-recorder.service -n 100

# 查看今天的日志
journalctl --user -u polymarket-recorder.service --since today

# 查看最近1小时的日志
journalctl --user -u polymarket-recorder.service --since "1 hour ago"

# 同时查看两个服务的日志
journalctl --user -u polymarket-recorder.service -u cex-recorder.service -f

# 查看错误日志
journalctl --user -u polymarket-recorder.service -p err
```

### 开机自启管理

```bash
# 启用开机自启
systemctl --user enable polymarket-recorder.service

# 禁用开机自启
systemctl --user disable polymarket-recorder.service

# 启用并立即启动
systemctl --user enable --now polymarket-recorder.service
```

---

## 日志管理

### 日志位置

systemd日志存储在journal中，使用`journalctl`查看：

```bash
# 查看所有polymarket相关日志
journalctl --user -u polymarket-recorder.service
journalctl --user -u cex-recorder.service
```

### 日志筛选

```bash
# 按时间范围
journalctl --user -u polymarket-recorder.service \
  --since "2024-01-10 00:00:00" \
  --until "2024-01-10 23:59:59"

# 按优先级
journalctl --user -u polymarket-recorder.service -p warning

# 导出日志到文件
journalctl --user -u polymarket-recorder.service > poly.log
```

### 日志格式

```bash
# JSON格式（便于解析）
journalctl --user -u polymarket-recorder.service -o json

# 详细格式
journalctl --user -u polymarket-recorder.service -o verbose

# 简短格式
journalctl --user -u polymarket-recorder.service -o short
```

---

## 资源限制

服务文件中已配置资源限制：

```ini
MemoryLimit=512M    # 内存限制512MB
CPUQuota=50%        # CPU使用不超过50%
```

### 修改资源限制

编辑服务文件：

```bash
# 编辑服务文件
systemctl --user edit polymarket-recorder.service

# 在编辑器中添加或修改：
[Service]
MemoryLimit=1G
CPUQuota=80%

# 重新加载配置
systemctl --user daemon-reload

# 重启服务
systemctl --user restart polymarket-recorder.service
```

### 查看资源使用

```bash
# 查看服务资源使用情况
systemctl --user status polymarket-recorder.service

# 详细资源信息
systemctl --user show polymarket-recorder.service | grep -E '(Memory|CPU)'
```

---

## 故障排查

### 问题1: 服务启动失败

```bash
# 查看详细错误信息
systemctl --user status polymarket-recorder.service -l

# 查看最近的错误日志
journalctl --user -u polymarket-recorder.service -n 50 -p err

# 检查Python路径
ls -l ~/polymarket/venv/bin/python3

# 检查脚本路径
ls -l ~/polymarket/scripts/polymarket_multi_market_recorder.py
```

### 问题2: 服务频繁重启

```bash
# 查看重启记录
journalctl --user -u polymarket-recorder.service | grep -i restart

# 查看崩溃前的日志
journalctl --user -u polymarket-recorder.service -n 200

# 临时禁用自动重启（调试用）
systemctl --user edit polymarket-recorder.service
# 添加: Restart=no
```

### 问题3: 日志查看权限问题

```bash
# 确保用户有权限查看日志
loginctl enable-linger $USER

# 检查systemd用户服务状态
systemctl --user status
```

### 问题4: 服务未开机自启

```bash
# 确保loginctl linger启用
loginctl show-user $USER | grep Linger
# 应该显示: Linger=yes

# 如果不是，启用它
loginctl enable-linger $USER

# 重新启用服务
systemctl --user enable polymarket-recorder.service
```

---

## 对比nohup

| 特性 | systemd | nohup |
|------|---------|-------|
| 自动重启 | ✅ | ❌ |
| 开机自启 | ✅ | ❌ (需要crontab) |
| 日志管理 | ✅ journalctl | ⚠️ 手动管理 |
| 进程管理 | ✅ systemctl | ⚠️ ps/kill |
| 资源限制 | ✅ | ❌ |
| 依赖管理 | ✅ | ❌ |
| 状态监控 | ✅ | ⚠️ 手动检查 |

---

## 高级配置

### 1. 添加依赖关系

如果Polymarket采集器依赖CEX采集器：

```bash
systemctl --user edit polymarket-recorder.service

# 添加：
[Unit]
After=cex-recorder.service
Requires=cex-recorder.service
```

### 2. 设置环境变量

```bash
systemctl --user edit polymarket-recorder.service

# 添加：
[Service]
Environment="API_KEY=your_key"
Environment="TIMEOUT=10"
```

### 3. 自定义重启策略

```bash
systemctl --user edit polymarket-recorder.service

# 添加：
[Service]
Restart=on-failure      # 仅失败时重启
RestartSec=30           # 重启前等待30秒
StartLimitBurst=5       # 5次启动失败后停止尝试
StartLimitIntervalSec=600  # 10分钟内
```

### 4. 添加通知（可选）

```bash
# 安装通知工具
sudo apt install mailutils

# 编辑服务
systemctl --user edit polymarket-recorder.service

# 添加：
[Service]
ExecStopPost=/usr/bin/mail -s "Polymarket Recorder Stopped" your@email.com
```

---

## 完整部署流程

### 一键部署脚本

```bash
#!/bin/bash
# 在VPS上运行

# 1. 部署基础环境
cd ~/polymarket
./deploy_vps.sh

# 2. 安装systemd服务
cd ~/polymarket/systemd
./install_services.sh

# 3. 启动服务
systemctl --user start polymarket-recorder.service cex-recorder.service

# 4. 查看状态
systemctl --user status polymarket-recorder.service cex-recorder.service

# 5. 实时查看日志
journalctl --user -u polymarket-recorder.service -u cex-recorder.service -f
```

---

## 快速参考

### 最常用的10个命令

```bash
# 1. 启动服务
systemctl --user start polymarket-recorder.service

# 2. 停止服务
systemctl --user stop polymarket-recorder.service

# 3. 重启服务
systemctl --user restart polymarket-recorder.service

# 4. 查看状态
systemctl --user status polymarket-recorder.service

# 5. 实时日志
journalctl --user -u polymarket-recorder.service -f

# 6. 最近日志
journalctl --user -u polymarket-recorder.service -n 100

# 7. 启用开机自启
systemctl --user enable polymarket-recorder.service

# 8. 重新加载配置
systemctl --user daemon-reload

# 9. 查看所有采集服务
systemctl --user list-units 'polymarket-*' 'cex-*'

# 10. 查看资源使用
systemctl --user status polymarket-recorder.service | grep -E '(Memory|CPU|Tasks)'
```

---

## 总结

使用systemd管理采集器的优势：

✅ **可靠性**: 自动重启，确保服务不中断
✅ **便捷性**: 统一的systemctl命令
✅ **可观测性**: journalctl集中查看日志
✅ **可控性**: 资源限制，防止失控
✅ **专业性**: 生产环境标准做法

推荐在生产环境使用systemd而不是nohup！

