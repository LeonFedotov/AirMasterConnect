#!/usr/bin/env python3
import argparse, json, socket, struct, sys, time
from datetime import datetime

REQUEST_CMD = bytes([0x55, 0xCD, 0x47, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x69, 0x0D, 0x0A])

def parse_udp_packet(data):
    if len(data) < 37: return None
    if data[0:4] != b'\x00\x00\x00\x03': return None
    if len(data) < 37: return None
    try:
        vals = struct.unpack_from('>HHHHHHH', data, 23)
    except struct.error:
        return None
    return {
        'timestamp': datetime.now().isoformat(),
        'pm25': vals[0], 'pm10': vals[1],
        'hcho': vals[2] / 100.0, 'tvoc': vals[3] / 100.0,
        'co2': vals[4],
        'temperature': (vals[5] - 3500) / 100.0,
        'humidity': vals[6] / 100.0,
    }

def parse_serial_packet(buf):
    if len(buf) < 40 or buf[0] != 0xAA: return None
    def u16(o): return (buf[o] << 8) | buf[o + 1]
    # verify checksum
    expected = u16(36)
    actual = sum(buf[:36])
    if expected != actual: return None
    pm25, pm10 = u16(1), u16(3)
    hcho, tvoc = u16(5), u16(7)
    co2, temp, rh = u16(9), u16(11), u16(13)
    return {
        'timestamp': datetime.now().isoformat(),
        'pm25': pm25 if pm25 < 1000 else None,
        'pm10': pm10 if pm10 < 1000 else None,
        'hcho': hcho / 1000.0 if hcho < 3100 else None,
        'tvoc': tvoc / 1000.0 if tvoc < 3100 else None,
        'co2': co2 if co2 < 5100 else None,
        'temperature': temp / 100.0 if temp < 10100 else None,
        'humidity': rh / 100.0 if rh < 10100 else None,
        'particles': {'>0.3um': u16(19), '>0.5um': u16(21), '>1.0um': u16(23), '>2.5um': u16(25), '>5.0um': u16(27), '>10um': u16(29)},
    }

def fmt(d):
    lines = [f"  [{d['timestamp']}]"]
    if d.get('pm25') is not None: lines.append(f"  PM2.5: {d['pm25']} µg/m³  |  PM10: {d['pm10']} µg/m³")
    if d.get('hcho') is not None: lines.append(f"  HCHO:  {d['hcho']:.3f} mg/m³  |  TVOC: {d['tvoc']:.3f} mg/m³")
    if d.get('co2') is not None: lines.append(f"  CO2:   {d['co2']} ppm")
    if d.get('temperature') is not None: lines.append(f"  Temp:  {d['temperature']:.1f} °C  |  RH: {d['humidity']:.1f}%")
    if 'particles' in d: lines.append(f"  Particles: {' | '.join(f'{k}:{v}' for k, v in d['particles'].items())}")
    return '\n'.join(lines)

def cmd_listen(args):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(('', 12414))
    print('Listening on UDP port 12414... Ctrl+C to stop.\n')
    known = set()
    try:
        while True:
            data, addr = sock.recvfrom(1024)
            ip = addr[0]
            if ip not in known:
                print(f'  New device: {ip} (packet len={len(data)})')
                known.add(ip)
            reading = parse_udp_packet(data)
            if reading:
                print(fmt(reading))
                print()
            else:
                print(f'  Unparsed packet from {ip}: len={len(data)} hex={data[:8].hex()}...')
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        sock.close()

def cmd_serial(args):
    import serial, serial.tools.list_ports
    port = args.port
    if not port:
        for p in serial.tools.list_ports.comports():
            desc = (p.description or '').lower()
            if 'cp210' in desc or (p.vid and f'{p.vid:04x}' == '10c4'):
                port = p.device; break
    if not port: print('Could not find CP2102. Use --port'); sys.exit(1)

    print(f'Opening {port} at 19200 (no DTR reset)...\n')
    ser = serial.Serial()
    ser.port = port
    ser.baudrate = 19200
    ser.timeout = 2
    ser.dtr = False
    ser.rts = False
    ser.open()

    time.sleep(0.5)  # let things settle without resetting

    try:
        while True:
            # flush any stale data
            ser.reset_input_buffer()

            ser.write(REQUEST_CMD)
            time.sleep(0.5)

            # sync to 0xAA header
            header = ser.read(1)
            if not header or header[0] != 0xAA:
                # scan for 0xAA
                for _ in range(80):
                    b = ser.read(1)
                    if not b: break
                    if b[0] == 0xAA:
                        header = b
                        break
                else:
                    print('  No response, retrying...')
                    time.sleep(2)
                    continue

            rest = ser.read(39)
            if len(rest) < 39:
                print(f'  Short read: {1 + len(rest)} bytes')
                time.sleep(2)
                continue

            buf = header + rest
            reading = parse_serial_packet(buf)
            if reading:
                print(fmt(reading))
                print()
            else:
                print(f'  Checksum mismatch, skipping')

            time.sleep(2.5)
    except KeyboardInterrupt:
        print('\nStopped.')
    finally:
        ser.close()

def main():
    p = argparse.ArgumentParser(description='AirMaster AM7 Tool')
    sub = p.add_subparsers(dest='command')
    sub.add_parser('listen', help='Listen for WiFi UDP broadcasts on port 12414')
    ps = sub.add_parser('serial', help='Read via USB serial (CP2102)')
    ps.add_argument('--port', help='Serial port path')
    args = p.parse_args()
    if args.command == 'listen': cmd_listen(args)
    elif args.command == 'serial': cmd_serial(args)
    else: p.print_help()

if __name__ == '__main__':
    main()
