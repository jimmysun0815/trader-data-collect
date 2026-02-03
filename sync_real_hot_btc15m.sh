#!/bin/bash
# 从 VPS 拉取 real_hot 下「从指定 timestamp 起」的 BTC 15 分钟 Polymarket 涨跌 JSONL 到本地 real_hot
# 用法: ./sync_real_hot_btc15m.sh [起始timestamp，默认 1768982400]

set -e

KEY="$HOME/Desktop/workspace/polymarket/key/trader-data-collecter.pem"
REMOTE="ubuntu@3.98.140.208"
REMOTE_DIR="/home/ubuntu/trader-data-collect/real_hot"
LOCAL_DIR="/Users/jimmysun/Desktop/workspace/polymarket/real_hot"
MIN_TS="${1:-1768982400}"

mkdir -p "$LOCAL_DIR"

echo "拉取 btc-updown-15m-*.jsonl (timestamp >= $MIN_TS) 从 $REMOTE:$REMOTE_DIR -> $LOCAL_DIR"

# 在远端列出符合条件的文件名（文件名中 btc-updown-15m- 后的数字 >= MIN_TS）
FILE_LIST=$(ssh -i "$KEY" "$REMOTE" "cd $REMOTE_DIR 2>/dev/null && for f in btc-updown-15m-*.jsonl; do
  [ -f \"\$f\" ] || continue
  ts=\${f#btc-updown-15m-}
  ts=\${ts%.jsonl}
  ts=\${ts%%_*}
  if [ -n \"\$ts\" ] && [ \"\$ts\" -ge $MIN_TS ]; then echo \"\$f\"; fi
done")

if [ -z "$FILE_LIST" ]; then
  echo "远端没有符合条件的文件 (timestamp >= $MIN_TS)"
  exit 0
fi

echo "$FILE_LIST" | rsync -avz --files-from=- -e "ssh -i $KEY" "$REMOTE:$REMOTE_DIR/" "$LOCAL_DIR/"

echo "完成. 本地文件数: $(echo "$FILE_LIST" | wc -l)"
ls -la "$LOCAL_DIR"/btc-updown-15m-*.jsonl 2>/dev/null | tail -5
