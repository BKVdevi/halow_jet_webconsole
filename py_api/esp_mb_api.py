import minimalmodbus
import serial
import time

instrument = minimalmodbus.Instrument('COM4', 0x01, close_port_after_each_call=False)
instrument.serial.bytesize = 8
instrument.serial.parity   = serial.PARITY_NONE
instrument.serial.stopbits = serial.STOPBITS_ONE
instrument.serial.baudrate = 512000


def unsigned_to_signed_16bit(val):
    if val >= 2**15:
        val -= 2**16
    return val


def signed_to_unsigned_16bit(val):
    if val < 0:
        val += 2**16
    return val

def write_chanel(chanel, data = -100):
    if data >= 100:
        data = 100
    elif data <= -100:
        data = -100
    if chanel > 15:
        raise Exception("chanel max = 15")
    if chanel < 0:
        raise Exception("chanel min = 0")
    data = signed_to_unsigned_16bit(data)
    while True:
        try:
            instrument.write_register(chanel, data)
            break
        except serial.SerialException as e:
            print(e)
            while True:
                time.sleep(0.1)
                try:
                    instrument.serial.close()
                    instrument.serial.open()
                    break
                except Exception as e:
                    print(e)
                    pass
        except Exception as e:
            print(e)
            pass



def read_data():
    while True:
        try:
            data = instrument.read_registers(0, 16)
            break
        except serial.SerialException as e:
            print(e)
            while True:
                time.sleep(0.1)
                try:
                    instrument.serial.close()
                    instrument.serial.open()
                    break
                except Exception as e:
                    print(e)
        except Exception as e:
            print(e)
    for i in range(len(data)):
        data[i] = unsigned_to_signed_16bit(data[i])
    return data

if __name__ == "__main__":
    while True:
        write_chanel(3, -100)
        write_chanel(1, 15)
        write_chanel(5, 99)
        write_chanel(7, 84)
        write_chanel(12, 110)
        print(read_data())
