# 快速部署指南 - trader-data-collect

## 📁 目录结构

你的VPS上的目录结构：

```
/home/ubuntu/trader-data-collect/
├── scripts/                              # 采集脚本
│   ├── polymarket_multi_market_recorder.py
│   ├── cex_multi_asset_recorder.py
│   └── archive_old_data.py
├── systemd/                              # Systemd服务文件
│   ├── polymarket-recorder.service
│   ├── cex-recorder.service
│   ├── polymarket-recorders.target
│   └── install_services.sh
├── venv/                                 # Python虚拟环境
├── real_hot/                             # 热数据（最近7天）
├── real_archive/                         # 归档数据
├── logs/                                 # 日志文件
├── requirements.txt
└── deploy_vps.sh
```

---

## 🚀 部署步骤（5步）

### 1️⃣ 上传文件到VPS

```bash
# 在本地
cd /path/to/polymarket/collect_data

# 上传整个目录到VPS
scp -r . ubuntu@<your-vps-ip>:/home/ubuntu/trader-data-collect/

# 或者使用rsync（推荐，更快）
rsync -avz --exclude 'venv' --exclude '.git' \
  . ubuntu@<your-vps-ip>:/home/ubuntu/trader-data-collect/
```

### 2️⃣ SSH到VPS并运行部署脚本

```bash
# SSH连接
ssh ubuntu@<your-vps-ip>

# 运行部署脚本
cd /home/ubuntu/trader-data-collect
chmod +x deploy_vps.sh
./deploy_vps.sh
```

这会：
- ✅ 创建所有必要的目录
- ✅ 创建Python虚拟环境
- ✅ 安装所有依赖
- ✅ 设置定时归档任务

### 3️⃣ 安装systemd服务

```bash
cd /home/ubuntu/trader-data-collect/systemd
chmod +x install_services.sh
./install_services.sh
```

这会：
- ✅ 安装服务文件到systemd
- ✅ 启用开机自启
- ✅ 配置自动重启

### 4️⃣ 启动服务

```bash
# 启动所有采集器
systemctl --user start polymarket-recorder.service cex-recorder.service

# 查看状态
systemctl --user status polymarket-recorder.service
systemctl --user status cex-recorder.service
```

### 5️⃣ 验证运行

```bash
# 实时查看日志
journalctl --user -u polymarket-recorder.service -f
journalctl --user -u cex-recorder.service -f

# 查看生成的文件
ls -lh /home/ubuntu/trader-data-collect/real_hot/

# 查看资源使用
systemctl --user status polymarket-recorder.service cex-recorder.service
```

---

## 📋 常用命令

### 服务管理

```bash
# 启动
systemctl --user start polymarket-recorder.service
systemctl --user start cex-recorder.service

# 停止
systemctl --user stop polymarket-recorder.service
systemctl --user stop cex-recorder.service

# 重启
systemctl --user restart polymarket-recorder.service
systemctl --user restart cex-recorder.service

# 查看状态
systemctl --user status polymarket-recorder.service
systemctl --user status cex-recorder.service
```

### 日志查看

```bash
# 实时日志
journalctl --user -u polymarket-recorder.service -f

# 最近100行
journalctl --user -u polymarket-recorder.service -n 100

# 今天的日志
journalctl --user -u polymarket-recorder.service --since today

# 错误日志
journalctl --user -u polymarket-recorder.service -p err
```

### 数据查看

```bash
# 查看生成的文件
ls -lh /home/ubuntu/trader-data-collect/real_hot/

# Polymarket数据
ls -lh /home/ubuntu/trader-data-collect/real_hot/*.jsonl | tail -20

# CEX数据
ls -lh /home/ubuntu/trader-data-collect/real_hot/cex_*.csv

# 查看磁盘使用
du -sh /home/ubuntu/trader-data-collect/real_hot
du -sh /home/ubuntu/trader-data-collect/real_archive
```

---

## 🔧 配置说明

### 路径配置

所有路径都已配置为 `/home/ubuntu/trader-data-collect`：

| 组件 | 路径 |
|------|------|
| 基础目录 | `/home/ubuntu/trader-data-collect` |
| 脚本目录 | `/home/ubuntu/trader-data-collect/scripts` |
| 虚拟环境 | `/home/ubuntu/trader-data-collect/venv` |
| 热数据 | `/home/ubuntu/trader-data-collect/real_hot` |
| 归档数据 | `/home/ubuntu/trader-data-collect/real_archive` |
| 日志 | `/home/ubuntu/trader-data-collect/logs` |

### 服务配置

**polymarket-recorder.service**:
```ini
WorkingDirectory=/home/ubuntu/trader-data-collect/scripts
ExecStart=/home/ubuntu/trader-data-collect/venv/bin/python3 polymarket_multi_market_recorder.py
```

**cex-recorder.service**:
```ini
WorkingDirectory=/home/ubuntu/trader-data-collect/scripts
ExecStart=/home/ubuntu/trader-data-collect/venv/bin/python3 cex_multi_asset_recorder.py
```

---

## ⚙️ 高级配置

### 修改资源限制

```bash
# 编辑服务文件
systemctl --user edit polymarket-recorder.service

# 添加或修改：
[Service]
MemoryLimit=1G        # 从512M改为1G
CPUQuota=80%          # 从50%改为80%

# 重新加载并重启
systemctl --user daemon-reload
systemctl --user restart polymarket-recorder.service
```

### 修改采集频率

编辑采集脚本：

```bash
# 编辑文件
nano /home/ubuntu/trader-data-collect/scripts/cex_multi_asset_recorder.py

# 找到这一行并修改
hz = 1.0  # 每秒采集1次，改为0.5则为每2秒采集一次

# 重启服务
systemctl --user restart cex-recorder.service
```

### 设置环境变量

```bash
# 编辑服务
systemctl --user edit polymarket-recorder.service

# 添加环境变量
[Service]
Environment="API_KEY=your_key"
Environment="TIMEOUT=10"

# 重新加载
systemctl --user daemon-reload
systemctl --user restart polymarket-recorder.service
```

---

## 🐛 故障排查

### 问题1: 服务启动失败

```bash
# 查看详细错误
systemctl --user status polymarket-recorder.service -l

# 查看日志
journalctl --user -u polymarket-recorder.service -n 50

# 检查Python和脚本路径
ls -l /home/ubuntu/trader-data-collect/venv/bin/python3
ls -l /home/ubuntu/trader-data-collect/scripts/polymarket_multi_market_recorder.py

# 手动测试运行
cd /home/ubuntu/trader-data-collect/scripts
../venv/bin/python3 polymarket_multi_market_recorder.py
```

### 问题2: 权限问题

```bash
# 确保目录权限正确
chmod 755 /home/ubuntu/trader-data-collect
chmod 755 /home/ubuntu/trader-data-collect/scripts
chmod +x /home/ubuntu/trader-data-collect/scripts/*.py

# 确保用户服务可用
loginctl enable-linger ubuntu

# 检查用户服务状态
systemctl --user status
```

### 问题3: 内存不足

```bash
# 查看内存使用
free -h

# 查看服务内存使用
systemctl --user status polymarket-recorder.service | grep Memory

# 如果内存不足，增加资源限制
systemctl --user edit polymarket-recorder.service
# 添加: MemoryLimit=1G
```

### 问题4: 网络问题

```bash
# 测试Polymarket API
curl -I https://gamma-api.polymarket.com/markets

# 测试CEX API
curl -I https://api.binance.com/api/v3/depth?symbol=BTCUSDT

# 查看网络错误日志
journalctl --user -u cex-recorder.service -p err
```

---

## 📊 监控

### 查看服务状态

```bash
# 一次查看所有服务
systemctl --user status polymarket-recorder.service cex-recorder.service

# 查看服务是否开机自启
systemctl --user is-enabled polymarket-recorder.service
systemctl --user is-enabled cex-recorder.service

# 查看所有采集服务
systemctl --user list-units 'polymarket-*' 'cex-*'
```

### 监控数据生成

```bash
# 监控新文件生成
watch -n 5 'ls -lht /home/ubuntu/trader-data-collect/real_hot/ | head -20'

# 统计文件数量
find /home/ubuntu/trader-data-collect/real_hot -name "*.jsonl" | wc -l
find /home/ubuntu/trader-data-collect/real_hot -name "*.csv" | wc -l

# 查看最新文件
ls -lt /home/ubuntu/trader-data-collect/real_hot/ | head -10
```

### 磁盘使用监控

```bash
# 查看磁盘使用
df -h

# 查看目录大小
du -sh /home/ubuntu/trader-data-collect/*

# 查看最大的文件
find /home/ubuntu/trader-data-collect/real_hot -type f -exec ls -lh {} \; | sort -k5 -hr | head -20
```

---

## 🔄 更新和维护

### 更新采集脚本

```bash
# 1. 在本地修改脚本
# 2. 上传到VPS
scp polymarket_multi_market_recorder.py ubuntu@<ip>:/home/ubuntu/trader-data-collect/scripts/

# 3. 重启服务
ssh ubuntu@<ip>
systemctl --user restart polymarket-recorder.service

# 4. 查看日志确认
journalctl --user -u polymarket-recorder.service -f
```

### 手动归档数据

```bash
cd /home/ubuntu/trader-data-collect/scripts

# 预览要归档的文件
../venv/bin/python3 archive_old_data.py --dry-run

# 执行归档
../venv/bin/python3 archive_old_data.py
```

### 清理旧日志

```bash
# 查看journal大小
journalctl --disk-usage

# 清理超过7天的日志
journalctl --vacuum-time=7d

# 限制journal大小
sudo journalctl --vacuum-size=500M
```

---

## ✅ 检查清单

部署完成后，确认以下项目：

- [ ] 服务启动成功
  ```bash
  systemctl --user status polymarket-recorder.service
  systemctl --user status cex-recorder.service
  ```

- [ ] 开机自启已启用
  ```bash
  systemctl --user is-enabled polymarket-recorder.service
  systemctl --user is-enabled cex-recorder.service
  ```

- [ ] 数据文件正在生成
  ```bash
  ls -lh /home/ubuntu/trader-data-collect/real_hot/
  ```

- [ ] 日志正常
  ```bash
  journalctl --user -u polymarket-recorder.service -n 50
  journalctl --user -u cex-recorder.service -n 50
  ```

- [ ] 磁盘空间充足
  ```bash
  df -h
  ```

- [ ] 内存使用正常
  ```bash
  free -h
  ```

---

## 🎯 快速命令参考

### 一行命令启动并查看日志

```bash
systemctl --user start polymarket-recorder.service cex-recorder.service && \
journalctl --user -u polymarket-recorder.service -u cex-recorder.service -f
```

### 一行命令重启并验证

```bash
systemctl --user restart polymarket-recorder.service cex-recorder.service && \
sleep 5 && \
systemctl --user status polymarket-recorder.service cex-recorder.service
```

### 一行命令检查所有状态

```bash
echo "=== Services ===" && \
systemctl --user status polymarket-recorder.service cex-recorder.service && \
echo -e "\n=== Files ===" && \
ls -lht /home/ubuntu/trader-data-collect/real_hot/ | head -10 && \
echo -e "\n=== Disk ===" && \
du -sh /home/ubuntu/trader-data-collect/real_hot
```

---

**部署完成！你的数据采集系统现在正在运行！** 🎉

