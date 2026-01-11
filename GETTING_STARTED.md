# 🚀 远程数据访问系统 - 5分钟快速入门

## 你将得到什么？

- ✅ VPS上运行数据收集，本地实时访问
- ✅ 本地只保留最近7天数据（节省90%+空间）
- ✅ 历史数据按需自动拉取
- ✅ 现有代码只需加2行就能用

## 第一步：VPS部署（5分钟）

### 1. 上传脚本到VPS

```bash
# 在本地终端运行
scp deploy_vps.sh archive_old_data.py your_user@your_vps_ip:/tmp/
```

### 2. 在VPS上运行部署

```bash
# SSH到VPS
ssh your_user@your_vps_ip

# 运行部署脚本
cd /tmp
chmod +x deploy_vps.sh
./deploy_vps.sh
```

部署脚本会自动：
- ✓ 创建目录结构
- ✓ 配置Python环境
- ✓ 设置定时归档任务

### 3. 启动数据采集（如果还没运行）

**复制采集脚本到VPS**:
```bash
# 从本地上传（在本地运行）
scp polymarket_btc15m_record.py cex_multi_venue_recorder.py \
    $VPS_USER@$VPS_HOST:~/polymarket/scripts/
```

**在VPS上启动数据采集**:
```bash
# 在VPS上
cd ~/polymarket/scripts
source ~/polymarket/venv/bin/activate

# 1. 启动Polymarket数据采集（后台）
nohup python3 polymarket_btc15m_record.py \
    --output ~/polymarket/real_hot \
    > ~/polymarket/logs/poly.log 2>&1 &

# 2. 启动CEX数据采集（后台）
nohup python3 cex_multi_venue_recorder.py \
    --out ~/polymarket/logs/cex_multi_venue_books.csv \
    --hz 1.0 --venues binance_spot,okx_spot,bybit_spot \
    > ~/polymarket/logs/cex.log 2>&1 &

# 查看进程
ps aux | grep python3

# 查看日志
tail -f ~/polymarket/logs/poly.log
tail -f ~/polymarket/logs/cex.log
```

## 第二步：本地配置（5分钟）

### 1. 设置环境变量

```bash
# 编辑 ~/.zshrc（如果用bash则是 ~/.bash_profile）
echo 'export VPS_USER="your_username"' >> ~/.zshrc
echo 'export VPS_HOST="your_vps_ip"' >> ~/.zshrc

# 重新加载
source ~/.zshrc

# 验证
echo $VPS_USER
echo $VPS_HOST
```

### 2. 配置SSH密钥（如果还没配置）

```bash
# 生成密钥（如果没有）
ssh-keygen -t rsa -b 4096

# 复制公钥到VPS
ssh-copy-id $VPS_USER@$VPS_HOST

# 测试免密登录
ssh $VPS_USER@$VPS_HOST "echo 'OK'"
```

### 3. 运行SSHFS配置脚本

```bash
cd ~/Desktop/workspace/polymarket
./setup_sshfs_mount.sh
```

按照提示完成配置，脚本会自动：
- ✓ 安装SSHFS（通过Homebrew）
- ✓ 配置挂载点
- ✓ 设置开机自动挂载

## 第三步：验证（1分钟）

```bash
# 运行诊断工具
python3 diagnose_remote_setup.py
```

如果看到"✓ 所有检查通过"，恭喜你成功了！

## 第四步：使用（1行代码）

在你的任何分析脚本开头添加：

```python
from data_path_compat import auto_patch
auto_patch()

# 之后所有代码保持不变！
import glob
files = glob.glob("real/btc-updown-15m-*.jsonl")
```

就这么简单！

## 测试一下

```bash
# 测试数据访问
python3 data_accessor.py

# 运行示例
python3 example_migration.py

# 测试你的脚本
python3 research/btc15m_strong_signal_enhanced_rule_search.py --help
```

## 后台监控（可选但推荐）

```bash
# 启动监控守护进程（自动检查挂载状态）
nohup python3 monitor_sync.py --daemon > logs/monitor.log 2>&1 &
```

## 常用命令速查

**Polymarket数据**:
```bash
# 查看挂载状态
mount | grep polymarket

# 手动挂载
~/.local/bin/mount_polymarket.sh

# 手动卸载
~/.local/bin/unmount_polymarket.sh

# 清理缓存
python3 monitor_sync.py --cleanup

# 系统诊断
python3 diagnose_remote_setup.py
```

**CEX数据**:
```bash
# 同步到本地
rsync -avz $VPS_USER@$VPS_HOST:~/polymarket/logs/cex_multi_venue_books.csv \
    ~/Desktop/workspace/polymarket/real/

# 查看VPS上的文件大小
ssh $VPS_USER@$VPS_HOST "ls -lh ~/polymarket/logs/cex_multi_venue_books.csv"

# 查看最近数据
ssh $VPS_USER@$VPS_HOST "tail -n 10 ~/polymarket/logs/cex_multi_venue_books.csv"
```

## 遇到问题？

1. **运行诊断**: `python3 diagnose_remote_setup.py`
2. **查看详细文档**: `REMOTE_DATA_SETUP.md`
3. **快速参考**: `QUICK_REFERENCE.md`

## 完整文档索引

- 📘 **完整设置文档**: `REMOTE_DATA_SETUP.md`
- 📙 **迁移指南**: `MIGRATION_GUIDE.md`
- 📗 **快速参考**: `QUICK_REFERENCE.md`
- 📕 **实施总结**: `IMPLEMENTATION_SUMMARY.md`
- 📊 **CEX数据访问**: `CEX_DATA_ACCESS.md` ← 新增

---

**🎉 现在你可以愉快地在VPS上跑数据收集，本地像访问本地文件一样使用了！**

整个过程预计10-15分钟完成（首次配置）。

