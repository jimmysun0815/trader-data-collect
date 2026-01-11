#!/bin/bash
# 远程数据访问系统 - 部署前检查清单

echo "==================================="
echo "远程数据访问系统 - 部署检查清单"
echo "==================================="
echo ""

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 计数器
TOTAL=0
PASSED=0

check_item() {
    TOTAL=$((TOTAL + 1))
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✓${NC} $2"
        PASSED=$((PASSED + 1))
    else
        echo -e "${RED}✗${NC} $2"
    fi
}

echo "1. 检查必需文件..."
echo "-----------------------------------"

files=(
    "deploy_vps.sh"
    "archive_old_data.py"
    "setup_sshfs_mount.sh"
    "data_accessor.py"
    "data_path_compat.py"
    "monitor_sync.py"
    "diagnose_remote_setup.py"
    "example_migration.py"
    "REMOTE_DATA_SETUP.md"
    "MIGRATION_GUIDE.md"
    "QUICK_REFERENCE.md"
    "IMPLEMENTATION_SUMMARY.md"
)

for file in "${files[@]}"; do
    if [ -f "$file" ]; then
        check_item 0 "$file"
    else
        check_item 1 "$file (缺失)"
    fi
done

echo ""
echo "2. 检查可执行权限..."
echo "-----------------------------------"

exec_files=(
    "deploy_vps.sh"
    "setup_sshfs_mount.sh"
    "archive_old_data.py"
    "monitor_sync.py"
    "diagnose_remote_setup.py"
)

for file in "${exec_files[@]}"; do
    if [ -x "$file" ]; then
        check_item 0 "$file 可执行"
    else
        check_item 1 "$file 不可执行"
    fi
done

echo ""
echo "3. 检查Python语法..."
echo "-----------------------------------"

python_files=(
    "archive_old_data.py"
    "data_accessor.py"
    "data_path_compat.py"
    "monitor_sync.py"
    "diagnose_remote_setup.py"
    "example_migration.py"
)

for file in "${python_files[@]}"; do
    if python3 -m py_compile "$file" 2>/dev/null; then
        check_item 0 "$file 语法正确"
    else
        check_item 1 "$file 语法错误"
    fi
done

echo ""
echo "4. 检查文档完整性..."
echo "-----------------------------------"

# 检查文档中是否包含关键章节
if grep -q "快速开始" REMOTE_DATA_SETUP.md; then
    check_item 0 "REMOTE_DATA_SETUP.md 包含快速开始"
else
    check_item 1 "REMOTE_DATA_SETUP.md 缺少快速开始章节"
fi

if grep -q "迁移方法" MIGRATION_GUIDE.md; then
    check_item 0 "MIGRATION_GUIDE.md 包含迁移方法"
else
    check_item 1 "MIGRATION_GUIDE.md 缺少迁移方法章节"
fi

if grep -q "常用命令" QUICK_REFERENCE.md; then
    check_item 0 "QUICK_REFERENCE.md 包含常用命令"
else
    check_item 1 "QUICK_REFERENCE.md 缺少常用命令章节"
fi

echo ""
echo "5. 检查代码集成示例..."
echo "-----------------------------------"

if grep -q "auto_patch" research/btc15m_strong_signal_enhanced_rule_search.py; then
    check_item 0 "示例脚本已集成 auto_patch"
else
    check_item 1 "示例脚本未集成 auto_patch"
fi

if grep -q "auto_patch" example_migration.py; then
    check_item 0 "example_migration.py 包含 auto_patch 示例"
else
    check_item 1 "example_migration.py 缺少 auto_patch 示例"
fi

echo ""
echo "==================================="
echo "检查结果: ${PASSED}/${TOTAL} 通过"
echo "==================================="
echo ""

if [ $PASSED -eq $TOTAL ]; then
    echo -e "${GREEN}✓ 所有检查通过！系统已准备好部署${NC}"
    echo ""
    echo "下一步:"
    echo "  1. 在VPS上运行: ./deploy_vps.sh"
    echo "  2. 在本地运行: ./setup_sshfs_mount.sh"
    echo "  3. 运行诊断: python3 diagnose_remote_setup.py"
    echo "  4. 查看文档: cat QUICK_REFERENCE.md"
    exit 0
else
    echo -e "${YELLOW}⚠ 部分检查未通过，请修复上述问题${NC}"
    exit 1
fi

