import serial
import struct
import time
import threading
from collections import deque
from flask import Flask, jsonify, request
from datetime import datetime
from queue import Queue, Empty

app = Flask(__name__)

# ===== CONFIG =====
PORT = '/dev/ttyACM0'
BAUDRATE = 115200
SLAVE_ADDRESS = 5
MAX_READ_QUANTITY = 47
TIMEOUT = 1.0
LOGS_ENABLED = 1
MAX_ERROR_LOGS = 30
POLLING_INTERVAL = 0.1  # Задержка между запросами к железу (секунды)
RANGE_TIMEOUT = 10  # Таймаут неиспользуемого диапазона (секунды)

# ===== GLOBAL STATE =====
class ModbusClient:
    def __init__(self):
        self.port = None
        self.status = 'offline'
        self.last_packets = deque(maxlen=10)
        self.error_logs = deque(maxlen=MAX_ERROR_LOGS)
        self.lock = threading.Lock()
        
        # Кэш данных регистров
        self.register_cache = {}  # {address: value}
        self.cache_lock = threading.Lock()
        
        # Диапазоны для polling
        self.active_ranges = {}  # {(start, end): last_access_time}
        self.ranges_lock = threading.Lock()
        
        # Очередь задач
        self.task_queue = Queue()
        
        # Флаг работы фонового потока
        self.running = False
        self.worker_thread = None
    
    def log(self, message, level='INFO'):
        """Log message with timestamp"""
        if not LOGS_ENABLED:
            return
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        log_msg = f"[{timestamp}] [{level}] {message}"
        print(log_msg)
    
    def add_error_log(self, message):
        """Add error to log buffer"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        error_msg = f"[{timestamp}] {message}"
        self.error_logs.append(error_msg)
        self.log(f"ERROR: {message}", 'ERROR')
    
    def record_packet_time(self, response_time):
        """Record response time for average calculation"""
        self.last_packets.append(response_time)
    
    def get_avg_response_time(self):
        """Get average response time from last 10 packets"""
        if not self.last_packets:
            return 0
        return sum(self.last_packets) / len(self.last_packets)
    
    def is_connected(self):
        """Check if port is open and working"""
        if self.port is None or not self.port.is_open:
            return False
        return True
    
    def open_port(self):
        """Try to open serial port"""
        try:
            if self.port and self.port.is_open:
                self.port.close()
            
            self.port = serial.Serial(PORT, BAUDRATE, timeout=TIMEOUT)
            self.status = 'online'
            self.log(f"Serial port opened: {PORT} @ {BAUDRATE} baud")
            return True
        except Exception as e:
            self.status = 'mb-offline'
            self.add_error_log(f"Failed to open port {PORT}: {str(e)}")
            return False
    
    def close_port(self):
        """Close serial port"""
        try:
            if self.port and self.port.is_open:
                self.port.close()
                self.log("Serial port closed")
        except Exception as e:
            self.add_error_log(f"Error closing port: {str(e)}")
    
    def update_active_range(self, start, end):
        """Обновить активный диапазон"""
        with self.ranges_lock:
            self.active_ranges[(start, end)] = time.time()
    
    def get_polling_range(self):
        """Получить текущий диапазон для polling (самый широкий)"""
        with self.ranges_lock:
            current_time = time.time()
            
            # Удалить устаревшие диапазоны
            to_remove = []
            for range_key, last_access in self.active_ranges.items():
                if current_time - last_access > RANGE_TIMEOUT:
                    to_remove.append(range_key)
            
            for key in to_remove:
                del self.active_ranges[key]
                self.log(f"Range {key} removed (timeout)")
            
            if not self.active_ranges:
                return None
            
            # Найти самый широкий диапазон
            min_addr = min(start for start, end in self.active_ranges.keys())
            max_addr = max(end for start, end in self.active_ranges.keys())
            
            return (min_addr, max_addr)
    
    def get_cached_registers(self, start, quantity):
        """Получить данные из кэша"""
        with self.cache_lock:
            registers = []
            for i in range(quantity):
                addr = start + i
                registers.append(self.register_cache.get(addr, 0))
            return registers
    
    def update_cache(self, start, registers):
        """Обновить кэш данных"""
        with self.cache_lock:
            for i, value in enumerate(registers):
                self.register_cache[start + i] = value

client = ModbusClient()

# ===== CRC CALCULATION =====
def calculate_crc16(data):
    """Calculate CRC16 for Modbus RTU"""
    crc = 0xFFFF
    
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    
    return struct.pack('<H', crc)

# ===== MODBUS FUNCTIONS =====
def create_modbus_read_request(slave_address, register_address, quantity=1):
    """Create Modbus RTU read request (function 0x03)"""
    pdu = struct.pack('>BHH', 
                      0x03,
                      register_address,
                      quantity)
    
    request = bytes([slave_address]) + pdu
    crc = calculate_crc16(request)
    request += crc
    
    return request

def create_modbus_write_request(slave_address, register_address, value):
    """Create Modbus RTU write single register request (function 0x06)"""
    pdu = struct.pack('>BHH',
                      0x06,
                      register_address,
                      value)
    
    request = bytes([slave_address]) + pdu
    crc = calculate_crc16(request)
    request += crc
    
    return request

def parse_modbus_response(response):
    """Parse Modbus RTU response"""
    if len(response) < 3:
        return {
            'status': 'error',
            'message': 'Response too short',
            'raw': response.hex()
        }
    
    result = {
        'status': 'success',
        'raw': response.hex(),
        'slave_address': response[0],
        'function_code': response[1],
        'registers': []
    }
    
    if response[1] & 0x80:
        result['status'] = 'error'
        result['exception_code'] = response[2] if len(response) > 2 else None
        return result
    
    if response[1] == 0x03:
        byte_count = response[2]
        data = response[3:3 + byte_count]
        
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                register_value = (data[i] << 8) | data[i + 1]
                result['registers'].append(register_value)
    
    elif response[1] == 0x06:
        result['registers'].append((response[2] << 8) | response[3])
    
    return result

def send_modbus_request_raw(slave_address, register_address, quantity=None, write_value=None):
    """Отправить Modbus запрос к железу (используется только фоновым потоком)"""
    with client.lock:
        if not client.is_connected():
            if not client.open_port():
                return {
                    'status': 'error',
                    'message': 'Cannot open serial port'
                }
        
        try:
            if write_value is not None:
                request_data = create_modbus_write_request(slave_address, register_address, write_value)
                operation = 'WRITE'
            else:
                request_data = create_modbus_read_request(slave_address, register_address, quantity)
                operation = 'READ'
            
            start_time = time.time()
            client.port.write(request_data)
            time.sleep(0.05)
            
            response = client.port.read(256)
            response_time = time.time() - start_time
            
            client.record_packet_time(response_time)
            
            parsed = parse_modbus_response(response)
            
            if parsed['status'] == 'success':
                client.status = 'online'
            else:
                client.status = 'mb-offline'
                client.add_error_log(f"Modbus error response: {parsed.get('message')}")
            
            return parsed
            
        except serial.SerialException as e:
            client.status = 'mb-offline'
            client.add_error_log(f"Serial exception: {str(e)}")
            client.close_port()
            return {'status': 'error', 'message': f'Serial error: {str(e)}'}
        
        except Exception as e:
            client.status = 'mb-offline'
            client.add_error_log(f"Unexpected error: {str(e)}")
            return {'status': 'error', 'message': f'Error: {str(e)}'}

# ===== BACKGROUND WORKER =====
def background_worker():
    """Фоновый поток для обработки Modbus запросов"""
    client.log("Background worker started")
    
    while client.running:
        try:
            # Проверяем очередь задач
            try:
                task = client.task_queue.get(timeout=0.1)
                
                if task['type'] == 'write':
                    # Задача на запись
                    addr = task['address']
                    value = task['value']
                    
                    result = send_modbus_request_raw(
                        SLAVE_ADDRESS,
                        addr,
                        write_value=value
                    )
                    
                    if result['status'] == 'success':
                        client.log(f"Write success: addr={addr}, value={value}")
                    else:
                        client.add_error_log(f"Write failed: addr={addr}, {result.get('message')}")
                
                client.task_queue.task_done()
                time.sleep(POLLING_INTERVAL)
                
            except Empty:
                # Очередь пуста - делаем polling регистров
                polling_range = client.get_polling_range()
                
                if polling_range:
                    start_addr, end_addr = polling_range
                    total_quantity = end_addr - start_addr + 1
                    
                    # Разбиваем на части по MAX_READ_QUANTITY
                    current_addr = start_addr
                    remaining = total_quantity
                    
                    while remaining > 0:
                        read_qty = min(remaining, MAX_READ_QUANTITY)
                        
                        result = send_modbus_request_raw(
                            SLAVE_ADDRESS,
                            current_addr,
                            quantity=read_qty
                        )
                        
                        if result['status'] == 'success':
                            client.update_cache(current_addr, result['registers'])
                        else:
                            client.add_error_log(f"Polling failed: addr={current_addr}, qty={read_qty}")
                        
                        current_addr += read_qty
                        remaining -= read_qty
                        time.sleep(POLLING_INTERVAL)
                else:
                    # Нет активных диапазонов - ждём
                    time.sleep(0.5)
        
        except Exception as e:
            client.add_error_log(f"Worker error: {str(e)}")
            time.sleep(1)
    
    client.log("Background worker stopped")

# ===== API ENDPOINTS =====
@app.route('/get_data', methods=['POST'])
def get_data():
    """Читать регистры (мгновенный ответ из кэша)"""
    try:
        data = request.get_json()
        
        if not data or 'address' not in data or 'quantity' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing address or quantity parameter'
            }), 400
        
        address = int(data['address'])
        quantity = int(data['quantity'])
        
        if quantity <= 0 or quantity > 1000:
            return jsonify({
                'status': 'error',
                'message': 'Invalid quantity'
            }), 400
        
        # Обновить активный диапазон
        client.update_active_range(address, address + quantity - 1)
        
        # Получить данные из кэша
        registers = client.get_cached_registers(address, quantity)
        
        return jsonify({
            'status': 'success',
            'address': address,
            'quantity': quantity,
            'registers': registers
        }), 200
    
    except Exception as e:
        client.add_error_log(f"GET_DATA error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/send_data', methods=['POST'])
def send_data():
    """Записать регистр (мгновенный ответ, задача в очередь)"""
    try:
        data = request.get_json()
        
        if not data or 'address' not in data or 'value' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing address or value parameter'
            }), 400
        
        address = int(data['address'])
        value = int(data['value'])
        
        if value < 0 or value > 65535:
            return jsonify({
                'status': 'error',
                'message': 'Value must be 0-65535'
            }), 400
        
        # Поставить задачу в очередь
        client.task_queue.put({
            'type': 'write',
            'address': address,
            'value': value
        })
        
        # Мгновенный ответ
        return jsonify({
            'status': 'success',
            'address': address,
            'value': value,
            'message': 'Write task queued'
        }), 200
    
    except Exception as e:
        client.add_error_log(f"SEND_DATA error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/status', methods=['GET'])
def status():
    """Получить статус (без обращения к порту)"""
    polling_range = client.get_polling_range()
    
    return jsonify({
        'status': 'success',
        'modbus_status': client.status,
        'connection_state': 'connected' if client.is_connected() else 'disconnected',
        'avg_response_time_ms': round(client.get_avg_response_time() * 1000, 2),
        'last_10_packets_count': len(client.last_packets),
        'error_logs': list(client.error_logs),
        'error_log_count': len(client.error_logs),
        'polling_range': f"{polling_range[0]}-{polling_range[1]}" if polling_range else "none",
        'active_ranges_count': len(client.active_ranges),
        'queue_size': client.task_queue.qsize(),
        'cache_size': len(client.register_cache),
        'timestamp': datetime.now().isoformat()
    }), 200

# ===== STARTUP/SHUTDOWN =====
def start_background_worker():
    """Запустить фоновый поток"""
    client.running = True
    client.worker_thread = threading.Thread(target=background_worker, daemon=True)
    client.worker_thread.start()
    client.log("Background worker thread started")

def stop_background_worker():
    """Остановить фоновый поток"""
    client.running = False
    if client.worker_thread:
        client.worker_thread.join(timeout=5)
    client.log("Background worker thread stopped")

def cleanup():
    """Cleanup on application exit"""
    stop_background_worker()
    client.close_port()

if __name__ == "__main__":
    client.log("Modbus API Server Starting", 'INFO')
    
    # Запуск фонового потока
    start_background_worker()
    
    try:
        app.run(host='0.0.0.0', port=8082, debug=False, threaded=True)
    except KeyboardInterrupt:
        client.log("Shutdown signal received", 'INFO')
    finally:
        cleanup()
        client.log("Modbus API Server Stopped", 'INFO')
