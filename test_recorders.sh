#!/bin/bash
# 测试新采集脚本

echo "=========================================="
echo "测试1: Polymarket多市场采集脚本"
echo "=========================================="

cd "$(dirname "$0")"

# 创建临时测试目录
TEST_DIR="../test_output"
mkdir -p "$TEST_DIR"

# 测试Polymarket采集脚本（5秒）
echo "[INFO] 启动Polymarket采集器..."
./venv/bin/python3 polymarket_multi_market_recorder.py &
PID1=$!

sleep 5

echo "[INFO] 停止Polymarket采集器..."
kill $PID1 2>/dev/null
wait $PID1 2>/dev/null

# 检查输出文件
echo ""
echo "[INFO] 检查生成的文件:"
ls -lh ../real_hot/*.jsonl 2>/dev/null | tail -5

echo ""
echo "=========================================="
echo "测试2: CEX多资产采集脚本"
echo "=========================================="

# 测试CEX采集脚本（5秒）
echo "[INFO] 启动CEX采集器..."
./venv/bin/python3 cex_multi_asset_recorder.py &
PID2=$!

sleep 5

echo "[INFO] 停止CEX采集器..."
kill $PID2 2>/dev/null
wait $PID2 2>/dev/null

# 检查输出文件
echo ""
echo "[INFO] 检查生成的文件:"
ls -lh ../real_hot/cex_*.csv 2>/dev/null

echo ""
echo "=========================================="
echo "测试完成"
echo "=========================================="

