# 系统级Systemd服务迁移指南

## 🎯 目标

将数据采集服务从**用户级**改为**系统级**systemd服务，彻底解决SSH断开导致服务停止的问题。

---

## 📋 主要变化

### 之前（用户级）
- ❌ 依赖SSH会话，断开后服务停止
- ❌ 需要`loginctl enable-linger`
- 命令：`systemctl --user xxx`
- 位置：`~/.config/systemd/user/`

### 现在（系统级）
- ✅ 完全独立，SSH断开不影响
- ✅ 开机自动启动
- 命令：`sudo systemctl xxx`
- 位置：`/etc/systemd/system/`

---

## 🚀 部署步骤

### 1. 本地推送代码

```bash
cd /Users/jimmysun/Desktop/workspace/polymarket/collect_data
git add systemd/
git commit -m "改为系统级systemd服务"
git push
```

### 2. VPS更新并安装

```bash
# SSH登录
ssh -i ~/Desktop/workspace/polymarket/key/trader-data-collecter.pem ubuntu@3.98.140.208

# 进入目录
cd ~/trader-data-collect

# 更新代码
git pull

# 创建logs目录（如果不存在）
mkdir -p logs

# 运行安装脚本（需要sudo）
sudo bash systemd/install_services.sh
```

### 3. 验证服务状态

```bash
# 查看服务状态
sudo systemctl status polymarket-recorder.service
sudo systemctl status cex-recorder.service
sudo systemctl status archive-data.timer

# 查看实时日志
sudo journalctl -u polymarket-recorder.service -f

# 或者查看最近100行
sudo journalctl -u polymarket-recorder.service -n 100 --no-pager
sudo journalctl -u cex-recorder.service -n 100 --no-pager
```

### 4. 测试SSH断开

```bash
# 退出SSH
exit

# 等待1分钟后重新登录
ssh -i ~/Desktop/workspace/polymarket/key/trader-data-collecter.pem ubuntu@3.98.140.208

# 验证服务仍在运行
sudo systemctl status polymarket-recorder.service cex-recorder.service
```

---

## 🔧 常用命令

### 服务管理

```bash
# 启动
sudo systemctl start polymarket-recorder.service
sudo systemctl start cex-recorder.service

# 停止
sudo systemctl stop polymarket-recorder.service
sudo systemctl stop cex-recorder.service

# 重启
sudo systemctl restart polymarket-recorder.service
sudo systemctl restart cex-recorder.service

# 查看状态
sudo systemctl status polymarket-recorder.service
sudo systemctl status cex-recorder.service

# 开机自启（已自动设置）
sudo systemctl enable polymarket-recorder.service
sudo systemctl enable cex-recorder.service

# 禁用开机自启
sudo systemctl disable polymarket-recorder.service
```

### 日志查看

```bash
# 实时日志
sudo journalctl -u polymarket-recorder.service -f
sudo journalctl -u cex-recorder.service -f

# 最近N行
sudo journalctl -u polymarket-recorder.service -n 100 --no-pager
sudo journalctl -u cex-recorder.service -n 100 --no-pager

# 查看今天的日志
sudo journalctl -u polymarket-recorder.service --since today

# 查看最近1小时
sudo journalctl -u polymarket-recorder.service --since "1 hour ago"

# 查看错误日志
sudo journalctl -u polymarket-recorder.service -p err
```

### 定时任务

```bash
# 查看所有定时器
sudo systemctl list-timers

# 查看归档定时器状态
sudo systemctl status archive-data.timer

# 手动触发归档
sudo systemctl start archive-data.service

# 查看归档日志
sudo journalctl -u archive-data.service -n 50 --no-pager
```

---

## 🐛 故障排查

### 问题1：服务启动失败

```bash
# 查看详细错误
sudo journalctl -u polymarket-recorder.service -n 50 --no-pager

# 检查文件权限
ls -la /home/ubuntu/trader-data-collect/

# 确保ubuntu用户可以访问
sudo chown -R ubuntu:ubuntu /home/ubuntu/trader-data-collect/
```

### 问题2：Python环境问题

```bash
# 测试Python脚本是否能直接运行
cd /home/ubuntu/trader-data-collect
./venv/bin/python3 polymarket_multi_market_recorder.py
# Ctrl+C 停止

# 检查venv
ls -la venv/bin/python3
```

### 问题3：网络问题

```bash
# 测试API连接
curl -s "https://clob.polymarket.com/markets/slug/btc-updown-15m-$(date -u +%s | awk '{print int($1/900)*900}')" | head -20
```

---

## ⚠️ 重要提示

1. **权限变化**：
   - 现在所有服务管理命令都需要`sudo`
   - 日志查看也需要`sudo journalctl`

2. **自动重启**：
   - 服务配置了`Restart=always`，崩溃会自动重启
   - 重启延迟10秒（`RestartSec=10`）

3. **资源限制**：
   - 内存限制：512MB
   - CPU限制：50%

4. **开机自启**：
   - 服务已设置为开机自动启动
   - VPS重启后会自动恢复数据采集

---

## ✅ 验证清单

- [ ] 本地代码已推送
- [ ] VPS已更新代码
- [ ] 安装脚本运行成功
- [ ] 两个采集服务都在running状态
- [ ] 定时器已启用
- [ ] 退出SSH后服务仍在运行
- [ ] 日志正常输出数据
- [ ] SSHFS挂载可以看到最新数据

---

## 📞 需要帮助？

如果遇到问题，提供以下信息：
```bash
# 服务状态
sudo systemctl status polymarket-recorder.service cex-recorder.service

# 最近日志
sudo journalctl -u polymarket-recorder.service -n 50 --no-pager
sudo journalctl -u cex-recorder.service -n 50 --no-pager

# 系统信息
uname -a
free -h
df -h
```
