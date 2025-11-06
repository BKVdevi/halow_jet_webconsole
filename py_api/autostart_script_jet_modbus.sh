#!/bin/bash

# Скрипт для создания systemd сервиса для jet_web_app

SERVICE_NAME="mb-jet-web-api"
PYTHON_PATH="/home/jet/jet_web_app/py_api/.venv/bin/python"
SCRIPT_PATH="/home/jet/jet_web_app/py_api/jet_talk_modbus_api.py"
WORKING_DIR="/home/jet/jet_web_app/py_api"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

echo "Создание systemd сервиса для ${SERVICE_NAME}..."

# Создание файла сервиса
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Jet Web App API Service
After=network.target

[Service]
Type=simple
User=jet
Group=jet
WorkingDirectory=$WORKING_DIR
Environment=PATH=/home/jet/jet_web_app/py_api/.venv/bin
ExecStart=$PYTHON_PATH $SCRIPT_PATH
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Установка прав доступа
sudo chmod 644 "$SERVICE_FILE"

# Перезагрузка systemd и включение сервиса
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME.service"

echo "Сервис $SERVICE_NAME успешно создан и добавлен в автозапуск!"
echo ""
echo "Полезные команды для управления сервисом:"
echo "  Запустить:     sudo systemctl start $SERVICE_NAME"
echo "  Остановить:    sudo systemctl stop $SERVICE_NAME"
echo "  Перезапустить: sudo systemctl restart $SERVICE_NAME"
echo "  Статус:        sudo systemctl status $SERVICE_NAME"
echo "  Логи:          journalctl -u $SERVICE_NAME -f"
echo "  Отключить автозапуск: sudo systemctl disable $SERVICE_NAME"
