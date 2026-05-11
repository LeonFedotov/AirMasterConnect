#!/usr/bin/env python3
"""
AirMaster AM7 Dashboard with persistent data and graphs.

Saves readings to ./airmaster_data/YYYY-MM-DD.jsonl
Serves a live graph dashboard at http://localhost:8080

Usage: python3 airmaster_dashboard.py
"""

import json
import os
import socket
import struct
import threading
import time
from datetime import datetime, date
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DATA_DIR = Path('airmaster_data')
DATA_DIR.mkdir(exist_ok=True)

latest_reading = {}
lock = threading.Lock()


def parse_udp_packet(data):
    if len(data) < 37 or data[0:4] != b'\x00\x00\x00\x03':
        return None
    try:
        vals = struct.unpack_from('>HHHHHHH', data, 23)
    except struct.error:
        return None
    return {
        't': datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        'pm25': vals[0],
        'pm10': vals[1],
        'hcho': round(vals[2] / 100.0, 3),
        'tvoc': round(vals[3] / 100.0, 3),
        'co2': vals[4],
        'temp': round((vals[5] - 3500) / 100.0, 1),
        'rh': round(vals[6] / 100.0, 1),
    }


def save_reading(reading):
    day = reading['t'][:10]
    path = DATA_DIR / f'{day}.jsonl'
    with open(path, 'a') as f:
        f.write(json.dumps(reading) + '\n')


def load_day(day_str):
    path = DATA_DIR / f'{day_str}.jsonl'
    if not path.exists():
        return []
    readings = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    readings.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return readings


def available_days():
    days = []
    for f in sorted(DATA_DIR.glob('*.jsonl')):
        days.append(f.stem)
    return days


def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if hasattr(socket, 'SO_REUSEPORT'):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.bind(('', 12414))
    print('UDP listener on port 12414')

    last_save = 0
    while True:
        data, addr = sock.recvfrom(1024)
        reading = parse_udp_packet(data)
        if not reading:
            continue

        now = time.time()
        if now - last_save >= 10:
            save_reading(reading)
            last_save = now

        with lock:
            global latest_reading
            latest_reading = reading


HTML_PAGE = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>AirMaster AM7 Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chartjs-adapter-date-fns@3/dist/chartjs-adapter-date-fns.bundle.min.js"></script>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
    background: #0a0a0f; color: #ccc;
    padding: 16px;
  }
  header {
    display: flex; align-items: center; justify-content: space-between;
    margin-bottom: 16px; flex-wrap: wrap; gap: 8px;
  }
  h1 { font-size: 1.2em; font-weight: 500; color: #888; }
  h1 span { color: #f0f0f0; }
  .controls { display: flex; gap: 8px; align-items: center; }
  select, button {
    background: #1a1a24; color: #ccc; border: 1px solid #333;
    border-radius: 6px; padding: 6px 12px; font-size: 0.85em; cursor: pointer;
  }
  select:hover, button:hover { border-color: #555; }
  #status { font-size: 0.8em; color: #555; }
  #status.live { color: #4a9; }

  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(120px, 1fr));
    gap: 8px; margin-bottom: 16px;
  }
  .card {
    background: #14141c; border: 1px solid #222;
    border-radius: 10px; padding: 12px; text-align: center;
  }
  .card .label { font-size: 0.7em; color: #555; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .val { font-size: 1.6em; font-weight: 600; font-variant-numeric: tabular-nums; margin: 4px 0; }
  .card .unit { font-size: 0.65em; color: #444; }
  .good { color: #4a9; } .moderate { color: #da3; }
  .unhealthy { color: #d63; } .hazardous { color: #c33; }

  .charts {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 12px;
  }
  @media (max-width: 700px) { .charts { grid-template-columns: 1fr; } }
  .chart-box {
    background: #14141c; border: 1px solid #222;
    border-radius: 10px; padding: 12px;
  }
  .chart-box h3 { font-size: 0.8em; color: #555; margin-bottom: 8px; font-weight: 400; }
  canvas { width: 100% !important; }
</style>
</head>
<body>
<header>
  <h1><span>AirMaster</span> AM7</h1>
  <div class="controls">
    <select id="daySelect"></select>
    <button onclick="loadToday()">Today</button>
    <span id="status">Loading...</span>
  </div>
</header>

<div class="cards">
  <div class="card"><div class="label">PM2.5</div><div class="val" id="v-pm25">--</div><div class="unit">µg/m³</div></div>
  <div class="card"><div class="label">PM10</div><div class="val" id="v-pm10">--</div><div class="unit">µg/m³</div></div>
  <div class="card"><div class="label">CO₂</div><div class="val" id="v-co2">--</div><div class="unit">ppm</div></div>
  <div class="card"><div class="label">HCHO</div><div class="val" id="v-hcho">--</div><div class="unit">mg/m³</div></div>
  <div class="card"><div class="label">TVOC</div><div class="val" id="v-tvoc">--</div><div class="unit">mg/m³</div></div>
  <div class="card"><div class="label">Temp</div><div class="val" id="v-temp">--</div><div class="unit">°C</div></div>
  <div class="card"><div class="label">RH</div><div class="val" id="v-rh">--</div><div class="unit">%</div></div>
</div>

<div class="charts">
  <div class="chart-box"><h3>PM2.5 / PM10 (µg/m³)</h3><canvas id="chart-pm"></canvas></div>
  <div class="chart-box"><h3>CO₂ (ppm)</h3><canvas id="chart-co2"></canvas></div>
  <div class="chart-box"><h3>HCHO / TVOC (mg/m³)</h3><canvas id="chart-voc"></canvas></div>
  <div class="chart-box"><h3>Temperature (°C) / Humidity (%)</h3><canvas id="chart-env"></canvas></div>
</div>

<script>
const chartOpts = (yLabel) => ({
  responsive: true,
  animation: false,
  interaction: { intersect: false, mode: 'index' },
  plugins: { legend: { labels: { color: '#777', boxWidth: 12, font: { size: 11 } } } },
  scales: {
    x: {
      type: 'time',
      time: { tooltipFormat: 'HH:mm:ss', displayFormats: { minute: 'HH:mm', hour: 'HH:mm' } },
      ticks: { color: '#444', maxTicksLimit: 12 },
      grid: { color: '#1a1a24' },
    },
    y: {
      ticks: { color: '#555' },
      grid: { color: '#1a1a24' },
      title: { display: true, text: yLabel, color: '#444', font: { size: 11 } },
    }
  }
})

const ds = (label, color) => ({
  label, borderColor: color, backgroundColor: color + '18',
  borderWidth: 1.5, pointRadius: 0, fill: false, tension: 0.3, data: []
})

const pmChart = new Chart(document.getElementById('chart-pm'), {
  type: 'line', data: { datasets: [ds('PM2.5','#e8733a'), ds('PM10','#a855f7')] }, options: chartOpts('µg/m³')
})
const co2Chart = new Chart(document.getElementById('chart-co2'), {
  type: 'line', data: { datasets: [ds('CO₂','#3b82f6')] }, options: chartOpts('ppm')
})
const vocChart = new Chart(document.getElementById('chart-voc'), {
  type: 'line', data: { datasets: [ds('HCHO','#f59e0b'), ds('TVOC','#10b981')] }, options: chartOpts('mg/m³')
})
const envChart = new Chart(document.getElementById('chart-env'), {
  type: 'line', data: { datasets: [ds('Temp','#ef4444'), ds('RH','#06b6d4')] }, options: chartOpts('°C / %')
})

const allCharts = [pmChart, co2Chart, vocChart, envChart]

function setChartData(readings) {
  const pm25 = [], pm10 = [], co2 = [], hcho = [], tvoc = [], temp = [], rh = []
  readings.forEach(r => {
    const t = new Date(r.t)
    pm25.push({x:t, y:r.pm25}); pm10.push({x:t, y:r.pm10})
    co2.push({x:t, y:r.co2})
    hcho.push({x:t, y:r.hcho}); tvoc.push({x:t, y:r.tvoc})
    temp.push({x:t, y:r.temp}); rh.push({x:t, y:r.rh})
  })
  pmChart.data.datasets[0].data = pm25; pmChart.data.datasets[1].data = pm10
  co2Chart.data.datasets[0].data = co2
  vocChart.data.datasets[0].data = hcho; vocChart.data.datasets[1].data = tvoc
  envChart.data.datasets[0].data = temp; envChart.data.datasets[1].data = rh
  allCharts.forEach(c => c.update())
}

function appendReading(r) {
  const t = new Date(r.t)
  pmChart.data.datasets[0].data.push({x:t, y:r.pm25})
  pmChart.data.datasets[1].data.push({x:t, y:r.pm10})
  co2Chart.data.datasets[0].data.push({x:t, y:r.co2})
  vocChart.data.datasets[0].data.push({x:t, y:r.hcho})
  vocChart.data.datasets[1].data.push({x:t, y:r.tvoc})
  envChart.data.datasets[0].data.push({x:t, y:r.temp})
  envChart.data.datasets[1].data.push({x:t, y:r.rh})
  allCharts.forEach(c => c.update())
}

const thresholds = {
  pm25: [35, 55, 150], pm10: [50, 150, 250], co2: [800, 1000, 2000],
  hcho: [0.08, 0.1, 0.3], tvoc: [0.3, 0.6, 1.5]
}
function colorize(key, val) {
  const t = thresholds[key]
  if (!t) return 'good'
  if (val >= t[2]) return 'hazardous'
  if (val >= t[1]) return 'unhealthy'
  if (val >= t[0]) return 'moderate'
  return 'good'
}

function updateCards(r) {
  const fields = ['pm25','pm10','co2','hcho','tvoc','temp','rh']
  fields.forEach(f => {
    const el = document.getElementById('v-' + f)
    if (el && r[f] !== undefined) {
      el.textContent = r[f]
      el.className = 'val ' + colorize(f, r[f])
    }
  })
}

let currentDay = ''
let lastTimestamp = ''

async function loadDays() {
  const res = await fetch('/api/days')
  const days = await res.json()
  const sel = document.getElementById('daySelect')
  sel.innerHTML = ''
  const today = new Date().toISOString().slice(0,10)
  const allDays = days.includes(today) ? days : [...days, today]
  allDays.forEach(d => {
    const opt = document.createElement('option')
    opt.value = d; opt.textContent = d
    sel.appendChild(opt)
  })
  sel.value = today
  currentDay = today
}

async function loadDay(day) {
  currentDay = day
  const res = await fetch('/api/history?day=' + day)
  const readings = await res.json()
  setChartData(readings)
  if (readings.length > 0) {
    updateCards(readings[readings.length - 1])
    lastTimestamp = readings[readings.length - 1].t
  }
}

async function loadToday() {
  const today = new Date().toISOString().slice(0,10)
  document.getElementById('daySelect').value = today
  await loadDay(today)
}

document.getElementById('daySelect').addEventListener('change', (e) => {
  loadDay(e.target.value)
})

async function pollLatest() {
  const today = new Date().toISOString().slice(0,10)
  try {
    const res = await fetch('/api/latest')
    const r = await res.json()
    if (!r.t) {
      document.getElementById('status').textContent = 'Waiting for data...'
      document.getElementById('status').className = ''
      return
    }
    updateCards(r)
    const ago = Math.round((Date.now() - new Date(r.t).getTime()) / 1000)
    const st = document.getElementById('status')
    st.textContent = ago < 15 ? 'Live' : ago + 's ago'
    st.className = ago < 15 ? 'live' : ''

    if (currentDay === today && r.t !== lastTimestamp) {
      appendReading(r)
      lastTimestamp = r.t
    }
  } catch(e) {}
}

(async () => {
  await loadDays()
  await loadToday()
  setInterval(pollLatest, 3000)
})()
</script>
</body>
</html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        if path == '/api/latest':
            self.json_response(latest_reading)
        elif path == '/api/days':
            self.json_response(available_days())
        elif path == '/api/history':
            day = params.get('day', [date.today().isoformat()])[0]
            self.json_response(load_day(day))
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode())

    def json_response(self, data):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, fmt, *args):
        pass


def main():
    t = threading.Thread(target=udp_listener, daemon=True)
    t.start()

    port = 8080
    server = HTTPServer(('0.0.0.0', port), Handler)
    print(f'Dashboard at http://localhost:{port}')
    print(f'Data stored in ./{DATA_DIR}/')
    print('Ctrl+C to stop.\n')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    main()
