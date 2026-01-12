# Systemd服务快速部署

## 📦 包含的文件

```
systemd/
├── polymarket-recorder.service      # Polymarket采集服务
├── cex-recorder.service             # CEX采集服务
├── polymarket-recorders.target      # 统一管理target
├── archive-data.service             # 数据归档服务
├── archive-data.timer               # 定时归档timer（每天4am）
├── install_services.sh              # 一键安装脚本
├── README.md                        # 本文件
└── SYSTEMD_GUIDE.md                 # 完整指南
```

---

## 🚀 快速部署（3步）

### 1. 上传到VPS

```bash
# 在本地
cd /path/to/polymarket/collect_data
scp -r systemd/ user@vps:~/polymarket/
```

### 2. 安装服务

```bash
# 在VPS上
ssh user@vps
cd ~/polymarket/systemd
chmod +x install_services.sh
./install_services.sh
```

### 3. 启动服务

```bash
# 启动数据采集服务
systemctl --user start polymarket-recorder.service cex-recorder.service

# 启动定时归档
systemctl --user start archive-data.timer

# 查看状态
systemctl --user status polymarket-recorder.service
systemctl --user status archive-data.timer

# 查看定时器列表
systemctl --user list-timers

# 查看日志
journalctl --user -u polymarket-recorder.service -f
```

完成！✅

---

## 📋 常用命令速查

### 启动/停止

```bash
# 启动
systemctl --user start polymarket-recorder.service

# 停止
systemctl --user stop polymarket-recorder.service

# 重启
systemctl --user restart polymarket-recorder.service

# 启动所有
systemctl --user start polymarket-recorder.service cex-recorder.service
```

### 状态查看

```bash
# 查看状态
systemctl --user status polymarket-recorder.service

# 查看所有采集服务
systemctl --user list-units 'polymarket-*' 'cex-*'

# 是否开机自启
systemctl --user is-enabled polymarket-recorder.service
```

### 日志查看

```bash
# 实时日志（类似tail -f）
journalctl --user -u polymarket-recorder.service -f

# 最近100行
journalctl --user -u polymarket-recorder.service -n 100

# 今天的日志
journalctl --user -u polymarket-recorder.service --since today

# 错误日志
journalctl --user -u polymarket-recorder.service -p err

# 两个服务的日志
journalctl --user -u polymarket-recorder.service -u cex-recorder.service -f

# 归档任务日志
journalctl --user -u archive-data.service -n 50
```

### 开机自启

```bash
# 启用
systemctl --user enable polymarket-recorder.service

# 禁用
systemctl --user disable polymarket-recorder.service

# 启用并立即启动
systemctl --user enable --now polymarket-recorder.service
```

---

## ✨ Systemd vs Nohup

| 特性 | systemd | nohup |
|------|---------|-------|
| 自动重启 | ✅ | ❌ |
| 开机自启 | ✅ | ❌ |
| 日志管理 | ✅ journalctl | ⚠️ 手动 |
| 进程管理 | ✅ systemctl | ⚠️ ps/kill |
| 资源限制 | ✅ | ❌ |

**推荐使用systemd！**

---

## 🔧 服务配置

### 资源限制

已配置：
- 内存: 512MB
- CPU: 50%

修改方法：
```bash
systemctl --user edit polymarket-recorder.service

# 添加：
[Service]
MemoryLimit=1G
CPUQuota=80%

# 重新加载
systemctl --user daemon-reload
systemctl --user restart polymarket-recorder.service
```

### 自动重启

已配置：
- 崩溃自动重启
- 重启延迟: 10秒

修改方法：
```bash
systemctl --user edit polymarket-recorder.service

# 添加：
[Service]
Restart=on-failure      # 仅失败时重启
RestartSec=30           # 等待30秒
```

---

## 🐛 故障排查

### 服务启动失败

```bash
# 查看详细错误
systemctl --user status polymarket-recorder.service -l

# 查看错误日志
journalctl --user -u polymarket-recorder.service -n 50 -p err

# 检查Python和脚本路径
ls -l ~/polymarket/venv/bin/python3
ls -l ~/polymarket/scripts/polymarket_multi_market_recorder.py
```

### 服务频繁重启

```bash
# 查看重启记录
journalctl --user -u polymarket-recorder.service | grep -i restart

# 查看崩溃前的日志
journalctl --user -u polymarket-recorder.service -n 200
```

### 未开机自启

```bash
# 启用linger（允许用户服务在未登录时运行）
loginctl enable-linger $USER

# 重新启用服务
systemctl --user enable polymarket-recorder.service
```

---

## 📚 详细文档

查看完整文档: `systemd/SYSTEMD_GUIDE.md`

包含：
- 详细配置说明
- 高级功能
- 完整故障排查
- 最佳实践

---

## 💡 快速提示

### 查看定时归档状态

```bash
# 查看所有定时器
systemctl --user list-timers

# 查看归档timer详情
systemctl --user status archive-data.timer

# 手动触发归档（测试用）
systemctl --user start archive-data.service

# 查看归档日志
journalctl --user -u archive-data.service -n 50
```

### 一行命令启动所有采集器

```bash
systemctl --user start polymarket-recorder.service cex-recorder.service && \
journalctl --user -u polymarket-recorder.service -u cex-recorder.service -f
```

### 一行命令查看所有状态

```bash
systemctl --user status polymarket-recorder.service cex-recorder.service
```

### 一行命令重启并查看日志

```bash
systemctl --user restart polymarket-recorder.service && \
journalctl --user -u polymarket-recorder.service -f
```

---

**使用systemd，让采集器更可靠！** 🚀

