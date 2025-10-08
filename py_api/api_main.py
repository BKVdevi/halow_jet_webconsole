from flask import Flask, request
import random
import json
import psutil
import threading
import time
from datetime import datetime

app = Flask(__name__)

# Глобальные переменные для хранения системных данных
system_data = {
    "param1": 0,
    "param2": 0,
    "cpu_percent": 0,
    "memory_percent": 0,
    "memory_used_gb": 0,
    "memory_total_gb": 0,
    "swap_percent": 0,
    "swap_used_gb": 0,
    "cpu_temp": 0,
    "last_update": ""
}

def get_cpu_temperature():
    """Получение температуры процессора"""
    try:
        # Для Linux систем
        if hasattr(psutil, "sensors_temperatures"):
            temps = psutil.sensors_temperatures()
            if temps:
                # Ищем основные температурные сенсоры
                for name, entries in temps.items():
                    if name in ['coretemp', 'k10temp', 'cpu_thermal']:
                        if entries:
                            return round(entries[0].current, 1)
                
                # Если специфические не найдены, берем первый доступный
                for name, entries in temps.items():
                    if entries:
                        return round(entries[0].current, 1)
        
        # Альтернативный метод для Raspberry Pi
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = float(f.read().strip()) / 1000
                return round(temp, 1)
        except:
            pass
            
        return 0  # Если температуру получить не удалось
    except Exception as e:
        print(f"Ошибка получения температуры: {e}")
        return 0

def update_system_data():
    """Обновление системных данных в отдельном потоке"""
    global system_data
    
    while True:
        try:
            # Получаем данные о процессоре
            cpu_percent = psutil.cpu_percent(interval=0.1)
            
            # Получаем данные о памяти
            memory = psutil.virtual_memory()
            memory_percent = memory.percent
            memory_used_gb = round(memory.used / (1024**3), 2)
            memory_total_gb = round(memory.total / (1024**3), 2)
            
            # Получаем данные о свопе
            swap = psutil.swap_memory()
            swap_percent = swap.percent
            swap_used_gb = round(swap.used / (1024**3), 2)
            
            # Получаем температуру процессора
            cpu_temp = get_cpu_temperature()
            
            # Обновляем глобальные данные
            system_data.update({
                "param1": random.random(),
                "param2": random.random(),
                "cpu_percent": round(cpu_percent, 1),
                "memory_percent": round(memory_percent, 1),
                "memory_used_gb": memory_used_gb,
                "memory_total_gb": memory_total_gb,
                "swap_percent": round(swap_percent, 1),
                "swap_used_gb": swap_used_gb,
                "cpu_temp": cpu_temp,
                "last_update": datetime.now().strftime("%H:%M:%S")
            })
            
        except Exception as e:
            print(f"Ошибка обновления системных данных: {e}")
        
        # Обновляем данные 3 раза в секунду
        time.sleep(0.33)

@app.route('/')
def api_data():
    """API эндпоинт для получения данных"""
    global system_data
    system_data["param1"] = random.random()
    system_data["param2"] = random.random()
    return json.dumps(system_data)

if __name__ == '__main__':
    # Запускаем поток обновления системных данных
    system_thread = threading.Thread(target=update_system_data, daemon=True)
    system_thread.start()
    
    app.run(host='localhost', port=8081, debug=False)
