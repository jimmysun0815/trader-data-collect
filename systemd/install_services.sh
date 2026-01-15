#!/bin/bash
# Systemd服务安装脚本 - 系统级服务（需要sudo）
# 在VPS上运行

set -e

echo "=== Polymarket采集器 Systemd服务安装（系统级）==="

# 配置变量
SERVICE_DIR="/home/ubuntu/trader-data-collect/systemd"
SYSTEMD_SYSTEM_DIR="/etc/systemd/system"

# 0. 检查是否有sudo权限
if ! sudo -n true 2>/dev/null; then
    echo "错误: 需要sudo权限才能安装系统级服务"
    echo "请运行: sudo bash install_services.sh"
    exit 1
fi

# 1. 停止并禁用旧的用户级服务（如果存在）
echo ""
echo "1. 清理旧的用户级服务..."
if systemctl --user is-active polymarket-recorder.service >/dev/null 2>&1; then
    echo "   停止用户级服务..."
    systemctl --user stop polymarket-recorder.service cex-recorder.service archive-data.timer || true
    systemctl --user disable polymarket-recorder.service cex-recorder.service archive-data.timer || true
    echo "   ✓ 已停止用户级服务"
fi

# 2. 复制服务文件到系统目录
echo ""
echo "2. 安装服务文件到系统目录..."

if [ ! -d "${SERVICE_DIR}" ]; then
    echo "   错误: 服务文件目录不存在: ${SERVICE_DIR}"
    echo "   请先上传systemd/目录到 /home/ubuntu/trader-data-collect/"
    exit 1
fi

for service in polymarket-recorder.service cex-recorder.service archive-data.service archive-data.timer; do
    if [ -f "${SERVICE_DIR}/${service}" ]; then
        sudo cp "${SERVICE_DIR}/${service}" "${SYSTEMD_SYSTEM_DIR}/"
        echo "   ✓ 已安装: ${service}"
    else
        echo "   ✗ 未找到: ${service}"
    fi
done

# 3. 重新加载systemd配置
echo ""
echo "3. 重新加载systemd配置..."
sudo systemctl daemon-reload
echo "   ✓ 配置已重新加载"

# 4. 启用服务（开机自启）
echo ""
echo "4. 启用服务（开机自启）..."
sudo systemctl enable polymarket-recorder.service
sudo systemctl enable cex-recorder.service
sudo systemctl enable archive-data.timer
echo "   ✓ 服务已设置为开机自启"
echo "   ✓ 归档定时器已启用（每天凌晨4点）"

# 5. 启动服务
echo ""
echo "5. 启动服务..."
sudo systemctl start polymarket-recorder.service
sudo systemctl start cex-recorder.service
sudo systemctl start archive-data.timer
echo "   ✓ 服务已启动"

# 6. 显示服务状态
echo ""
echo "6. 服务状态："
echo ""
sudo systemctl status polymarket-recorder.service --no-pager -l || true
echo ""
sudo systemctl status cex-recorder.service --no-pager -l || true
echo ""
sudo systemctl status archive-data.timer --no-pager -l || true

# 7. 显示帮助信息
echo ""
echo "=== 安装完成 ==="
echo ""
echo "常用命令（需要sudo）："
echo ""
echo "# 查看状态"
echo "sudo systemctl status polymarket-recorder.service"
echo "sudo systemctl status cex-recorder.service"
echo "sudo systemctl status archive-data.timer"
echo ""
echo "# 启动服务"
echo "sudo systemctl start polymarket-recorder.service"
echo "sudo systemctl start cex-recorder.service"
echo ""
echo "# 停止服务"
echo "sudo systemctl stop polymarket-recorder.service"
echo "sudo systemctl stop cex-recorder.service"
echo ""
echo "# 重启服务"
echo "sudo systemctl restart polymarket-recorder.service"
echo "sudo systemctl restart cex-recorder.service"
echo ""
echo "# 查看日志（实时）"
echo "sudo journalctl -u polymarket-recorder.service -f"
echo "sudo journalctl -u cex-recorder.service -f"
echo ""
echo "# 查看最近日志"
echo "sudo journalctl -u polymarket-recorder.service -n 100 --no-pager"
echo "sudo journalctl -u cex-recorder.service -n 100 --no-pager"
echo ""
echo "# 查看定时器列表"
echo "sudo systemctl list-timers"
echo ""
echo "# 手动触发归档（测试用）"
echo "sudo systemctl start archive-data.service"
echo ""
echo "✅ 所有服务已启动！即使SSH断开也会继续运行。"
echo ""
