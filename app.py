# like_web.py — NIROBxFREExLIKE v4.0 (Web + API + Admin)
import os
import json
import base64
import binascii
import asyncio
import time as _time
from threading import RLock
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, request, jsonify, Response, render_template_string, send_file
from flask_cors import CORS
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad
from google.protobuf.json_format import MessageToJson

import requests
import aiohttp
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

try:
    import orjson
    def _jsonify(data, status=200):
        return Response(orjson.dumps(data), status=status, mimetype='application/json')
except ImportError:
    def _jsonify(data, status=200):
        return Response(json.dumps(data, separators=(',', ':'), ensure_ascii=False),
                        status=status, mimetype='application/json')

import like_pb2
import like_count_pb2
import uid_generator_pb2

# ============================================================
#  CONFIG
# ============================================================
app = Flask(__name__)
CORS(app)

BRAND_NAME = "NIROBxFREExLIKE"
DEV_NAME = "NIROB"
OWNER_HANDLE = "TG: @MT_0G"
BADGE_TEXT = "LIKE • API • KEY • NIROBxLIKE"
VERSION = "4.0.0"
RELEASE_VERSION = "OB55"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JWT_API_BASE = "https://nirobxjwt.vercel.app/token"

JWT_WORKERS = 60
LIKE_CONCUR = 150
REQUEST_TIMEOUT = 15
API_KEY = "NirobAPI_Secret_2026_ChangeMe"

# ============================================================
#  🔄 BOT SE TOKEN FETCH — Runtime config
# ============================================================
# Bot ka URL (jahan uska report server chal raha hai)
# Same machine pe ho to 127.0.0.1 rakho, alag server pe ho to public URL daalo
BOT_CONFIG_URL = os.environ.get(
    "BOT_CONFIG_URL",
    "http://127.0.0.1:5001/api/get-tg-config"
)
BOT_CONFIG_KEY = os.environ.get(
    "BOT_CONFIG_KEY",
    "NirobBot_Report_Secret_2026"
)

# Runtime variables (bot se fetch honge)
TG_TOKEN = None
TG_ADMIN_ID = None

# Fallback (agar bot offline ho aur tu manually dekhna chahe)
FALLBACK_BOT_TOKEN = os.environ.get("FALLBACK_BOT_TOKEN", "")
FALLBACK_ADMIN_ID = int(os.environ.get("FALLBACK_ADMIN_ID", "0"))


def fetch_tg_config_from_bot():
    """Bot se token aur admin_id fetch karo"""
    global TG_TOKEN, TG_ADMIN_ID
    try:
        r = requests.get(
            BOT_CONFIG_URL,
            headers={"X-API-Key": BOT_CONFIG_KEY},
            timeout=10
        )
        if r.status_code == 200:
            d = r.json()
            token = d.get("bot_token")
            admin = d.get("admin_id")
            if token and admin:
                TG_TOKEN = token
                TG_ADMIN_ID = int(admin)
                print(f"✅ [BOT] Token fetched: ...{TG_TOKEN[-10:]}")
                print(f"✅ [BOT] Admin ID: {TG_ADMIN_ID}")
                return True
        print(f"❌ [BOT] HTTP {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"❌ [BOT] Fetch failed: {e}")
        return False


def get_tg_config():
    """Token uthao — agar cache nahi hai to bot se fetch karo, phir fallback"""
    global TG_TOKEN, TG_ADMIN_ID
    if not TG_TOKEN or not TG_ADMIN_ID:
        # 1. Bot se try karo
        if fetch_tg_config_from_bot():
            return TG_TOKEN, TG_ADMIN_ID
        # 2. Fallback use karo
        if FALLBACK_BOT_TOKEN and FALLBACK_ADMIN_ID:
            print("⚠️ [BOT] Using fallback config")
            TG_TOKEN = FALLBACK_BOT_TOKEN
            TG_ADMIN_ID = FALLBACK_ADMIN_ID
            return TG_TOKEN, TG_ADMIN_ID
        return None, None
    return TG_TOKEN, TG_ADMIN_ID


# Level based limits
LEVEL_LIMITS = {
    8: 20, 9: 50, 15: 100, 20: 100,
    21: 200, 25: 250, 30: 300, 31: 300,
    40: 400, 50: 500, 60: 600
}

def get_level_limit(level):
    if not level or level <= 0:
        return 20
    if level in LEVEL_LIMITS:
        return LEVEL_LIMITS[level]
    closest = 0
    for lvl in sorted(LEVEL_LIMITS.keys()):
        if lvl <= level:
            closest = lvl
        else:
            break
    if closest:
        return LEVEL_LIMITS[closest]
    if level <= 8:
        return 20
    return 500

# Counters
USED_PER_TOKEN = {}
COUNTER_LOCK = RLock()

def _today_key():
    return datetime.now().strftime("%Y-%m-%d")

def _get_used_per_token(uid):
    with COUNTER_LOCK:
        today = _today_key()
        if today not in USED_PER_TOKEN:
            USED_PER_TOKEN.clear()
            USED_PER_TOKEN[today] = {}
        return USED_PER_TOKEN[today].get(str(uid), 0)

def _increment_used_per_token(uid):
    with COUNTER_LOCK:
        today = _today_key()
        if today not in USED_PER_TOKEN:
            USED_PER_TOKEN.clear()
            USED_PER_TOKEN[today] = {}
        k = str(uid)
        USED_PER_TOKEN[today][k] = USED_PER_TOKEN[today].get(k, 0) + 1

# Files
HISTORY_FILE = os.path.join(BASE_DIR, "update_history.json")
GUESTS_DB_PATH = os.path.join(BASE_DIR, "guests_db.json")
STATS_FILE = os.path.join(BASE_DIR, "web_stats.json")
MAX_HISTORY = 30

SERVER_CONFIG = {
    "BD":  {"jwt": "jwt_bd.json",  "accounts": "account_bd.txt"},
    "IND": {"jwt": "jwt_ind.json", "accounts": "account_ind.txt"},
    "BR":  {"jwt": "jwt_br.json",  "accounts": "account_br.txt"},
    "US":  {"jwt": "jwt_us.json",  "accounts": "account_us.txt"},
    "SAC": {"jwt": "jwt_sac.json", "accounts": "account_sac.txt"},
    "NA":  {"jwt": "jwt_na.json",  "accounts": "account_na.txt"},
}

CONFIG_RO_PATH = os.path.join(BASE_DIR, "keys.json")
CONFIG_RW_PATH = "/tmp/keys.json"
config_lock = RLock()

# ============================================================
#  HELPERS
# ============================================================
def _read_config():
    path = CONFIG_RW_PATH if os.path.exists(CONFIG_RW_PATH) else CONFIG_RO_PATH
    if not os.path.exists(path):
        raise FileNotFoundError("keys.json not found")
    with open(path) as f:
        return json.load(f)

def get_allowed_keys():
    with config_lock:
        return _read_config()["ALLOWED_KEYS"]

def get_admin_keys():
    with config_lock:
        return set(_read_config()["ADMIN_KEYS"])

def is_valid_key(api_key):
    try:
        return api_key in get_allowed_keys() or api_key in get_admin_keys()
    except Exception:
        return False

def escape_html(text):
    return (str(text)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

# ============================================================
#  HISTORY
# ============================================================
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_history(h):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(h, f, indent=2)
    except Exception:
        pass

def add_history(event_type, details):
    history = load_history()
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "timestamp": int(datetime.now().timestamp()),
        "type": event_type,
        "details": details
    }
    history.insert(0, entry)
    save_history(history[:MAX_HISTORY])

# ============================================================
#  STATS
# ============================================================
def load_stats():
    if not os.path.exists(STATS_FILE):
        return {
            "total_likes": 0, "total_requests": 0, "total_errors": 0,
            "by_region": {r: {"likes": 0, "requests": 0} for r in SERVER_CONFIG.keys()},
            "by_key": {},
            "started": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
            "last_updated": None
        }
    try:
        with open(STATS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}

def save_stats(stats):
    stats["last_updated"] = datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    try:
        with open(STATS_FILE, "w") as f:
            json.dump(stats, f, indent=2)
    except Exception:
        pass

# ============================================================
#  GUEST DB
# ============================================================
def load_guests_db():
    if not os.path.exists(GUESTS_DB_PATH):
        return {r: [] for r in SERVER_CONFIG.keys()}
    try:
        with open(GUESTS_DB_PATH) as f:
            return json.load(f)
    except Exception:
        return {r: [] for r in SERVER_CONFIG.keys()}

def _build_guest_lookup():
    db = load_guests_db()
    lookup = {}
    for region, accounts in db.items():
        for acc in accounts:
            uid = str(acc.get("uid", ""))
            if uid:
                lookup[uid] = {
                    "password": str(acc.get("password", "")),
                    "level": int(acc.get("level", 8)),
                    "region": region
                }
    return lookup

# ============================================================
#  JWT HELPERS
# ============================================================
def _extract_jwt_list(data):
    tokens = []
    def is_jwt(s):
        return isinstance(s, str) and len(s) > 50 and s.count(".") == 2
    def push(v):
        if is_jwt(v):
            tokens.append(v)
    def handle(item):
        if isinstance(item, str):
            push(item); return
        if isinstance(item, dict):
            for k in ("token", "jwt_token", "jwt", "access_token"):
                if k in item:
                    push(item[k]); return
            for v in item.values():
                if isinstance(v, str):
                    push(v); return
    if isinstance(data, list):
        for x in data:
            handle(x)
    elif isinstance(data, dict):
        for w in ("tokens", "jwt_tokens", "data", "items"):
            if w in data and isinstance(data[w], list):
                for x in data[w]:
                    handle(x)
                if tokens:
                    return tokens
        for k, v in data.items():
            if isinstance(v, str):
                push(v)
            elif isinstance(v, dict):
                for kk in ("token", "jwt_token", "jwt"):
                    if kk in v:
                        push(v[kk]); break
            elif isinstance(v, list):
                for x in v:
                    handle(x)
    return tokens

def _decode_jwt_uid(token):
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        return d.get("external_uid") or d.get("account_id")
    except Exception:
        return None

def _decode_jwt_payload(token):
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}

def _load_jwt_for_server(region):
    cfg = SERVER_CONFIG.get(region.upper())
    if not cfg:
        return []
    path = os.path.join(BASE_DIR, cfg["jwt"])
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            return _extract_jwt_list(json.load(f))
    except Exception:
        return []

def _save_jwt_for_server(region, tokens):
    cfg = SERVER_CONFIG.get(region.upper())
    if not cfg:
        return 0
    path = os.path.join(BASE_DIR, cfg["jwt"])
    fmt = [{"token": t} for t in tokens if t]
    try:
        with open(path, "w") as f:
            json.dump(fmt, f, indent=2)
        return len(fmt)
    except Exception:
        return 0

def _load_accounts_for_server(region):
    cfg = SERVER_CONFIG.get(region.upper())
    if not cfg:
        return []
    path = os.path.join(BASE_DIR, cfg["accounts"])
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or ":" not in ln:
                continue
            uid, pw = ln.split(":", 1)
            uid, pw = uid.strip(), pw.strip()
            if uid.lower() == "uid" or pw.lower() == "password":
                continue
            if uid and pw:
                out.append((uid, pw))
    return out

# ============================================================
#  FF HELPERS
# ============================================================
def encrypt_message(plaintext):
    k = b'Yg&tc%DEuh6%Zc^8'
    iv = b'6oyZDr22E3ychjM%'
    return binascii.hexlify(AES.new(k, AES.MODE_CBC, iv).encrypt(pad(plaintext, 16))).decode()

def create_protobuf_message(uid, region):
    m = like_pb2.like()
    m.uid = int(uid)
    m.region = region
    return m.SerializeToString()

def create_protobuf(uid):
    m = uid_generator_pb2.uid_generator()
    m.krishna_ = int(uid)
    m.teamXdarks = 1
    return m.SerializeToString()

def enc(uid):
    return encrypt_message(create_protobuf(uid))

def _like_url_for(s):
    s = s.upper()
    if s == "IND":
        return "https://client.ind.freefiremobile.com/LikeProfile"
    if s in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com/LikeProfile"
    return "https://clientbp.ggpolarbear.com/LikeProfile"

def _show_url_for(s):
    s = s.upper()
    if s == "IND":
        return "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    if s in {"BR", "US", "SAC", "NA"}:
        return "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    return "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"

def make_request(encrypted, region, token, timeout=REQUEST_TIMEOUT):
    url = _show_url_for(region)
    edata = bytes.fromhex(encrypted)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': RELEASE_VERSION
    }
    try:
        resp = requests.post(url, data=edata, headers=headers, verify=False, timeout=timeout)
        obj = like_count_pb2.Info()
        obj.ParseFromString(bytes.fromhex(resp.content.hex()))
        return obj
    except Exception:
        return None

def _parse_account_info(pb_obj):
    try:
        if pb_obj is None:
            return None
        js = json.loads(MessageToJson(pb_obj))
        ai = js.get("AccountInfo", {})
        uid = int(ai.get("UID", 0))
        likes = int(ai.get("Likes", 0))
        name = str(ai.get("PlayerNickname", ""))
        if uid <= 0:
            return None
        return {"uid": uid, "likes": likes, "name": name}
    except Exception:
        return None

# ============================================================
#  ✅ REPORT TO BOT — Token runtime pe bot se aata hai
# ============================================================
def send_report_to_bot(target_uid, nickname, region, likes_given, before, after,
                       gift, elapsed, total_tokens, success_tokens):
    """Send report directly to Telegram Bot API — token bot se fetched"""
    token, admin_id = get_tg_config()
    if not token or not admin_id:
        print("[REPORT] ❌ No TG config available (bot offline?)")
        return False

    telegram_api = f"https://api.telegram.org/bot{token}/sendMessage"

    msg = "📊 <b>LIKE REPORT</b>\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"🎯 <b>Target:</b> <code>{target_uid}</code>\n"
    msg += f"👤 <b>Nickname:</b> <code>{escape_html(nickname)}</code>\n"
    msg += f"🌍 <b>Region:</b> <code>{region}</code>\n\n"
    msg += f"❤️ <b>Likes:</b> <code>+{likes_given}</code>\n"
    msg += f"📈 <b>{before} → {after}</b>\n"
    msg += f"🎁 <b>Gifts:</b> <code>{gift}</code>\n"
    msg += f"⏱ <b>Time:</b> <code>{elapsed}s</code>\n\n"
    msg += f"🎫 <b>Tokens:</b> <code>{len(success_tokens)}/{total_tokens}</code>\n"
    msg += f"🕐 <code>{datetime.now().strftime('%Y-%m-%d %I:%M:%S %p')}</code>\n\n"
    msg += "━━━━━━━━━━━━━━━━━━━━\n\n"

    if success_tokens:
        msg += f"✅ <b>SUCCESS UIDs ({len(success_tokens)})</b>\n\n"
        for t in success_tokens[:50]:
            uid_val = t.get("token_uid", "?")
            pwd_val = t.get("password", "")
            lvl = t.get("level", 8)
            msg += f"🔹 <code>{uid_val}</code> (Lvl {lvl})\n"
            if pwd_val and pwd_val != "NOT_FOUND":
                msg += f"    🔑 <code>{pwd_val}</code>\n"
            msg += "\n"
        if len(success_tokens) > 50:
            msg += f"<i>... +{len(success_tokens) - 50} more</i>\n"
    else:
        msg += "❌ <i>No success UIDs</i>"

    try:
        r = requests.post(
            telegram_api,
            json={
                "chat_id": admin_id,
                "text": msg,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            },
            timeout=15
        )
        if r.status_code == 200:
            print(f"[REPORT] ✅ Sent to Telegram (admin {admin_id})")
            return True
        print(f"[REPORT] ❌ TG API failed: {r.status_code} {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[REPORT] ❌ {e}")
        return False

# ============================================================
#  HTML
# ============================================================
INDEX_HTML = """
<!DOCTYPE html><html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{brand}} v{{version}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;font-family:'Segoe UI',sans-serif}
body{background:linear-gradient(135deg,#0a0a0f,#1a1a2e);color:#fff;min-height:100vh;padding:30px 20px;display:flex;flex-direction:column;align-items:center}
.brand{font-size:28px;font-weight:800;background:linear-gradient(90deg,#ffd700,#ff8c00);-webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:3px;margin-bottom:6px}
.subtitle{font-size:11px;color:#888;letter-spacing:4px;margin-bottom:20px}
.badge{background:linear-gradient(90deg,#ffd700,#ff8c00);color:#000;padding:4px 12px;border-radius:15px;font-size:10px;font-weight:700;letter-spacing:1px;margin-bottom:20px}
.card{background:rgba(20,20,30,.95);border:1px solid #ffd70033;border-radius:12px;padding:25px;width:100%;max-width:440px;box-shadow:0 8px 40px #ffd70011;margin-bottom:20px}
label{display:block;font-size:11px;color:#ffd700;letter-spacing:2px;margin-bottom:8px;text-transform:uppercase;font-weight:700}
input{width:100%;padding:12px 15px;background:#0009;border:1px solid #ffd70055;border-radius:8px;color:#fff;font-size:14px;outline:none;margin-bottom:15px}
input:focus{border-color:#ffd700}
.regions{display:flex;gap:6px;margin-bottom:15px;flex-wrap:wrap}
.rbtn{flex:1;padding:10px;background:#0009;border:1px solid #ffd70055;border-radius:8px;color:#fff;font-weight:600;cursor:pointer;min-width:55px;text-align:center;font-size:12px}
.rbtn.active{background:linear-gradient(90deg,#ffd700,#ff8c00);color:#000;border-color:#ffd700}
.btn{width:100%;padding:14px;margin-top:10px;background:linear-gradient(90deg,#ffd700,#ff8c00);color:#000;font-weight:800;border:none;border-radius:8px;font-size:15px;cursor:pointer;letter-spacing:2px}
.btn:disabled{opacity:.5}
.btn-sm{padding:8px 14px;background:#ffd700;color:#000;border:none;border-radius:6px;font-weight:700;cursor:pointer;font-size:12px}
.result{margin-top:20px;padding:15px;border-radius:8px;display:none;font-size:13px;line-height:1.7}
.result.show{display:block}
.result.ok{background:#0d3d1f;border:1px solid #00cc66;color:#00ff88}
.result.err{background:#3d0d0d;border:1px solid #cc0000;color:#ff6666}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.cell{background:#0a0a15;padding:8px;border-radius:6px}
.cell-lbl{font-size:10px;color:#888;text-transform:uppercase;letter-spacing:1px}
.cell-val{font-size:13px;color:#fff;font-weight:700}
.cell-val.green{color:#00ff88}
.cell-val.gold{color:#ffd700}
.row{display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid #ffd7001a;font-size:13px}
.row:last-child{border:none}
.row .label{color:#888}
.row .value{color:#00ff88;font-weight:700}
.row .value.big{color:#ffd700;font-size:15px}
.history-item{padding:10px 0;border-bottom:1px solid #ffd7001a;font-size:12px}
.history-time{color:#888;font-size:10px;margin-top:2px}
.history-type{color:#ffd700;font-weight:700}
.sec-title{color:#ffd700;font-size:13px;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:15px;display:flex;justify-content:space-between;align-items:center}
.footer{color:#444;font-size:11px;margin-top:25px;text-align:center}
.big-like{text-align:center;padding:20px;background:linear-gradient(135deg,#0d3d1f,#0a2a15);border-radius:12px;border:2px solid #00ff88;margin-bottom:15px}
.big-like-val{font-size:42px;font-weight:900;color:#00ff88;line-height:1}
.big-like-lbl{font-size:12px;color:#00ff88;letter-spacing:3px;margin-bottom:5px}
.big-like-sub{font-size:11px;color:#888;margin-top:8px}
.tok-success{background:#0d3d1f;padding:8px;border-radius:6px;margin-bottom:5px;font-size:11px}
.tok-line{display:flex;justify-content:space-between;margin-bottom:3px}
.tok-line .uid{color:#fff;font-family:monospace}
.tok-line .pwd{color:#ffd700;font-family:monospace;font-size:10px}
.badge-ok{background:#0f0;color:#000;padding:1px 6px;border-radius:8px;font-size:9px;font-weight:700}
</style></head><body>

<div class="brand">{{brand}}</div>
<div class="subtitle">VIP FREE FIRE LIKE SERVICE · v{{version}}</div>
<div class="badge">{{badge}}</div>

<div class="card">
  <label>Player UID (Target)</label>
  <input id="uid" placeholder="Enter target UID">
  <label>API Key</label>
  <input id="key" placeholder="API key">
  <label>Region</label>
  <div class="regions">
    <div class="rbtn" data-r="BD">BD</div>
    <div class="rbtn active" data-r="IND">IND</div>
    <div class="rbtn" data-r="BR">BR</div>
    <div class="rbtn" data-r="US">US</div>
    <div class="rbtn" data-r="SAC">SAC</div>
    <div class="rbtn" data-r="NA">NA</div>
  </div>
  <button class="btn" id="send">🚀 SEND LIKES</button>
  <div class="result" id="res"></div>
</div>

<div class="card">
  <div class="sec-title"><span>💎 Remaining (Level-Based)</span>
    <button class="btn-sm" onclick="loadRemain()">🔄</button></div>
  <div id="remainBox"><div class="row"><span class="label">Click 🔄</span><span class="value">-</span></div></div>
</div>

<div class="card">
  <div class="sec-title"><span>📊 Token Status</span>
    <button class="btn-sm" onclick="loadStatus()">🔄</button></div>
  <div id="tokenStatus">Loading...</div>
</div>

<div class="card">
  <div class="sec-title"><span>📈 Global Stats</span>
    <button class="btn-sm" onclick="loadStats()">🔄</button></div>
  <div id="statsBox">Loading...</div>
</div>

<div class="card">
  <div class="sec-title"><span>🔔 History (Last 30)</span>
    <button class="btn-sm" onclick="loadHistory()">🔄</button></div>
  <div id="history">Loading...</div>
</div>

<div class="footer">Developed by {{dev}} · {{owner}} · v{{version}}</div>

<script>
let r="IND";
document.querySelectorAll('.rbtn').forEach(b=>b.onclick=()=>{
  document.querySelectorAll('.rbtn').forEach(x=>x.classList.remove('active'));
  b.classList.add('active'); r=b.dataset.r;
});

document.getElementById('send').onclick=async()=>{
  const uid=document.getElementById('uid').value.trim();
  const key=document.getElementById('key').value.trim();
  const btn=document.getElementById('send'), res=document.getElementById('res');
  if(!uid||!key){alert('UID & Key daalo');return}
  btn.disabled=true; btn.textContent='⏳ SENDING...';
  res.className='result show ok';
  res.innerHTML='⏳ Processing (10-60s)...';
  const ctrl=new AbortController();
  const tid=setTimeout(()=>ctrl.abort(), 120000);
  try{
    const x=await fetch(`/api/like?uid=${uid}&server_name=${r}&key=${key}`, {signal:ctrl.signal});
    clearTimeout(tid);
    const d=await x.json();
    if(d.status===1){
      let html = `
        <div class="big-like">
          <div class="big-like-lbl">✅ LIKES SENT</div>
          <div class="big-like-val">+${d.LikesGivenByAPI}</div>
          <div class="big-like-sub">${d.PlayerNickname} · UID ${d.UID}</div>
        </div>
        <div class="grid">
          <div class="cell"><div class="cell-lbl">BEFORE</div><div class="cell-val">${d.LikesbeforeCommand}</div></div>
          <div class="cell"><div class="cell-lbl">AFTER</div><div class="cell-val green">${d.LikesafterCommand}</div></div>
          <div class="cell"><div class="cell-lbl">GIFTS</div><div class="cell-val gold">+${d.GiftCount}</div></div>
          <div class="cell"><div class="cell-lbl">TIME</div><div class="cell-val">${d.elapsed}s</div></div>
          <div class="cell"><div class="cell-lbl">TOKENS OK</div><div class="cell-val green">${d.tokens_success}/${d.tokens_total}</div></div>
          <div class="cell"><div class="cell-lbl">REQUESTS</div><div class="cell-val">${d.requests_sent || 0}</div></div>
        </div>`;
      if(d.success_tokens && d.success_tokens.length){
        html += `<div style="margin-top:15px;font-size:12px;color:#ffd700;font-weight:700">✅ SUCCESS UIDs (${d.tokens_success}):</div>`;
        d.success_tokens.slice(0,15).forEach(t=>{
          html += `<div class="tok-success">
            <div class="tok-line"><span class="uid">${t.token_uid}</span><span class="badge-ok">Lvl ${t.level||8}</span></div>
            <div class="tok-line"><span class="pwd">Pass: ${t.password||'?'}</span></div>
          </div>`;
        });
        if(d.success_tokens.length>15){
          html += `<div style="text-align:center;color:#888;font-size:11px;margin-top:5px">+${d.success_tokens.length-15} more</div>`;
        }
      }
      res.className='result show ok';
      res.innerHTML=html;
    } else if(d.status===0){
      res.className='result show err';
      res.innerHTML=`⚠️ No likes given<br>Player: ${d.PlayerNickname}<br>UID: ${d.UID}`;
    } else {
      res.className='result show err';
      res.innerHTML=`❌ ${d.error || 'Failed'}`;
    }
  }catch(e){
    clearTimeout(tid);
    res.className='result show err';
    res.textContent = e.name==='AbortError' ? '⏱ Timeout' : 'Error: '+e.message;
  } finally {
    btn.disabled=false; btn.textContent='🚀 SEND LIKES';
  }
};

async function loadRemain(){
  const box=document.getElementById('remainBox');
  box.innerHTML='<div class="row"><span class="label">⏳ Loading...</span><span class="value">-</span></div>';
  try{
    const x=await fetch(`/api/remain?region=${r}`);
    const d=await x.json();
    if(d.data && d.data[r]){
      const rd=d.data[r];
      let html = `
        <div class="row"><span class="label">🌍 Region</span><span class="value">${r}</span></div>
        <div class="row"><span class="label">🎫 Total Tokens</span><span class="value">${rd.total_tokens}</span></div>
        <div class="row"><span class="label">📊 Total Limit</span><span class="value">${rd.total_limit||0}</span></div>
        <div class="row"><span class="label">✅ Used Today</span><span class="value">${rd.total_used||0}</span></div>
        <div class="row"><span class="label">💎 Total Remaining</span><span class="value big">${rd.total_remaining}</span></div>
      `;
      if(rd.by_level){
        html += `<div style="margin-top:10px;font-size:11px;color:#ffd700">📊 BY LEVEL:</div>`;
        for(const [lvl, info] of Object.entries(rd.by_level)){
          html += `<div class="row"><span class="label">Lvl ${lvl} (${info.limit}/day)</span><span class="value">${info.remaining}/${info.limit}</span></div>`;
        }
      }
      box.innerHTML=html;
    } else {
      box.innerHTML='<div class="row"><span class="label">No data</span><span class="value">-</span></div>';
    }
  }catch(e){box.innerHTML='Error';}
}

async function loadStatus(){
  try{
    const x=await fetch('/api/tokens/status');
    const d=await x.json();
    let html='';
    let total=0;
    for(const [reg,cnt] of Object.entries(d.tokens)){
      const color = cnt==='missing'?'#ff6666':(cnt===0?'#ffaa00':'#00ff88');
      if(typeof cnt === 'number') total += cnt;
      html+=`<div class="row"><span class="label">🌍 ${reg}</span><span class="value" style="color:${color}">${cnt} tokens</span></div>`;
    }
    html += `<div class="row" style="border-top:1px solid #ffd70055;margin-top:5px;padding-top:8px">
      <span class="label" style="color:#ffd700">📦 TOTAL</span>
      <span class="value big">${total}</span>
    </div>`;
    document.getElementById('tokenStatus').innerHTML=html;
  }catch(e){document.getElementById('tokenStatus').innerHTML='Error';}
}

async function loadStats(){
  try{
    const x=await fetch('/api/stats');
    const d=await x.json();
    let html = `
      <div class="row"><span class="label">❤️ Total Likes</span><span class="value big">${d.total_likes||0}</span></div>
      <div class="row"><span class="label">📡 Total Requests</span><span class="value">${d.total_requests||0}</span></div>
      <div class="row"><span class="label">⚠️ Total Errors</span><span class="value">${d.total_errors||0}</span></div>
      <div class="row"><span class="label">🚀 Started</span><span class="value">${d.started||'?'}</span></div>
    `;
    document.getElementById('statsBox').innerHTML=html;
  }catch(e){document.getElementById('statsBox').innerHTML='Error';}
}

async function loadHistory(){
  try{
    const x=await fetch('/api/history');
    const d=await x.json();
    if(!d.history || !d.history.length){
      document.getElementById('history').innerHTML='<div class="history-time">No updates yet</div>';
      return;
    }
    let html='';
    d.history.forEach(h=>{
      html+=`<div class="history-item"><span class="history-type">${h.type}</span><br>${h.details}<div class="history-time">🕐 ${h.time}</div></div>`;
    });
    document.getElementById('history').innerHTML=html;
  }catch(e){document.getElementById('history').innerHTML='Error';}
}

loadStatus(); loadHistory(); loadStats(); loadRemain();
setInterval(loadStatus, 30000);
setInterval(loadHistory, 60000);
setInterval(loadStats, 60000);
</script>
</body></html>
"""

# ============================================================
#  ROUTES
# ============================================================
@app.get("/")
def index():
    return render_template_string(INDEX_HTML, brand=BRAND_NAME, dev=DEV_NAME,
                                  owner=OWNER_HANDLE, badge=BADGE_TEXT, version=VERSION)


@app.get("/health")
@app.get("/api/health")
def health():
    return _jsonify({"status": "ok", "service": BRAND_NAME,
                     "version": VERSION, "release": RELEASE_VERSION,
                     "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")})


# ============================================================
#  🔄 TG CONFIG ENDPOINTS (bot se sync)
# ============================================================
@app.get("/api/tg/refresh")
def api_tg_refresh():
    """Bot se dobara token fetch karo"""
    ok = fetch_tg_config_from_bot()
    if ok:
        return _jsonify({
            "status": "ok", "message": "Token refreshed from bot",
            "admin_id": TG_ADMIN_ID,
            "token_masked": f"{TG_TOKEN[:10]}...{TG_TOKEN[-6:]}"
        })
    return _jsonify({
        "status": "error",
        "message": "Bot not reachable",
        "bot_url": BOT_CONFIG_URL
    }), 503


@app.get("/api/tg/status")
def api_tg_status():
    """Current TG config status"""
    token, admin = get_tg_config()
    if not token:
        return _jsonify({
            "status": "error",
            "message": "No token available (bot offline)",
            "bot_url": BOT_CONFIG_URL
        })
    return _jsonify({
        "status": "ok",
        "token_masked": f"{token[:10]}...{token[-6:]}",
        "admin_id": admin,
        "source": "bot" if TG_TOKEN else "fallback"
    })


@app.get("/api/tokens/status")
def tokens_status():
    info = {}
    for region, cfg in SERVER_CONFIG.items():
        path = os.path.join(BASE_DIR, cfg["jwt"])
        if os.path.exists(path):
            try:
                with open(path) as f:
                    info[region] = len(_extract_jwt_list(json.load(f)))
            except Exception:
                info[region] = "error"
        else:
            info[region] = "missing"
    return _jsonify({"status": "ok", "tokens": info})


@app.get("/api/tokens/count")
def tokens_count():
    info = {}
    total = 0
    for region, cfg in SERVER_CONFIG.items():
        path = os.path.join(BASE_DIR, cfg["jwt"])
        cnt = 0
        if os.path.exists(path):
            try:
                with open(path) as f:
                    cnt = len(_extract_jwt_list(json.load(f)))
            except Exception:
                cnt = 0
        info[region] = cnt
        total += cnt
    return _jsonify({"status": "ok", "counts": info, "total": total})


@app.get("/api/history")
def api_history():
    return _jsonify({"status": "ok", "history": load_history()})


@app.get("/api/stats")
def api_stats():
    return _jsonify({"status": "ok", **load_stats()})


@app.get("/api/version")
def api_version():
    return _jsonify({"brand": BRAND_NAME, "version": VERSION,
                     "release": RELEASE_VERSION, "dev": DEV_NAME,
                     "owner": OWNER_HANDLE, "servers": list(SERVER_CONFIG.keys())})


@app.get("/api/ping")
def api_ping():
    return _jsonify({"pong": True, "time": int(_time.time() * 1000)})


@app.get("/api/guests/list")
def api_guests_list():
    db = load_guests_db()
    out = {}
    for region, arr in db.items():
        out[region] = {"count": len(arr), "uids": [a.get("uid") for a in arr[:50]]}
    return _jsonify({"status": "ok", "guests": out})


@app.get("/api/guests/count")
def api_guests_count():
    db = load_guests_db()
    counts = {r: len(arr) for r, arr in db.items()}
    return _jsonify({"status": "ok", "counts": counts, "total": sum(counts.values())})


@app.post("/api/token/check")
def api_token_check():
    data = request.get_json(force=True, silent=True) or {}
    token = (data.get("token") or "").strip()
    if not token:
        return _jsonify({"error": "no token"}), 400
    uid = _decode_jwt_uid(token)
    payload = _decode_jwt_payload(token)
    exp = payload.get("exp", 0)
    now = int(_time.time())
    region = payload.get("noti_region") or "IND"
    return _jsonify({
        "status": "ok", "uid": str(uid), "region": region,
        "expires_at": datetime.fromtimestamp(exp).strftime("%Y-%m-%d %I:%M:%S %p") if exp else "N/A",
        "valid": exp > now, "seconds_left": max(0, exp - now)
    })


@app.post("/api/tokens/check")
def api_tokens_check():
    data = request.get_json(force=True, silent=True) or {}
    tokens = data.get("tokens", [])
    if not isinstance(tokens, list):
        return _jsonify({"error": "tokens must be list"}), 400
    now = int(_time.time())
    out = []
    valid = 0
    expired = 0
    for t in tokens[:500]:
        uid = _decode_jwt_uid(t)
        payload = _decode_jwt_payload(t)
        exp = payload.get("exp", 0)
        is_valid = exp > now
        if is_valid:
            valid += 1
        else:
            expired += 1
        out.append({"uid": str(uid), "region": payload.get("noti_region", "?"),
                    "valid": is_valid, "seconds_left": max(0, exp - now)})
    return _jsonify({"status": "ok", "total": len(out),
                     "valid": valid, "expired": expired, "results": out})


@app.post("/api/tokens/push")
def tokens_push():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        add_history("❌ Push Rejected", "Unauthorized")
        return _jsonify({"error": "unauthorized"}), 401
    try:
        data = request.get_json(force=True, silent=True) or {}
        tokens = data.get("tokens", {})
        source = data.get("source", "bot")
        req_server = (data.get("server") or "").upper().strip()
        if not tokens or not isinstance(tokens, dict):
            add_history("❌ Push Rejected", "No tokens")
            return _jsonify({"error": "no tokens"}), 400
        valid_regions = set(SERVER_CONFIG.keys())
        def is_jwt(s):
            return (isinstance(s, str) and len(s) > 50
                    and s.count(".") == 2 and s.startswith("eyJ"))
        written = {}
        total = 0
        invalid = 0
        rejected = []
        for rk, jl in tokens.items():
            rk = rk.upper()
            if rk not in valid_regions:
                rejected.append(rk); continue
            if not isinstance(jl, list):
                continue
            vj = [t for t in jl if is_jwt(t)]
            invalid += len(jl) - len(vj)
            if not vj:
                continue
            cnt = _save_jwt_for_server(rk, vj)
            written[rk] = cnt
            total += cnt
        if total == 0:
            add_history("❌ Push Rejected", f"Invalid: {invalid} | Rejected: {rejected}")
            return _jsonify({"error": "no valid tokens"}), 400
        summary = ", ".join(f"{r}:{c}" for r, c in written.items() if c > 0)
        add_history("✅ Token Update", f"{source} | {total} tokens | {summary}")
        return _jsonify({"status": "ok", "written": written, "total": total,
                         "invalid_skipped": invalid})
    except Exception as e:
        add_history("❌ Push Error", str(e)[:100])
        return _jsonify({"error": str(e)}), 500


@app.post("/api/tokens/upload")
def tokens_upload():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    try:
        data = request.get_json(force=True, silent=True) or {}
        region = (data.get("region") or "").upper()
        tokens = data.get("tokens", [])
        if not region or region not in SERVER_CONFIG:
            return _jsonify({"error": "invalid region"}), 400
        if not isinstance(tokens, list):
            return _jsonify({"error": "tokens must be list"}), 400
        def is_jwt(s):
            return isinstance(s, str) and len(s) > 50 and s.count(".") == 2 and s.startswith("eyJ")
        valid = [t for t in tokens if is_jwt(t)]
        invalid = len(tokens) - len(valid)
        if not valid:
            return _jsonify({"error": "no valid JWTs", "invalid": invalid}), 400
        cnt = _save_jwt_for_server(region, valid)
        add_history("📥 Direct Upload", f"Region: {region} | {cnt} tokens | Invalid: {invalid}")
        return _jsonify({"status": "ok", "region": region, "uploaded": cnt, "invalid_skipped": invalid})
    except Exception as e:
        return _jsonify({"error": str(e)}), 500


@app.get("/api/tokens/get")
def tokens_get():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    region = request.args.get("region", "").upper()
    if not region or region not in SERVER_CONFIG:
        return _jsonify({"error": "invalid region"}), 400
    tokens = _load_jwt_for_server(region)
    return _jsonify({"status": "ok", "region": region,
                     "count": len(tokens), "tokens": tokens})


@app.post("/api/tokens/clear")
def tokens_clear():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    region = (data.get("region") or "").upper()
    if not region or region not in SERVER_CONFIG:
        return _jsonify({"error": "invalid region"}), 400
    _save_jwt_for_server(region, [])
    add_history("🗑️ Token Clear", f"Region: {region}")
    return _jsonify({"status": "ok", "cleared": region})


@app.get("/remain")
@app.get("/api/remain")
def remain_info():
    region = (request.args.get("region") or "IND").upper()
    if region not in SERVER_CONFIG:
        return _jsonify({"error": "invalid region"}), 400
    tokens = _load_jwt_for_server(region)
    guest_lookup = _build_guest_lookup()
    total_limit = 0
    total_used = 0
    by_level = {}
    for token in tokens:
        uid = str(_decode_jwt_uid(token) or "")
        if not uid:
            continue
        level = guest_lookup.get(uid, {}).get("level", 8)
        limit = get_level_limit(level)
        used = _get_used_per_token(uid)
        total_limit += limit
        total_used += used
        lvl_key = str(level)
        if lvl_key not in by_level:
            by_level[lvl_key] = {"count": 0, "limit": 0, "used": 0, "remaining": 0}
        by_level[lvl_key]["count"] += 1
        by_level[lvl_key]["limit"] += limit
        by_level[lvl_key]["used"] += used
        by_level[lvl_key]["remaining"] += max(0, limit - used)
    total_remaining = max(0, total_limit - total_used)
    return _jsonify({
        "status": "ok",
        "data": {region: {
            "total_tokens": len(tokens), "total_limit": total_limit,
            "total_used": total_used, "total_remaining": total_remaining,
            "by_level": by_level
        }},
        "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "note": "Level-based limits: 8→20, 21→200, 31→300"
    })


@app.get("/api/remain/detailed")
def remain_detailed():
    region = (request.args.get("region") or "IND").upper()
    if region not in SERVER_CONFIG:
        return _jsonify({"error": "invalid region"}), 400
    tokens = _load_jwt_for_server(region)
    if not tokens:
        return _jsonify({"status": "ok", "data": {}, "note": "No tokens"})
    limit = min(len(tokens), 50)
    check = tokens[:limit]
    def fetch_one(token, idx):
        try:
            uid = _decode_jwt_uid(token)
            if not uid:
                return {"index": idx, "status": "invalid"}
            enc_uid = enc(uid)
            info = _parse_account_info(make_request(enc_uid, region, token))
            if info:
                rem = max(0, 200 - info["likes"])
                return {"index": idx, "uid": info["uid"], "nickname": info["name"],
                        "likes": info["likes"], "remaining": rem, "status": "valid"}
            return {"index": idx, "uid": uid, "status": "expired"}
        except Exception as e:
            return {"index": idx, "status": str(e)[:30]}
    infos = [None] * limit
    with ThreadPoolExecutor(max_workers=20) as ex:
        futs = {ex.submit(fetch_one, t, i+1): i for i, t in enumerate(check)}
        for f in as_completed(futs):
            i = futs[f]
            try:
                infos[i] = f.result()
            except Exception:
                infos[i] = {"index": i+1, "status": "error"}
    valid = sum(1 for x in infos if x and x.get("status") == "valid")
    total_rem = sum(x.get("remaining", 0) for x in infos if x and x.get("status") == "valid")
    scale = len(tokens) / limit if limit else 1
    return _jsonify({"status": "ok", "region": region, "total_tokens": len(tokens),
                     "checked": limit, "valid": valid,
                     "total_remaining_estimate": int(total_rem * scale), "tokens": infos})


@app.get("/like")
@app.get("/api/like")
def handle_like():
    try:
        t_start = _time.time()
        uid = request.args.get("uid")
        server_name = (request.args.get("server_name") or request.args.get("region") or "").upper()
        api_key = request.args.get("key", "").strip()
        if not api_key or not is_valid_key(api_key):
            return _jsonify({"error": "Invalid API key 🔑"}), 403
        if not uid or not server_name:
            return _jsonify({"error": "UID and server_name required"}), 400
        if server_name not in SERVER_CONFIG:
            return _jsonify({"error": "Unsupported region"}), 400
        tokens = _load_jwt_for_server(server_name)
        if not tokens:
            return _jsonify({"error": "No tokens available"}), 500
        guest_lookup = _build_guest_lookup()
        first_token = tokens[0]
        encrypted = enc(uid)
        before = _parse_account_info(make_request(encrypted, server_name, first_token))
        if before is None:
            return _jsonify({
                "LikesGivenByAPI": 0, "LikesafterCommand": 0, "LikesbeforeCommand": 0,
                "PlayerNickname": "Unknown", "UID": uid, "GiftCount": 0,
                "tokens_total": len(tokens), "tokens_success": 0, "tokens_failed": 0,
                "success_tokens": [], "failed_tokens": [], "server_name": server_name,
                "status": 0, "elapsed": round(_time.time() - t_start, 2)
            })
        url = _like_url_for(server_name)
        msg = create_protobuf_message(uid, server_name)
        enc_uid = encrypt_message(msg)

        async def run_per_token():
            sem = asyncio.Semaphore(LIKE_CONCUR)
            connector = aiohttp.TCPConnector(limit=LIKE_CONCUR, ssl=False)

            async def try_token(token, idx):
                async with sem:
                    tok_uid = str(_decode_jwt_uid(token) or "unknown")
                    guest_info = guest_lookup.get(tok_uid, {})
                    tok_pwd = guest_info.get("password", "NOT_FOUND")
                    tok_level = int(guest_info.get("level", 8))
                    try:
                        edata = bytes.fromhex(enc_uid)
                        headers = {
                            'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
                            'Authorization': f"Bearer {token}",
                            'Content-Type': "application/x-www-form-urlencoded",
                            'X-Unity-Version': "2018.4.11f1",
                            'X-GA': "v1 1",
                            'ReleaseVersion': RELEASE_VERSION
                        }
                        async with aiohttp.ClientSession(connector=connector) as s:
                            async with s.post(url, data=edata, headers=headers,
                                              timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)) as r:
                                return {"index": idx, "token_uid": tok_uid,
                                        "password": tok_pwd, "level": tok_level,
                                        "http_status": r.status,
                                        "status": "success" if r.status == 200 else f"failed_{r.status}"}
                    except Exception:
                        return {"index": idx, "token_uid": tok_uid, "password": tok_pwd,
                                "level": tok_level, "http_status": 0, "status": "error"}

            tasks = []
            for i, t in enumerate(tokens):
                tok_uid = str(_decode_jwt_uid(t) or "")
                lvl = guest_lookup.get(tok_uid, {}).get("level", 8)
                if lvl >= 31:
                    repeats = 10
                elif lvl >= 21:
                    repeats = 5
                else:
                    repeats = 2
                for _ in range(repeats):
                    tasks.append(asyncio.create_task(try_token(t, i+1)))
            return await asyncio.gather(*tasks)

        per_token_results = asyncio.run(run_per_token())
        token_map = {}
        for r in per_token_results:
            uid_k = r["token_uid"]
            if uid_k not in token_map:
                token_map[uid_k] = r
            elif r["status"] == "success":
                token_map[uid_k] = r
        unique_results = list(token_map.values())
        success_tokens = [r for r in unique_results if r["status"] == "success"]
        failed_tokens = [r for r in unique_results if r["status"] != "success"]
        after = _parse_account_info(make_request(encrypted, server_name, first_token)) or {
            "likes": before["likes"], "uid": before["uid"], "name": before["name"]}
        like_given = max(0, int(after["likes"]) - int(before["likes"]))
        gift = like_given * 2 if like_given > 0 else 0
        for t in success_tokens:
            _increment_used_per_token(t["token_uid"])
        if success_tokens:
            try:
                send_report_to_bot(uid, str(after["name"]), server_name, like_given,
                                   int(before["likes"]), int(after["likes"]), gift,
                                   round(_time.time() - t_start, 2), len(tokens), success_tokens)
            except Exception as e:
                print(f"[REPORT] {e}")
        add_history("❤️ Like Sent",
                    f"Target: {after['name']} ({after['uid']}) | +{like_given} | {len(success_tokens)}/{len(tokens)} tokens")
        return _jsonify({
            "LikesGivenByAPI": like_given,
            "LikesafterCommand": int(after["likes"]),
            "LikesbeforeCommand": int(before["likes"]),
            "PlayerNickname": str(after["name"]),
            "UID": int(after["uid"]),
            "GiftCount": gift,
            "server_name": server_name,
            "status": 1 if like_given > 0 else 2,
            "elapsed": round(_time.time() - t_start, 2),
            "tokens_total": len(tokens),
            "tokens_success": len(success_tokens),
            "tokens_failed": len(failed_tokens),
            "requests_sent": len(per_token_results),
            "success_tokens": success_tokens[:50],
            "failed_tokens": failed_tokens[:50]
        })
    except Exception as e:
        return _jsonify({"error": "runtime_error", "detail": str(e)}), 500


@app.get("/api/player/info")
def api_player_info():
    uid = request.args.get("uid")
    region = (request.args.get("region") or "IND").upper()
    api_key = request.args.get("key", "").strip()
    if not api_key or not is_valid_key(api_key):
        return _jsonify({"error": "Invalid API key"}), 403
    if not uid or region not in SERVER_CONFIG:
        return _jsonify({"error": "uid required"}), 400
    tokens = _load_jwt_for_server(region)
    if not tokens:
        return _jsonify({"error": "No tokens"}), 500
    encrypted = enc(uid)
    info = _parse_account_info(make_request(encrypted, region, tokens[0]))
    if info:
        return _jsonify({"status": "ok", **info})
    return _jsonify({"status": "error", "error": "Not found"}), 404


@app.get("/api/player/check")
def api_player_check():
    uid = request.args.get("uid")
    region = (request.args.get("region") or "IND").upper()
    if not uid or region not in SERVER_CONFIG:
        return _jsonify({"error": "uid required"}), 400
    tokens = _load_jwt_for_server(region)
    if not tokens:
        return _jsonify({"exists": False, "error": "no tokens"}), 500
    encrypted = enc(uid)
    info = _parse_account_info(make_request(encrypted, region, tokens[0]))
    return _jsonify({"exists": info is not None, "uid": uid, "region": region, "data": info})


@app.get("/api/history/likes")
def api_history_likes():
    history = load_history()
    likes = [h for h in history if "Like" in h.get("type", "")]
    return _jsonify({"status": "ok", "likes": likes[:20]})


@app.post("/api/jwt/generate")
def api_jwt_generate():
    data = request.get_json(force=True, silent=True) or {}
    uid = str(data.get("uid", "")).strip()
    pwd = str(data.get("password", "")).strip()
    if not uid or not pwd:
        return _jsonify({"error": "uid & password required"}), 400
    try:
        r = requests.get(JWT_API_BASE, params={"uid": uid, "password": pwd}, timeout=30)
        if r.status_code == 200:
            return _jsonify({"status": "ok", "response": r.json()})
        return _jsonify({"error": f"HTTP {r.status_code}"}), 500
    except Exception as e:
        return _jsonify({"error": str(e)}), 500


@app.post("/api/jwt/generate/bulk")
def api_jwt_generate_bulk():
    data = request.get_json(force=True, silent=True) or {}
    accounts = data.get("accounts", [])
    if not isinstance(accounts, list) or not accounts:
        return _jsonify({"error": "accounts list required"}), 400
    accounts = accounts[:200]
    tuples = [(str(a.get("uid", "")), str(a.get("password", ""))) for a in accounts if a.get("uid")]

    def fetch_one(t):
        uid, pwd = t
        try:
            r = requests.get(JWT_API_BASE, params={"uid": uid, "password": pwd}, timeout=60)
            if r.status_code == 200:
                j = r.json()
                return j.get("token") or j.get("jwt_token")
        except Exception:
            pass
        return None

    tokens = []
    with ThreadPoolExecutor(max_workers=30) as ex:
        for res in ex.map(fetch_one, tuples):
            if res:
                tokens.append(res)
    return _jsonify({"status": "ok", "total": len(tuples),
                     "success": len(tokens), "tokens": tokens})


@app.post("/api/admin/add-guest")
def api_admin_add_guest():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    region = (data.get("region") or "").upper()
    uid = str(data.get("uid", "")).strip()
    pwd = str(data.get("password", "")).strip()
    level = int(data.get("level", 8))
    if not region or region not in SERVER_CONFIG or not uid or not pwd:
        return _jsonify({"error": "region, uid, password required"}), 400
    db = load_guests_db()
    existing = {(a.get("uid"), a.get("password")) for a in db.get(region, [])}
    if (uid, pwd) in existing:
        return _jsonify({"status": "exists"})
    db.setdefault(region, []).append({"uid": uid, "password": pwd, "level": level})
    with open(GUESTS_DB_PATH, "w") as f:
        json.dump(db, f, indent=2)
    return _jsonify({"status": "ok", "region": region, "uid": uid, "level": level})


@app.post("/api/admin/remove-guest")
def api_admin_remove_guest():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    region = (data.get("region") or "").upper()
    uid = str(data.get("uid", "")).strip()
    if not region or not uid:
        return _jsonify({"error": "region & uid required"}), 400
    db = load_guests_db()
    old = len(db.get(region, []))
    db[region] = [a for a in db.get(region, []) if str(a.get("uid")) != uid]
    new = len(db[region])
    with open(GUESTS_DB_PATH, "w") as f:
        json.dump(db, f, indent=2)
    return _jsonify({"status": "ok", "removed": old - new, "left": new})


@app.post("/api/admin/clear-guests")
def api_admin_clear_guests():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    data = request.get_json(force=True, silent=True) or {}
    region = (data.get("region") or "").upper()
    if not region or region not in SERVER_CONFIG:
        return _jsonify({"error": "valid region required"}), 400
    db = load_guests_db()
    old = len(db.get(region, []))
    db[region] = []
    with open(GUESTS_DB_PATH, "w") as f:
        json.dump(db, f, indent=2)
    return _jsonify({"status": "ok", "region": region, "cleared": old})


@app.post("/api/admin/clear-history")
def api_admin_clear_history():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    save_history([])
    return _jsonify({"status": "ok"})


@app.post("/api/admin/reset-stats")
def api_admin_reset_stats():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    stats = {
        "total_likes": 0, "total_requests": 0, "total_errors": 0,
        "by_region": {r: {"likes": 0, "requests": 0} for r in SERVER_CONFIG.keys()},
        "by_key": {},
        "started": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "last_updated": None
    }
    save_stats(stats)
    return _jsonify({"status": "ok"})


@app.get("/api/key/info")
def api_key_info():
    key = request.args.get("key", "").strip()
    if not key:
        return _jsonify({"error": "key required"}), 400
    try:
        allowed = get_allowed_keys()
        admin = get_admin_keys()
        if key in allowed:
            return _jsonify({"status": "ok", "type": "allowed", "limit": allowed.get(key, 0)})
        if key in admin:
            return _jsonify({"status": "ok", "type": "admin", "limit": 999999})
        return _jsonify({"status": "invalid"})
    except Exception as e:
        return _jsonify({"error": str(e)}), 500


@app.get("/api/leaderboard")
def api_leaderboard():
    stats = load_stats()
    by_key = stats.get("by_key", {})
    sorted_keys = sorted(by_key.items(), key=lambda x: x[1].get("likes", 0), reverse=True)
    board = [{"key": k, "likes": v.get("likes", 0), "requests": v.get("requests", 0)}
             for k, v in sorted_keys[:10]]
    return _jsonify({"status": "ok", "leaderboard": board})


@app.get("/api/bot/health")
def api_bot_health():
    return _jsonify({"status": "ok", "bot_friendly": True})


# ============================================================
#  RUN
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 {BRAND_NAME} v{VERSION} on port {port}")
    print(f"📋 Endpoints: 35+")
    print(f"🔗 Bot Config URL: {BOT_CONFIG_URL}")

    # ✅ Startup pe bot se token fetch karo
    print("🔄 Fetching TG config from bot...")
    if fetch_tg_config_from_bot():
        print(f"✅ TG config ready! Admin: {TG_ADMIN_ID}")
    else:
        print("⚠️ Bot offline — report baad me try hogi (fetch on demand)")

    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)