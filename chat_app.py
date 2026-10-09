"""
Music Room — ห้องซ้อมดนตรีออนไลน์ & Live Jamming Studio (Flask Single File)
- ธีมสีฟ้าสดใสโทนสว่าง (Bright Sky & Ocean Blue Light Theme)
- ซ้อมดนตรีสด & แจมเพลงออนไลน์แบบ Ultra-Low Latency (WebRTC Peer-to-Peer)
- รองรับทั้งการต่อ "เครื่องดนตรีจริง" (กีตาร์ เบส คีย์บอร์ด กลองไฟฟ้า ผ่าน Audio Interface / iPhone / iPad)
  และโหมดพูดคุยผ่านไมค์ พร้อมปิด DSP Echo Cancellation/Noise Filter ในโหมดดนตรี เพื่อเสียงที่ใส คมชัด ไม่โดนตัดทอน
- ระบบเลือกอุปกรณ์ Audio Interface / Sound Card / ไมค์ / ลำโพง-หูฟัง โดยตรง
- ระบบ Direct In-Ear Monitor (🎧 ฟังเสียงตัวเองในหูฟัง) ปรับ Gain ความดังได้ 0 - 300%
- เปิดกล้องวิดีโอ (Webcam / มือถือ) และแชร์หน้าจอ / โน้ตเพลง / DAW
- ระบบบันทึกวิดีโอและเสียงสด (Live Studio Recorder) พร้อมดาวน์โหลดไฟล์
- เครื่องเคาะจังหวะ Metronome ซิงค์ห้อง + เครื่องเทียบเสียง Tuner
- แชทสด ส่งคอร์ด แท็บเพลง แนบไฟล์เสียง/ภาพ
"""

import base64
import json
import os
import random
import re
import socket
import sqlite3
import time
from flask import (Flask, request, session, redirect, url_for, jsonify,
                   render_template_string, g, flash, abort)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "music-room-sky-blue-key-2026")
app.config['MAX_CONTENT_LENGTH'] = 64 * 1024 * 1024
DB_PATH = os.environ.get("CHAT_DB", "chat.db")

MAX_NAME = 30
MAX_ROOM_NAME = 50
MAX_MSG = 4000
MAX_PASSWORD = 30
ONLINE_WINDOW = 12


# ───────────────────────────── Network Helpers ─────────────────────────────
def get_local_ips():
    ips = []
    seen = set()
    try:
        host_ips = socket.gethostbyname_ex(socket.gethostname())[2]
        for ip in host_ips:
            if ip not in seen and not ip.startswith("127."):
                seen.add(ip)
                ips.append(ip)
    except Exception:
        pass

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.2)
        s.connect(("8.8.8.8", 80))
        default_ip = s.getsockname()[0]
        s.close()
        if default_ip not in seen and not default_ip.startswith("127."):
            seen.add(default_ip)
            ips.insert(0, default_ip)
    except Exception:
        pass

    categorized = []
    for ip in ips:
        if ip.startswith("100."):
            categorized.append({"ip": ip, "type": "Tailscale VPN", "priority": 1})
        elif ip.startswith("192.168."):
            categorized.append({"ip": ip, "type": "Wi-Fi / LAN", "priority": 2})
        elif ip.startswith("10.") or ip.startswith("172."):
            categorized.append({"ip": ip, "type": "LAN / Network", "priority": 3})
        else:
            categorized.append({"ip": ip, "type": "Network IP", "priority": 4})

    categorized.sort(key=lambda x: x["priority"])
    if not categorized:
        categorized.append({"ip": "127.0.0.1", "type": "Localhost", "priority": 99})

    return categorized


# ───────────────────────────── Database ─────────────────────────────
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS rooms (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            icon TEXT DEFAULT '🎸',
            created_by TEXT NOT NULL,
            password TEXT DEFAULT '',
            is_private INTEGER DEFAULT 0,
            bpm INTEGER DEFAULT 120,
            genre TEXT DEFAULT 'Rock / Pop',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT NOT NULL,
            author TEXT,
            body TEXT NOT NULL,
            msg_type TEXT DEFAULT 'text',
            file_data TEXT,
            avatar TEXT DEFAULT '',
            role TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_msg_room ON messages(room_code, id);
        
        CREATE TABLE IF NOT EXISTS presence (
            room_code TEXT NOT NULL,
            name TEXT NOT NULL,
            avatar TEXT DEFAULT '',
            role TEXT DEFAULT '🎸 Guitar',
            has_audio INTEGER DEFAULT 0,
            has_video INTEGER DEFAULT 0,
            is_instrument_mode INTEGER DEFAULT 1,
            last_seen REAL NOT NULL,
            PRIMARY KEY (room_code, name)
        );
        
        CREATE TABLE IF NOT EXISTS room_moderation (
            room_code TEXT NOT NULL,
            username TEXT NOT NULL,
            muted_until REAL DEFAULT 0,
            is_kicked INTEGER DEFAULT 0,
            PRIMARY KEY (room_code, username)
        );
        
        CREATE TABLE IF NOT EXISTS webrtc_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_code TEXT NOT NULL,
            sender TEXT NOT NULL,
            recipient TEXT NOT NULL,
            type TEXT NOT NULL,
            data TEXT NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_signals ON webrtc_signals(room_code, recipient, id);
    """)

    cur.execute("PRAGMA table_info(rooms)")
    room_cols = [row[1] for row in cur.fetchall()]
    if "bpm" not in room_cols:
        cur.execute("ALTER TABLE rooms ADD COLUMN bpm INTEGER DEFAULT 120")
    if "genre" not in room_cols:
        cur.execute("ALTER TABLE rooms ADD COLUMN genre TEXT DEFAULT 'Rock / Pop'")
    if "password" not in room_cols:
        cur.execute("ALTER TABLE rooms ADD COLUMN password TEXT DEFAULT ''")
    if "is_private" not in room_cols:
        cur.execute("ALTER TABLE rooms ADD COLUMN is_private INTEGER DEFAULT 0")
    if "icon" not in room_cols:
        cur.execute("ALTER TABLE rooms ADD COLUMN icon TEXT DEFAULT '🎸'")

    cur.execute("PRAGMA table_info(presence)")
    pres_cols = [row[1] for row in cur.fetchall()]
    if "role" not in pres_cols:
        cur.execute("ALTER TABLE presence ADD COLUMN role TEXT DEFAULT '🎸 Guitar'")
    if "has_audio" not in pres_cols:
        cur.execute("ALTER TABLE presence ADD COLUMN has_audio INTEGER DEFAULT 0")
    if "has_video" not in pres_cols:
        cur.execute("ALTER TABLE presence ADD COLUMN has_video INTEGER DEFAULT 0")
    if "is_instrument_mode" not in pres_cols:
        cur.execute("ALTER TABLE presence ADD COLUMN is_instrument_mode INTEGER DEFAULT 1")

    cur.execute("PRAGMA table_info(messages)")
    msg_cols = [row[1] for row in cur.fetchall()]
    if "role" not in msg_cols:
        cur.execute("ALTER TABLE messages ADD COLUMN role TEXT DEFAULT ''")

    conn.commit()
    conn.close()


def get_room(code):
    return get_db().execute("SELECT * FROM rooms WHERE code=?", (code,)).fetchone()


def new_room_code():
    db = get_db()
    used = {r["code"] for r in db.execute("SELECT code FROM rooms")}
    free = [f"{n:04d}" for n in range(1000, 10000) if f"{n:04d}" not in used]
    if not free:
        return None
    return random.choice(free)


def remember_room(code):
    recent = session.get("recent", [])
    if code in recent:
        recent.remove(code)
    recent.insert(0, code)
    session["recent"] = recent[:10]


def is_room_unlocked(code, room=None):
    if not room:
        room = get_room(code)
    if not room:
        return False
    if not room["is_private"] or not room["password"]:
        return True
    if session.get("name") and room["created_by"] == session.get("name"):
        return True
    unlocked = session.get("unlocked_rooms", [])
    return code in unlocked


def unlock_room(code):
    unlocked = session.get("unlocked_rooms", [])
    if code not in unlocked:
        unlocked.append(code)
    session["unlocked_rooms"] = unlocked


# ───────────────────────────── HTML Templates (Bright Sky Blue Theme) ─────────────────────────────
BASE = r"""<!doctype html>
<html lang="th">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>{{ title or 'Music Room — ห้องซ้อมดนตรีออนไลน์ & Live Jam Studio' }}</title>
<script src="https://cdn.tailwindcss.com"></script>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Thai:wght@300;400;500;600;700&family=Plus+Jakarta+Sans:wght@500;600;700;800;900&family=JetBrains+Mono:wght@500;700&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js"></script>
<script>
  tailwind.config = {
    theme: {
      extend: {
        fontFamily: {
          sans: ['"IBM Plex Sans Thai"', '"Plus Jakarta Sans"', 'ui-sans-serif', 'system-ui', 'sans-serif'],
          display: ['"Plus Jakarta Sans"', '"IBM Plex Sans Thai"', 'sans-serif'],
          mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace']
        },
        colors: {
          brand: {
            50: '#f0f9ff',
            100: '#e0f2fe',
            200: '#bae6fd',
            300: '#7dd3fc',
            400: '#38bdf8',
            500: '#0ea5e9',
            600: '#0284c7',
            700: '#0369a1',
            800: '#075985',
            900: '#0c4a6e',
          }
        }
      }
    }
  }
</script>
<style>
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-thumb { background: #bae6fd; border-radius: 999px; }
  ::-webkit-scrollbar-thumb:hover { background: #7dd3fc; }
  ::-webkit-scrollbar-track { background: transparent; }

  @keyframes pulseGlow {
    0%, 100% { opacity: 1; filter: drop-shadow(0 0 12px rgba(14, 165, 233, 0.45)); }
    50% { opacity: 0.8; filter: drop-shadow(0 0 4px rgba(14, 165, 233, 0.2)); }
  }
  .pulse-glow { animation: pulseGlow 2.5s infinite ease-in-out; }

  @keyframes recBlink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.25; }
  }
  .rec-blink { animation: recBlink 1.2s infinite ease-in-out; }

  .glass-light {
    background: rgba(255, 255, 255, 0.88);
    backdrop-filter: blur(14px);
    -webkit-backdrop-filter: blur(14px);
    border: 1px solid rgba(186, 230, 253, 0.7);
  }
  .glass-card {
    background: rgba(255, 255, 255, 0.95);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
    border: 1px solid rgba(224, 242, 254, 0.9);
  }
  .glow-sky {
    box-shadow: 0 10px 30px -10px rgba(14, 165, 233, 0.35);
  }
  .vu-bar {
    transition: height 0.05s ease-out, width 0.05s ease-out;
  }
</style>
</head>
<body class="bg-gradient-to-br from-sky-50 via-blue-50/60 to-cyan-50 text-slate-800 font-sans min-h-screen selection:bg-sky-500 selection:text-white antialiased">
{% with msgs = get_flashed_messages(with_categories=true) %}
  {% if msgs %}
  <div id="toast" class="fixed top-5 left-1/2 -translate-x-1/2 z-50 space-y-2 max-w-[90vw] sm:max-w-md">
    {% for cat, m in msgs %}
    <div class="px-5 py-3.5 rounded-2xl shadow-xl text-sm font-semibold flex items-center gap-3 animate-bounce
      {{ 'bg-rose-500 text-white shadow-rose-500/30' if cat=='error' else 'bg-sky-600 text-white shadow-sky-500/30' }}">
      <span class="text-lg">{{ '⚠️' if cat=='error' else '✨' }}</span>
      <span class="flex-1">{{ m }}</span>
    </div>
    {% endfor %}
  </div>
  <script>setTimeout(()=>document.getElementById('toast')?.remove(), 4000)</script>
  {% endif %}
{% endwith %}

%%BODY%%

<!-- Global QR Code Modal -->
<div id="qrModal" class="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm hidden items-center justify-center p-4">
  <div class="bg-white rounded-3xl p-6 sm:p-8 max-w-sm w-full shadow-2xl border border-sky-100 text-center relative">
    <button onclick="closeQrModal()" class="absolute top-4 right-4 h-8 w-8 rounded-full bg-slate-100 hover:bg-slate-200 text-slate-500 flex items-center justify-center transition">&times;</button>
    <div class="h-14 w-14 rounded-2xl bg-sky-100 text-sky-600 flex items-center justify-center mx-auto mb-3 text-2xl border border-sky-200">
      🎸
    </div>
    <h3 class="text-xl font-bold text-slate-800 font-display" id="qrModalTitle">สแกนต่อมือถือ / iPad</h3>
    <p class="text-xs text-slate-500 mt-1 mb-4">เปิดกล้อง iPhone, iPad หรือ Android เพื่อเข้าร่วมห้องซ้อมดนตรีสด</p>
    
    <div class="bg-sky-50 p-4 rounded-2xl inline-block border border-sky-100 mb-4 shadow-inner">
      <div id="qrcodeCanvas" class="flex justify-center"></div>
    </div>

    <div class="space-y-2 text-left">
      <label class="text-xs font-semibold text-slate-500">URL สำหรับเปิดในเบราว์เซอร์:</label>
      <div class="flex items-center gap-1.5 bg-slate-50 p-2 rounded-xl border border-slate-200">
        <input id="qrUrlInput" readonly class="text-xs text-sky-700 font-mono bg-transparent w-full outline-none select-all">
        <button onclick="copyQrUrl()" id="copyQrBtn" class="shrink-0 px-3 py-1 text-xs font-semibold bg-sky-600 hover:bg-sky-700 text-white rounded-lg transition">คัดลอก</button>
      </div>
    </div>
  </div>
</div>

<script>
function showQrModal(url, title) {
  const modal = document.getElementById('qrModal');
  const canvasBox = document.getElementById('qrcodeCanvas');
  const urlInput = document.getElementById('qrUrlInput');
  if(title) document.getElementById('qrModalTitle').textContent = title;
  canvasBox.innerHTML = '';
  urlInput.value = url;
  new QRCode(canvasBox, { text: url, width: 180, height: 180, colorDark : "#0369a1", colorLight : "#ffffff", correctLevel : QRCode.CorrectLevel.M });
  modal.classList.remove('hidden'); modal.classList.add('flex');
}
function closeQrModal() { const m = document.getElementById('qrModal'); m.classList.add('hidden'); m.classList.remove('flex'); }

async function copyQrUrl() {
  const urlInput = document.getElementById('qrUrlInput');
  const btn = document.getElementById('copyQrBtn');
  try {
    await navigator.clipboard.writeText(urlInput.value);
    btn.textContent = 'คัดลอกแล้ว!';
    btn.classList.replace('bg-sky-600', 'bg-emerald-600');
    setTimeout(() => { btn.textContent = 'คัดลอก'; btn.classList.replace('bg-emerald-600', 'bg-sky-600'); }, 1500);
  } catch(e) { urlInput.select(); document.execCommand('copy'); }
}
</script>
</body>
</html>"""


# ───────────────────────────── Login Page ─────────────────────────────
LOGIN = r"""
<div class="min-h-screen flex items-center justify-center p-4 bg-gradient-to-br from-sky-400 via-blue-500 to-cyan-600 relative overflow-hidden">
  <!-- Glowing Sky Orbs -->
  <div class="absolute -top-32 -left-32 w-80 h-80 bg-white/20 rounded-full blur-2xl pointer-events-none"></div>
  <div class="absolute -bottom-32 -right-32 w-80 h-80 bg-cyan-300/25 rounded-full blur-2xl pointer-events-none"></div>

  <div class="w-full max-w-md bg-white/95 backdrop-blur-xl rounded-3xl shadow-2xl p-8 border border-white/40 relative z-10">
    <div class="flex flex-col items-center text-center mb-6">
      <div class="h-20 w-20 rounded-3xl bg-gradient-to-tr from-sky-400 via-blue-500 to-cyan-400 p-0.5 shadow-xl shadow-sky-500/30 mb-4 pulse-glow">
        <div class="w-full h-full bg-white rounded-[22px] flex items-center justify-center text-3xl">
          🎸
        </div>
      </div>
      <h1 class="text-3xl font-extrabold text-slate-800 tracking-tight font-display">Music Room</h1>
      <p class="text-sky-700 font-semibold text-sm mt-1">ห้องซ้อมดนตรีออนไลน์ & Live Jam Studio</p>
      <div class="flex items-center gap-2 mt-2.5 px-3 py-1 bg-sky-50 rounded-full border border-sky-200 text-xs text-sky-700 font-medium">
        <span class="h-2 w-2 rounded-full bg-emerald-500 animate-pulse"></span>
        <span>Ultra-Low Latency · Instrument / Mic Studio</span>
      </div>
    </div>

    <!-- Network Info -->
    <div class="bg-sky-50/80 border border-sky-200 rounded-2xl p-3.5 mb-6 text-xs text-slate-700 space-y-2">
      <div class="flex items-center justify-between font-semibold text-sky-900">
        <span class="flex items-center gap-1.5">
          <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
          ที่อยู่ IP สำหรับเชื่อมต่อห้องซ้อม
        </span>
        <button type="button" onclick="showQrModal('{{ current_url }}', 'สแกนเข้าสู่ห้องซ้อมผ่านมือถือ/iPad')" class="text-sky-600 hover:text-sky-800 font-bold underline flex items-center gap-1">
          📱 QR มือถือ
        </button>
      </div>
      {% for item in net_ips %}
      <div class="flex justify-between items-center font-mono py-1 border-b border-sky-100 last:border-0 text-[11px]">
        <span class="font-medium text-slate-500">[{{ item.type }}]</span>
        <span class="text-sky-800 font-bold">http://{{ item.ip }}:{{ port }}</span>
      </div>
      {% endfor %}
    </div>

    <form method="post" action="{{ url_for('login') }}" class="space-y-4">
      <div>
        <label class="block text-sm font-semibold text-slate-700 mb-1.5">ชื่อของคุณ / ชื่อนักดนตรี</label>
        <div class="relative">
          <input name="name" required maxlength="{{ max_name }}" autofocus placeholder="เช่น เจ กีตาร์, มาร์ค กลอง, บีม ร้องนำ"
            class="w-full rounded-2xl border border-slate-200 bg-slate-50/50 px-4 py-3.5 pl-11 text-slate-800 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-sky-100 focus:border-sky-500 transition font-medium">
          <div class="absolute inset-y-0 left-0 pl-3.5 flex items-center pointer-events-none text-slate-400 text-lg">
            🎵
          </div>
        </div>
      </div>

      <div>
        <label class="block text-sm font-semibold text-slate-700 mb-1.5">ตำแหน่งเครื่องดนตรีหลัก</label>
        <select name="role" class="w-full rounded-2xl border border-slate-200 bg-slate-50/50 px-4 py-3 text-slate-800 focus:bg-white focus:outline-none focus:ring-4 focus:ring-sky-100 focus:border-sky-500 transition font-medium">
          <option value="🎸 Lead Guitar">🎸 Lead Guitar (กีตาร์ลีด)</option>
          <option value="🎸 Rhythm Guitar">🎸 Rhythm Guitar (กีตาร์คอร์ด)</option>
          <option value="🎸 Bass">🎸 Bass (เบส)</option>
          <option value="🥁 Drums">🥁 Drums (กลองชุด/กลองไฟฟ้า)</option>
          <option value="🎹 Keyboards">🎹 Keyboards / Piano (คีย์บอร์ด)</option>
          <option value="🎤 Lead Vocal">🎤 Lead Vocal (นักร้องนำ)</option>
          <option value="🎷 Sax / Brass">🎷 Saxophone / Brass (เครื่องเป่า)</option>
          <option value="🎛️ Audio Mixer">🎛️ Sound Engineer / Mixer (มิกเซอร์)</option>
          <option value="🎧 Listener">🎧 Band Listener (ผู้ฟัง/คนดู)</option>
        </select>
      </div>

      <button class="w-full rounded-2xl bg-gradient-to-r from-sky-500 via-blue-600 to-cyan-600 hover:from-sky-600 hover:to-blue-700 text-white font-bold py-3.5 shadow-lg shadow-sky-500/30 hover:shadow-xl active:scale-[.99] transition duration-150 flex items-center justify-center gap-2 mt-2">
        <span>เข้าสู่สตูดิโอห้องซ้อม</span>
        <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
      </button>
    </form>
  </div>
</div>
"""


# ───────────────────────────── Lobby Page ─────────────────────────────
LOBBY = r"""
<div class="min-h-screen bg-gradient-to-b from-sky-100/70 via-blue-50/40 to-slate-100 text-slate-800">
  <!-- Top Navbar -->
  <header class="bg-white/90 backdrop-blur-md border-b border-sky-100 sticky top-0 z-30 shadow-sm">
    <div class="max-w-6xl mx-auto flex items-center justify-between px-4 py-3">
      <div class="flex items-center gap-3">
        <div class="h-10 w-10 rounded-2xl bg-gradient-to-tr from-sky-400 to-blue-600 flex items-center justify-center shadow-md shadow-sky-500/20 text-white text-xl">
          🎸
        </div>
        <div>
          <span class="text-xl font-bold bg-gradient-to-r from-sky-600 to-blue-700 bg-clip-text text-transparent font-display">Music Room</span>
          <span class="hidden sm:inline-block ml-2 px-2 py-0.5 rounded-md bg-sky-100 text-sky-700 text-[11px] font-bold uppercase tracking-wider">Live Jam Studio</span>
        </div>
      </div>

      <div class="flex items-center gap-2 sm:gap-3">
        <button onclick="showQrModal('{{ current_url }}', 'สแกน QR เข้าระบบด้วยมือถือ/iPad')"
          class="flex items-center gap-1.5 px-3 py-1.5 rounded-xl bg-sky-50 hover:bg-sky-100 text-sky-700 border border-sky-200 text-xs font-semibold transition" title="เปิด QR Code สำหรับมือถือ">
          <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"/></svg>
          <span class="hidden sm:inline">QR มือถือ / iPad</span>
        </button>

        <!-- User Role Pill -->
        <div class="flex items-center gap-2 bg-white rounded-full pl-2 pr-3 py-1 border border-slate-200 shadow-sm">
          <span class="text-base">{{ my_role.split(' ')[0] if my_role else '🎵' }}</span>
          <div class="text-left">
            <div class="text-xs font-bold text-slate-800 leading-none">{{ me }}</div>
            <div class="text-[10px] text-sky-600 leading-none mt-0.5">{{ my_role.split(' ')[1] if my_role and ' ' in my_role else 'Musician' }}</div>
          </div>
        </div>
        
        <a href="{{ url_for('logout') }}" class="p-2 text-slate-400 hover:text-rose-600 transition" title="ออกจากระบบ">
          <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1"/></svg>
        </a>
      </div>
    </div>
  </header>

  <main class="max-w-6xl mx-auto px-4 py-6 sm:py-8 space-y-8">
    <!-- Hero Banner -->
    <div class="relative overflow-hidden rounded-3xl bg-gradient-to-r from-sky-600 via-blue-600 to-cyan-600 p-6 sm:p-8 text-white shadow-xl glow-sky">
      <div class="relative z-10 flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div>
          <div class="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-white/20 backdrop-blur-md text-xs font-semibold text-sky-100 mb-3">
            <span class="h-2 w-2 rounded-full bg-emerald-400 animate-ping"></span>
            Real-time WebRTC Audio Engine พร้อมใช้งาน
          </div>
          <h2 class="text-2xl sm:text-3xl font-extrabold tracking-tight font-display">สตูดิโอห้องซ้อมดนตรีออนไลน์ 🎸</h2>
          <p class="text-sky-100 text-sm mt-1 max-w-xl">
            เสียบเครื่องดนตรีจริง (Audio Interface / iPhone / iPad) หรือใช้ไมค์ เล่นสดพร้อมกันแบบดีเลย์ต่ำสุด เปิดกล้อง และอัดเสียง/วิดีโอได้ทันที
          </p>
        </div>

        <div class="bg-white/15 backdrop-blur-md border border-white/20 rounded-2xl p-4 sm:p-5 shrink-0 space-y-2 text-xs">
          <div class="font-bold flex items-center justify-between text-sky-100">
            <span>🌐 ที่อยู่เซิร์ฟเวอร์สำหรับต่อพ่วง</span>
          </div>
          <div class="space-y-1 font-mono text-[11px]">
            {% for item in net_ips %}
            <div class="flex items-center justify-between gap-3 bg-black/20 px-3 py-1 rounded-lg">
              <span class="text-sky-200">[{{ item.type }}]</span>
              <span class="font-bold text-white">http://{{ item.ip }}:{{ port }}</span>
            </div>
            {% endfor %}
          </div>
        </div>
      </div>
    </div>

    <!-- Actions Grid: Join vs Create -->
    <div class="grid md:grid-cols-2 gap-6">
      <!-- Join Box -->
      <div class="glass-card rounded-3xl p-6 sm:p-7 shadow-sm border border-sky-100 flex flex-col justify-between hover:shadow-md transition">
        <div>
          <div class="flex items-center gap-3 mb-4">
            <div class="h-12 w-12 rounded-2xl bg-sky-100 text-sky-600 flex items-center justify-center text-2xl border border-sky-200">
              🚪
            </div>
            <div>
              <h3 class="font-bold text-lg text-slate-800">เข้าห้องซ้อมด้วยรหัส 4 หลัก</h3>
              <p class="text-xs text-slate-500">กรอกรหัสตัวเลข 4 หลักที่เพื่อนร่วมวงส่งให้</p>
            </div>
          </div>

          <form id="joinForm" method="post" action="{{ url_for('join') }}" class="mt-4">
            <div class="flex gap-2">
              <input type="text" name="code" id="joinCodeInput" maxlength="4" pattern="\d{4}" inputmode="numeric" required placeholder="เช่น 1024"
                class="w-full bg-slate-50 border border-slate-200 rounded-2xl px-4 py-3.5 text-center text-2xl font-mono tracking-widest font-extrabold text-sky-700 focus:bg-white focus:outline-none focus:ring-4 focus:ring-sky-100 focus:border-sky-500 transition">
              <button type="submit" class="px-6 rounded-2xl bg-gradient-to-r from-sky-600 to-blue-600 hover:from-sky-700 hover:to-blue-700 text-white font-bold transition flex items-center gap-2 shrink-0 shadow-lg shadow-sky-500/25">
                <span>เข้าห้อง</span>
                <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
              </button>
            </div>
          </form>
        </div>

        <div class="mt-4 pt-4 border-t border-slate-100 flex items-center justify-between text-xs text-slate-500">
          <span>รองรับการเข้าพร้อมกันหลายคน</span>
          <span class="text-sky-600 font-semibold">WebRTC Direct P2P</span>
        </div>
      </div>

      <!-- Create Room Box -->
      <div class="glass-card rounded-3xl p-6 sm:p-7 shadow-sm border border-sky-100 flex flex-col justify-between hover:shadow-md transition">
        <div>
          <div class="flex items-center gap-3 mb-4">
            <div class="h-12 w-12 rounded-2xl bg-blue-100 text-blue-600 flex items-center justify-center text-2xl border border-blue-200">
              🎙️
            </div>
            <div>
              <h3 class="font-bold text-lg text-slate-800">สร้างห้องซ้อมดนตรีใหม่</h3>
              <p class="text-xs text-slate-500">กำหนดชื่อวง จังหวะ BPM และสไตล์เพลง</p>
            </div>
          </div>

          <form method="post" action="{{ url_for('create') }}" class="space-y-3">
            <div>
              <input type="text" name="room_name" maxlength="{{ max_room }}" placeholder="ชื่อห้องซ้อม เช่น ซ้อมวง The Echoes, Jam Night" required
                class="w-full bg-slate-50 border border-slate-200 rounded-2xl px-4 py-3 text-slate-800 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-sky-100 focus:border-sky-500 transition text-sm">
            </div>

            <div class="grid grid-cols-2 gap-3">
              <div>
                <label class="block text-[11px] text-slate-500 font-semibold mb-1">ไอคอนห้อง</label>
                <select name="room_icon" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-800 text-xs focus:bg-white focus:outline-none focus:ring-2 focus:ring-sky-200">
                  <option value="🎸">🎸 กีตาร์ (Guitar)</option>
                  <option value="🥁">🥁 กลอง (Drums)</option>
                  <option value="🎹">🎹 คีย์บอร์ด (Keys)</option>
                  <option value="🎤">🎤 ไมค์ร้อง (Vocal)</option>
                  <option value="🎷">🎷 แซกโซโฟน (Sax)</option>
                  <option value="🎧">🎧 สตูดิโอแจม (Jam)</option>
                  <option value="🔥">🔥 ร็อคสเตจ (Rock Stage)</option>
                </select>
              </div>

              <div>
                <label class="block text-[11px] text-slate-500 font-semibold mb-1">จังหวะเริ่มต้น (BPM)</label>
                <input type="number" name="bpm" value="120" min="40" max="240" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-sky-700 font-mono text-xs font-bold focus:bg-white focus:outline-none focus:ring-2 focus:ring-sky-200">
              </div>
            </div>

            <div class="bg-sky-50/70 p-3 rounded-2xl border border-sky-100 space-y-2">
              <label class="flex items-center gap-2 cursor-pointer text-xs text-slate-700 font-medium">
                <input type="checkbox" id="privateCheck" name="is_private" value="1" onchange="togglePassInput()" class="rounded border-slate-300 text-sky-600 focus:ring-sky-500">
                <span>ล็อกห้องด้วยรหัสผ่าน (Private Rehearsal)</span>
              </label>
              <div id="passBox" class="hidden">
                <input type="password" name="password" maxlength="{{ max_pass }}" placeholder="กำหนดรหัสผ่านห้อง" class="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sky-200">
              </div>
            </div>

            <button type="submit" class="w-full rounded-2xl bg-gradient-to-r from-sky-500 via-blue-600 to-cyan-600 hover:from-sky-600 hover:to-blue-700 text-white font-bold py-3 shadow-lg shadow-sky-500/25 transition">
              ➕ เปิดห้องซ้อมดนตรี
            </button>
          </form>
        </div>
      </div>
    </div>

    <!-- Active Rehearsal Rooms List -->
    <div class="space-y-4">
      <div class="flex items-center justify-between">
        <h3 class="text-xl font-bold text-slate-800 flex items-center gap-2 font-display">
          <span>ห้องซ้อมที่เปิดอยู่ขณะนี้</span>
          <span class="px-2.5 py-0.5 rounded-full bg-sky-100 text-sky-700 text-xs font-mono font-bold">{{ all_rooms|length }}</span>
        </h3>
      </div>

      {% if not all_rooms %}
      <div class="glass-card rounded-3xl p-8 text-center border border-sky-100">
        <div class="text-4xl mb-2">🎶</div>
        <div class="text-slate-700 font-semibold">ยังไม่มีห้องซ้อมที่เปิดอยู่ในขณะนี้</div>
        <p class="text-xs text-slate-500 mt-1">กดปุ่ม "เปิดห้องซ้อมดนตรีใหม่" ด้านบนเพื่อเริ่มแจมเพลงกับเพื่อนได้เลย!</p>
      </div>
      {% else %}
      <div class="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {% for r in all_rooms %}
        <div class="glass-card rounded-2xl p-5 border border-sky-100 hover:border-sky-300 hover:shadow-md transition flex flex-col justify-between group">
          <div>
            <div class="flex items-start justify-between gap-2 mb-2">
              <div class="flex items-center gap-2.5">
                <div class="h-10 w-10 rounded-xl bg-sky-50 flex items-center justify-center text-xl border border-sky-200">
                  {{ r.icon or '🎸' }}
                </div>
                <div>
                  <h4 class="font-bold text-slate-800 group-hover:text-sky-600 transition text-sm sm:text-base leading-snug">{{ r.name }}</h4>
                  <div class="text-[11px] text-slate-500">โดย: {{ r.created_by }}</div>
                </div>
              </div>

              {% if r.is_private %}
              <span class="px-2 py-0.5 rounded-md bg-amber-50 text-amber-700 border border-amber-200 text-[10px] font-bold shrink-0">🔒 รหัสผ่าน</span>
              {% else %}
              <span class="px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 border border-emerald-200 text-[10px] font-bold shrink-0">🌐 สาธารณะ</span>
              {% endif %}
            </div>

            <div class="flex items-center gap-3 text-xs text-slate-600 my-3 bg-sky-50/60 p-2.5 rounded-xl border border-sky-100">
              <div class="font-mono text-sky-700 font-bold">#{{ r.code }}</div>
              <div>•</div>
              <div>BPM: <span class="text-blue-700 font-mono font-bold">{{ r.bpm or 120 }}</span></div>
              <div>•</div>
              <div class="flex items-center gap-1">
                <span class="h-2 w-2 rounded-full {{ 'bg-emerald-500 animate-pulse' if r.online > 0 else 'bg-slate-400' }}"></span>
                <span>{{ r.online }} คนในห้อง</span>
              </div>
            </div>
          </div>

          <a href="{{ url_for('room', code=r.code) }}" class="w-full py-2.5 text-center text-xs font-bold rounded-xl bg-sky-600 hover:bg-sky-700 text-white shadow-md shadow-sky-500/20 transition flex items-center justify-center gap-1.5">
            <span>เข้าร่วมห้องซ้อมนี้</span>
            <svg class="h-4 w-4" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M9 5l7 7-7 7"/></svg>
          </a>
        </div>
        {% endfor %}
      </div>
      {% endif %}
    </div>
  </main>
</div>

<script>
function togglePassInput() {
  const chk = document.getElementById('privateCheck');
  const box = document.getElementById('passBox');
  box.classList.toggle('hidden', !chk.checked);
}
</script>
"""


# ───────────────────────────── Password Prompt ─────────────────────────────
PASSWORD_PROMPT = r"""
<div class="min-h-screen flex items-center justify-center p-4 bg-gradient-to-br from-sky-400 via-blue-500 to-cyan-600">
  <div class="w-full max-w-sm bg-white/95 backdrop-blur-xl rounded-3xl p-8 border border-white/40 text-center shadow-2xl">
    <div class="h-16 w-16 rounded-2xl bg-amber-100 text-amber-600 flex items-center justify-center mx-auto mb-4 text-3xl border border-amber-200">
      🔒
    </div>
    <h2 class="text-xl font-bold text-slate-800 mb-1 font-display">ห้องนี้ต้องใช้รหัสผ่าน</h2>
    <p class="text-xs text-slate-500 mb-6 font-mono">ห้อง: “{{ room.name }}” (#{{ room.code }})</p>

    <form method="post" action="{{ url_for('verify_room_password', code=room.code) }}" class="space-y-4">
      <input type="password" name="password" required autofocus placeholder="กรอกรหัสผ่านเพื่อเข้าห้อง"
        class="w-full bg-slate-50 border border-slate-200 rounded-2xl px-4 py-3.5 text-center text-slate-800 placeholder-slate-400 focus:bg-white focus:outline-none focus:ring-4 focus:ring-sky-100 focus:border-sky-500 transition">
      <button type="submit" class="w-full py-3.5 bg-gradient-to-r from-sky-500 to-blue-600 hover:from-sky-600 hover:to-blue-700 text-white font-bold rounded-2xl shadow-lg shadow-sky-500/30 transition">
        ปลดล็อกเข้าห้องซ้อม
      </button>
      <a href="{{ url_for('index') }}" class="block text-xs text-slate-500 hover:text-slate-800 mt-2">กลับสู่หน้าล็อบบี้</a>
    </form>
  </div>
</div>
"""


# ───────────────────────────── Main Studio Rehearsal Room (Bright Sky Blue) ─────────────────────────────
ROOM = r"""
<div class="min-h-screen bg-slate-100 text-slate-800 flex flex-col h-screen overflow-hidden" onclick="unlockAudioContext()">
  <!-- Top Control Bar / Studio Header -->
  <header class="bg-white/95 backdrop-blur-md border-b border-sky-100 px-3 py-2 shrink-0 z-30 flex items-center justify-between gap-2 shadow-sm">
    <!-- Left: Room info & back -->
    <div class="flex items-center gap-2 sm:gap-3 min-w-0">
      <a href="{{ url_for('index') }}" class="p-1.5 rounded-xl bg-sky-50 hover:bg-sky-100 text-sky-700 border border-sky-200 transition" title="กลับล็อบบี้">
        <svg class="h-5 w-5" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" d="M15 19l-7-7 7-7"/></svg>
      </a>

      <div class="flex items-center gap-2 min-w-0">
        <div class="h-9 w-9 rounded-xl bg-gradient-to-tr from-sky-400 to-blue-600 flex items-center justify-center text-lg text-white shadow-sm shrink-0">
          {{ room.icon or '🎸' }}
        </div>
        <div class="min-w-0">
          <div class="flex items-center gap-2">
            <h1 class="text-sm sm:text-base font-bold text-slate-800 truncate font-display">{{ room.name }}</h1>
            <span class="px-2 py-0.5 rounded-md bg-sky-50 text-sky-700 font-mono text-xs font-bold border border-sky-200">#{{ room.code }}</span>
          </div>
          <div class="text-[10px] text-slate-500 flex items-center gap-1.5">
            <span class="h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse"></span>
            <span>สด P2P Mesh</span>
            <span>•</span>
            <span id="memberCountText">1 สมาชิก</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Center: Master Metronome & Recording Status Bar -->
    <div class="hidden md:flex items-center gap-3 bg-sky-50/80 px-4 py-1.5 rounded-2xl border border-sky-200">
      <!-- Metronome Control -->
      <div class="flex items-center gap-2">
        <button id="metronomeBtn" onclick="toggleMetronome()" class="p-1.5 rounded-lg bg-white hover:bg-sky-100 text-slate-700 border border-slate-200 text-xs font-bold flex items-center gap-1.5 transition">
          <span id="metronomeIcon">⏱️</span>
          <span>BPM</span>
        </button>
        <div class="flex items-center gap-1">
          <input type="number" id="bpmInput" value="{{ room.bpm or 120 }}" min="40" max="240" onchange="updateBpm(this.value)"
            class="w-14 bg-white border border-slate-200 rounded-lg px-2 py-1 text-xs font-mono text-center font-bold text-sky-700 focus:outline-none focus:ring-2 focus:ring-sky-300">
          <div id="metroIndicator" class="h-3 w-3 rounded-full bg-slate-300 transition"></div>
        </div>
      </div>

      <div class="h-4 w-px bg-sky-200"></div>

      <!-- Live Studio Recorder -->
      <div class="flex items-center gap-2">
        <button id="recordBtn" onclick="toggleRecording()" class="px-3 py-1 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 text-xs font-bold flex items-center gap-1.5 transition">
          <span class="h-2 w-2 rounded-full bg-rose-500" id="recDot"></span>
          <span id="recText">บันทึกวิดีโอ & เสียง</span>
        </button>
        <span id="recTimer" class="font-mono text-xs text-rose-600 hidden font-bold">00:00</span>
      </div>
    </div>

    <!-- Right: Audio Setup & Mode Selector -->
    <div class="flex items-center gap-1.5 sm:gap-2 shrink-0">
      <!-- Audio Hardware Setup Button -->
      <button onclick="openAudioSettingsModal()" class="px-2.5 py-1.5 rounded-xl bg-white hover:bg-sky-50 text-sky-800 border border-sky-200 text-xs font-bold flex items-center gap-1.5 transition shadow-sm" title="ตั้งค่า Sound Card / ไมค์ / ลำโพง / หูฟัง">
        <span>⚙️</span>
        <span class="hidden sm:inline">ตั้งค่า Sound Card</span>
      </button>

      <!-- Direct In-Ear Monitor Toggle (Hear Myself) -->
      <button id="monitorBtn" onclick="toggleSelfMonitor()" class="px-2.5 py-1.5 rounded-xl bg-slate-100 hover:bg-sky-100 text-slate-700 border border-slate-200 text-xs font-bold flex items-center gap-1.5 transition shadow-sm" title="ฟังเสียงตัวเองในหูฟังสดๆ Direct Monitoring">
        <span id="monitorIcon">🎧</span>
        <span class="hidden lg:inline" id="monitorText">ฟังเสียงตัวเอง (OFF)</span>
      </button>

      <!-- Instrument / Mic Audio Mode Switch -->
      <button id="audioModeBtn" onclick="toggleAudioMode()" class="px-2.5 py-1.5 rounded-xl bg-sky-100 hover:bg-sky-200 text-sky-800 border border-sky-300 text-xs font-bold flex items-center gap-1.5 transition shadow-sm" title="คลิกเพื่อสลับระหว่างโหมดเครื่องดนตรีจริง (ปิด Echo Filter เสียงใสเต็มย่าน) หรือโหมดไมค์พูดคุย">
        <span id="audioModeIcon">🎸</span>
        <span class="hidden lg:inline" id="audioModeText">เครื่องดนตรีจริง (Hi-Fi)</span>
      </button>

      <button onclick="showQrModal('{{ room_url }}', 'สแกน QR เข้าร่วมห้องซ้อมนี้')" class="p-2 rounded-xl bg-sky-50 hover:bg-sky-100 text-sky-700 border border-sky-200 text-xs transition" title="QR Code เข้าห้องนี้">
        📱
      </button>

      {% if is_admin %}
      <button onclick="confirmDeleteRoom()" class="p-2 rounded-xl bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 text-xs transition" title="ลบห้องนี้ (เฉพาะ Admin)">
        🗑️
      </button>
      {% endif %}
    </div>
  </header>

  <!-- Autoplay Audio Unlock Banner if suspended -->
  <div id="audioUnlockBanner" class="bg-gradient-to-r from-sky-600 to-blue-600 text-white px-4 py-2 text-xs font-bold flex items-center justify-between hidden shadow-md">
    <div class="flex items-center gap-2">
      <span>🔊</span>
      <span>เบราว์เซอร์หยุดระบบเสียงอัตโนมัติ กรุณากดปุ่มเพื่อเปิดใช้งานเสียงเต็มรูปแบบ</span>
    </div>
    <button onclick="unlockAudioContext(true)" class="px-3 py-1 bg-white text-sky-700 rounded-lg font-bold hover:bg-sky-50 shadow-sm transition">
      เปิดเสียงเดี๋ยวนี้
    </button>
  </div>

  <!-- Mobile Metronome & Record bar -->
  <div class="md:hidden flex items-center justify-between px-3 py-1.5 bg-sky-50 border-b border-sky-200 text-xs">
    <div class="flex items-center gap-2">
      <button onclick="toggleMetronome()" class="px-2 py-1 rounded bg-white text-slate-700 border border-slate-200 flex items-center gap-1 font-bold">
        <span>⏱️</span> <span id="mBpmVal">120</span>
      </button>
      <div id="mMetroIndicator" class="h-2.5 w-2.5 rounded-full bg-slate-300"></div>
    </div>
    <div class="flex items-center gap-2">
      <button onclick="toggleRecording()" class="px-2 py-1 rounded bg-rose-50 text-rose-600 border border-rose-200 flex items-center gap-1 font-bold">
        <span class="h-2 w-2 rounded-full bg-rose-500"></span>
        <span id="mRecText">อัดสด</span>
      </button>
      <span id="mRecTimer" class="font-mono text-rose-600 hidden font-bold text-[11px]">00:00</span>
    </div>
  </div>

  <!-- Main Studio Workspace (Video/Audio Stage + Side Chat/Tools) -->
  <div class="flex-1 flex overflow-hidden">
    <!-- Left Stage: Band Video & Audio Mesh Grid -->
    <div class="flex-1 flex flex-col min-w-0 bg-slate-100/80 relative overflow-y-auto p-3 sm:p-4 space-y-3">
      <!-- Stage Grid Container -->
      <div id="stageGrid" class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3 sm:gap-4 flex-1 items-stretch auto-rows-fr min-h-[320px]">
        
        <!-- Local User Tile (My Stream) -->
        <div class="glass-card rounded-2xl p-3 border border-sky-200 relative flex flex-col justify-between overflow-hidden shadow-md group">
          <!-- Video element or Avatar stage -->
          <div class="relative w-full flex-1 min-h-[160px] bg-gradient-to-br from-sky-900 to-slate-900 rounded-xl overflow-hidden flex items-center justify-center">
            <video id="localVideo" autoplay playsinline muted class="w-full h-full object-cover hidden"></video>
            
            <!-- Default Instrument Avatar Display -->
            <div id="localAvatarBox" class="flex flex-col items-center justify-center p-4 text-center">
              <div class="h-20 w-20 rounded-2xl bg-gradient-to-tr from-sky-400 to-blue-600 p-0.5 mb-2 shadow-lg">
                <div class="w-full h-full bg-white rounded-[14px] flex items-center justify-center text-3xl">
                  {{ my_role.split(' ')[0] if my_role else '🎸' }}
                </div>
              </div>
              <div class="text-sm font-bold text-white">{{ me }} (คุณ)</div>
              <div class="text-xs text-sky-200 font-semibold">{{ my_role or '🎸 Lead Guitar' }}</div>
            </div>

            <!-- Audio Waveform / VU Overlay on Video -->
            <div class="absolute bottom-2 left-2 right-2 flex items-center justify-between bg-black/60 backdrop-blur-md px-2.5 py-1.5 rounded-lg border border-white/15 text-xs text-white">
              <div class="flex items-center gap-1.5">
                <span id="localMicIcon">🎤</span>
                <div class="w-20 sm:w-28 bg-slate-800 h-2 rounded-full overflow-hidden">
                  <div id="localVuBar" class="bg-gradient-to-r from-emerald-400 via-amber-400 to-rose-500 h-full w-0 vu-bar"></div>
                </div>
              </div>
              <span id="localAudioStatusBadge" class="text-[10px] text-emerald-400 font-mono font-bold">LIVE HI-FI</span>
            </div>
          </div>

          <!-- My Tile Bottom Status -->
          <div class="mt-2 flex items-center justify-between text-xs pt-1 border-t border-slate-100">
            <div class="flex items-center gap-1.5 font-bold text-slate-800 text-xs truncate">
              <span>{{ me }}</span>
              <span class="text-slate-500 font-normal">({{ my_role.split(' ')[1] if my_role and ' ' in my_role else 'Musician' }})</span>
            </div>
            <div class="flex items-center gap-1">
              <span id="myCamStatus" class="p-1 rounded bg-slate-100 text-slate-500 text-[10px]">📷 ปิด</span>
              <span id="myMicStatus" class="p-1 rounded bg-emerald-100 text-emerald-700 text-[10px] font-bold">🎙️ ส่งเสียง</span>
            </div>
          </div>
        </div>

        <!-- Remote Peer Tiles will be dynamically injected here -->
      </div>

      <!-- Studio Stage Bottom Audio & Video Hardware Controls -->
      <div class="bg-white/95 backdrop-blur-md rounded-2xl p-3 border border-sky-100 shadow-sm flex flex-wrap items-center justify-between gap-3 shrink-0">
        <!-- Mic & Instrument Input Selection -->
        <div class="flex items-center gap-2 flex-wrap">
          <!-- Mic / Instrument On/Off Toggle -->
          <button id="toggleMicBtn" onclick="toggleAudioTrack()" class="px-3 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white font-bold text-xs flex items-center gap-1.5 transition shadow-sm">
            <span id="micBtnIcon">🎙️</span>
            <span id="micBtnText">ส่งเสียงสด (ON)</span>
          </button>

          <!-- Camera On/Off Toggle -->
          <button id="toggleCamBtn" onclick="toggleVideoTrack()" class="px-3 py-2 rounded-xl bg-slate-100 hover:bg-slate-200 text-slate-700 border border-slate-200 font-bold text-xs flex items-center gap-1.5 transition">
            <span id="camBtnIcon">📷</span>
            <span id="camBtnText">เปิดกล้อง</span>
          </button>

          <!-- Screen / Sheet Music Share -->
          <button id="shareScreenBtn" onclick="toggleScreenShare()" class="px-3 py-2 rounded-xl bg-sky-50 hover:bg-sky-100 text-sky-700 border border-sky-200 font-bold text-xs flex items-center gap-1.5 transition">
            <span>📑</span>
            <span id="shareScreenText">แชร์โน้ต/จอ</span>
          </button>

          <!-- Audio Settings Modal Trigger -->
          <button onclick="openAudioSettingsModal()" class="px-3 py-2 rounded-xl bg-sky-50 hover:bg-sky-100 text-sky-700 border border-sky-200 font-bold text-xs flex items-center gap-1.5 transition">
            <span>🎛️</span>
            <span>อุปกรณ์เสียง & Gain</span>
          </button>

          <!-- Instrument Tuner Quick Modal -->
          <button onclick="toggleTunerModal()" class="px-3 py-2 rounded-xl bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200 font-bold text-xs flex items-center gap-1.5 transition">
            <span>🎯</span>
            <span>เทียบเสียง Tuner</span>
          </button>
        </div>

        <!-- Latency & Buffer Diagnostic Badge -->
        <div class="flex items-center gap-3 text-xs">
          <div class="flex items-center gap-1.5 font-mono text-slate-600 bg-sky-50 px-2.5 py-1.5 rounded-lg border border-sky-200">
            <span class="h-2 w-2 rounded-full bg-emerald-500"></span>
            <span>Latency: <strong class="text-sky-700" id="pingMs">~15-30ms</strong></span>
          </div>
        </div>
      </div>
    </div>

    <!-- Right Sidebar: Tabs for Chat / Sheet Music / Band Members -->
    <div class="w-80 lg:w-96 bg-white border-l border-sky-100 flex flex-col shrink-0 hidden md:flex shadow-sm">
      <!-- Sidebar Navigation Header -->
      <div class="p-2 border-b border-sky-100 flex items-center justify-between gap-1 bg-sky-50/50">
        <button onclick="switchSidebarTab('chat')" id="tabChatBtn" class="flex-1 py-1.5 rounded-lg text-xs font-bold transition bg-sky-600 text-white shadow-sm">
          💬 แชท & คอร์ด
        </button>
        <button onclick="switchSidebarTab('members')" id="tabMembersBtn" class="flex-1 py-1.5 rounded-lg text-xs font-bold transition text-slate-600 hover:text-sky-700">
          👥 สมาชิก (<span id="memberCountBadge">1</span>)
        </button>
        <button onclick="switchSidebarTab('tools')" id="tabToolsBtn" class="flex-1 py-1.5 rounded-lg text-xs font-bold transition text-slate-600 hover:text-sky-700">
          🎛️ เครื่องมือ
        </button>
      </div>

      <!-- Tab 1: Chat & Chords Tab -->
      <div id="tabChat" class="flex-1 flex flex-col min-h-0">
        <!-- Messages Area -->
        <div id="messagesBox" class="flex-1 p-3 overflow-y-auto space-y-3 font-sans text-xs">
          <div class="p-3 rounded-xl bg-sky-50 border border-sky-100 text-sky-800 text-center">
            🎸 ยินดีต้อนรับสู่ห้องซ้อมดนตรีสด พิมพ์แชท ส่งคอร์ดเพลง หรือแชร์ไฟล์ที่นี่
          </div>
        </div>

        <!-- Chat Input Form -->
        <div class="p-3 border-t border-sky-100 bg-sky-50/40 space-y-2">
          <!-- Quick Chord Helper Bar -->
          <div class="flex items-center gap-1 overflow-x-auto pb-1 scroll-thin text-[11px]">
            <span class="text-slate-400 text-[10px] shrink-0">คอร์ดลัด:</span>
            <button onclick="insertChord('[C] ')" class="px-2 py-0.5 bg-white border border-sky-200 hover:bg-sky-100 text-sky-700 rounded font-mono font-bold">C</button>
            <button onclick="insertChord('[Dm] ')" class="px-2 py-0.5 bg-white border border-sky-200 hover:bg-sky-100 text-sky-700 rounded font-mono font-bold">Dm</button>
            <button onclick="insertChord('[Em] ')" class="px-2 py-0.5 bg-white border border-sky-200 hover:bg-sky-100 text-sky-700 rounded font-mono font-bold">Em</button>
            <button onclick="insertChord('[F] ')" class="px-2 py-0.5 bg-white border border-sky-200 hover:bg-sky-100 text-sky-700 rounded font-mono font-bold">F</button>
            <button onclick="insertChord('[G] ')" class="px-2 py-0.5 bg-white border border-sky-200 hover:bg-sky-100 text-sky-700 rounded font-mono font-bold">G</button>
            <button onclick="insertChord('[Am] ')" class="px-2 py-0.5 bg-white border border-sky-200 hover:bg-sky-100 text-sky-700 rounded font-mono font-bold">Am</button>
          </div>

          <form id="chatForm" onsubmit="sendChatMessage(event)" class="flex gap-2">
            <input type="text" id="chatInput" placeholder="พิมพ์ข้อความ / คอร์ดเพลง..." maxlength="{{ max_msg }}" autocomplete="off"
              class="flex-1 bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 placeholder-slate-400 focus:outline-none focus:ring-2 focus:ring-sky-300">
            
            <label class="p-2 rounded-xl bg-white border border-slate-200 hover:bg-slate-100 text-slate-600 cursor-pointer flex items-center justify-center transition" title="แนบไฟล์เสียง / แท็บเพลง / ภาพ">
              <input type="file" id="chatFileInput" onchange="handleChatFileUpload(this)" class="hidden" accept="image/*,audio/*,.pdf,.txt,.gp,.gp5">
              📎
            </label>

            <button type="submit" class="px-3 py-2 bg-sky-600 hover:bg-sky-700 text-white rounded-xl text-xs font-bold transition flex items-center shadow-sm">
              ส่ง
            </button>
          </form>
        </div>
      </div>

      <!-- Tab 2: Band Members Tab -->
      <div id="tabMembers" class="flex-1 p-3 overflow-y-auto space-y-2 hidden">
        <h4 class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-2">สมาชิกในห้องซ้อม</h4>
        <div id="membersList" class="space-y-2"></div>
      </div>

      <!-- Tab 3: Studio Tools Tab -->
      <div id="tabTools" class="flex-1 p-4 overflow-y-auto space-y-4 hidden text-xs">
        <div class="glass-card rounded-2xl p-3 border border-sky-100 space-y-2 shadow-sm">
          <h4 class="font-bold text-slate-800 flex items-center gap-1.5">
            <span>🎧</span> <span>ฟังเสียงตัวเอง (Direct Monitoring)</span>
          </h4>
          <p class="text-[11px] text-slate-500 leading-relaxed">
            เปิดเพื่อให้เสียงจากไมค์ / Audio Interface ดังเข้าหูฟังของคุณทันที (แนะนำให้ใส่หูฟัง)
          </p>
          <div class="flex items-center gap-2 pt-1">
            <button onclick="toggleSelfMonitor()" id="toolMonitorBtn" class="px-3 py-1.5 bg-sky-600 text-white font-bold rounded-lg transition text-xs">
              เปิดฟังเสียงตัวเอง
            </button>
            <input type="range" id="monitorVolRange" min="0" max="1.5" step="0.05" value="0.8" oninput="setMonitorVolume(this.value)" class="flex-1 accent-sky-600" title="ระดับเสียงหูฟัง">
          </div>
        </div>

        <div class="glass-card rounded-2xl p-3 border border-sky-100 space-y-2 shadow-sm">
          <h4 class="font-bold text-slate-800 flex items-center gap-1.5">
            <span>⏱️</span> <span>เครื่องเคาะจังหวะ Metronome</span>
          </h4>
          <div class="flex items-center justify-between">
            <span class="text-slate-500">Tempo:</span>
            <span class="font-mono text-sky-700 font-bold text-sm" id="toolBpmDisplay">120 BPM</span>
          </div>
          <input type="range" min="40" max="240" value="{{ room.bpm or 120 }}" oninput="updateBpm(this.value)" class="w-full accent-sky-600">
          <div class="flex gap-2">
            <button onclick="toggleMetronome()" class="flex-1 py-1.5 bg-sky-600 hover:bg-sky-700 text-white font-bold rounded-lg transition text-xs shadow-sm">
              เปิด / ปิด Metronome
            </button>
            <button onclick="tapTempo()" class="px-3 py-1.5 bg-white border border-slate-200 hover:bg-slate-50 text-sky-700 font-bold rounded-lg transition text-xs">
              Tap Tempo
            </button>
          </div>
        </div>

        <div class="glass-card rounded-2xl p-3 border border-sky-100 space-y-2 shadow-sm">
          <h4 class="font-bold text-slate-800 flex items-center gap-1.5">
            <span>🔊</span> <span>ทดสอบเสียงลำโพง / หูฟัง</span>
          </h4>
          <p class="text-[11px] text-slate-500">กดเพื่อทดสอบว่าหูฟังหรือลำโพงของคุณทำงานได้ยินเสียงหรือไม่</p>
          <button onclick="playTestChime()" class="w-full py-2 bg-emerald-600 hover:bg-emerald-700 text-white font-bold rounded-xl transition shadow-sm">
            🔔 เล่นเสียงทดสอบ (Test Sound Output)
          </button>
        </div>
      </div>
    </div>
  </div>

  <!-- Mobile Bottom Tab Bar -->
  <div class="md:hidden bg-white border-t border-sky-100 px-4 py-2 flex items-center justify-around text-xs shrink-0 shadow-lg">
    <button onclick="toggleMobileChatSheet()" class="flex flex-col items-center gap-1 text-sky-600 font-bold">
      <span class="text-base">💬</span>
      <span class="text-[10px]">แชท/คอร์ด</span>
    </button>
    <button onclick="toggleAudioTrack()" class="flex flex-col items-center gap-1 text-emerald-600 font-bold">
      <span class="text-base" id="mMicBtnIcon">🎙️</span>
      <span class="text-[10px]">ส่งเสียง</span>
    </button>
    <button onclick="toggleSelfMonitor()" class="flex flex-col items-center gap-1 text-purple-600 font-bold">
      <span class="text-base">🎧</span>
      <span class="text-[10px]">ฟังตัวเอง</span>
    </button>
    <button onclick="openAudioSettingsModal()" class="flex flex-col items-center gap-1 text-blue-600 font-bold">
      <span class="text-base">⚙️</span>
      <span class="text-[10px]">ตั้งค่าเสียง</span>
    </button>
  </div>
</div>

<!-- Audio Hardware Settings Modal -->
<div id="audioSettingsModal" class="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm hidden items-center justify-center p-4">
  <div class="bg-white rounded-3xl p-6 sm:p-7 max-w-md w-full border border-sky-100 text-left relative space-y-4 shadow-2xl">
    <button onclick="closeAudioSettingsModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-600 text-xl font-bold">&times;</button>
    
    <div class="flex items-center gap-3">
      <div class="h-11 w-11 rounded-2xl bg-sky-100 text-sky-600 flex items-center justify-center text-xl font-bold border border-sky-200">
        🎛️
      </div>
      <div>
        <h3 class="text-lg font-bold text-slate-800 font-display">ตั้งค่าอุปกรณ์เสียง & Sound Card</h3>
        <p class="text-xs text-slate-500">เลือกช่องสัญญาณเสียงเข้า-ออก และปรับความดัง</p>
      </div>
    </div>

    <!-- Input device select -->
    <div class="space-y-1.5">
      <label class="block text-xs font-bold text-slate-700">🎤 ช่องสัญญาณเสียงเข้า (Input / Sound Card):</label>
      <select id="audioInSelect" onchange="changeAudioInputDevice(this.value)" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-medium focus:bg-white focus:outline-none focus:ring-2 focus:ring-sky-200">
        <option value="">กำลังโหลดอุปกรณ์เสียง...</option>
      </select>
    </div>

    <!-- Output device select (if supported) -->
    <div class="space-y-1.5" id="audioOutBox">
      <label class="block text-xs font-bold text-slate-700">🔊 ช่องสัญญาณเสียงออก (Output / หูฟัง / ลำโพง):</label>
      <select id="audioOutSelect" onchange="changeAudioOutputDevice(this.value)" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800 font-medium focus:bg-white focus:outline-none focus:ring-2 focus:ring-sky-200">
        <option value="">กำลังโหลดอุปกรณ์เสียง...</option>
      </select>
    </div>

    <!-- Input Gain Booster -->
    <div class="bg-sky-50/60 p-3 rounded-2xl border border-sky-100 space-y-2">
      <div class="flex items-center justify-between text-xs font-bold text-slate-700">
        <span>🎚️ เพิ่มความดังไมค์/เครื่องดนตรี (Input Gain):</span>
        <span class="text-sky-700 font-mono" id="gainDisplay">100%</span>
      </div>
      <input type="range" id="inputGainSlider" min="0" max="3" step="0.1" value="1" oninput="setInputGain(this.value)" class="w-full accent-sky-600">
      <div class="flex justify-between text-[10px] text-slate-400">
        <span>0% (ปิด)</span>
        <span>100% (ปกติ)</span>
        <span>200% (ดังขึ้น)</span>
        <span>300% (ขยายแรง)</span>
      </div>
    </div>

    <!-- Direct Monitoring Toggle -->
    <div class="bg-purple-50/60 p-3 rounded-2xl border border-purple-100 flex items-center justify-between">
      <div>
        <div class="text-xs font-bold text-purple-900">🎧 ฟังเสียงตัวเอง (Direct Monitor)</div>
        <div class="text-[10px] text-purple-600">ส่งเสียงไมค์/กีตาร์ตรงเข้าหูฟังสดๆ</div>
      </div>
      <button onclick="toggleSelfMonitor()" id="modalMonitorBtn" class="px-3 py-1.5 bg-purple-600 hover:bg-purple-700 text-white font-bold rounded-xl text-xs transition">
        เปิดฟังเสียง
      </button>
    </div>

    <div class="pt-2 flex gap-2">
      <button onclick="playTestChime()" class="flex-1 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 font-bold rounded-xl text-xs transition">
        🔔 ทดสอบเสียงออก
      </button>
      <button onclick="closeAudioSettingsModal()" class="px-6 py-2 bg-sky-600 hover:bg-sky-700 text-white font-bold rounded-xl text-xs transition">
        เสร็จสิ้น
      </button>
    </div>
  </div>
</div>

<!-- Mobile Chat Slide-over Modal -->
<div id="mobileChatSheet" class="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm hidden flex-col justify-end md:hidden">
  <div class="bg-white rounded-t-3xl border-t border-sky-200 h-[80vh] flex flex-col p-4 shadow-2xl">
    <div class="flex items-center justify-between pb-3 border-b border-slate-100">
      <h3 class="font-bold text-slate-800 text-sm">💬 แชท & คอร์ดเพลงในห้องซ้อม</h3>
      <button onclick="toggleMobileChatSheet()" class="p-1 rounded-full bg-slate-100 text-slate-500">&times;</button>
    </div>
    <div id="mChatMessagesBox" class="flex-1 overflow-y-auto p-2 space-y-2 text-xs"></div>
    <form onsubmit="sendChatMessage(event, true)" class="flex gap-2 pt-2 border-t border-slate-100">
      <input type="text" id="mChatInput" placeholder="พิมพ์ข้อความ..." class="flex-1 bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-800">
      <button type="submit" class="px-4 py-2 bg-sky-600 text-white font-bold rounded-xl text-xs">ส่ง</button>
    </form>
  </div>
</div>

<!-- Tuner Reference Modal -->
<div id="tunerModal" class="fixed inset-0 z-50 bg-slate-900/60 backdrop-blur-sm hidden items-center justify-center p-4">
  <div class="bg-white rounded-3xl p-6 max-w-sm w-full border border-sky-100 text-center relative space-y-4 shadow-2xl">
    <button onclick="toggleTunerModal()" class="absolute top-4 right-4 text-slate-400 hover:text-slate-600">&times;</button>
    <div class="text-3xl">🎯</div>
    <h3 class="text-lg font-bold text-slate-800 font-display">Guitar / Bass Tuner Reference</h3>
    <p class="text-xs text-slate-500">กดเพื่อฟังเสียงเทียบสายมาตรฐาน</p>
    
    <div class="grid grid-cols-3 gap-2 text-xs font-mono">
      <button onclick="playNoteFrequency(82.41, 'E2 (สาย 6)')" class="p-2 bg-sky-50 rounded-xl border border-sky-200 hover:bg-sky-100 text-sky-800 font-bold">E2 (สาย 6)</button>
      <button onclick="playNoteFrequency(110.00, 'A2 (สาย 5)')" class="p-2 bg-sky-50 rounded-xl border border-sky-200 hover:bg-sky-100 text-sky-800 font-bold">A2 (สาย 5)</button>
      <button onclick="playNoteFrequency(146.83, 'D3 (สาย 4)')" class="p-2 bg-sky-50 rounded-xl border border-sky-200 hover:bg-sky-100 text-sky-800 font-bold">D3 (สาย 4)</button>
      <button onclick="playNoteFrequency(196.00, 'G3 (สาย 3)')" class="p-2 bg-sky-50 rounded-xl border border-sky-200 hover:bg-sky-100 text-sky-800 font-bold">G3 (สาย 3)</button>
      <button onclick="playNoteFrequency(246.94, 'B3 (สาย 2)')" class="p-2 bg-sky-50 rounded-xl border border-sky-200 hover:bg-sky-100 text-sky-800 font-bold">B3 (สาย 2)</button>
      <button onclick="playNoteFrequency(329.63, 'E4 (สาย 1)')" class="p-2 bg-sky-50 rounded-xl border border-sky-200 hover:bg-sky-100 text-sky-800 font-bold">E4 (สาย 1)</button>
    </div>

    <div class="pt-2">
      <button onclick="stopAllTunerTones()" class="w-full py-2 bg-rose-50 hover:bg-rose-100 text-rose-600 text-xs font-bold rounded-xl border border-rose-200">
        หยุดเสียง
      </button>
    </div>
  </div>
</div>

<script>
/* =========================================================================
   Music Room Core Studio Client (WebRTC P2P Mesh + Web Audio DSP Engine)
   ========================================================================= */
const ROOM_CODE = "{{ room.code }}";
const MY_NAME = "{{ me }}";
const MY_ROLE = "{{ my_role or '🎸 Lead Guitar' }}";
const IS_ADMIN = {{ 'true' if is_admin else 'false' }};

// State Variables
let localStream = null;
let audioTrack = null;
let videoTrack = null;
let isAudioEnabled = true;
let isVideoEnabled = false;
let isInstrumentMode = true;
let isScreenSharing = false;

// Audio Device & Gain Nodes
let selectedAudioInputId = '';
let selectedAudioOutputId = '';
let inputGainNode = null;
let monitorGainNode = null;
let isSelfMonitoring = false;
let monitorVolume = 0.8;
let inputGainValue = 1.0;

// WebRTC Peer Connections Map: { [peerName]: RTCPeerConnection }
const peerConnections = {};
const remoteStreams = {};
let lastMessageId = 0;
let lastSignalId = 0;

// Audio Context & VU Meter
let audioCtx = null;
let localSource = null;
let localAnalyser = null;
let localVuInterval = null;

// Metronome & Tuner
let metronomePlaying = false;
let metronomeInterval = null;
let currentBpm = {{ room.bpm or 120 }};
let tunerOscillator = null;

// Recorder
let mediaRecorder = null;
let recordedChunks = [];
let recordStartTime = 0;
let recordTimerInterval = null;

// STUN Configuration for WebRTC P2P
const rtcConfig = {
  iceServers: [
    { urls: "stun:stun.l.google.com:19302" },
    { urls: "stun:stun1.l.google.com:19302" },
    { urls: "stun:stun2.l.google.com:19302" }
  ]
};

// ──────────────── Audio Context Unlock ────────────────
function unlockAudioContext(fromButton=false) {
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (audioCtx.state === 'suspended') {
    audioCtx.resume().then(() => {
      document.getElementById('audioUnlockBanner')?.classList.add('hidden');
      if (fromButton) showToast("ระบบเสียงพร้อมทำงานแล้ว 🔊", "ok");
    });
  }
}

// ──────────────── Initialize Studio Audio & Media ────────────────
async function initStudioMedia() {
  unlockAudioContext();
  try {
    const audioConstraints = {
      echoCancellation: !isInstrumentMode,
      noiseSuppression: !isInstrumentMode,
      autoGainControl: !isInstrumentMode,
      channelCount: isInstrumentMode ? 2 : 1,
      sampleRate: 48000,
      latency: 0
    };

    if (selectedAudioInputId) {
      audioConstraints.deviceId = { exact: selectedAudioInputId };
    }

    localStream = await navigator.mediaDevices.getUserMedia({
      audio: audioConstraints,
      video: isVideoEnabled ? { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 } } : false
    });

    audioTrack = localStream.getAudioTracks()[0];
    if (audioTrack) {
      setupLocalAudioChain(localStream);
    }

    enumerateAudioHardware();
    startPresenceHeartbeat();
    startSignalPolling();
    startMessagePolling();
  } catch (err) {
    console.warn("Could not start audio automatically:", err);
    showToast("กรุณากดเปิดสิทธิ์ไมโครโฟน/อุปกรณ์เสียง เพื่อเล่นสดร่วมกัน", "warning");
    startPresenceHeartbeat();
    startSignalPolling();
    startMessagePolling();
  }
}

function setupLocalAudioChain(stream) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();
    
    if (localSource) {
      try { localSource.disconnect(); } catch(e) {}
    }

    localSource = audioCtx.createMediaStreamSource(stream);
    
    // Gain Booster Node
    if (!inputGainNode) {
      inputGainNode = audioCtx.createGain();
      inputGainNode.gain.value = inputGainValue;
    }

    // Direct Monitor Gain Node (for hearing yourself)
    if (!monitorGainNode) {
      monitorGainNode = audioCtx.createGain();
      monitorGainNode.gain.value = isSelfMonitoring ? monitorVolume : 0;
    }

    localAnalyser = audioCtx.createAnalyser();
    localAnalyser.fftSize = 64;

    // Connect chain:
    // localSource -> inputGainNode -> localAnalyser
    // inputGainNode -> monitorGainNode -> audioCtx.destination (hear myself)
    localSource.connect(inputGainNode);
    inputGainNode.connect(localAnalyser);
    inputGainNode.connect(monitorGainNode);
    monitorGainNode.connect(audioCtx.destination);

    const dataArray = new Uint8Array(localAnalyser.frequencyBinCount);
    const vuBar = document.getElementById('localVuBar');

    if (localVuInterval) clearInterval(localVuInterval);
    localVuInterval = setInterval(() => {
      if (!isAudioEnabled) {
        if (vuBar) vuBar.style.width = '0%';
        return;
      }
      localAnalyser.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
      let avg = sum / dataArray.length;
      let pct = Math.min(100, Math.round((avg / 128) * 100));
      if (vuBar) vuBar.style.width = pct + '%';
    }, 50);
  } catch(e) {
    console.error("Audio chain setup error:", e);
  }
}

// ──────────────── Direct In-Ear Monitor (Hear Yourself) ────────────────
function toggleSelfMonitor() {
  unlockAudioContext();
  isSelfMonitoring = !isSelfMonitoring;
  
  if (monitorGainNode) {
    monitorGainNode.gain.value = isSelfMonitoring ? monitorVolume : 0;
  }

  const btn = document.getElementById('monitorBtn');
  const text = document.getElementById('monitorText');
  const toolBtn = document.getElementById('toolMonitorBtn');
  const modalBtn = document.getElementById('modalMonitorBtn');

  if (isSelfMonitoring) {
    if (btn) btn.className = 'px-2.5 py-1.5 rounded-xl bg-purple-600 text-white border border-purple-400 text-xs font-bold flex items-center gap-1.5 transition shadow-sm';
    if (text) text.textContent = 'ฟังเสียงตัวเอง (ON)';
    if (toolBtn) { toolBtn.textContent = 'ปิดฟังเสียงตัวเอง'; toolBtn.className = 'px-3 py-1.5 bg-rose-600 text-white font-bold rounded-lg transition text-xs'; }
    if (modalBtn) { modalBtn.textContent = 'ปิดฟังเสียง'; modalBtn.className = 'px-3 py-1.5 bg-rose-600 text-white font-bold rounded-xl text-xs transition'; }
    showToast("เปิดระบบฟังเสียงตัวเองในหูฟังแล้ว 🎧 (Direct Monitoring)", "ok");
  } else {
    if (btn) btn.className = 'px-2.5 py-1.5 rounded-xl bg-slate-100 hover:bg-sky-100 text-slate-700 border border-slate-200 text-xs font-bold flex items-center gap-1.5 transition shadow-sm';
    if (text) text.textContent = 'ฟังเสียงตัวเอง (OFF)';
    if (toolBtn) { toolBtn.textContent = 'เปิดฟังเสียงตัวเอง'; toolBtn.className = 'px-3 py-1.5 bg-sky-600 text-white font-bold rounded-lg transition text-xs'; }
    if (modalBtn) { modalBtn.textContent = 'เปิดฟังเสียง'; modalBtn.className = 'px-3 py-1.5 bg-purple-600 text-white font-bold rounded-xl text-xs transition'; }
    showToast("ปิดระบบฟังเสียงตัวเอง", "ok");
  }
}

function setMonitorVolume(vol) {
  monitorVolume = parseFloat(vol);
  if (monitorGainNode && isSelfMonitoring) {
    monitorGainNode.gain.value = monitorVolume;
  }
}

function setInputGain(val) {
  inputGainValue = parseFloat(val);
  if (inputGainNode) {
    inputGainNode.gain.value = inputGainValue;
  }
  const pct = Math.round(inputGainValue * 100) + '%';
  document.getElementById('gainDisplay').textContent = pct;
}

// ──────────────── Enumerate & Change Audio Hardware ────────────────
async function enumerateAudioHardware() {
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const inSelect = document.getElementById('audioInSelect');
    const outSelect = document.getElementById('audioOutSelect');
    
    if (inSelect) inSelect.innerHTML = '';
    if (outSelect) outSelect.innerHTML = '';

    devices.forEach((dev, idx) => {
      if (dev.kind === 'audioinput') {
        const opt = document.createElement('option');
        opt.value = dev.deviceId;
        opt.textContent = dev.label || `ไมโครโฟน / Sound Card ${idx + 1}`;
        if (selectedAudioInputId === dev.deviceId) opt.selected = true;
        inSelect?.appendChild(opt);
      } else if (dev.kind === 'audiooutput') {
        const opt = document.createElement('option');
        opt.value = dev.deviceId;
        opt.textContent = dev.label || `ลำโพง / หูฟัง ${idx + 1}`;
        if (selectedAudioOutputId === dev.deviceId) opt.selected = true;
        outSelect?.appendChild(opt);
      }
    });

    if (!HTMLMediaElement.prototype.setSinkId) {
      document.getElementById('audioOutBox')?.classList.add('hidden');
    }
  } catch(e) {}
}

async function changeAudioInputDevice(deviceId) {
  selectedAudioInputId = deviceId;
  showToast("กำลังสลับช่องสัญญาณเสียงเข้า...", "ok");
  await refreshAudioTrack();
}

async function changeAudioOutputDevice(deviceId) {
  selectedAudioOutputId = deviceId;
  try {
    const audios = document.querySelectorAll('audio');
    for (let el of audios) {
      if (typeof el.setSinkId === 'function') {
        await el.setSinkId(deviceId);
      }
    }
    showToast("สลับช่องสัญญาณเสียงออกเรียบร้อย", "ok");
  } catch(e) {
    console.warn("setSinkId failed:", e);
  }
}

async function refreshAudioTrack() {
  if (!localStream) return;
  try {
    const audioConstraints = {
      echoCancellation: !isInstrumentMode,
      noiseSuppression: !isInstrumentMode,
      autoGainControl: !isInstrumentMode,
      channelCount: isInstrumentMode ? 2 : 1,
      sampleRate: 48000
    };
    if (selectedAudioInputId) {
      audioConstraints.deviceId = { exact: selectedAudioInputId };
    }

    const newStream = await navigator.mediaDevices.getUserMedia({ audio: audioConstraints });
    const newTrack = newStream.getAudioTracks()[0];

    for (const peerName in peerConnections) {
      const pc = peerConnections[peerName];
      const senders = pc.getSenders();
      const audioSender = senders.find(s => s.track && s.track.kind === 'audio');
      if (audioSender) {
        audioSender.replaceTrack(newTrack);
      }
    }

    if (audioTrack) audioTrack.stop();
    audioTrack = newTrack;
    localStream.removeTrack(localStream.getAudioTracks()[0]);
    localStream.addTrack(newTrack);
    setupLocalAudioChain(localStream);
    showToast("เชื่อมต่ออุปกรณ์เสียงสำเร็จ 🎸", "ok");
  } catch(err) {
    console.error("Refresh audio failed:", err);
    showToast("ไม่สามารถเปิดอุปกรณ์เสียงที่เลือกได้", "error");
  }
}

function openAudioSettingsModal() {
  enumerateAudioHardware();
  const modal = document.getElementById('audioSettingsModal');
  modal.classList.remove('hidden');
  modal.classList.add('flex');
}

function closeAudioSettingsModal() {
  const modal = document.getElementById('audioSettingsModal');
  modal.classList.add('hidden');
  modal.classList.remove('flex');
}

// ──────────────── Test Audio Output Chime ────────────────
function playTestChime() {
  unlockAudioContext(true);
  try {
    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.type = 'sine';
    osc.frequency.setValueAtTime(523.25, audioCtx.currentTime); // C5
    osc.frequency.exponentialRampToValueAtTime(659.25, audioCtx.currentTime + 0.15); // E5
    osc.frequency.exponentialRampToValueAtTime(783.99, audioCtx.currentTime + 0.3); // G5

    gain.gain.setValueAtTime(0.3, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.6);

    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + 0.6);
    showToast("กำลังเล่นเสียงทดสอบลำโพง/หูฟัง 🔔", "ok");
  } catch(e) {}
}

// ──────────────── Toggle Instrument vs Voice DSP Mode ────────────────
async function toggleAudioMode() {
  isInstrumentMode = !isInstrumentMode;
  const icon = document.getElementById('audioModeIcon');
  const text = document.getElementById('audioModeText');
  const badge = document.getElementById('localAudioStatusBadge');

  if (isInstrumentMode) {
    if (icon) icon.textContent = '🎸';
    if (text) text.textContent = 'เครื่องดนตรีจริง (Hi-Fi)';
    if (badge) { badge.textContent = 'LIVE HI-FI'; badge.className = 'text-[10px] text-emerald-400 font-mono font-bold'; }
    showToast("เปิดโหมดเครื่องดนตรีจริง (ปิด Echo/Noise Filter เพื่อเสียงใสเต็มย่าน)", "ok");
  } else {
    if (icon) icon.textContent = '🎙️';
    if (text) text.textContent = 'ไมค์พูดคุย (Voice)';
    if (badge) { badge.textContent = 'VOICE CHAT'; badge.className = 'text-[10px] text-sky-400 font-mono font-bold'; }
    showToast("เปิดโหมดไมค์พูดคุย (เปิดตัวตัดเสียงก้อง)", "ok");
  }

  await refreshAudioTrack();
}

// ──────────────── Toggle Audio / Video Tracks ────────────────
function toggleAudioTrack() {
  if (!audioTrack) {
    initStudioMedia();
    return;
  }
  isAudioEnabled = !isAudioEnabled;
  audioTrack.enabled = isAudioEnabled;

  const micBtnText = document.getElementById('micBtnText');
  const micBtnIcon = document.getElementById('micBtnIcon');
  const myMicStatus = document.getElementById('myMicStatus');

  if (isAudioEnabled) {
    if (micBtnText) micBtnText.textContent = 'ส่งเสียงสด (ON)';
    if (micBtnIcon) micBtnIcon.textContent = '🎙️';
    if (myMicStatus) { myMicStatus.textContent = '🎙️ ส่งเสียง'; myMicStatus.className = 'p-1 rounded bg-emerald-100 text-emerald-700 text-[10px] font-bold'; }
    document.getElementById('toggleMicBtn')?.classList.replace('bg-slate-700', 'bg-emerald-600');
  } else {
    if (micBtnText) micBtnText.textContent = 'ปิดเสียง (Muted)';
    if (micBtnIcon) micBtnIcon.textContent = '🔇';
    if (myMicStatus) { myMicStatus.textContent = '🔇 ปิดเสียง'; myMicStatus.className = 'p-1 rounded bg-rose-100 text-rose-700 text-[10px] font-bold'; }
    document.getElementById('toggleMicBtn')?.classList.replace('bg-emerald-600', 'bg-slate-700');
  }
}

async function toggleVideoTrack() {
  isVideoEnabled = !isVideoEnabled;
  const localVideo = document.getElementById('localVideo');
  const localAvatarBox = document.getElementById('localAvatarBox');
  const myCamStatus = document.getElementById('myCamStatus');
  const camBtnText = document.getElementById('camBtnText');

  if (isVideoEnabled) {
    try {
      const vStream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30 } }
      });
      videoTrack = vStream.getVideoTracks()[0];
      localStream.addTrack(videoTrack);
      
      if (localVideo) {
        localVideo.srcObject = localStream;
        localVideo.classList.remove('hidden');
      }
      if (localAvatarBox) localAvatarBox.classList.add('hidden');
      if (myCamStatus) { myCamStatus.textContent = '📷 เปิดกล้อง'; myCamStatus.className = 'p-1 rounded bg-sky-100 text-sky-700 text-[10px] font-bold'; }
      if (camBtnText) camBtnText.textContent = 'ปิดกล้อง';

      for (const peerName in peerConnections) {
        const pc = peerConnections[peerName];
        pc.addTrack(videoTrack, localStream);
        createOfferForPeer(peerName);
      }
    } catch(err) {
      console.error("Camera access failed:", err);
      showToast("ไม่สามารถเปิดกล้องได้", "error");
      isVideoEnabled = false;
    }
  } else {
    if (videoTrack) {
      videoTrack.stop();
      localStream.removeTrack(videoTrack);
      videoTrack = null;
    }
    if (localVideo) {
      localVideo.classList.add('hidden');
      localVideo.srcObject = null;
    }
    if (localAvatarBox) localAvatarBox.classList.remove('hidden');
    if (myCamStatus) { myCamStatus.textContent = '📷 ปิด'; myCamStatus.className = 'p-1 rounded bg-slate-100 text-slate-500 text-[10px]'; }
    if (camBtnText) camBtnText.textContent = 'เปิดกล้อง';
  }
}

// ──────────────── Screen / Sheet Music Share ────────────────
async function toggleScreenShare() {
  if (isScreenSharing) {
    isScreenSharing = false;
    document.getElementById('shareScreenText').textContent = 'แชร์โน้ต/จอ';
    if (videoTrack) {
      toggleVideoTrack();
    }
    return;
  }

  try {
    const screenStream = await navigator.mediaDevices.getDisplayMedia({ video: true });
    const screenTrack = screenStream.getVideoTracks()[0];
    isScreenSharing = true;
    document.getElementById('shareScreenText').textContent = 'หยุดแชร์';

    const localVideo = document.getElementById('localVideo');
    const localAvatarBox = document.getElementById('localAvatarBox');
    if (localVideo) {
      localVideo.srcObject = screenStream;
      localVideo.classList.remove('hidden');
    }
    if (localAvatarBox) localAvatarBox.classList.add('hidden');

    for (const peerName in peerConnections) {
      const pc = peerConnections[peerName];
      const senders = pc.getSenders();
      const videoSender = senders.find(s => s.track && s.track.kind === 'video');
      if (videoSender) {
        videoSender.replaceTrack(screenTrack);
      } else {
        pc.addTrack(screenTrack, screenStream);
        createOfferForPeer(peerName);
      }
    }

    screenTrack.onended = () => {
      isScreenSharing = false;
      document.getElementById('shareScreenText').textContent = 'แชร์โน้ต/จอ';
      if (localVideo) localVideo.classList.add('hidden');
      if (localAvatarBox) localAvatarBox.classList.remove('hidden');
    };
  } catch(err) {
    console.warn("Screen share cancelled:", err);
  }
}

// ──────────────── WebRTC Signaling & Peer Mesh Management ────────────────
function getOrCreatePeerConnection(peerName) {
  if (peerConnections[peerName]) return peerConnections[peerName];

  const pc = new RTCPeerConnection(rtcConfig);
  peerConnections[peerName] = pc;

  if (localStream) {
    localStream.getTracks().forEach(track => {
      pc.addTrack(track, localStream);
    });
  }

  pc.onicecandidate = (event) => {
    if (event.candidate) {
      sendSignal(peerName, 'ice', JSON.stringify(event.candidate));
    }
  };

  pc.ontrack = (event) => {
    handleRemoteTrack(peerName, event.streams[0] || new MediaStream([event.track]));
  };

  pc.onconnectionstatechange = () => {
    if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed' || pc.connectionState === 'closed') {
      removePeerTile(peerName);
      delete peerConnections[peerName];
    }
  };

  return pc;
}

async function createOfferForPeer(peerName) {
  const pc = getOrCreatePeerConnection(peerName);
  const offer = await pc.createOffer({
    offerToReceiveAudio: true,
    offerToReceiveVideo: true
  });
  
  let sdp = offer.sdp;
  sdp = sdp.replace(/a=fmtp:(\d+) (.*)/g, (match, pt, params) => {
    if (params.includes('minptime=') || params.includes('useinbandfec=')) {
      return `a=fmtp:${pt} ${params};stereo=1;sprop-stereo=1;maxaveragebitrate=256000;cbr=1`;
    }
    return match;
  });
  offer.sdp = sdp;

  await pc.setLocalDescription(offer);
  sendSignal(peerName, 'offer', JSON.stringify(offer));
}

async function handleRemoteTrack(peerName, stream) {
  remoteStreams[peerName] = stream;
  renderPeerTile(peerName, stream);
}

function renderPeerTile(peerName, stream) {
  const stageGrid = document.getElementById('stageGrid');
  let tileId = 'peer_tile_' + peerName.replace(/\s+/g, '_');
  let tile = document.getElementById(tileId);

  if (!tile) {
    tile = document.createElement('div');
    tile.id = tileId;
    tile.className = 'glass-card rounded-2xl p-3 border border-sky-100 relative flex flex-col justify-between overflow-hidden shadow-md';
    tile.innerHTML = `
      <div class="relative w-full flex-1 min-h-[160px] bg-gradient-to-br from-sky-900 to-slate-900 rounded-xl overflow-hidden flex items-center justify-center">
        <video id="video_${tileId}" autoplay playsinline class="w-full h-full object-cover hidden"></video>
        <audio id="audio_${tileId}" autoplay playsinline></audio>
        
        <div id="avatar_${tileId}" class="flex flex-col items-center justify-center p-4 text-center">
          <div class="h-20 w-20 rounded-2xl bg-white/10 border border-white/20 flex items-center justify-center text-3xl mb-2 shadow-inner">
            🎸
          </div>
          <div class="text-sm font-bold text-white">${peerName}</div>
          <div class="text-xs text-sky-200 font-semibold" id="role_${tileId}">Band Musician</div>
        </div>

        <div class="absolute bottom-2 left-2 right-2 flex items-center justify-between bg-black/60 backdrop-blur-md px-2.5 py-1.5 rounded-lg border border-white/15 text-xs text-white">
          <div class="flex items-center gap-1.5">
            <span>🔊</span>
            <div class="w-20 sm:w-28 bg-slate-800 h-2 rounded-full overflow-hidden">
              <div id="vu_${tileId}" class="bg-gradient-to-r from-emerald-400 via-sky-400 to-blue-500 h-full w-0 vu-bar"></div>
            </div>
          </div>
          <span class="text-[10px] text-sky-300 font-mono font-bold">STEREO</span>
        </div>
      </div>

      <div class="mt-2 flex items-center justify-between text-xs pt-1 border-t border-slate-100">
        <div class="font-bold text-slate-800 truncate">${peerName}</div>
        <div class="flex items-center gap-2">
          <input type="range" min="0" max="1.5" step="0.1" value="1" title="Volume สำหรับผู้เล่นนี้" oninput="setPeerVolume('${tileId}', this.value)" class="w-16 accent-sky-600">
        </div>
      </div>
    `;
    stageGrid.appendChild(tile);
  }

  const videoEl = document.getElementById('video_' + tileId);
  const audioEl = document.getElementById('audio_' + tileId);
  const avatarEl = document.getElementById('avatar_' + tileId);

  if (stream.getVideoTracks().length > 0 && stream.getVideoTracks()[0].enabled) {
    videoEl.srcObject = stream;
    videoEl.classList.remove('hidden');
    avatarEl.classList.add('hidden');
  } else {
    videoEl.classList.add('hidden');
    avatarEl.classList.remove('hidden');
  }

  if (audioEl) {
    audioEl.srcObject = stream;
    audioEl.play().catch(e => {
      document.getElementById('audioUnlockBanner')?.classList.remove('hidden');
    });
    if (selectedAudioOutputId && typeof audioEl.setSinkId === 'function') {
      audioEl.setSinkId(selectedAudioOutputId).catch(e => {});
    }
    setupPeerAudioMeter(stream, 'vu_' + tileId);
  }
}

function setPeerVolume(tileId, vol) {
  const audioEl = document.getElementById('audio_' + tileId);
  if (audioEl) audioEl.volume = Math.min(1, Math.max(0, vol));
}

function setupPeerAudioMeter(stream, vuId) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    const source = audioCtx.createMediaStreamSource(stream);
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 64;
    source.connect(analyser);

    const dataArray = new Uint8Array(analyser.frequencyBinCount);
    const vuBar = document.getElementById(vuId);

    setInterval(() => {
      if (!vuBar) return;
      analyser.getByteFrequencyData(dataArray);
      let sum = 0;
      for (let i = 0; i < dataArray.length; i++) sum += dataArray[i];
      let avg = sum / dataArray.length;
      let pct = Math.min(100, Math.round((avg / 128) * 100));
      vuBar.style.width = pct + '%';
    }, 60);
  } catch(e) {}
}

function removePeerTile(peerName) {
  let tileId = 'peer_tile_' + peerName.replace(/\s+/g, '_');
  document.getElementById(tileId)?.remove();
}

async function sendSignal(recipient, type, data) {
  try {
    await fetch(`/api/room/${ROOM_CODE}/signal`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ recipient, type, data })
    });
  } catch(e) {}
}

// ──────────────── Polling: Signals, Heartbeats, Messages ────────────────
function startSignalPolling() {
  setInterval(async () => {
    try {
      const res = await fetch(`/api/room/${ROOM_CODE}/signals?after=${lastSignalId}`);
      if (!res.ok) return;
      const data = await res.json();
      
      for (const sig of data.signals) {
        lastSignalId = Math.max(lastSignalId, sig.id);
        const sender = sig.sender;
        const pc = getOrCreatePeerConnection(sender);

        if (sig.type === 'offer') {
          const offerDesc = JSON.parse(sig.data);
          await pc.setRemoteDescription(new RTCSessionDescription(offerDesc));
          const answer = await pc.createAnswer();
          await pc.setLocalDescription(answer);
          sendSignal(sender, 'answer', JSON.stringify(answer));
        } else if (sig.type === 'answer') {
          const answerDesc = JSON.parse(sig.data);
          await pc.setRemoteDescription(new RTCSessionDescription(answerDesc));
        } else if (sig.type === 'ice') {
          const candidate = JSON.parse(sig.data);
          await pc.addIceCandidate(new RTCIceCandidate(candidate));
        }
      }
    } catch(e) {}
  }, 700);
}

function startPresenceHeartbeat() {
  const doHeartbeat = async () => {
    try {
      const res = await fetch(`/api/room/${ROOM_CODE}/presence`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          role: MY_ROLE,
          has_audio: isAudioEnabled ? 1 : 0,
          has_video: isVideoEnabled ? 1 : 0,
          is_instrument_mode: isInstrumentMode ? 1 : 0
        })
      });
      if (res.ok) {
        const data = await res.json();
        updateMembersList(data.members || []);
      }
    } catch(e) {}
  };
  doHeartbeat();
  setInterval(doHeartbeat, 4000);
}

function updateMembersList(members) {
  const countText = document.getElementById('memberCountText');
  const countBadge = document.getElementById('memberCountBadge');
  const listEl = document.getElementById('membersList');

  if (countText) countText.textContent = `${members.length} สมาชิก`;
  if (countBadge) countBadge.textContent = members.length;

  if (listEl) {
    listEl.innerHTML = members.map(m => `
      <div class="glass-card p-2.5 rounded-xl border border-sky-100 flex items-center justify-between shadow-sm">
        <div class="flex items-center gap-2">
          <span class="text-base">${(m.role || '🎸').split(' ')[0]}</span>
          <div>
            <div class="font-bold text-slate-800 text-xs">${m.name} ${m.name === MY_NAME ? '(คุณ)' : ''}</div>
            <div class="text-[10px] text-sky-600">${m.role || 'Musician'}</div>
          </div>
        </div>
        <div class="flex items-center gap-1.5 text-[10px]">
          <span class="px-1.5 py-0.5 rounded ${m.has_audio ? 'bg-emerald-100 text-emerald-700' : 'bg-slate-100 text-slate-400'}">🎙️</span>
          <span class="px-1.5 py-0.5 rounded ${m.has_video ? 'bg-sky-100 text-sky-700' : 'bg-slate-100 text-slate-400'}">📷</span>
        </div>
      </div>
    `).join('');
  }

  members.forEach(m => {
    if (m.name !== MY_NAME && !peerConnections[m.name]) {
      if (MY_NAME > m.name) {
        createOfferForPeer(m.name);
      }
    }
  });
}

// ──────────────── Real-time Text & Chord Chat ────────────────
function startMessagePolling() {
  const poll = async () => {
    try {
      const res = await fetch(`/api/room/${ROOM_CODE}/messages?after=${lastMessageId}`);
      if (!res.ok) return;
      const data = await res.json();
      
      if (data.messages && data.messages.length > 0) {
        const box = document.getElementById('messagesBox');
        const mBox = document.getElementById('mChatMessagesBox');
        
        data.messages.forEach(msg => {
          lastMessageId = Math.max(lastMessageId, msg.id);
          appendChatMessage(msg, box);
          if (mBox) appendChatMessage(msg, mBox);
        });

        box.scrollTop = box.scrollHeight;
        if (mBox) mBox.scrollTop = mBox.scrollHeight;
      }
    } catch(e) {}
  };
  poll();
  setInterval(poll, 1000);
}

function appendChatMessage(msg, container) {
  const isMe = msg.author === MY_NAME;
  const isSystem = !msg.author;

  const msgDiv = document.createElement('div');
  msgDiv.className = isSystem ? 'text-center my-2' : (isMe ? 'flex flex-col items-end my-1.5' : 'flex flex-col items-start my-1.5');

  if (isSystem) {
    msgDiv.innerHTML = `<span class="px-2.5 py-1 rounded-full bg-sky-50 border border-sky-100 text-[10px] text-sky-700 font-mono">${msg.body}</span>`;
  } else {
    let contentHtml = escapeHtml(msg.body);
    contentHtml = contentHtml.replace(/\[([A-G][b#]?[m]?[0-9]?[a-zA-Z]*)\]/g, '<span class="px-1.5 py-0.5 rounded bg-sky-100 text-sky-800 font-mono font-bold text-xs border border-sky-200">$1</span>');

    let fileHtml = '';
    if (msg.file_data) {
      if (msg.msg_type === 'image') {
        fileHtml = `<img src="${msg.file_data}" class="rounded-xl max-h-48 mt-1 border border-slate-200 cursor-pointer shadow-sm" onclick="window.open(this.src)">`;
      } else if (msg.msg_type === 'audio') {
        fileHtml = `<audio src="${msg.file_data}" controls class="w-full mt-1.5"></audio>`;
      } else {
        fileHtml = `<a href="${msg.file_data}" download="attachment" class="inline-flex items-center gap-1.5 mt-1 px-3 py-1.5 rounded-lg bg-white border border-sky-200 text-sky-700 font-bold text-xs hover:underline shadow-sm">📥 ดาวน์โหลดไฟล์แนบ</a>`;
      }
    }

    msgDiv.innerHTML = `
      <div class="text-[10px] text-slate-500 mb-0.5 px-1">${msg.author} <span class="text-sky-600 font-semibold">(${msg.role || '🎸'})</span></div>
      <div class="px-3.5 py-2.5 rounded-2xl max-w-[85%] break-words shadow-sm ${isMe ? 'bg-gradient-to-r from-sky-500 to-blue-600 text-white rounded-br-none' : 'bg-white border border-slate-200 text-slate-800 rounded-bl-none'}">
        <div>${contentHtml}</div>
        ${fileHtml}
      </div>
    `;
  }
  container.appendChild(msgDiv);
}

async function sendChatMessage(e, isMobile=false) {
  if (e) e.preventDefault();
  const input = isMobile ? document.getElementById('mChatInput') : document.getElementById('chatInput');
  const body = input.value.trim();
  if (!body) return;

  input.value = '';
  try {
    await fetch(`/api/room/${ROOM_CODE}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ body, msg_type: 'text', role: MY_ROLE })
    });
  } catch(e) {}
}

function insertChord(chord) {
  const input = document.getElementById('chatInput');
  if (input) {
    input.value += chord;
    input.focus();
  }
}

async function handleChatFileUpload(input) {
  const file = input.files[0];
  if (!file) return;

  const reader = new FileReader();
  reader.onload = async (e) => {
    let msg_type = 'file';
    if (file.type.startsWith('image/')) msg_type = 'image';
    if (file.type.startsWith('audio/')) msg_type = 'audio';

    await fetch(`/api/room/${ROOM_CODE}/messages`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        body: `แชร์ไฟล์: ${file.name}`,
        file_data: e.target.result,
        msg_type: msg_type,
        role: MY_ROLE
      })
    });
    input.value = '';
  };
  reader.readAsDataURL(file);
}

// ──────────────── Studio Metronome & Tap Tempo ────────────────
function toggleMetronome() {
  unlockAudioContext(true);
  metronomePlaying = !metronomePlaying;
  const btn = document.getElementById('metronomeBtn');
  const dot = document.getElementById('metroIndicator');
  const mDot = document.getElementById('mMetroIndicator');

  if (metronomePlaying) {
    if (btn) btn.classList.add('bg-sky-600', 'text-white');
    startMetronomeLoop();
  } else {
    if (btn) btn.classList.remove('bg-sky-600', 'text-white');
    if (metronomeInterval) clearInterval(metronomeInterval);
    if (dot) dot.className = 'h-3 w-3 rounded-full bg-slate-300';
    if (mDot) mDot.className = 'h-2.5 w-2.5 rounded-full bg-slate-300';
  }
}

function startMetronomeLoop() {
  if (metronomeInterval) clearInterval(metronomeInterval);
  const intervalMs = (60 / currentBpm) * 1000;
  let beat = 0;

  metronomeInterval = setInterval(() => {
    beat = (beat % 4) + 1;
    playMetronomeClick(beat === 1);

    const dot = document.getElementById('metroIndicator');
    const mDot = document.getElementById('mMetroIndicator');
    if (dot) dot.className = `h-3 w-3 rounded-full ${beat === 1 ? 'bg-sky-500 shadow-md shadow-sky-500/80 scale-125' : 'bg-blue-400 scale-100'} transition duration-75`;
    if (mDot) mDot.className = `h-2.5 w-2.5 rounded-full ${beat === 1 ? 'bg-sky-500' : 'bg-blue-400'}`;
  }, intervalMs);
}

function playMetronomeClick(accent) {
  try {
    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === 'suspended') audioCtx.resume();

    const osc = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    osc.frequency.value = accent ? 1200 : 800;
    gain.gain.setValueAtTime(0.15, audioCtx.currentTime);
    gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.05);

    osc.connect(gain);
    gain.connect(audioCtx.destination);
    osc.start();
    osc.stop(audioCtx.currentTime + 0.05);
  } catch(e) {}
}

function updateBpm(bpm) {
  currentBpm = parseInt(bpm) || 120;
  document.getElementById('bpmInput').value = currentBpm;
  document.getElementById('mBpmVal').textContent = currentBpm;
  document.getElementById('toolBpmDisplay').textContent = currentBpm + ' BPM';
  if (metronomePlaying) startMetronomeLoop();
}

let tapTimes = [];
function tapTempo() {
  const now = Date.now();
  tapTimes.push(now);
  if (tapTimes.length > 4) tapTimes.shift();

  if (tapTimes.length >= 2) {
    let diffs = [];
    for (let i = 1; i < tapTimes.length; i++) diffs.push(tapTimes[i] - tapTimes[i-1]);
    let avg = diffs.reduce((a, b) => a + b) / diffs.length;
    let bpm = Math.round(60000 / avg);
    if (bpm >= 40 && bpm <= 240) {
      updateBpm(bpm);
    }
  }
}

// ──────────────── Tuner Reference Notes ────────────────
function playNoteFrequency(freq, name) {
  stopAllTunerTones();
  unlockAudioContext(true);
  try {
    tunerOscillator = audioCtx.createOscillator();
    const gain = audioCtx.createGain();
    tunerOscillator.type = 'triangle';
    tunerOscillator.frequency.value = freq;
    gain.gain.setValueAtTime(0.2, audioCtx.currentTime);

    tunerOscillator.connect(gain);
    gain.connect(audioCtx.destination);
    tunerOscillator.start();
    showToast(`เล่นเสียง ${name} (${freq} Hz)`, "ok");
  } catch(e) {}
}

function playA440Tone() {
  if (tunerOscillator) {
    stopAllTunerTones();
  } else {
    playNoteFrequency(440, "A440 (มาตรฐานสากล)");
  }
}

function stopAllTunerTones() {
  if (tunerOscillator) {
    try { tunerOscillator.stop(); tunerOscillator.disconnect(); } catch(e) {}
    tunerOscillator = null;
  }
}

// ──────────────── Live Video & Audio Recording Studio ────────────────
async function toggleRecording() {
  unlockAudioContext(true);
  if (mediaRecorder && mediaRecorder.state === 'recording') {
    mediaRecorder.stop();
    clearInterval(recordTimerInterval);
    document.getElementById('recordBtn').classList.remove('bg-rose-500', 'text-white');
    document.getElementById('recDot').classList.remove('rec-blink');
    document.getElementById('recText').textContent = 'บันทึกวิดีโอ & เสียง';
    document.getElementById('recTimer').classList.add('hidden');
    document.getElementById('mRecTimer').classList.add('hidden');
    return;
  }

  try {
    const dest = audioCtx.createMediaStreamDestination();

    if (localStream && localStream.getAudioTracks().length > 0) {
      const src = audioCtx.createMediaStreamSource(localStream);
      src.connect(dest);
    }

    for (const peerName in remoteStreams) {
      const stream = remoteStreams[peerName];
      if (stream.getAudioTracks().length > 0) {
        const peerSource = audioCtx.createMediaStreamSource(stream);
        peerSource.connect(dest);
      }
    }

    const tracks = [...dest.stream.getAudioTracks()];
    if (localStream && localStream.getVideoTracks().length > 0) {
      tracks.push(localStream.getVideoTracks()[0]);
    }

    const mixedStream = new MediaStream(tracks);
    recordedChunks = [];
    
    let mimeType = 'video/webm;codecs=vp9,opus';
    if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'video/webm';
    if (!MediaRecorder.isTypeSupported(mimeType)) mimeType = 'audio/webm';

    mediaRecorder = new MediaRecorder(mixedStream, { mimeType });

    mediaRecorder.ondataavailable = (e) => {
      if (e.data.size > 0) recordedChunks.push(e.data);
    };

    mediaRecorder.onstop = () => {
      const blob = new Blob(recordedChunks, { type: mimeType });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `MusicRoom_Rehearsal_${ROOM_CODE}_${Date.now()}.webm`;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => { document.body.removeChild(a); URL.revokeObjectURL(url); }, 2000);
      showToast("บันทึกเสร็จสิ้น! กำลังดาวน์โหลดไฟล์การซ้อมดนตรี", "ok");
    };

    mediaRecorder.start(1000);
    recordStartTime = Date.now();

    document.getElementById('recordBtn').classList.add('bg-rose-500', 'text-white');
    document.getElementById('recDot').classList.add('rec-blink');
    document.getElementById('recText').textContent = 'กำลังบันทึก (REC)';
    document.getElementById('recTimer').classList.remove('hidden');
    document.getElementById('mRecTimer').classList.remove('hidden');

    recordTimerInterval = setInterval(() => {
      const elapsedSec = Math.floor((Date.now() - recordStartTime) / 1000);
      const m = String(Math.floor(elapsedSec / 60)).padStart(2, '0');
      const s = String(elapsedSec % 60).padStart(2, '0');
      const timerStr = `${m}:${s}`;
      document.getElementById('recTimer').textContent = timerStr;
      document.getElementById('mRecTimer').textContent = timerStr;
    }, 1000);

    showToast("เริ่มบันทึกการซ้อมดนตรีสดแล้ว 🔴", "ok");
  } catch(err) {
    console.error("Recording failed:", err);
    showToast("ไม่สามารถเริ่มบันทึกได้บนเบราว์เซอร์นี้", "error");
  }
}

// ──────────────── UI Helpers ────────────────
function switchSidebarTab(tab) {
  document.getElementById('tabChat').classList.toggle('hidden', tab !== 'chat');
  document.getElementById('tabMembers').classList.toggle('hidden', tab !== 'members');
  document.getElementById('tabTools').classList.toggle('hidden', tab !== 'tools');

  document.getElementById('tabChatBtn').className = `flex-1 py-1.5 rounded-lg text-xs font-bold transition ${tab === 'chat' ? 'bg-sky-600 text-white shadow-sm' : 'text-slate-600 hover:text-sky-700'}`;
  document.getElementById('tabMembersBtn').className = `flex-1 py-1.5 rounded-lg text-xs font-bold transition ${tab === 'members' ? 'bg-sky-600 text-white shadow-sm' : 'text-slate-600 hover:text-sky-700'}`;
  document.getElementById('tabToolsBtn').className = `flex-1 py-1.5 rounded-lg text-xs font-bold transition ${tab === 'tools' ? 'bg-sky-600 text-white shadow-sm' : 'text-slate-600 hover:text-sky-700'}`;
}

function toggleMobileChatSheet() {
  const sheet = document.getElementById('mobileChatSheet');
  sheet.classList.toggle('hidden');
  sheet.classList.toggle('flex');
}

function toggleTunerModal() {
  const modal = document.getElementById('tunerModal');
  modal.classList.toggle('hidden');
  modal.classList.toggle('flex');
  if (modal.classList.contains('hidden')) stopAllTunerTones();
}

function confirmDeleteRoom() {
  if (confirm("คุณแน่ใจหรือไม่ว่าต้องการปิดและลบห้องซ้อมนี้?")) {
    const form = document.createElement('form');
    form.method = 'POST';
    form.action = `/room/${ROOM_CODE}/delete`;
    document.body.appendChild(form);
    form.submit();
  }
}

function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#039;");
}

function showToast(msg, type='ok') {
  const toast = document.createElement('div');
  toast.className = `fixed bottom-20 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 rounded-2xl text-xs font-bold shadow-xl backdrop-blur-xl border ${type === 'error' ? 'bg-rose-500 text-white border-rose-400 shadow-rose-500/30' : 'bg-sky-600 text-white border-sky-400 shadow-sky-500/30'}`;
  toast.textContent = msg;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

window.addEventListener('DOMContentLoaded', () => {
  initStudioMedia();
});
</script>
"""


# ───────────────────────────── Flask Routes ─────────────────────────────
@app.route("/")
def index():
    port = int(os.environ.get("PORT", 5000))
    net_ips = get_local_ips()
    current_url = request.host_url.rstrip("/")

    me = session.get("name")
    my_role = session.get("role", "🎸 Lead Guitar")
    if not me:
        return render_template_string(BASE.replace("%%BODY%%", LOGIN),
                                      title="เข้าสู่ระบบ · Music Room",
                                      max_name=MAX_NAME, net_ips=net_ips, port=port,
                                      current_url=current_url)

    db = get_db()
    now = time.time()
    
    raw_rooms = db.execute("SELECT * FROM rooms ORDER BY created_at DESC").fetchall()
    all_rooms = []
    for r in raw_rooms:
        online = db.execute("SELECT COUNT(*) FROM presence WHERE room_code=? AND last_seen>?",
                            (r["code"], now - ONLINE_WINDOW)).fetchone()[0]
        all_rooms.append({
            "code": r["code"],
            "name": r["name"],
            "icon": r["icon"] if "icon" in r.keys() else "🎸",
            "bpm": r["bpm"] if "bpm" in r.keys() else 120,
            "created_by": r["created_by"],
            "is_private": bool(r["is_private"]),
            "online": online,
            "created_at": r["created_at"]
        })

    return render_template_string(BASE.replace("%%BODY%%", LOBBY),
                                  title="Music Room — หน้าล็อบบี้สตูดิโอ",
                                  me=me, my_role=my_role, all_rooms=all_rooms,
                                  net_ips=net_ips, port=port, current_url=current_url,
                                  max_room=MAX_ROOM_NAME, max_pass=MAX_PASSWORD)


@app.post("/login")
def login():
    name = re.sub(r"\s+", " ", request.form.get("name", "")).strip()[:MAX_NAME]
    role = request.form.get("role", "🎸 Lead Guitar").strip()
    if not name:
        flash("กรุณากรอกชื่อสำหรับเข้าใช้งาน", "error")
    else:
        session["name"] = name
        session["role"] = role
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    session.pop("name", None)
    session.pop("role", None)
    session.pop("unlocked_rooms", None)
    return redirect(url_for("index"))


@app.post("/create")
def create():
    me = session.get("name")
    if not me:
        return redirect(url_for("index"))
    
    room_name = request.form.get("room_name", "").strip()[:MAX_ROOM_NAME] or f"ห้องซ้อมของ {me}"
    room_icon = request.form.get("room_icon", "🎸").strip()[:10]
    bpm = int(request.form.get("bpm", 120) or 120)
    is_private = 1 if request.form.get("is_private") == "1" else 0
    password = request.form.get("password", "").strip()[:MAX_PASSWORD] if is_private else ""

    db = get_db()
    code = new_room_code()
    if code is None:
        flash("ห้องเต็มแล้ว (ครบ 9,000 ห้อง)", "error")
        return redirect(url_for("index"))

    now = time.time()
    db.execute("""
        INSERT INTO rooms (code, name, icon, bpm, created_by, password, is_private, created_at)
        VALUES (?,?,?,?,?,?,?,?)
    """, (code, room_name, room_icon, bpm, me, password, is_private, now))

    status_txt = "🔒 ห้องส่วนตัว" if is_private else "🌐 ห้องสาธารณะ"
    db.execute("""
        INSERT INTO messages (room_code, author, body, msg_type, role, created_at)
        VALUES (?,?,?,?,?,?)
    """, (code, None, f"{me} เปิดห้องซ้อม “{room_name}” [{status_txt} · {bpm} BPM] รหัสห้อง #{code}", "text", "", now))
    db.commit()

    unlock_room(code)
    flash(f"เปิดห้องซ้อมสำเร็จ! รหัสห้องคือ {code}", "ok")
    return redirect(url_for("room", code=code))


@app.post("/join")
def join():
    if not session.get("name"):
        return redirect(url_for("index"))
    code = request.form.get("code", "").strip()
    if not re.fullmatch(r"\d{4}", code):
        flash("รหัสห้องต้องเป็นตัวเลข 4 หลัก", "error")
        return redirect(url_for("index"))
    r = get_room(code)
    if not r:
        flash(f"ไม่พบห้องซ้อมรหัส {code}", "error")
        return redirect(url_for("index"))

    return redirect(url_for("room", code=code))


@app.route("/room/<code>")
def room(code):
    me = session.get("name")
    if not me:
        return redirect(url_for("index"))
    r = get_room(code)
    if not r:
        flash(f"ไม่พบห้องรหัส {code}", "error")
        return redirect(url_for("index"))

    db = get_db()
    mod = db.execute("SELECT * FROM room_moderation WHERE room_code=? AND username=?", (code, me)).fetchone()
    if mod and mod["is_kicked"]:
        flash("คุณถูกหัวหน้าห้องเตะออกจากห้องนี้แล้ว", "error")
        return redirect(url_for("index"))

    if not is_room_unlocked(code, r):
        return render_template_string(BASE.replace("%%BODY%%", PASSWORD_PROMPT),
                                      title=f"ใส่รหัสผ่านห้อง · #{code}", room=r)

    remember_room(code)
    is_admin = (r["created_by"] == me)
    room_url = f"{request.host_url.rstrip('/')}/room/{code}"
    my_role = session.get("role", "🎸 Lead Guitar")

    return render_template_string(BASE.replace("%%BODY%%", ROOM),
                                  title=f"{r['name']} · #{code} — Music Room Studio",
                                  me=me, my_role=my_role, room=r, is_admin=is_admin,
                                  room_url=room_url, max_msg=MAX_MSG)


@app.post("/room/<code>/verify")
def verify_room_password(code):
    me = session.get("name")
    if not me:
        return redirect(url_for("index"))
    r = get_room(code)
    if not r:
        flash(f"ไม่พบห้องรหัส {code}", "error")
        return redirect(url_for("index"))

    input_pass = request.form.get("password", "").strip()
    if input_pass == r["password"]:
        unlock_room(code)
        flash("รหัสผ่านถูกต้อง เข้าห้องสำเร็จ", "ok")
        return redirect(url_for("room", code=code))
    else:
        flash("รหัสผ่านห้องไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง", "error")
        return render_template_string(BASE.replace("%%BODY%%", PASSWORD_PROMPT),
                                      title=f"ใส่รหัสผ่านห้อง · #{code}", room=r)


@app.post("/room/<code>/delete")
def delete_room(code):
    me = session.get("name")
    if not me:
        return redirect(url_for("index"))
    r = get_room(code)
    if not r:
        flash(f"ไม่พบห้องรหัส {code}", "error")
        return redirect(url_for("index"))

    if r["created_by"] != me:
        flash("เฉพาะผู้สร้างห้องเท่านั้นที่สามารถลบห้องได้", "error")
        return redirect(url_for("room", code=code))

    db = get_db()
    db.execute("DELETE FROM messages WHERE room_code=?", (code,))
    db.execute("DELETE FROM presence WHERE room_code=?", (code,))
    db.execute("DELETE FROM room_moderation WHERE room_code=?", (code,))
    db.execute("DELETE FROM webrtc_signals WHERE room_code=?", (code,))
    db.execute("DELETE FROM rooms WHERE code=?", (code,))
    db.commit()

    flash(f"ปิดและลบห้อง “{r['name']}” เรียบร้อยแล้ว", "ok")
    return redirect(url_for("index"))


# ──────────────── WebRTC Signaling Endpoints ────────────────
@app.post("/api/room/<code>/signal")
def api_send_signal(code):
    me = session.get("name")
    if not me:
        return jsonify(error="unauthorized"), 401
    
    payload = request.get_json(silent=True) or {}
    recipient = payload.get("recipient", "").strip()
    sig_type = payload.get("type", "").strip()
    data = payload.get("data", "")

    if not recipient or not sig_type:
        return jsonify(error="bad_request"), 400

    db = get_db()
    now = time.time()
    db.execute("""
        INSERT INTO webrtc_signals (room_code, sender, recipient, type, data, created_at)
        VALUES (?,?,?,?,?,?)
    """, (code, me, recipient, sig_type, data, now))
    
    db.execute("DELETE FROM webrtc_signals WHERE created_at < ?", (now - 60,))
    db.commit()
    return jsonify(ok=True)


@app.get("/api/room/<code>/signals")
def api_get_signals(code):
    me = session.get("name")
    if not me:
        return jsonify(error="unauthorized"), 401
    
    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        after = 0

    db = get_db()
    rows = db.execute("""
        SELECT * FROM webrtc_signals
        WHERE room_code=? AND recipient=? AND id>?
        ORDER BY id ASC LIMIT 50
    """, (code, me, after)).fetchall()

    return jsonify(signals=[{
        "id": r["id"],
        "sender": r["sender"],
        "type": r["type"],
        "data": r["data"],
        "created_at": r["created_at"]
    } for r in rows])


# ──────────────── Presence & Messages API ────────────────
@app.post("/api/room/<code>/presence")
def api_presence(code):
    me = session.get("name")
    if not me:
        return jsonify(error="unauthorized"), 401

    payload = request.get_json(silent=True) or {}
    role = payload.get("role", session.get("role", "🎸 Lead Guitar"))
    has_audio = 1 if payload.get("has_audio") else 0
    has_video = 1 if payload.get("has_video") else 0
    is_instrument_mode = 1 if payload.get("is_instrument_mode") else 0

    db = get_db()
    now = time.time()
    db.execute("""
        INSERT INTO presence (room_code, name, role, has_audio, has_video, is_instrument_mode, last_seen)
        VALUES (?,?,?,?,?,?,?)
        ON CONFLICT(room_code, name) DO UPDATE SET
            role=excluded.role,
            has_audio=excluded.has_audio,
            has_video=excluded.has_video,
            is_instrument_mode=excluded.is_instrument_mode,
            last_seen=excluded.last_seen
    """, (code, me, role, has_audio, has_video, is_instrument_mode, now))
    db.commit()

    online_rows = db.execute("""
        SELECT name, role, has_audio, has_video, is_instrument_mode
        FROM presence
        WHERE room_code=? AND last_seen>?
        ORDER BY name
    """, (code, now - ONLINE_WINDOW)).fetchall()

    return jsonify(members=[{
        "name": r["name"],
        "role": r["role"],
        "has_audio": bool(r["has_audio"]),
        "has_video": bool(r["has_video"]),
        "is_instrument_mode": bool(r["is_instrument_mode"])
    } for r in online_rows])


@app.route("/api/room/<code>/messages", methods=["GET", "POST"])
def api_messages(code):
    me = session.get("name")
    if not me:
        return jsonify(error="unauthorized"), 401
    r = get_room(code)
    if not r:
        abort(404)

    if not is_room_unlocked(code, r):
        return jsonify(error="forbidden_password_required"), 403

    db = get_db()
    now = time.time()

    if request.method == "POST":
        payload = request.get_json(silent=True) or {}
        body = str(payload.get("body", "")).strip()[:MAX_MSG]
        msg_type = payload.get("msg_type", "text")
        file_data = payload.get("file_data")
        role = payload.get("role", session.get("role", "🎸"))

        if not body and not file_data:
            return jsonify(error="empty"), 400

        db.execute("""
            INSERT INTO messages (room_code, author, body, msg_type, file_data, role, created_at)
            VALUES (?,?,?,?,?,?,?)
        """, (code, me, body, msg_type, file_data, role, now))
        db.commit()
        return jsonify(ok=True)

    try:
        after = int(request.args.get("after", 0))
    except ValueError:
        after = 0

    if after == 0:
        rows = db.execute("""
            SELECT * FROM (
                SELECT * FROM messages WHERE room_code=? ORDER BY id DESC LIMIT 150
            ) ORDER BY id
        """, (code,)).fetchall()
    else:
        rows = db.execute("""
            SELECT * FROM messages WHERE room_code=? AND id>? ORDER BY id LIMIT 300
        """, (code, after)).fetchall()

    return jsonify(
        messages=[{
            "id": row["id"],
            "author": row["author"],
            "body": row["body"],
            "msg_type": row["msg_type"] if "msg_type" in row.keys() else "text",
            "file_data": row["file_data"] if "file_data" in row.keys() else None,
            "role": row["role"] if "role" in row.keys() else "",
            "created_at": row["created_at"]
        } for row in rows]
    )


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("\n" + "="*60)
    print(" 🎸 Music Room — Live Jam Studio กำลังทำงานบนเซิร์ฟเวอร์...")
    print(f" 🌐 สำหรับเครื่องนี้: http://127.0.0.1:{port}")
    ips = get_local_ips()
    for item in ips:
        print(f" 📱 สำหรับมือถือ/iPad/เครื่องอื่น [{item['type']}]: http://{item['ip']}:{port}")
    print("="*60 + "\n")
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
