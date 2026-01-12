#!/bin/bash
# 本地测试迁移脚本

set -e

echo "==================================="
echo "文件名迁移工具 - 本地测试"
echo "==================================="
echo ""

# 创建测试目录
TEST_DIR="/tmp/polymarket_migrate_test"
rm -rf "$TEST_DIR"
mkdir -p "$TEST_DIR"

echo "1. 创建测试数据..."

# 创建一些模拟的旧格式文件
cat > "$TEST_DIR/btc-updown-15m-1768110300_20260110_214626.jsonl" << 'EOF'
{"timestamp": 1768110300000, "market": "btc", "data": "line1"}
{"timestamp": 1768110301000, "market": "btc", "data": "line2"}
EOF

cat > "$TEST_DIR/btc-updown-15m-1768110300_20260110_220134.jsonl" << 'EOF'
{"timestamp": 1768110350000, "market": "btc", "data": "line3"}
{"timestamp": 1768110351000, "market": "btc", "data": "line4"}
EOF

cat > "$TEST_DIR/eth-updown-15m-1768110300_20260110_214627.jsonl" << 'EOF'
{"timestamp": 1768110300000, "market": "eth", "data": "eth1"}
EOF

cat > "$TEST_DIR/bitcoin-up-or-down-january-11-12am-et_20260110_214823.jsonl" << 'EOF'
{"timestamp": 1768110300000, "market": "btc_1h", "data": "btc1h_line1"}
{"timestamp": 1768110301000, "market": "btc_1h", "data": "btc1h_line2"}
EOF

# 创建一个新格式文件（不应该被迁移）
cat > "$TEST_DIR/btc-updown-15m-1768111200.jsonl" << 'EOF'
{"timestamp": 1768111200000, "market": "btc", "data": "new_format"}
EOF

# 创建CEX文件（不应该被迁移）
cat > "$TEST_DIR/cex_btc_20260111_00-12.csv" << 'EOF'
timestamp,venue,price,size
1768110300,binance,96000,1.5
1768110301,binance,96001,2.0
EOF

echo "   创建了 $(ls $TEST_DIR | wc -l) 个测试文件"
echo ""

echo "2. 查看测试文件列表:"
ls -lh "$TEST_DIR"
echo ""

echo "3. 运行迁移脚本（模拟）..."
cd "$(dirname "$0")"
python3 migrate_filenames.py --hot-dir "$TEST_DIR" --dry-run
echo ""

read -p "是否执行实际迁移？(y/n) " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    echo ""
    echo "4. 执行实际迁移..."
    python3 migrate_filenames.py --hot-dir "$TEST_DIR"
    echo ""
    
    echo "5. 迁移后文件列表:"
    ls -lh "$TEST_DIR"
    echo ""
    
    echo "6. 验证合并结果:"
    echo ""
    echo "--- btc-updown-15m-1768110300.jsonl (应有4行) ---"
    cat "$TEST_DIR/btc-updown-15m-1768110300.jsonl"
    echo ""
    
    echo "--- eth-updown-15m-1768110300.jsonl (应有1行) ---"
    cat "$TEST_DIR/eth-updown-15m-1768110300.jsonl"
    echo ""
    
    echo "--- bitcoin-up-or-down-january-11-12am-et.jsonl (应有2行) ---"
    cat "$TEST_DIR/bitcoin-up-or-down-january-11-12am-et.jsonl"
    echo ""
    
    echo "--- btc-updown-15m-1768111200.jsonl (新格式，不变) ---"
    cat "$TEST_DIR/btc-updown-15m-1768111200.jsonl"
    echo ""
    
    echo "7. 检查备份文件(.old):"
    ls -lh "$TEST_DIR"/*.old 2>/dev/null || echo "   (无备份文件)"
    echo ""
    
    echo "✅ 测试完成！"
    echo ""
    echo "测试目录: $TEST_DIR"
    echo "可以手动检查结果，确认无误后删除: rm -rf $TEST_DIR"
else
    echo ""
    echo "取消执行。测试目录: $TEST_DIR"
    echo "删除测试数据: rm -rf $TEST_DIR"
fi

