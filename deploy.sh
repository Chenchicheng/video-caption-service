#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ ! -f ".env" ]; then
    echo "首次部署，请输入 SILICONFLOW_API_KEY（输入时不会显示）："
    read -r -s -p "API Key: " API_KEY
    echo
    if [ -z "$API_KEY" ]; then
        echo "错误: API Key 不能为空"
        exit 1
    fi
    echo "SILICONFLOW_API_KEY=$API_KEY" > .env
    echo "已保存到 .env"
fi

echo "=== 1. 创建虚拟环境并安装依赖 ==="
if [ ! -d "venv" ]; then
    python3 -m venv venv
fi
source venv/bin/activate
pip install -r requirements.txt

echo ""
echo "=== 2. 创建日志目录 ==="
mkdir -p "$SCRIPT_DIR/logs"
echo "日志目录: $SCRIPT_DIR/logs"

echo ""
echo "=== 3. 安装 systemd 服务 ==="
RUN_USER="$(stat -c '%U' "$SCRIPT_DIR" 2>/dev/null || whoami)"
sed -e "s|/home/admin/video-caption-service|$SCRIPT_DIR|g" -e "s|User=admin|User=$RUN_USER|g" video-caption.service | sudo tee /etc/systemd/system/video-caption.service > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable video-caption
sudo systemctl start video-caption
echo "服务已启动"
systemctl status video-caption --no-pager

echo ""
echo "=== 4. 安装日志轮转 ==="
sed "s|/home/admin/video-caption-service|$SCRIPT_DIR|g" video-caption-logrotate.conf | sudo tee /etc/logrotate.d/video-caption > /dev/null
echo "logrotate 配置已安装"

echo ""
echo "=== 部署完成 ==="
echo "服务管理: sudo systemctl start|stop|restart|status video-caption"
echo "查看日志: tail -f $SCRIPT_DIR/logs/service.log"
