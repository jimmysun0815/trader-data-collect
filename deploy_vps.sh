#!/bin/bash
# VPS部署脚本 - 在VPS上运行此脚本进行初始化

set -e

echo "=== Polymarket数据收集系统 VPS部署 ==="

# 配置变量
BASE_DIR="/home/ubuntu/trader-data-collect"
REAL_HOT="${BASE_DIR}/real_hot"
REAL_ARCHIVE="${BASE_DIR}/real_archive"
SCRIPTS_DIR="${BASE_DIR}/scripts"

# 1. 创建目录结构
echo "1. 创建目录结构..."
mkdir -p "${REAL_HOT}"
mkdir -p "${REAL_ARCHIVE}"
mkdir -p "${SCRIPTS_DIR}"
mkdir -p "${BASE_DIR}/logs"

echo "   目录创建完成:"
echo "   - 热数据: ${REAL_HOT}"
echo "   - 归档: ${REAL_ARCHIVE}"
echo "   - 脚本: ${SCRIPTS_DIR}"

# 2. 检查Python环境
echo ""
echo "2. 检查Python环境..."
if ! command -v python3 &> /dev/null; then
    echo "   错误: 未找到python3，请先安装Python 3.7+"
    exit 1
fi

PYTHON_VERSION=$(python3 --version)
echo "   Python版本: ${PYTHON_VERSION}"

# 3. 创建虚拟环境（如果需要）
if [ ! -d "${BASE_DIR}/venv" ]; then
    echo ""
    echo "3. 创建Python虚拟环境..."
    python3 -m venv "${BASE_DIR}/venv"
    echo "   虚拟环境创建完成"
fi

# 4. 安装依赖
echo ""
echo "4. 安装Python依赖..."
source "${BASE_DIR}/venv/bin/activate"
pip install requests web3 py-clob-client -q
echo "   依赖安装完成"

# 5. 设置定时归档任务
echo ""
echo "5. 设置定时归档任务（每天凌晨4点）..."

CRON_CMD="${BASE_DIR}/venv/bin/python3 ${SCRIPTS_DIR}/archive_old_data.py >> ${BASE_DIR}/logs/archive.log 2>&1"
CRON_ENTRY="0 4 * * * ${CRON_CMD}"

# 检查crontab是否已存在该任务
if crontab -l 2>/dev/null | grep -q "archive_old_data.py"; then
    echo "   定时任务已存在，跳过添加"
else
    # 添加到crontab
    (crontab -l 2>/dev/null; echo "${CRON_ENTRY}") | crontab -
    echo "   定时任务已添加到crontab"
fi

# 6. 显示下一步操作
echo ""
echo "=== 部署完成 ==="
echo ""
echo "下一步操作："
echo "1. 将数据采集脚本复制到 ${SCRIPTS_DIR}/"
echo "   - polymarket_multi_market_recorder.py (BTC/ETH 15分钟+1小时市场)"
echo "   - cex_multi_asset_recorder.py (BTC/ETH order book)"
echo "   - archive_old_data.py (归档脚本)"
echo ""
echo "2. 安装systemd服务（推荐）:"
echo "   cd ${BASE_DIR}/systemd"
echo "   ./install_services.sh"
echo ""
echo "   # 启动服务"
echo "   systemctl --user start polymarket-recorder.service cex-recorder.service"
echo ""
echo "   # 查看状态"
echo "   systemctl --user status polymarket-recorder.service"
echo "   journalctl --user -u polymarket-recorder.service -f"
echo ""
echo "3. 或使用nohup启动（不推荐）:"
echo "   cd ${SCRIPTS_DIR}"
echo "   source ${BASE_DIR}/venv/bin/activate"
echo ""
echo "   # Polymarket多市场数据"
echo "   nohup python3 polymarket_multi_market_recorder.py > ${BASE_DIR}/logs/poly_multi.log 2>&1 &"
echo ""
echo "   # CEX多资产数据"
echo "   nohup python3 cex_multi_asset_recorder.py > ${BASE_DIR}/logs/cex_multi.log 2>&1 &"
echo ""
echo "4. 测试归档脚本:"
echo "   python3 ${SCRIPTS_DIR}/archive_old_data.py --dry-run"
echo ""
echo "5. 查看生成的文件:"
echo "   ls -lh ${REAL_HOT}/*.jsonl | tail"
echo "   ls -lh ${REAL_HOT}/cex_*.csv"
echo ""
echo "目录权限设置建议:"
echo "   chmod 755 ${REAL_HOT} ${REAL_ARCHIVE}"
echo "   chmod 644 ${REAL_HOT}/*.jsonl ${REAL_HOT}/*.csv 2>/dev/null || true"

