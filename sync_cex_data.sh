#!/bin/bash
# CEX数据同步脚本 - 从VPS同步cex_multi_venue_books.csv到本地

set -e

# 配置（从环境变量读取）
VPS_USER="${VPS_USER:-}"
VPS_HOST="${VPS_HOST:-}"
REMOTE_PATH="${REMOTE_CEX_PATH:-~/polymarket/logs/cex_multi_venue_books.csv}"
LOCAL_DIR="${LOCAL_CEX_DIR:-$HOME/Desktop/workspace/polymarket/real}"

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "==================================="
echo "CEX数据同步工具"
echo "==================================="
echo ""

# 检查环境变量
if [ -z "$VPS_USER" ] || [ -z "$VPS_HOST" ]; then
    echo -e "${RED}错误: 未设置VPS连接信息${NC}"
    echo "请设置环境变量:"
    echo "  export VPS_USER='your_username'"
    echo "  export VPS_HOST='your_vps_ip'"
    exit 1
fi

echo "VPS: ${VPS_USER}@${VPS_HOST}"
echo "远程路径: ${REMOTE_PATH}"
echo "本地目录: ${LOCAL_DIR}"
echo ""

# 创建本地目录
mkdir -p "${LOCAL_DIR}"

# 检查远程文件
echo "检查远程文件..."
REMOTE_SIZE=$(ssh "${VPS_USER}@${VPS_HOST}" "stat -f%z ${REMOTE_PATH} 2>/dev/null || stat -c%s ${REMOTE_PATH} 2>/dev/null" 2>/dev/null || echo "0")

if [ "$REMOTE_SIZE" = "0" ]; then
    echo -e "${RED}错误: 远程文件不存在或为空${NC}"
    exit 1
fi

echo -e "${GREEN}✓${NC} 远程文件大小: $(numfmt --to=iec-i --suffix=B $REMOTE_SIZE 2>/dev/null || echo "${REMOTE_SIZE} bytes")"

# 检查本地文件
LOCAL_FILE="${LOCAL_DIR}/cex_multi_venue_books.csv"
if [ -f "${LOCAL_FILE}" ]; then
    LOCAL_SIZE=$(stat -f%z "${LOCAL_FILE}" 2>/dev/null || stat -c%s "${LOCAL_FILE}" 2>/dev/null || echo "0")
    echo "本地文件大小: $(numfmt --to=iec-i --suffix=B $LOCAL_SIZE 2>/dev/null || echo "${LOCAL_SIZE} bytes")"
    
    if [ "$REMOTE_SIZE" -le "$LOCAL_SIZE" ]; then
        echo -e "${YELLOW}⚠${NC}  远程文件不大于本地文件，可能已是最新"
        read -p "继续同步? (y/n) " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            echo "已取消"
            exit 0
        fi
    fi
else
    echo "本地文件不存在，将首次下载"
fi

# 执行同步
echo ""
echo "开始同步..."
START_TIME=$(date +%s)

if rsync -avz --progress \
    "${VPS_USER}@${VPS_HOST}:${REMOTE_PATH}" \
    "${LOCAL_FILE}"; then
    
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    NEW_SIZE=$(stat -f%z "${LOCAL_FILE}" 2>/dev/null || stat -c%s "${LOCAL_FILE}" 2>/dev/null || echo "0")
    
    echo ""
    echo -e "${GREEN}✓ 同步完成${NC}"
    echo "  本地文件: ${LOCAL_FILE}"
    echo "  文件大小: $(numfmt --to=iec-i --suffix=B $NEW_SIZE 2>/dev/null || echo "${NEW_SIZE} bytes")"
    echo "  耗时: ${ELAPSED}秒"
    
    # 显示最后几行
    echo ""
    echo "最新数据（最后5行）:"
    tail -n 5 "${LOCAL_FILE}"
    
else
    echo -e "${RED}✗ 同步失败${NC}"
    exit 1
fi

echo ""
echo "==================================="
echo "提示:"
echo "  - 设置定时同步: crontab -e"
echo "  - 添加: */30 * * * * $0 >> /tmp/cex_sync.log 2>&1"
echo "  - 查看数据: head ${LOCAL_FILE}"

