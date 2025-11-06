import serial
import struct
import time
import threading
from collections import deque
from flask import Flask, jsonify, request
from datetime import datetime

app = Flask(__name__)

# ===== CONFIG =====
PORT = '/dev/ttyACM0'
BAUDRATE = 115200
SLAVE_ADDRESS = 5
MAX_READ_QUANTITY = 47
TIMEOUT = 1.0
LOGS_ENABLED = 1  # 1 = enabled, 0 = disabled
MAX_ERROR_LOGS = 10

# ===== GLOBAL STATE =====
class ModbusClient:
    def __init__(self):
        self.port = None
        self.status = 'offline'
        self.last_packets = deque(maxlen=10)  # For average response time
        self.error_logs = deque(maxlen=MAX_ERROR_LOGS)
        self.lock = threading.Lock()
    
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
        self.log(f"ERROR LOGGED: {message}", 'ERROR')
    
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
    
    # Check for error (bit 7 set)
    if response[1] & 0x80:
        result['status'] = 'error'
        result['exception_code'] = response[2] if len(response) > 2 else None
        return result
    
    # Parse read holding registers response
    if response[1] == 0x03:
        byte_count = response[2]
        data = response[3:3 + byte_count]
        
        for i in range(0, len(data), 2):
            if i + 1 < len(data):
                register_value = (data[i] << 8) | data[i + 1]
                result['registers'].append(register_value)
    
    # Parse write single register response
    elif response[1] == 0x06:
        result['registers'].append((response[2] << 8) | response[3])
    
    return result

def send_modbus_request(slave_address, register_address, quantity=None, write_value=None):
    """Send Modbus RTU request and get response"""
    with client.lock:
        # Ensure port is open
        if not client.is_connected():
            if not client.open_port():
                return {
                    'status': 'error',
                    'message': 'Cannot open serial port'
                }
        
        try:
            # Create appropriate request
            if write_value is not None:
                request_data = create_modbus_write_request(slave_address, register_address, write_value)
                operation = 'WRITE'
            else:
                request_data = create_modbus_read_request(slave_address, register_address, quantity)
                operation = 'READ'
            
            client.log(f"{operation} REQ: addr={register_address}, qty/val={quantity or write_value}")
            
            # Send request and measure response time
            start_time = time.time()
            client.port.write(request_data)
            time.sleep(0.05)
            
            response = client.port.read(256)
            response_time = time.time() - start_time
            
            client.record_packet_time(response_time)
            client.log(f"{operation} OK: {len(response)} bytes, {response_time*1000:.1f}ms")
            
            # Parse response
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

# ===== API ENDPOINTS =====
@app.route('/get_data', methods=['POST'])
def get_data():
    """Read registers from Modbus device"""
    try:
        data = request.get_json()
        
        if not data or 'address' not in data or 'quantity' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing address or quantity parameter'
            }), 400
        
        address = int(data['address'])
        quantity = int(data['quantity'])
        
        if quantity > MAX_READ_QUANTITY * 10:  # Arbitrary upper limit
            return jsonify({
                'status': 'error',
                'message': f'Quantity too large (max ~{MAX_READ_QUANTITY * 10})'
            }), 400
        
        all_registers = []
        current_addr = address
        remaining = quantity
        
        # Split into multiple requests if needed
        while remaining > 0:
            read_qty = min(remaining, MAX_READ_QUANTITY)
            
            result = send_modbus_request(
                slave_address=SLAVE_ADDRESS,
                register_address=current_addr,
                quantity=read_qty
            )
            
            if result['status'] != 'success':
                return jsonify({
                    'status': 'error',
                    'message': result.get('message', 'Modbus read failed'),
                    'partial_data': all_registers
                }), 500
            
            all_registers.extend(result['registers'])
            current_addr += read_qty
            remaining -= read_qty
            time.sleep(0.01)
        
        return jsonify({
            'status': 'success',
            'address': address,
            'quantity': quantity,
            'registers': all_registers
        }), 200
    
    except Exception as e:
        client.add_error_log(f"GET_DATA endpoint error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/send_data', methods=['POST'])
def send_data():
    """Write single register to Modbus device"""
    try:
        data = request.get_json()
        
        if not data or 'address' not in data or 'value' not in data:
            return jsonify({
                'status': 'error',
                'message': 'Missing address or value parameter'
            }), 400
        
        address = int(data['address'])
        value = int(data['value'])
        
        # Validate value range (0-65535 for unsigned 16-bit)
        if value < 0 or value > 65535:
            return jsonify({
                'status': 'error',
                'message': 'Value must be 0-65535'
            }), 400
        
        result = send_modbus_request(
            slave_address=SLAVE_ADDRESS,
            register_address=address,
            write_value=value
        )
        
        if result['status'] != 'success':
            return jsonify({
                'status': 'error',
                'message': result.get('message', 'Modbus write failed')
            }), 500
        
        return jsonify({
            'status': 'success',
            'address': address,
            'value': value,
            'written': result['registers'][0] if result['registers'] else value
        }), 200
    
    except Exception as e:
        client.add_error_log(f"SEND_DATA endpoint error: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/status', methods=['GET'])
def status():
    """Get current connection status and metrics"""
    return jsonify({
        'status': 'success',
        'modbus_status': client.status,
        'connection_state': 'connected' if client.is_connected() else 'disconnected',
        'avg_response_time_ms': round(client.get_avg_response_time() * 1000, 2),
        'last_10_packets_count': len(client.last_packets),
        'error_logs': list(client.error_logs),
        'error_log_count': len(client.error_logs),
        'timestamp': datetime.now().isoformat()
    }), 200

# ===== STARTUP/SHUTDOWN =====
@app.before_request
def before_request():
    """Ensure connection before each request"""
    if not client.is_connected():
        client.open_port()

@app.teardown_appcontext
def shutdown(exception=None):
    """Cleanup on shutdown"""
    pass

def cleanup():
    """Cleanup on application exit"""
    client.close_port()

if __name__ == "__main__":
    client.log("Modbus API Server Starting", 'INFO')
    try:
        app.run(host='localhost', port=8082, debug=False, threaded=True)
    except KeyboardInterrupt:
        client.log("Shutdown signal received", 'INFO')
    finally:
        cleanup()
        client.log("Modbus API Server Stopped", 'INFO')
