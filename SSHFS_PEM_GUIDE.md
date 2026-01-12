# SSHFS挂载配置指南 - 支持AWS PEM密钥

## 🔑 两种SSH认证方式

### 方式1: AWS .pem密钥（推荐用于EC2）

**适用场景**: AWS EC2, Lightsail等使用.pem密钥的实例

```bash
# 1. 设置环境变量
export VPS_USER=ubuntu
export VPS_HOST=your-ec2-ip.com
export VPS_PEM_KEY=~/Downloads/your-aws-key.pem

# 2. 运行配置脚本
cd /Users/jimmysun/Desktop/workspace/polymarket/collect_data
./setup_sshfs_mount.sh
```

或者一行命令：
```bash
VPS_USER=ubuntu VPS_HOST=1.2.3.4 VPS_PEM_KEY=~/your-key.pem ./setup_sshfs_mount.sh
```

### 方式2: 标准SSH密钥（~/.ssh/id_rsa）

**适用场景**: 需要手动配置公钥的VPS

```bash
# 1. 设置环境变量（不需要VPS_PEM_KEY）
export VPS_USER=ubuntu
export VPS_HOST=your-vps-ip.com

# 2. 运行配置脚本
./setup_sshfs_mount.sh

# 3. 按提示将公钥添加到VPS
# 脚本会显示公钥内容，复制到VPS的 ~/.ssh/authorized_keys
```

---

## 📋 完整示例

### AWS EC2实例

```bash
# 1. 确认你的PEM密钥位置
ls ~/Downloads/my-ec2-key.pem

# 2. 配置SSHFS挂载
cd /Users/jimmysun/Desktop/workspace/polymarket/collect_data
VPS_USER=ubuntu \
VPS_HOST=ec2-54-123-45-67.compute-1.amazonaws.com \
VPS_PEM_KEY=~/Downloads/my-ec2-key.pem \
./setup_sshfs_mount.sh

# 3. 测试挂载
ls ~/Desktop/workspace/polymarket/real_hot
# 应该能看到VPS上的数据文件

# 4. 测试数据访问
cat ~/Desktop/workspace/polymarket/real_hot/btc-updown-15m-*.jsonl | head -5
```

### AWS Lightsail实例

```bash
# Lightsail默认也使用.pem密钥
VPS_USER=ubuntu \
VPS_HOST=12.34.56.78 \
VPS_PEM_KEY=~/.ssh/LightsailDefaultKey-us-east-1.pem \
./setup_sshfs_mount.sh
```

### 普通VPS（DigitalOcean, Vultr等）

```bash
# 这些通常使用标准SSH密钥
VPS_USER=root \
VPS_HOST=vultr-vps.example.com \
./setup_sshfs_mount.sh

# 按提示将公钥添加到VPS
```

---

## ✅ 验证配置

### 1. 检查挂载状态

```bash
# 方法1: 使用mount命令
mount | grep polymarket

# 方法2: 列出挂载点
ls -lh ~/Desktop/workspace/polymarket/real_hot/

# 方法3: 检查文件系统
df -h | grep polymarket
```

### 2. 测试数据读取

```bash
# 查看最新的Polymarket文件
ls -lht ~/Desktop/workspace/polymarket/real_hot/*.jsonl | head -5

# 读取文件内容
cat ~/Desktop/workspace/polymarket/real_hot/btc-updown-15m-*.jsonl | tail -5 | python3 -m json.tool
```

### 3. 测试性能

```bash
# 首次读取（会稍慢，约1秒）
time cat ~/Desktop/workspace/polymarket/real_hot/btc-updown-15m-1768110300.jsonl > /dev/null

# 第二次读取（应该很快，< 0.1秒，因为已缓存）
time cat ~/Desktop/workspace/polymarket/real_hot/btc-updown-15m-1768110300.jsonl > /dev/null
```

---

## 🔧 常用命令

### 手动挂载

```bash
~/.local/bin/mount_polymarket.sh
```

### 手动卸载

```bash
~/.local/bin/unmount_polymarket.sh

# 或强制卸载（如果上面的命令失败）
diskutil unmount force ~/Desktop/workspace/polymarket/real_hot
```

### 重新挂载

```bash
~/.local/bin/unmount_polymarket.sh
~/.local/bin/mount_polymarket.sh
```

### 查看挂载点

```bash
ls -la ~/Desktop/workspace/polymarket/real_hot/
```

---

## 🐛 故障排查

### 问题1: "Permission denied (publickey)"

**PEM密钥方式**:
```bash
# 检查PEM文件权限（必须是400或600）
ls -l ~/your-key.pem

# 修复权限
chmod 400 ~/your-key.pem

# 测试SSH连接
ssh -i ~/your-key.pem ubuntu@your-vps-ip
```

**标准密钥方式**:
```bash
# 检查公钥是否已添加到VPS
ssh ubuntu@your-vps-ip "cat ~/.ssh/authorized_keys"

# 手动添加公钥
cat ~/.ssh/id_rsa.pub | ssh ubuntu@your-vps-ip "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys"
```

### 问题2: "Transport endpoint is not connected"

```bash
# 强制卸载
diskutil unmount force ~/Desktop/workspace/polymarket/real_hot

# 重新挂载
~/.local/bin/mount_polymarket.sh
```

### 问题3: 挂载后看不到文件

```bash
# 1. 检查VPS上的路径是否正确
ssh -i ~/your-key.pem ubuntu@your-vps-ip "ls -lh /home/ubuntu/trader-data-collect/real_hot/"

# 2. 检查SSHFS挂载选项
cat ~/.local/bin/mount_polymarket.sh

# 3. 手动测试挂载
sshfs -o IdentityFile=~/your-key.pem \
      ubuntu@your-vps-ip:/home/ubuntu/trader-data-collect/real_hot \
      ~/Desktop/workspace/polymarket/real_hot \
      -o reconnect,cache=yes
```

### 问题4: macOS升级后SSHFS不工作

```bash
# 重新安装macFUSE（需要重启）
brew reinstall --cask macfuse

# 系统偏好设置 -> 安全性与隐私 -> 允许macFUSE

# 重启Mac
sudo reboot
```

---

## 🔐 安全建议

### PEM密钥安全

```bash
# 1. PEM文件应该只有所有者可读
chmod 400 ~/your-key.pem

# 2. 不要把PEM文件提交到git
echo "*.pem" >> ~/.gitignore

# 3. 备份PEM文件（离线保存）
cp ~/your-key.pem /secure/backup/location/

# 4. 为不同服务使用不同的密钥
# 不要所有服务都用同一个PEM文件
```

### SSH配置优化

在 `~/.ssh/config` 中添加：

```bash
# AWS EC2/Lightsail
Host polymarket-vps
    HostName your-ec2-ip.com
    User ubuntu
    IdentityFile ~/path/to/your-key.pem
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

然后可以简化命令：
```bash
# 直接使用别名
ssh polymarket-vps

# SSHFS也可以用别名
sshfs polymarket-vps:/home/ubuntu/trader-data-collect/real_hot ~/mount/point
```

---

## 📚 相关文档

- **REMOTE_DATA_SETUP.md** - 完整的远程数据访问系统文档
- **VPS_UPDATE_GUIDE.md** - VPS更新部署指南
- **SSHFS_MECHANISM.md** - SSHFS工作原理

---

## 💡 高级技巧

### 自动重连

SSHFS已配置自动重连：
- 每15秒发送一次心跳 (`ServerAliveInterval=15`)
- 3次心跳失败后重连 (`ServerAliveCountMax=3`)
- 断线后自动重连 (`reconnect`)

### 性能优化

```bash
# 如果觉得慢，可以调整缓存设置
# 编辑 ~/.local/bin/mount_polymarket.sh，添加：
-o cache_timeout=115200  # 32小时缓存
-o attr_timeout=115200   # 属性缓存
```

### 多VPS管理

如果有多个VPS，可以创建多个配置：

```bash
# VPS 1 (生产环境)
VPS_USER=ubuntu VPS_HOST=prod-vps.com VPS_PEM_KEY=~/prod.pem \
LOCAL_MOUNT_POINT=~/polymarket/prod ./setup_sshfs_mount.sh

# VPS 2 (测试环境)
VPS_USER=ubuntu VPS_HOST=test-vps.com VPS_PEM_KEY=~/test.pem \
LOCAL_MOUNT_POINT=~/polymarket/test ./setup_sshfs_mount.sh
```

---

**现在你可以使用AWS的.pem密钥轻松配置SSHFS了！** 🎉

