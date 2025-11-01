from flask import Flask, request
import json
import psutil
import threading
import time
from datetime import datetime
from queue import Queue
import esp_mb_api

app = Flask(__name__)

# Глобальные переменные для хранения системных данных
system_data = {
    "cpu_percent": 0,
    "memory_percent": 0,
    "memory_used_gb": 0,
    "memory_total_gb": 0,
    "swap_percent": 0,
    "swap_used_gb": 0,
    "cpu_temp": 0,
    "last_update": ""
}

# Глобальные переменные для Modbus данных
modbus_data = {
    "chanel_0": 0,
    "chanel_1": 0,
    "chanel_2": 0,
    "chanel_3": 0,
    "chanel_4": 0,
    "chanel_5": 0,
    "chanel_6": 0,
    "chanel_7": 0,
    "chanel_8": 0,
    "chanel_9": 0,
    "chanel_10": 0,
    "chanel_11": 0,
    "chanel_12": 0,
    "chanel_13": 0,
    "chanel_14": 0,
    "chanel_15": 0,
}

# Очередь для команд записи в каналы
write_queue = Queue()

# Блокировка для безопасного доступа к modbus_data
modbus_lock = threading.Lock()


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


def update_modbus_data():
    """Фоновое обновление данных из Modbus и обработка команд записи"""
    global modbus_data
    
    while True:
        try:
            # Обработка команд из очереди (без блокировок)
            if not write_queue.empty():
                try:
                    chanel, data = write_queue.get_nowait()
                    esp_mb_api.write_chanel(chanel, data)
                except:
                    pass
            
            # Чтение данных из Modbus
            try:
                raw_data = esp_mb_api.read_data()
                
                # Обновляем глобальные данные с блокировкой
                with modbus_lock:
                    for i, value in enumerate(raw_data):
                        modbus_data[f"chanel_{i}"] = value
            except Exception as e:
                print(f"Ошибка чтения Modbus данных: {e}")
            
            # Небольшая пауза для разгрузки CPU
            time.sleep(0.05)
        
        except Exception as e:
            print(f"Ошибка в потоке обновления Modbus: {e}")
            time.sleep(0.1)


@app.route('/')
def api_data():
    """API эндпоинт для получения системных данных"""
    global system_data
    return json.dumps(system_data)


@app.route('/get_chanel', methods=['GET'])
def get_chanel():
    """API эндпоинт для получения значений всех каналов"""
    with modbus_lock:
        # Возвращаем копию данных для избежания проблем с многопоточностью
        return json.dumps(modbus_data.copy())


@app.route('/set_chanel', methods=['POST'])
def set_chanel():
    """API эндпоинт для установки значения канала (неблокирующий)"""
    try:
        request_data = request.get_json()
        
        # Валидация входных данных
        if not request_data or 'chanel' not in request_data or 'data' not in request_data:
            return json.dumps({
                "status": "error",
                "message": "Missing 'chanel' or 'data' field"
            }), 400
        
        chanel = int(request_data['chanel'])
        data = int(request_data['data'])
        
        # Проверка диапазонов
        if chanel < 0 or chanel > 15:
            return json.dumps({
                "status": "error",
                "message": "Channel must be between 0 and 15"
            }), 400
        
        if data < -100 or data > 100:
            return json.dumps({
                "status": "error",
                "message": "Data must be between -100 and 100"
            }), 400
        
        # Добавляем команду в очередь (это не блокирует запрос)
        write_queue.put((chanel, data))
        
        return json.dumps({
            "status": "success",
            "message": f"Channel {chanel} write request queued",
            "chanel": chanel,
            "data": data
        }), 200
    
    except ValueError:
        return json.dumps({
            "status": "error",
            "message": "Invalid data type. 'chanel' and 'data' must be integers"
        }), 400
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        }), 500


@app.route('/set_chanel/<int:chanel>/<int:data>', methods=['GET'])
def set_chanel_url(chanel, data):
    """Alternative API эндпоинт для установки значения канала через URL параметры"""
    try:
        # Проверка диапазонов
        if chanel < 0 or chanel > 15:
            return json.dumps({
                "status": "error",
                "message": "Channel must be between 0 and 15"
            }), 400
        
        if data < -100 or data > 100:
            return json.dumps({
                "status": "error",
                "message": "Data must be between -100 and 100"
            }), 400
        
        # Добавляем команду в очередь
        write_queue.put((chanel, data))
        
        return json.dumps({
            "status": "success",
            "message": f"Channel {chanel} write request queued",
            "chanel": chanel,
            "data": data
        }), 200
    
    except Exception as e:
        return json.dumps({
            "status": "error",
            "message": str(e)
        }), 500


if __name__ == '__main__':
    # Запускаем поток обновления системных данных
    system_thread = threading.Thread(target=update_system_data, daemon=True)
    system_thread.start()
    
    # Запускаем поток обновления Modbus данных
    modbus_thread = threading.Thread(target=update_modbus_data, daemon=True)
    modbus_thread.start()
    
    # Запускаем Flask приложение
    app.run(host='localhost', port=8081, debug=False)