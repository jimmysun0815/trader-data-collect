# AWS实例选型指南

## 资源需求分析

### 系统负载特点
- **CPU**: I/O密集型（HTTP请求），CPU使用率<10%
- **内存**: 两个Python进程，总共约500MB-1GB
- **网络**: 每秒14个API请求，流量~12GB/月
- **存储**: 每天500MB，30天约15-20GB

---

## 推荐方案

### ✅ 方案1: AWS Lightsail（最推荐）

**最佳性价比，固定价格，适合稳定负载**

#### 1️⃣ Lightsail $5/月 实例
```
配置:
- 1 vCPU
- 1 GB RAM
- 40 GB SSD
- 2 TB 传输流量

价格: $5/月 (约¥35/月)
```

**适用场景**: ✅ 完全满足需求，性价比最高

**优点**:
- ✅ 价格固定，不会突然增加
- ✅ 流量充足（2TB/月，实际只用12GB）
- ✅ 管理简单，适合长期运行
- ✅ 可以升级到更高配置

**缺点**:
- ⚠️ CPU性能略低（但对本场景足够）
- ⚠️ 可扩展性有限

#### 2️⃣ Lightsail $10/月 实例（如果需要更稳定）
```
配置:
- 2 vCPU
- 2 GB RAM
- 60 GB SSD
- 3 TB 传输流量

价格: $10/月 (约¥70/月)
```

**适用场景**: 如果担心1GB内存不够，或未来想运行其他服务

---

### ⭐ 方案2: EC2 t3/t4g 实例

**按需计费，灵活性高，适合不确定负载**

#### 1️⃣ t4g.micro (ARM架构，最便宜)
```
配置:
- 2 vCPU (ARM Graviton2)
- 1 GB RAM
- Burstable性能

价格: 
- 按需: $0.0084/小时 = ~$6/月
- 预留1年: $0.0042/小时 = ~$3/月
```

**注意**: ARM架构，需要确保Python包兼容

#### 2️⃣ t3.micro (x86架构)
```
配置:
- 2 vCPU
- 1 GB RAM
- Burstable性能

价格:
- 按需: $0.0104/小时 = ~$7.5/月
- 预留1年: $0.0052/小时 = ~$3.8/月
```

**适用场景**: ✅ 性能足够，价格合理

#### 3️⃣ t3.small
```
配置:
- 2 vCPU
- 2 GB RAM
- Burstable性能

价格:
- 按需: $0.0208/小时 = ~$15/月
- 预留1年: $0.0104/小时 = ~$7.6/月
```

**适用场景**: 如果需要更多内存或更稳定性能

**EC2优点**:
- ✅ 灵活性高，可随时更换实例类型
- ✅ 预留实例价格更低
- ✅ 更多配置选项（网络、安全组）

**EC2缺点**:
- ⚠️ 流量单独计费（约$0.09/GB出站）
- ⚠️ 需要管理更多配置
- ⚠️ 价格不固定，可能超预算

---

### 🏃 方案3: 其他云服务商（更便宜的选择）

#### Vultr
```
配置:
- 1 vCPU
- 1 GB RAM
- 25 GB SSD
- 1 TB 流量

价格: $5/月
```

#### DigitalOcean
```
配置:
- 1 vCPU
- 1 GB RAM
- 25 GB SSD
- 1 TB 流量

价格: $6/月
```

#### Linode (Akamai)
```
配置:
- 1 vCPU
- 1 GB RAM
- 25 GB SSD
- 1 TB 流量

价格: $5/月
```

---

## 📋 选择决策树

### 如果你...

**想要最省钱 + 固定价格**
→ AWS Lightsail $5/月

**想要AWS生态 + 灵活性**
→ EC2 t3.micro 或 t4g.micro

**不确定负载，想要保险**
→ AWS Lightsail $10/月 或 EC2 t3.small

**想要更便宜**
→ Vultr/DigitalOcean $5/月

---

## 💡 我的推荐

### 🥇 首选: AWS Lightsail $5/月

**理由**:
1. ✅ **价格固定**: $5/月，不会意外增加
2. ✅ **配置足够**: 1 vCPU + 1GB RAM完全够用
3. ✅ **流量充足**: 2TB/月（实际只用12GB）
4. ✅ **管理简单**: 适合长期稳定运行
5. ✅ **在AWS生态内**: 方便与其他服务集成

### 🥈 备选: EC2 t3.micro (预留1年)

**理由**:
1. ✅ **更便宜**: 预留1年约$3.8/月
2. ✅ **性能更好**: 2 vCPU
3. ✅ **更灵活**: 可以随时调整

**但需要注意**:
- ⚠️ 流量单独计费（12GB/月约$1.08）
- ⚠️ 需要管理EBS卷（约$2/月 for 20GB）
- **实际总成本**: ~$7/月

---

## 🎯 具体配置建议

### Lightsail $5/月 配置

```bash
# 创建实例
Instance plan: $5/月
OS: Ubuntu 22.04 LTS
Region: 选择离你最近的（如东京 ap-northeast-1）
```

### 启动后配置

```bash
# 1. 更新系统
sudo apt update && sudo apt upgrade -y

# 2. 安装Python
sudo apt install python3 python3-pip python3-venv -y

# 3. 上传collect_data目录
scp -r collect_data/ ubuntu@<lightsail-ip>:~/polymarket/

# 4. 运行部署脚本
cd ~/polymarket
./deploy_vps.sh

# 5. 安装systemd服务
cd systemd
./install_services.sh

# 6. 启动服务
systemctl --user start polymarket-recorder.service cex-recorder.service
```

---

## 📊 成本对比

| 方案 | 月费用 | 年费用 | 流量 | 适用场景 |
|------|--------|--------|------|---------|
| **Lightsail $5** | $5 | $60 | 2TB | ✅ 最推荐 |
| Lightsail $10 | $10 | $120 | 3TB | 保险选择 |
| EC2 t3.micro (按需) | ~$8.5 | ~$102 | 付费 | 灵活性 |
| EC2 t3.micro (预留1年) | ~$5 | ~$60 | 付费 | 最便宜 |
| Vultr | $5 | $60 | 1TB | AWS替代 |

---

## ⚠️ 注意事项

### 内存监控

虽然1GB RAM理论上足够，但建议：

```bash
# 监控内存使用
free -h
htop

# 如果内存不足，升级到2GB
# Lightsail: 升级到$10/月方案
# EC2: 升级到t3.small
```

### CPU credits (T系列实例)

T系列实例是"突发性能"，有CPU积分机制：

```bash
# 查看CPU积分
aws cloudwatch get-metric-statistics \
  --namespace AWS/EC2 \
  --metric-name CPUCreditBalance \
  --dimensions Name=InstanceId,Value=<instance-id>
```

如果经常用完积分：
- 升级到更大实例
- 或使用unlimited模式（但会额外收费）

### 存储空间

```bash
# 定期检查存储使用
df -h

# 如果空间不足
# Lightsail: 添加额外磁盘
# EC2: 扩展EBS卷
```

---

## 🚀 快速启动

### 1分钟决策

**如果你只想要简单可靠**:
```
选择: AWS Lightsail $5/月
理由: 固定价格，配置足够，管理简单
```

**如果你想要最便宜**:
```
选择: EC2 t3.micro 预留1年
理由: 约$3.8/月，但需要管理流量和存储
```

**如果你还在测试阶段**:
```
选择: EC2 t3.micro 按需
理由: 灵活，可随时停止，测试完再决定
```

---

## 📞 需要帮助？

### 监控和告警

建议设置CloudWatch告警：
- CPU使用率 > 80%
- 内存使用 > 80%
- 磁盘使用 > 80%
- 网络流量异常

### 成本优化

- ✅ 使用预留实例（省40-60%）
- ✅ 定期清理旧数据（archive）
- ✅ 监控流量使用
- ✅ 考虑AWS Savings Plans

---

## 总结

### 🎯 最终推荐: AWS Lightsail $5/月

**配置**: 1 vCPU, 1GB RAM, 40GB SSD, 2TB流量

**为什么**:
- ✅ 价格固定，预算可控
- ✅ 配置完全满足需求
- ✅ 管理简单，适合长期运行
- ✅ 可以随时升级

**开始使用**:
1. 登录AWS控制台
2. 选择Lightsail
3. 创建实例（Ubuntu 22.04）
4. 上传并运行部署脚本
5. 完成！

**月成本**: $5
**年成本**: $60

简单、可靠、便宜！🎉

