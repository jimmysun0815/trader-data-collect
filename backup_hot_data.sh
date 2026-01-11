#!/bin/bash
# 定时备份热数据到本地（真正下载到本地磁盘）
# 用法: ./backup_hot_data.sh [保留天数，默认3]

set -e

KEEP_DAYS=${1:-3}
SSHFS_HOT="${HOME}/polymarket/real_hot"
LOCAL_BACKUP="${HOME}/polymarket/real_backup"

# 检查SSHFS是否挂载
if ! mount | grep -q "${SSHFS_HOT}"; then
    echo "❌ SSHFS未挂载: ${SSHFS_HOT}"
    exit 1
fi

# 创建备份目录
mkdir -p "${LOCAL_BACKUP}"

# 使用rsync增量同步
echo "🔄 开始同步热数据到本地备份..."
rsync -av --progress \
    --include="*.jsonl" \
    --include="*.csv" \
    --exclude="*" \
    "${SSHFS_HOT}/" "${LOCAL_BACKUP}/"

# 删除超过N天的旧文件
echo "🗑️  清理超过 ${KEEP_DAYS} 天的旧备份..."
find "${LOCAL_BACKUP}" -name "*.jsonl" -mtime +${KEEP_DAYS} -delete
find "${LOCAL_BACKUP}" -name "*.csv" -mtime +${KEEP_DAYS} -delete

echo "✓ 本地备份完成: ${LOCAL_BACKUP}"
echo "  保留最近 ${KEEP_DAYS} 天的数据"
du -sh "${LOCAL_BACKUP}"

