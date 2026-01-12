#!/bin/bash
# 数据采集系统健康检查脚本

set -e

VPS_HOST="ubuntu@3.98.140.208"
VPS_KEY="$HOME/Desktop/workspace/polymarket/key/trader-data-collecter.pem"
LOCAL_MOUNT="/Users/jimmysun/Desktop/workspace/polymarket/real_hot"

echo "=========================================="
echo "🔍 数据采集系统健康检查"
echo "=========================================="
echo ""

# 1. 检查本地SSHFS挂载状态
echo "📂 1. 检查本地SSHFS挂载状态"
if mount | grep -q "$LOCAL_MOUNT"; then
    echo "   ✅ SSHFS已挂载"
    mount | grep "$LOCAL_MOUNT"
else
    echo "   ❌ SSHFS未挂载！请运行: cd collect_data && ./setup_sshfs_mount.sh"
    exit 1
fi
echo ""

# 2. 检查本地可见的最新文件
echo "📊 2. 本地可见的最新数据文件"
ls -lth "$LOCAL_MOUNT"/*.jsonl 2>/dev/null | head -5 || echo "   ⚠️  没有找到数据文件"
echo ""

# 3. 计算当前应该的窗口
echo "🕐 3. 当前时间窗口"
python3 <<'PYEOF'
from datetime import datetime, timezone
import time

now = int(time.time())
window_15m = now - (now % 900)
window_1h = now - (now % 3600)

print(f"   当前UTC时间: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
print(f"   当前15分钟窗口: {window_15m}")
print(f"   当前1小时窗口: {window_1h}")
PYEOF
echo ""

# 4. 检查VPS服务状态
echo "🖥️  4. VPS服务状态"
ssh -i "$VPS_KEY" "$VPS_HOST" "systemctl --user is-active polymarket-recorder.service" > /tmp/pm_status.txt 2>&1 || true
PM_STATUS=$(cat /tmp/pm_status.txt)

ssh -i "$VPS_KEY" "$VPS_HOST" "systemctl --user is-active cex-recorder.service" > /tmp/cex_status.txt 2>&1 || true
CEX_STATUS=$(cat /tmp/cex_status.txt)

if [ "$PM_STATUS" = "active" ]; then
    echo "   ✅ Polymarket采集器: 运行中"
else
    echo "   ❌ Polymarket采集器: $PM_STATUS"
fi

if [ "$CEX_STATUS" = "active" ]; then
    echo "   ✅ CEX采集器: 运行中"
else
    echo "   ❌ CEX采集器: $CEX_STATUS"
fi
echo ""

# 5. 检查VPS最新文件
echo "📁 5. VPS上的最新数据文件"
ssh -i "$VPS_KEY" "$VPS_HOST" "ls -lth ~/trader-data-collect/real_hot/*.jsonl | head -5"
echo ""

# 6. 检查数据延迟
echo "⏱️  6. 数据延迟检查"
ssh -i "$VPS_KEY" "$VPS_HOST" bash <<'SSHEOF'
cd ~/trader-data-collect/real_hot
LATEST_FILE=$(ls -t btc-updown-15m-*.jsonl 2>/dev/null | head -1)
if [ -n "$LATEST_FILE" ]; then
    LATEST_WINDOW=$(echo "$LATEST_FILE" | grep -oE '[0-9]{10}')
    NOW=$(date +%s)
    CURRENT_WINDOW=$((NOW - NOW % 900))
    DELAY=$((CURRENT_WINDOW - LATEST_WINDOW))
    
    echo "   最新窗口: $LATEST_WINDOW"
    echo "   当前窗口: $CURRENT_WINDOW"
    
    if [ $DELAY -eq 0 ]; then
        echo "   ✅ 数据实时（延迟0秒）"
    elif [ $DELAY -le 900 ]; then
        echo "   ⚠️  数据延迟: ${DELAY}秒 (~$((DELAY/60))分钟)"
    else
        echo "   ❌ 数据严重延迟: ${DELAY}秒 (~$((DELAY/60))分钟)"
    fi
else
    echo "   ❌ 未找到数据文件！"
fi
SSHEOF
echo ""

# 7. 检查CEX数据
echo "💱 7. CEX数据状态"
ssh -i "$VPS_KEY" "$VPS_HOST" bash <<'SSHEOF'
cd ~/trader-data-collect/real_hot
LATEST_CEX=$(ls -t cex_*.csv 2>/dev/null | head -1)
if [ -n "$LATEST_CEX" ]; then
    LINES=$(wc -l < "$LATEST_CEX")
    SIZE=$(du -h "$LATEST_CEX" | cut -f1)
    echo "   最新CEX文件: $LATEST_CEX"
    echo "   行数: $LINES, 大小: $SIZE"
    echo "   ✅ CEX数据正常"
else
    echo "   ❌ 未找到CEX数据文件！"
fi
SSHEOF
echo ""

echo "=========================================="
echo "✅ 健康检查完成"
echo "=========================================="
