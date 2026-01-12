#!/bin/bash

# VPS数据采集验证脚本
# 用于验证Polymarket和CEX数据是否正常采集

set -e

echo "=================================="
echo "📊 数据采集验证脚本"
echo "=================================="
echo ""

DATA_DIR="/home/ubuntu/trader-data-collect/real_hot"
LOG_RETENTION_HOURS=1

# 颜色输出
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_info() {
    echo "ℹ️  $1"
}

# 1. 检查服务状态
echo "=== 1. 检查服务状态 ==="
if systemctl --user is-active --quiet polymarket-recorder.service; then
    print_success "Polymarket服务运行中"
else
    print_error "Polymarket服务未运行"
    systemctl --user status polymarket-recorder.service --no-pager | tail -5
fi

if systemctl --user is-active --quiet cex-recorder.service; then
    print_success "CEX服务运行中"
else
    print_error "CEX服务未运行"
    systemctl --user status cex-recorder.service --no-pager | tail -5
fi
echo ""

# 2. 检查数据目录
echo "=== 2. 检查数据目录 ==="
if [ -d "$DATA_DIR" ]; then
    print_success "数据目录存在: $DATA_DIR"
else
    print_error "数据目录不存在: $DATA_DIR"
    exit 1
fi

# 统计文件数量
POLY_FILES=$(find "$DATA_DIR" -name "*.jsonl" -type f | wc -l)
CEX_FILES=$(find "$DATA_DIR" -name "cex_*.csv" -type f | wc -l)
print_info "Polymarket文件数: $POLY_FILES"
print_info "CEX文件数: $CEX_FILES"
echo ""

# 3. 检查Polymarket数据
echo "=== 3. 验证Polymarket数据 ==="

# 获取最新的Polymarket文件
LATEST_POLY=$(find "$DATA_DIR" -name "*.jsonl" -type f -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)

if [ -n "$LATEST_POLY" ]; then
    FILE_SIZE=$(stat -f%z "$LATEST_POLY" 2>/dev/null || stat -c%s "$LATEST_POLY" 2>/dev/null)
    LINE_COUNT=$(wc -l < "$LATEST_POLY")
    FILE_NAME=$(basename "$LATEST_POLY")
    
    print_info "最新文件: $FILE_NAME"
    print_info "文件大小: $(numfmt --to=iec-i --suffix=B $FILE_SIZE 2>/dev/null || echo "${FILE_SIZE}B")"
    print_info "数据行数: $LINE_COUNT"
    
    # 检查文件是否为空
    if [ "$FILE_SIZE" -eq 0 ]; then
        print_error "文件为空！"
    else
        print_success "文件有数据"
        
        # 验证JSON格式
        echo ""
        print_info "验证JSON格式..."
        if tail -1 "$LATEST_POLY" | python3 -m json.tool > /dev/null 2>&1; then
            print_success "JSON格式正确"
            
            # 显示数据样例
            echo ""
            print_info "数据样例（最后一行）:"
            echo "---"
            tail -1 "$LATEST_POLY" | python3 -m json.tool 2>/dev/null | head -30
            echo "..."
            echo "---"
            
            # 验证必需字段
            LAST_LINE=$(tail -1 "$LATEST_POLY")
            HAS_TIMESTAMP=$(echo "$LAST_LINE" | grep -o '"timestamp"' || true)
            HAS_TOKENS=$(echo "$LAST_LINE" | grep -o '"tokens"' || true)
            HAS_ORDERBOOK=$(echo "$LAST_LINE" | grep -o '"orderbook"' || true)
            
            if [ -n "$HAS_TIMESTAMP" ] && [ -n "$HAS_TOKENS" ] && [ -n "$HAS_ORDERBOOK" ]; then
                print_success "数据结构完整（包含timestamp, tokens, orderbook）"
            else
                print_warning "数据结构可能不完整"
            fi
        else
            print_error "JSON格式错误"
            echo "最后一行内容："
            tail -1 "$LATEST_POLY"
        fi
    fi
else
    print_warning "没有找到Polymarket数据文件"
fi
echo ""

# 4. 检查CEX数据
echo "=== 4. 验证CEX数据 ==="

# 获取最新的CEX BTC文件
LATEST_CEX_BTC=$(find "$DATA_DIR" -name "cex_btc_*.csv" -type f -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)

if [ -n "$LATEST_CEX_BTC" ]; then
    FILE_SIZE=$(stat -f%z "$LATEST_CEX_BTC" 2>/dev/null || stat -c%s "$LATEST_CEX_BTC" 2>/dev/null)
    LINE_COUNT=$(wc -l < "$LATEST_CEX_BTC")
    FILE_NAME=$(basename "$LATEST_CEX_BTC")
    
    print_info "最新BTC文件: $FILE_NAME"
    print_info "文件大小: $(numfmt --to=iec-i --suffix=B $FILE_SIZE 2>/dev/null || echo "${FILE_SIZE}B")"
    print_info "数据行数: $LINE_COUNT"
    
    if [ "$LINE_COUNT" -gt 1 ]; then
        print_success "CEX BTC数据正常"
        
        echo ""
        print_info "CSV表头:"
        head -1 "$LATEST_CEX_BTC"
        
        print_info "数据样例（最后3行）:"
        tail -3 "$LATEST_CEX_BTC"
        
        # 验证CSV格式
        FIELD_COUNT=$(head -1 "$LATEST_CEX_BTC" | awk -F',' '{print NF}')
        print_info "CSV字段数: $FIELD_COUNT"
        
        if [ "$FIELD_COUNT" -eq 7 ]; then
            print_success "CSV格式正确（7个字段）"
        else
            print_warning "CSV字段数不对，预期7个字段"
        fi
    else
        print_error "CEX BTC文件只有表头，没有数据"
    fi
else
    print_warning "没有找到CEX BTC数据文件"
fi
echo ""

# 获取最新的CEX ETH文件
LATEST_CEX_ETH=$(find "$DATA_DIR" -name "cex_eth_*.csv" -type f -printf '%T@ %p\n' | sort -n | tail -1 | cut -d' ' -f2-)

if [ -n "$LATEST_CEX_ETH" ]; then
    FILE_SIZE=$(stat -f%z "$LATEST_CEX_ETH" 2>/dev/null || stat -c%s "$LATEST_CEX_ETH" 2>/dev/null)
    LINE_COUNT=$(wc -l < "$LATEST_CEX_ETH")
    FILE_NAME=$(basename "$LATEST_CEX_ETH")
    
    print_info "最新ETH文件: $FILE_NAME"
    print_info "文件大小: $(numfmt --to=iec-i --suffix=B $FILE_SIZE 2>/dev/null || echo "${FILE_SIZE}B")"
    print_info "数据行数: $LINE_COUNT"
    
    if [ "$LINE_COUNT" -gt 1 ]; then
        print_success "CEX ETH数据正常"
    else
        print_error "CEX ETH文件只有表头，没有数据"
    fi
else
    print_warning "没有找到CEX ETH数据文件"
fi
echo ""

# 5. 检查日志
echo "=== 5. 检查服务日志 ==="

print_info "Polymarket服务日志（最近10行）:"
echo "---"
journalctl --user -u polymarket-recorder.service --since "${LOG_RETENTION_HOURS} hour ago" --no-pager | tail -10
echo "---"
echo ""

print_info "CEX服务日志（最近10行）:"
echo "---"
journalctl --user -u cex-recorder.service --since "${LOG_RETENTION_HOURS} hour ago" --no-pager | tail -10
echo "---"
echo ""

# 检查是否有错误日志
POLY_ERRORS=$(journalctl --user -u polymarket-recorder.service --since "${LOG_RETENTION_HOURS} hour ago" --no-pager | grep -i "error\|exception\|traceback" | wc -l)
CEX_ERRORS=$(journalctl --user -u cex-recorder.service --since "${LOG_RETENTION_HOURS} hour ago" --no-pager | grep -i "error\|exception\|traceback" | wc -l)

if [ "$POLY_ERRORS" -eq 0 ]; then
    print_success "Polymarket日志无错误"
else
    print_warning "Polymarket日志发现 $POLY_ERRORS 个错误"
fi

if [ "$CEX_ERRORS" -eq 0 ]; then
    print_success "CEX日志无错误"
else
    print_warning "CEX日志发现 $CEX_ERRORS 个错误"
fi
echo ""

# 6. 数据实时性检查
echo "=== 6. 数据实时性检查 ==="

if [ -n "$LATEST_POLY" ]; then
    # 尝试Linux格式，再尝试macOS格式
    if stat -c %Y "$LATEST_POLY" &>/dev/null; then
        LAST_MODIFIED=$(stat -c %Y "$LATEST_POLY" 2>/dev/null)
    else
        LAST_MODIFIED=$(stat -f %m "$LATEST_POLY" 2>/dev/null)
    fi
    
    CURRENT_TIME=$(date +%s)
    TIME_DIFF=$((CURRENT_TIME - LAST_MODIFIED))
    
    # 格式化时间显示
    if date -d @$LAST_MODIFIED '+%Y-%m-%d %H:%M:%S' &>/dev/null; then
        TIME_STR=$(date -d @$LAST_MODIFIED '+%Y-%m-%d %H:%M:%S')
    else
        TIME_STR=$(date -r $LAST_MODIFIED '+%Y-%m-%d %H:%M:%S')
    fi
    
    print_info "Polymarket最新文件修改时间: $TIME_STR"
    print_info "距离现在: ${TIME_DIFF}秒"
    
    if [ "$TIME_DIFF" -lt 30 ]; then
        print_success "Polymarket数据实时更新中"
    else
        print_warning "Polymarket数据可能已停止更新（超过30秒无更新）"
    fi
fi

if [ -n "$LATEST_CEX_BTC" ]; then
    # 尝试Linux格式，再尝试macOS格式
    if stat -c %Y "$LATEST_CEX_BTC" &>/dev/null; then
        LAST_MODIFIED=$(stat -c %Y "$LATEST_CEX_BTC" 2>/dev/null)
    else
        LAST_MODIFIED=$(stat -f %m "$LATEST_CEX_BTC" 2>/dev/null)
    fi
    
    CURRENT_TIME=$(date +%s)
    TIME_DIFF=$((CURRENT_TIME - LAST_MODIFIED))
    
    # 格式化时间显示
    if date -d @$LAST_MODIFIED '+%Y-%m-%d %H:%M:%S' &>/dev/null; then
        TIME_STR=$(date -d @$LAST_MODIFIED '+%Y-%m-%d %H:%M:%S')
    else
        TIME_STR=$(date -r $LAST_MODIFIED '+%Y-%m-%d %H:%M:%S')
    fi
    
    print_info "CEX最新文件修改时间: $TIME_STR"
    print_info "距离现在: ${TIME_DIFF}秒"
    
    if [ "$TIME_DIFF" -lt 10 ]; then
        print_success "CEX数据实时更新中"
    else
        print_warning "CEX数据可能已停止更新（超过10秒无更新）"
    fi
fi
echo ""

# 7. 总结
echo "==================================="
echo "📋 验证总结"
echo "==================================="

TOTAL_ISSUES=0

# 服务状态
if systemctl --user is-active --quiet polymarket-recorder.service && systemctl --user is-active --quiet cex-recorder.service; then
    print_success "所有服务运行正常"
else
    print_error "有服务未运行"
    ((TOTAL_ISSUES++))
fi

# 文件数量
if [ "$POLY_FILES" -gt 0 ] && [ "$CEX_FILES" -gt 0 ]; then
    print_success "数据文件已生成"
else
    print_error "数据文件缺失"
    ((TOTAL_ISSUES++))
fi

# 数据内容
if [ -n "$LATEST_POLY" ] && [ "$FILE_SIZE" -gt 0 ]; then
    print_success "Polymarket数据格式正确"
else
    print_error "Polymarket数据有问题"
    ((TOTAL_ISSUES++))
fi

if [ -n "$LATEST_CEX_BTC" ] && [ "$LINE_COUNT" -gt 1 ]; then
    print_success "CEX数据格式正确"
else
    print_error "CEX数据有问题"
    ((TOTAL_ISSUES++))
fi

echo ""
if [ "$TOTAL_ISSUES" -eq 0 ]; then
    print_success "验证完成！所有检查都通过 ✨"
    exit 0
else
    print_error "验证发现 $TOTAL_ISSUES 个问题，请检查上面的详细信息"
    exit 1
fi

