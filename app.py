# like_web.py — NIROBxFREExLIKE (Web + API + History + Remain)
import os
import json
import base64
import binascii
import asyncio
from threading import RLock
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, request, jsonify, Response, render_template_string
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

OWNER_HANDLE = "TG: @MT_0G"
DEV_NAME     = "NIROB"
TELEGRAM     = "@MT_0G"
BRAND_NAME   = "NIROBxFREExLIKE"
BADGE_TEXT   = "LIKE • API • KEY • NIROBxLIKE"
RELEASE_VERSION = "OB55"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JWT_API_BASE = "https://nirobxjwt.vercel.app/token"

JWT_WORKERS = 60
LIKE_CONCUR = 150
REPEATS_PER_TOKEN = 20

# Quota estimate per token (Garena ~ 200 per account)
DAILY_QUOTA_ESTIMATE = 200

API_KEY = "NirobAPI_Secret_2026_ChangeMe"   # ← strong password

HISTORY_FILE = os.path.join(BASE_DIR, "update_history.json")
MAX_HISTORY = 10

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

def _active_config_path_for_read():
    return CONFIG_RW_PATH if os.path.exists(CONFIG_RW_PATH) else CONFIG_RO_PATH

def _read_config():
    path = _active_config_path_for_read()
    if not os.path.exists(path):
        raise FileNotFoundError("keys.json not found")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_allowed_keys():
    with config_lock:
        return _read_config()["ALLOWED_KEYS"]

def get_admin_keys():
    with config_lock:
        return set(_read_config()["ADMIN_KEYS"])

def is_valid_key(api_key: str) -> bool:
    try:
        return api_key in get_allowed_keys() or api_key in get_admin_keys()
    except Exception:
        return False


# ============================================================
#  HISTORY
# ============================================================
def load_history():
    if not os.path.exists(HISTORY_FILE):
        return []
    try:
        with open(HISTORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return []

def save_history(h):
    try:
        with open(HISTORY_FILE, "w") as f:
            json.dump(h, f, indent=2)
    except Exception as e:
        print(f"[HISTORY] Save fail: {e}")

def add_history_event(event_type, details):
    history = load_history()
    entry = {
        "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "timestamp": int(datetime.now().timestamp()),
        "type": event_type,
        "details": details
    }
    history.insert(0, entry)
    history = history[:MAX_HISTORY]
    save_history(history)
    print(f"[HISTORY] {event_type} — {details}")
    return entry


# ============================================================
#  JWT HELPERS
# ============================================================
def _extract_jwt_list(data) -> list:
    tokens = []
    def is_jwt(s):
        return isinstance(s, str) and len(s) > 50 and s.count(".") == 2
    def push(v):
        if is_jwt(v): tokens.append(v)
    def handle_item(item):
        if isinstance(item, str): push(item); return
        if isinstance(item, dict):
            for key in ("token", "jwt_token", "jwt", "access_token"):
                if key in item: push(item[key]); return
            for v in item.values():
                if isinstance(v, str): push(v); return
    if isinstance(data, list):
        for item in data: handle_item(item)
    elif isinstance(data, dict):
        for w in ("tokens", "jwt_tokens", "data", "items"):
            if w in data and isinstance(data[w], list):
                for item in data[w]: handle_item(item)
                if tokens: return tokens
        for k, v in data.items():
            if isinstance(v, str): push(v)
            elif isinstance(v, dict):
                for key in ("token", "jwt_token", "jwt"):
                    if key in v: push(v[key]); break
            elif isinstance(v, list):
                for item in v: handle_item(item)
    return tokens


def _decode_jwt_payload(token):
    """JWT payload decode karo"""
    try:
        p = token.split(".")[1]
        p += "=" * (-len(p) % 4)
        return json.loads(base64.urlsafe_b64decode(p))
    except Exception:
        return {}


def _decode_jwt_uid(token):
    """JWT se UID nikalo"""
    d = _decode_jwt_payload(token)
    return d.get("external_uid") or d.get("account_id")


def _load_jwt_for_server(server_name: str) -> list:
    cfg = SERVER_CONFIG.get(server_name.upper())
    if not cfg: return []
    path = os.path.join(BASE_DIR, cfg["jwt"])
    if not os.path.exists(path): return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _extract_jwt_list(json.load(f))
    except Exception as e:
        print(f"[!] Load fail {cfg['jwt']}: {e}")
        return []


def _save_jwt_for_server(server_name: str, tokens: list) -> int:
    cfg = SERVER_CONFIG.get(server_name.upper())
    if not cfg: return 0
    path = os.path.join(BASE_DIR, cfg["jwt"])
    formatted = [{"token": t} for t in tokens if t]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(formatted, f, indent=2)
        return len(formatted)
    except Exception as e:
        print(f"[!] Save fail {path}: {e}")
        return 0


def _load_accounts_for_server(server_name: str) -> list:
    cfg = SERVER_CONFIG.get(server_name.upper())
    if not cfg: return []
    path = os.path.join(BASE_DIR, cfg["accounts"])
    if not os.path.exists(path): return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if not ln or ln.startswith("#") or ":" not in ln: continue
            uid, pw = ln.split(":", 1)
            uid, pw = uid.strip(), pw.strip()
            if uid.lower() == "uid" or pw.lower() == "password": continue
            if uid and pw: out.append((uid, pw))
    return out


# ============================================================
#  FALLBACK JWT
# ============================================================
def _fetch_single_jwt(uid, pw, timeout=60):
    try:
        r = requests.get(JWT_API_BASE, params={"uid": uid, "password": pw},
                        timeout=timeout, verify=False)
        if r.status_code != 200: return uid, None
        j = r.json()
        token = None
        if isinstance(j, dict):
            token = j.get("jwt_token") or j.get("token") or j.get("access_token")
        return uid, token
    except Exception:
        return uid, None


def generate_jwts_live(accounts, max_workers=JWT_WORKERS):
    if not accounts: return []
    tokens = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_single_jwt, uid, pw): uid for uid, pw in accounts}
        for fut in as_completed(futures):
            uid, token = fut.result()
            if token: tokens.append(token)
    return tokens


# ============================================================
#  LIKE HELPERS
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


async def send_request(encrypted_uid, token, url, session_):
    edata = bytes.fromhex(encrypted_uid)
    headers = {
        'User-Agent': "Dalvik/2.1.0 (Linux; U; Android 9; ASUS_Z01QD Build/PI)",
        'Authorization': f"Bearer {token}",
        'Content-Type': "application/x-www-form-urlencoded",
        'X-Unity-Version': "2018.4.11f1",
        'X-GA': "v1 1",
        'ReleaseVersion': RELEASE_VERSION
    }
    try:
        async with session_.post(url, data=edata, headers=headers) as resp:
            return resp.status
    except Exception:
        return 0


async def _burst_all_tokens(uid, region, url, tokens, repeats=REPEATS_PER_TOKEN, concurrency=LIKE_CONCUR):
    msg = create_protobuf_message(uid, region)
    enc_uid = encrypt_message(msg)
    if not tokens: return []
    expanded = []
    for t in tokens: expanded.extend([t] * repeats)
    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession(connector=connector) as sess:
        async def worker(tok):
            async with sem:
                return await send_request(enc_uid, tok, url, sess)
        return await asyncio.gather(*[worker(t) for t in expanded])


def send_likes_from_all_tokens(uid, region, url, tokens, repeats=REPEATS_PER_TOKEN):
    if not tokens: return 0
    results = asyncio.run(_burst_all_tokens(uid, region, url, tokens, repeats))
    return sum(1 for s in results if s == 200)


def _like_url_for(s):
    s = s.upper()
    if s == "IND": return "https://client.ind.freefiremobile.com/LikeProfile"
    if s in {"BR", "US", "SAC", "NA"}: return "https://client.us.freefiremobile.com/LikeProfile"
    return "https://clientbp.ggpolarbear.com/LikeProfile"


def _show_url_for(s):
    s = s.upper()
    if s == "IND": return "https://client.ind.freefiremobile.com/GetPlayerPersonalShow"
    if s in {"BR", "US", "SAC", "NA"}: return "https://client.us.freefiremobile.com/GetPlayerPersonalShow"
    return "https://clientbp.ggpolarbear.com/GetPlayerPersonalShow"


def make_request(encrypted, region, token):
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
        resp = requests.post(url, data=edata, headers=headers, verify=False, timeout=30)
        obj = like_count_pb2.Info()
        obj.ParseFromString(bytes.fromhex(resp.content.hex()))
        return obj
    except Exception:
        return None


def _parse_account_info(pb_obj):
    try:
        if pb_obj is None: return None
        js = json.loads(MessageToJson(pb_obj))
        ai = js.get("AccountInfo", {})
        uid = int(ai.get("UID", 0))
        likes = int(ai.get("Likes", 0))
        name = str(ai.get("PlayerNickname", ""))
        if uid <= 0: return None
        return {"uid": uid, "likes": likes, "name": name}
    except Exception:
        return None


# ============================================================
#  HTML
# ============================================================
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{brand}}</title>
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
.row{display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #ffd7001a;font-size:13px}
.row:last-child{border:none}
.row .label{color:#888}
.row .value{color:#00ff88;font-weight:700}
.row .value.big{color:#ffd700;font-size:15px}
.history-item{padding:10px 0;border-bottom:1px solid #ffd7001a;font-size:12px}
.history-item:last-child{border:none}
.history-time{color:#888;font-size:10px;margin-top:2px}
.history-type{color:#ffd700;font-weight:700}
.history-type.err{color:#ff6666}
.footer{color:#444;font-size:11px;margin-top:25px;letter-spacing:2px;text-align:center}
.sec-title{color:#ffd700;font-size:13px;font-weight:700;letter-spacing:2px;text-transform:uppercase;margin-bottom:15px;display:flex;justify-content:space-between;align-items:center}
</style></head><body>
<div class="brand">{{brand}}</div>
<div class="subtitle">VIP FREE FIRE LIKE SERVICE</div>
<div class="badge">{{badge}}</div>

<div class="card">
  <label>Player UID</label>
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

<!-- REMAIN CARD -->
<div class="card">
  <div class="sec-title">
    <span>💎 Remaining Likes</span>
    <button class="btn-sm" onclick="loadRemain()">🔄 Check</button>
  </div>
  <div id="remainBox">
    <div class="row"><span class="label">Loading...</span><span class="value">-</span></div>
  </div>
</div>

<!-- TOKEN STATUS -->
<div class="card">
  <div class="sec-title">
    <span>📊 Token Status</span>
    <button class="btn-sm" onclick="loadStatus()">🔄</button>
  </div>
  <div id="tokenStatus">Loading...</div>
</div>

<!-- HISTORY -->
<div class="card">
  <div class="sec-title">
    <span>🔔 History (Last 10)</span>
    <button class="btn-sm" onclick="loadHistory()">🔄</button>
  </div>
  <div id="history">Loading...</div>
</div>

<div class="footer">Developed by {{dev}} · {{owner}}</div>

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
  try{
    const x=await fetch(`/like?uid=${uid}&server_name=${r}&key=${key}`);
    const d=await x.json();
    res.className='result show '+(d.status===1?'ok':'err');
    if(d.status===1) res.innerHTML=`✅ Likes: <b>${d.LikesGivenByAPI}</b><br>👤 ${d.PlayerNickname}<br>🌍 ${d.server_name}<br>🎫 Tokens: ${d.tokens_used}`;
    else res.innerHTML=`❌ ${d.error || 'Failed'}`;
  }catch(e){res.className='result show err';res.textContent='Error: '+e.message}
  finally{btn.disabled=false;btn.textContent='🚀 SEND LIKES'}
};

async function loadRemain(){
  const box = document.getElementById('remainBox');
  box.innerHTML = '<div class="row"><span class="label">⏳ Checking...</span><span class="value">-</span></div>';
  try{
    const x = await fetch(`/api/remain?region=${r}`);
    const d = await x.json();
    const rd = d.data[r];
    if(!rd){ box.innerHTML = '<div class="row"><span class="label">❌ No data</span><span class="value">-</span></div>'; return; }
    let html = `
      <div class="row"><span class="label">🌍 Region</span><span class="value">${r}</span></div>
      <div class="row"><span class="label">🎫 Total Tokens</span><span class="value">${rd.total_tokens}</span></div>
      <div class="row"><span class="label">✅ Valid Tokens</span><span class="value">${rd.valid_tokens}</span></div>
      <div class="row"><span class="label">💎 Total Remaining</span><span class="value big">${rd.total_remaining_estimate}</span></div>
    `;
    box.innerHTML = html;
  }catch(e){
    box.innerHTML = '<div class="row"><span class="label">❌ Error</span><span class="value">-</span></div>';
  }
}

async function loadStatus(){
  try{
    const x=await fetch('/api/tokens/status');
    const d=await x.json();
    let html='';
    for(const [reg,cnt] of Object.entries(d.tokens)){
      const color = cnt === 'missing' ? '#ff6666' : '#00ff88';
      html+=`<div class="row"><span class="label">🌍 ${reg}</span><span class="value" style="color:${color}">${cnt} tokens</span></div>`;
    }
    document.getElementById('tokenStatus').innerHTML=html;
  }catch(e){document.getElementById('tokenStatus').innerHTML='Error';}
}

async function loadHistory(){
  try{
    const x=await fetch('/api/history');
    const d=await x.json();
    if(!d.history || d.history.length===0){
      document.getElementById('history').innerHTML='<div class="history-time">No updates yet</div>';
      return;
    }
    let html='';
    d.history.forEach(h=>{
      const cls = h.type.toLowerCase().includes('fail') || h.type.toLowerCase().includes('reject') ? 'err' : '';
      html+=`<div class="history-item"><span class="history-type ${cls}">${h.type}</span><br>${h.details}<div class="history-time">🕐 ${h.time}</div></div>`;
    });
    document.getElementById('history').innerHTML=html;
  }catch(e){document.getElementById('history').innerHTML='Error';}
}

loadStatus();
loadHistory();
loadRemain();
setInterval(loadStatus, 60000);
setInterval(loadHistory, 60000);
</script>
</body></html>
"""


# ============================================================
#  ROUTES
# ============================================================
@app.get("/")
def index():
    return render_template_string(INDEX_HTML, brand=BRAND_NAME, dev=DEV_NAME,
                                  owner=OWNER_HANDLE, badge=BADGE_TEXT)


@app.get("/health")
@app.get("/api/health")
def health():
    return _jsonify({
        "status": "ok",
        "service": BRAND_NAME,
        "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "release": RELEASE_VERSION,
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
    return _jsonify({
        "status": "ok",
        "tokens": info,
        "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
    })


@app.get("/api/history")
def api_history():
    return _jsonify({"status": "ok", "history": load_history()})


# ============================================================
#  ✅ REMAIN — Total Remaining Likes
# ============================================================
@app.get("/remain")
@app.get("/api/remain")
def remain_info():
    """
    Per-token remaining likes check.
    Query: ?region=IND (optional — agar nahi diya, saare regions)
    """
    region = (request.args.get("region") or "").upper()

    if region and region not in SERVER_CONFIG:
        return _jsonify({
            "error": "invalid region",
            "allowed": list(SERVER_CONFIG.keys())
        }), 400

    results = {}
    regions_to_check = [region] if region else list(SERVER_CONFIG.keys())

    for r in regions_to_check:
        tokens = _load_jwt_for_server(r)
        if not tokens:
            results[r] = {
                "total_tokens": 0,
                "valid_tokens": 0,
                "total_remaining_estimate": 0,
                "tokens": []
            }
            continue

        token_infos = []
        for idx, token in enumerate(tokens, 1):
            uid = _decode_jwt_uid(token)
            if not uid:
                token_infos.append({
                    "index": idx,
                    "uid": "unknown",
                    "status": "invalid_token"
                })
                continue

            encrypted = enc(uid)
            info = _parse_account_info(make_request(encrypted, r, token))

            if info:
                remaining = max(0, DAILY_QUOTA_ESTIMATE - info["likes"])
                token_infos.append({
                    "index": idx,
                    "uid": info["uid"],
                    "nickname": info["name"],
                    "current_likes": info["likes"],
                    "estimated_quota": DAILY_QUOTA_ESTIMATE,
                    "remaining_estimate": remaining,
                    "status": "valid"
                })
            else:
                token_infos.append({
                    "index": idx,
                    "uid": uid,
                    "status": "expired_or_invalid"
                })

        valid_tokens = sum(1 for t in token_infos if t["status"] == "valid")
        total_remaining = sum(t.get("remaining_estimate", 0)
                              for t in token_infos if t["status"] == "valid")

        results[r] = {
            "total_tokens": len(tokens),
            "valid_tokens": valid_tokens,
            "total_remaining_estimate": total_remaining,
            "tokens": token_infos
        }

    return _jsonify({
        "status": "ok",
        "data": results,
        "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p"),
        "note": f"remaining_estimate = {DAILY_QUOTA_ESTIMATE} - current_likes (approx)"
    })


# ============================================================
#  TOKEN PUSH
# ============================================================
@app.post("/api/tokens/push")
def tokens_push():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        add_history_event("❌ Push Rejected", "Unauthorized API key")
        return _jsonify({"error": "unauthorized"}), 401

    try:
        data = request.get_json(force=True, silent=True) or {}
        tokens = data.get("tokens", {})
        source = data.get("source", "bot")
        requested_server = (data.get("server") or "").upper().strip()

        if not tokens or not isinstance(tokens, dict):
            add_history_event("❌ Push Rejected", "No tokens field")
            return _jsonify({"error": "no tokens field"}), 400

        valid_regions = set(SERVER_CONFIG.keys())
        if requested_server and requested_server not in valid_regions:
            add_history_event("❌ Push Rejected", f"Invalid server: {requested_server}")
            return _jsonify({"error": "invalid server",
                             "server": requested_server,
                             "allowed": list(valid_regions)}), 400

        def is_jwt(s):
            return (isinstance(s, str) and len(s) > 50
                    and s.count(".") == 2 and s.startswith("eyJ"))

        written = {}
        total = 0
        invalid_count = 0
        rejected_regions = []

        for region_key, jwt_list in tokens.items():
            region_key = region_key.upper()
            if region_key not in valid_regions:
                rejected_regions.append(region_key)
                continue
            if not isinstance(jwt_list, list):
                continue
            valid_jwts = [t for t in jwt_list if is_jwt(t)]
            invalid_count += len(jwt_list) - len(valid_jwts)
            if not valid_jwts:
                continue
            cnt = _save_jwt_for_server(region_key, valid_jwts)
            written[region_key] = cnt
            total += cnt

        if total == 0:
            err_details = []
            if rejected_regions:
                err_details.append(f"Invalid regions: {rejected_regions}")
            if invalid_count:
                err_details.append(f"{invalid_count} invalid JWTs")
            detail = " | ".join(err_details) or "No valid tokens"
            add_history_event("❌ Push Rejected", detail)
            return _jsonify({
                "error": "no valid tokens to save",
                "rejected_regions": rejected_regions,
                "invalid_count": invalid_count,
                "allowed_regions": list(valid_regions)
            }), 400

        regions_summary = ", ".join(f"{r}:{c}" for r, c in written.items() if c > 0)
        hist_msg = f"Source: {source} | Total: {total} | {regions_summary}"
        if rejected_regions:
            hist_msg += f" | ⚠️ Rejected: {rejected_regions}"
        if invalid_count:
            hist_msg += f" | ⚠️ {invalid_count} invalid"
        add_history_event("✅ Token Update", hist_msg)

        return _jsonify({
            "status": "ok",
            "written": written,
            "total": total,
            "rejected_regions": rejected_regions,
            "invalid_skipped": invalid_count,
            "time": datetime.now().strftime("%Y-%m-%d %I:%M:%S %p")
        })

    except Exception as e:
        add_history_event("❌ Push Error", str(e)[:200])
        return _jsonify({"error": str(e)}), 500


@app.get("/api/tokens/get")
def tokens_get():
    key = request.headers.get("X-API-Key", "").strip()
    if key != API_KEY:
        return _jsonify({"error": "unauthorized"}), 401
    region = request.args.get("region", "").upper()
    if not region or region not in SERVER_CONFIG:
        return _jsonify({"error": "invalid region",
                         "allowed": list(SERVER_CONFIG.keys())}), 400
    tokens = _load_jwt_for_server(region)
    return _jsonify({
        "status": "ok",
        "region": region,
        "count": len(tokens),
        "tokens": tokens
    })


# ============================================================
#  LIKE
# ============================================================
@app.get("/like")
@app.get("/api/like")
def handle_like():
    try:
        uid = request.args.get("uid")
        server_name = (request.args.get("server_name") or request.args.get("region") or "").upper()
        api_key = request.args.get("key", "").strip()

        if not api_key or not is_valid_key(api_key):
            return _jsonify({"error": "Invalid API key 🔑"}), 403
        if not uid or not server_name:
            return _jsonify({"error": "UID and server_name required"}), 400
        if server_name not in SERVER_CONFIG:
            return _jsonify({"error": "Unsupported region",
                             "allowed": list(SERVER_CONFIG.keys())}), 400

        tokens = _load_jwt_for_server(server_name)
        source = SERVER_CONFIG[server_name]["jwt"]

        if not tokens:
            accounts = _load_accounts_for_server(server_name)
            if not accounts:
                return _jsonify({"error": f"No tokens in {source}"}), 500
            tokens = generate_jwts_live(accounts, JWT_WORKERS)
            source = f"live ({len(tokens)} tokens)"

        if not tokens:
            return _jsonify({"error": "No tokens available"}), 500

        token = tokens[0]
        encrypted = enc(uid)

        before = _parse_account_info(make_request(encrypted, server_name, token))
        if before is None:
            return _jsonify({
                "LikesGivenByAPI": 0, "LikesafterCommand": 0, "LikesbeforeCommand": 0,
                "PlayerNickname": "Unknown", "UID": uid, "GiftCount": 0,
                "tokens_used": len(tokens), "server_name": server_name,
                "source": source, "status": 0
            })

        url = _like_url_for(server_name)
        send_likes_from_all_tokens(uid, server_name, url, tokens, REPEATS_PER_TOKEN)

        after = _parse_account_info(make_request(encrypted, server_name, token)) or {
            "likes": before["likes"], "uid": before["uid"], "name": before["name"]
        }
        like_given = max(0, int(after["likes"]) - int(before["likes"]))
        gift = like_given * 2 if like_given > 0 else 0

        return _jsonify({
            "LikesGivenByAPI": like_given,
            "LikesafterCommand": int(after["likes"]),
            "LikesbeforeCommand": int(before["likes"]),
            "PlayerNickname": str(after["name"]),
            "UID": int(after["uid"]),
            "GiftCount": gift,
            "server_name": server_name,
            "tokens_used": len(tokens),
            "requests_sent": len(tokens) * REPEATS_PER_TOKEN,
            "source": source,
            "status": 1 if like_given > 0 else 2
        })
    except Exception as e:
        return _jsonify({"error": "runtime_error", "detail": str(e)}), 500


# ============================================================
#  RUN
# ============================================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)