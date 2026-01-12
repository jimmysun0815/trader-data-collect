#!/bin/bash
# Systemd服务安装脚本 - 在VPS上运行

set -e

echo "=== Polymarket采集器 Systemd服务安装 ==="

# 配置变量
SERVICE_DIR="/home/ubuntu/trader-data-collect/systemd"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"

# 1. 检查systemd是否支持用户服务
echo ""
echo "1. 检查systemd用户服务支持..."
if ! systemctl --user status >/dev/null 2>&1; then
    echo "   警告: systemd用户服务可能未启用"
    echo "   尝试启用: loginctl enable-linger $USER"
    loginctl enable-linger "$USER" || true
fi

# 2. 创建用户systemd目录
echo ""
echo "2. 创建systemd用户服务目录..."
mkdir -p "${SYSTEMD_USER_DIR}"

# 3. 复制服务文件
echo ""
echo "3. 安装服务文件..."

if [ ! -d "${SERVICE_DIR}" ]; then
    echo "   错误: 服务文件目录不存在: ${SERVICE_DIR}"
    echo "   请先上传systemd/目录到 /home/ubuntu/trader-data-collect/"
    exit 1
fi

for service in polymarket-recorder.service cex-recorder.service polymarket-recorders.target archive-data.service archive-data.timer; do
    if [ -f "${SERVICE_DIR}/${service}" ]; then
        cp "${SERVICE_DIR}/${service}" "${SYSTEMD_USER_DIR}/"
        echo "   ✓ 已安装: ${service}"
    else
        echo "   ✗ 未找到: ${service}"
    fi
done

# 4. 重新加载systemd配置
echo ""
echo "4. 重新加载systemd配置..."
systemctl --user daemon-reload

# 5. 启用服务（开机自启）
echo ""
echo "5. 启用服务（开机自启）..."
systemctl --user enable polymarket-recorder.service
systemctl --user enable cex-recorder.service
systemctl --user enable archive-data.timer
echo "   ✓ 服务已设置为开机自启"
echo "   ✓ 归档定时器已启用（每天凌晨4点）"

# 6. 显示服务状态
echo ""
echo "=== 安装完成 ==="
echo ""
echo "常用命令："
echo ""
echo "# 启动所有采集器"
echo "systemctl --user start polymarket-recorder.service cex-recorder.service"
echo ""
echo "# 启动归档定时器"
echo "systemctl --user start archive-data.timer"
echo ""
echo "# 停止所有采集器"
echo "systemctl --user stop polymarket-recorder.service cex-recorder.service"
echo ""
echo "# 重启采集器"
echo "systemctl --user restart polymarket-recorder.service"
echo "systemctl --user restart cex-recorder.service"
echo ""
echo "# 查看状态"
echo "systemctl --user status polymarket-recorder.service"
echo "systemctl --user status cex-recorder.service"
echo "systemctl --user status archive-data.timer"
echo ""
echo "# 手动触发归档（测试用）"
echo "systemctl --user start archive-data.service"
echo ""
echo "# 查看定时器列表"
echo "systemctl --user list-timers"
echo ""
echo "# 查看日志（实时）"
echo "journalctl --user -u polymarket-recorder.service -f"
echo "journalctl --user -u cex-recorder.service -f"
echo "journalctl --user -u archive-data.service -f"
echo ""
echo "# 查看最近日志"
echo "journalctl --user -u polymarket-recorder.service -n 100"
echo "journalctl --user -u cex-recorder.service -n 100"
echo "journalctl --user -u archive-data.service -n 50"
echo ""
echo "现在运行以下命令启动服务："
echo "systemctl --user start polymarket-recorder.service cex-recorder.service archive-data.timer"

