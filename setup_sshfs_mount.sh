#!/bin/bash
# SSHFS自动挂载配置脚本 - 在本地Mac上运行
#
# 用法:
#   方式1: 使用AWS .pem密钥（推荐用于EC2）
#     VPS_USER=ubuntu VPS_HOST=1.2.3.4 VPS_PEM_KEY=~/path/to/key.pem ./setup_sshfs_mount.sh
#
#   方式2: 使用标准SSH密钥（~/.ssh/id_rsa）
#     VPS_USER=ubuntu VPS_HOST=1.2.3.4 ./setup_sshfs_mount.sh
#
#   方式3: 交互式配置
#     编辑下面的配置变量，然后运行: ./setup_sshfs_mount.sh

set -e

echo "=== Polymarket SSHFS挂载配置 ==="

# 配置变量（请根据实际情况修改）
VPS_USER="${VPS_USER:-ubuntu}"
VPS_HOST="${VPS_HOST:-your_vps_ip}"
VPS_PEM_KEY="${VPS_PEM_KEY:-}"  # AWS .pem文件路径（可选）
VPS_REMOTE_PATH="${VPS_REMOTE_PATH:-/home/ubuntu/trader-data-collect/real_hot}"
LOCAL_MOUNT_POINT="${HOME}/Desktop/workspace/polymarket/real_hot"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 1. 检查macFUSE和SSHFS是否已安装
echo ""
echo "1. 检查依赖..."

if ! command -v sshfs &> /dev/null; then
    echo -e "${YELLOW}   未找到sshfs，开始安装...${NC}"
    
    if ! command -v brew &> /dev/null; then
        echo -e "${RED}   错误: 未找到Homebrew${NC}"
        echo "   请先安装Homebrew: https://brew.sh/"
        exit 1
    fi
    
    echo "   安装macFUSE和sshfs..."
    brew install --cask macfuse
    brew install gromgit/fuse/sshfs-mac
    
    echo -e "${YELLOW}   注意: macFUSE需要在系统偏好设置 -> 安全性与隐私中允许${NC}"
    echo "   安装完成后请重启终端"
else
    echo -e "${GREEN}   ✓ sshfs已安装${NC}"
fi

# 2. 检查SSH密钥配置
echo ""
echo "2. 检查SSH密钥配置..."

# 检查是否使用PEM密钥
if [ -n "${VPS_PEM_KEY}" ]; then
    if [ ! -f "${VPS_PEM_KEY}" ]; then
        echo -e "${RED}   ✗ PEM密钥文件不存在: ${VPS_PEM_KEY}${NC}"
        exit 1
    fi
    
    # 检查PEM文件权限（必须是400或600）
    PEM_PERMS=$(stat -f "%OLp" "${VPS_PEM_KEY}" 2>/dev/null || stat -c "%a" "${VPS_PEM_KEY}" 2>/dev/null)
    if [ "$PEM_PERMS" != "400" ] && [ "$PEM_PERMS" != "600" ]; then
        echo -e "${YELLOW}   ⚠ PEM密钥权限不正确，正在修复...${NC}"
        chmod 400 "${VPS_PEM_KEY}"
    fi
    
    SSH_KEY="${VPS_PEM_KEY}"
    echo -e "${GREEN}   ✓ 使用PEM密钥: ${SSH_KEY}${NC}"
    SKIP_KEY_COPY=true
else
    # 使用标准SSH密钥
    SSH_KEY="${HOME}/.ssh/id_rsa"
    if [ ! -f "${SSH_KEY}" ]; then
        echo -e "${YELLOW}   未找到SSH密钥，生成新密钥...${NC}"
        ssh-keygen -t rsa -b 4096 -f "${SSH_KEY}" -N ""
        echo -e "${GREEN}   SSH密钥已生成${NC}"
    fi
    
    echo ""
    echo -e "${YELLOW}重要: 请将以下公钥添加到VPS的 ~/.ssh/authorized_keys${NC}"
    echo "====== 复制以下内容 ======"
    cat "${SSH_KEY}.pub"
    echo "=========================="
    echo ""
    read -p "已添加公钥到VPS? (y/n) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "请先配置SSH密钥后再运行此脚本"
        exit 1
    fi
    SKIP_KEY_COPY=false
fi

# 3. 测试SSH连接
echo ""
echo "3. 测试SSH连接..."

SSH_OPTS="-o ConnectTimeout=5 -o BatchMode=yes"
if [ -n "${VPS_PEM_KEY}" ]; then
    SSH_OPTS="${SSH_OPTS} -i ${VPS_PEM_KEY}"
fi

if ssh ${SSH_OPTS} "${VPS_USER}@${VPS_HOST}" "echo '连接成功'" 2>/dev/null; then
    echo -e "${GREEN}   ✓ SSH连接测试成功${NC}"
else
    echo -e "${RED}   ✗ SSH连接失败${NC}"
    echo "   请检查:"
    echo "   - VPS地址是否正确: ${VPS_HOST}"
    echo "   - 用户名是否正确: ${VPS_USER}"
    if [ -n "${VPS_PEM_KEY}" ]; then
        echo "   - PEM密钥是否正确: ${VPS_PEM_KEY}"
        echo "   - PEM密钥权限是否为400/600"
    else
        echo "   - SSH密钥是否已添加到VPS"
    fi
    echo "   - VPS防火墙是否允许SSH连接"
    exit 1
fi

# 4. 创建本地挂载点
echo ""
echo "4. 创建本地挂载点..."

if [ ! -d "${LOCAL_MOUNT_POINT}" ]; then
    mkdir -p "${LOCAL_MOUNT_POINT}"
    echo -e "${GREEN}   ✓ 创建挂载点: ${LOCAL_MOUNT_POINT}${NC}"
else
    echo "   挂载点已存在: ${LOCAL_MOUNT_POINT}"
fi

# 5. 创建挂载脚本
echo ""
echo "5. 创建挂载脚本..."

MOUNT_SCRIPT="${HOME}/.local/bin/mount_polymarket.sh"
mkdir -p "$(dirname ${MOUNT_SCRIPT})"

cat > "${MOUNT_SCRIPT}" << 'EOF'
#!/bin/bash
# Polymarket SSHFS自动挂载脚本

VPS_USER="%%VPS_USER%%"
VPS_HOST="%%VPS_HOST%%"
VPS_PEM_KEY="%%VPS_PEM_KEY%%"
VPS_REMOTE_PATH="%%VPS_REMOTE_PATH%%"
LOCAL_MOUNT_POINT="%%LOCAL_MOUNT_POINT%%"

# 检查是否已挂载
if mount | grep -q "${LOCAL_MOUNT_POINT}"; then
    echo "✓ Polymarket数据已挂载"
    exit 0
fi

# 确保挂载点存在
mkdir -p "${LOCAL_MOUNT_POINT}"

# 构建sshfs命令
SSHFS_OPTS="-o reconnect,ServerAliveInterval=15,ServerAliveCountMax=3"
SSHFS_OPTS="${SSHFS_OPTS} -o cache=yes,kernel_cache"
SSHFS_OPTS="${SSHFS_OPTS} -o follow_symlinks"
SSHFS_OPTS="${SSHFS_OPTS} -o Ciphers=aes128-gcm@openssh.com"
SSHFS_OPTS="${SSHFS_OPTS} -o volname=PolymarketData"

# 如果有PEM密钥，添加到选项
if [ -n "${VPS_PEM_KEY}" ] && [ "${VPS_PEM_KEY}" != "NONE" ]; then
    SSHFS_OPTS="${SSHFS_OPTS} -o IdentityFile=${VPS_PEM_KEY}"
fi

# 执行挂载
echo "挂载Polymarket数据..."
sshfs "${VPS_USER}@${VPS_HOST}:${VPS_REMOTE_PATH}" "${LOCAL_MOUNT_POINT}" ${SSHFS_OPTS}

if [ $? -eq 0 ]; then
    echo "✓ 挂载成功: ${LOCAL_MOUNT_POINT}"
else
    echo "✗ 挂载失败"
    exit 1
fi
EOF

# 替换变量
sed -i '' "s|%%VPS_USER%%|${VPS_USER}|g" "${MOUNT_SCRIPT}"
sed -i '' "s|%%VPS_HOST%%|${VPS_HOST}|g" "${MOUNT_SCRIPT}"
sed -i '' "s|%%VPS_PEM_KEY%%|${VPS_PEM_KEY:-NONE}|g" "${MOUNT_SCRIPT}"
sed -i '' "s|%%VPS_REMOTE_PATH%%|${VPS_REMOTE_PATH}|g" "${MOUNT_SCRIPT}"
sed -i '' "s|%%LOCAL_MOUNT_POINT%%|${LOCAL_MOUNT_POINT}|g" "${MOUNT_SCRIPT}"

chmod +x "${MOUNT_SCRIPT}"
echo -e "${GREEN}   ✓ 挂载脚本已创建: ${MOUNT_SCRIPT}${NC}"

# 6. 创建卸载脚本
UNMOUNT_SCRIPT="${HOME}/.local/bin/unmount_polymarket.sh"

cat > "${UNMOUNT_SCRIPT}" << EOF
#!/bin/bash
# Polymarket SSHFS卸载脚本

LOCAL_MOUNT_POINT="${LOCAL_MOUNT_POINT}"

if mount | grep -q "\${LOCAL_MOUNT_POINT}"; then
    echo "卸载Polymarket数据..."
    umount "\${LOCAL_MOUNT_POINT}"
    if [ \$? -eq 0 ]; then
        echo "✓ 卸载成功"
    else
        echo "✗ 卸载失败，尝试强制卸载..."
        diskutil unmount force "\${LOCAL_MOUNT_POINT}"
    fi
else
    echo "未挂载"
fi
EOF

chmod +x "${UNMOUNT_SCRIPT}"
echo -e "${GREEN}   ✓ 卸载脚本已创建: ${UNMOUNT_SCRIPT}${NC}"

# 7. 配置开机自动挂载（使用LaunchAgent）
echo ""
echo "6. 配置开机自动挂载..."

LAUNCH_AGENT_DIR="${HOME}/Library/LaunchAgents"
PLIST_FILE="${LAUNCH_AGENT_DIR}/com.polymarket.sshfs.plist"

mkdir -p "${LAUNCH_AGENT_DIR}"

cat > "${PLIST_FILE}" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.polymarket.sshfs</string>
    <key>ProgramArguments</key>
    <array>
        <string>${MOUNT_SCRIPT}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <false/>
    <key>StandardOutPath</key>
    <string>${HOME}/Library/Logs/polymarket_sshfs.log</string>
    <key>StandardErrorPath</key>
    <string>${HOME}/Library/Logs/polymarket_sshfs_error.log</string>
</dict>
</plist>
EOF

launchctl unload "${PLIST_FILE}" 2>/dev/null || true
launchctl load "${PLIST_FILE}"

echo -e "${GREEN}   ✓ LaunchAgent已配置${NC}"

# 8. 立即执行挂载
echo ""
echo "7. 执行挂载..."
"${MOUNT_SCRIPT}"

# 9. 验证挂载
echo ""
echo "8. 验证挂载..."
sleep 2

if mount | grep -q "${LOCAL_MOUNT_POINT}"; then
    echo -e "${GREEN}   ✓ 挂载成功验证${NC}"
    echo ""
    echo "   测试读取文件..."
    if ls "${LOCAL_MOUNT_POINT}" > /dev/null 2>&1; then
        FILE_COUNT=$(ls -1 "${LOCAL_MOUNT_POINT}" | wc -l)
        echo -e "${GREEN}   ✓ 可以访问远程目录，包含 ${FILE_COUNT} 个文件${NC}"
    else
        echo -e "${YELLOW}   ⚠️  目录为空或无法访问${NC}"
    fi
else
    echo -e "${RED}   ✗ 挂载验证失败${NC}"
    exit 1
fi

# 完成
echo ""
echo "=== 配置完成 ==="
echo ""
echo "快捷命令:"
echo "  挂载: ${MOUNT_SCRIPT}"
echo "  卸载: ${UNMOUNT_SCRIPT}"
echo ""
echo "挂载点: ${LOCAL_MOUNT_POINT}"
echo ""
echo "注意事项:"
echo "  - 系统重启后会自动挂载"
echo "  - 如果网络断开，SSHFS会自动重连（最多等待45秒）"
echo "  - 如果长时间无法重连，请运行卸载后重新挂载"

