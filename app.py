# ektelegramcontrol.py - Complete Hosting Panel with Full Bot Control

from flask import Flask, request, jsonify, render_template_string, send_file, Response, stream_with_context
from flask_cors import CORS
from functools import wraps
import os
import json
import subprocess
import sys
import time
import threading
import shutil
import zipfile
import uuid
import re
import signal
import logging
import traceback
from datetime import datetime
from pathlib import Path
import requests
import queue
import select
import pty
import termios
import struct
import fcntl
import tempfile
import mimetypes
import telebot
from telebot import types

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'
CORS(app)

# ============================================================
# CONFIGURATION
# ============================================================

OWNER_ID = int(os.environ.get('OWNER_ID', 5674825926))
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8650477558:AAGCjvWFUVRBKV4Nh8vmS9CjNDioevibe-o')
BOT_USERNAME = os.environ.get('BOT_USERNAME', '@Card_hacker_12')
OWNER_PASSWORD = os.environ.get('OWNER_PASSWORD', 'riyaj1858')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, 'uploads')
PROCESSES_FILE = os.path.join(BASE_DIR, 'processes.json')
USERS_FILE = os.path.join(BASE_DIR, 'users.json')
LOGS_DIR = os.path.join(BASE_DIR, 'logs')
SETTINGS_FILE = os.path.join(BASE_DIR, 'settings.json')
TERMINAL_SESSIONS_FILE = os.path.join(BASE_DIR, 'terminal_sessions.json')
BOT_UPLOAD_DIR = os.path.join(BASE_DIR, 'upload_bots')
REQUIREMENTS_DIR = os.path.join(BASE_DIR, 'requirements_temp')

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
os.makedirs(BOT_UPLOAD_DIR, exist_ok=True)
os.makedirs(REQUIREMENTS_DIR, exist_ok=True)

# ============================================================
# TELEGRAM BOT INIT
# ============================================================

bot = telebot.TeleBot(BOT_TOKEN)

# ============================================================
# BOT THREAD START (FOR GUNICORN / RENDER)
# ============================================================

def start_bot():
    print("🚀 Bot starting polling...")
    try:
        bot.remove_webhook()
        print("✅ Webhook removed")
    except Exception as e:
        print(f"⚠️ Webhook error: {e}")
    
    while True:
        try:
            bot.polling(none_stop=True, interval=0, timeout=20)
        except Exception as e:
            print(f"❌ Bot polling error: {e}")
            time.sleep(5)
            print("🔄 Restarting bot polling...")

# Start bot thread when app loads
bot_thread = threading.Thread(target=start_bot, daemon=True)
bot_thread.start()
print("✅ Bot thread started")

# ============================================================
# SETTINGS MANAGEMENT
# ============================================================

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {
        "owner_telegram": BOT_USERNAME,
        "contact_owner": "Card_hacker_12",
        "signup_enabled": True,
        "telegram_popup": True,
        "telegram_link": "https://t.me/+m0R5z1yhmCtiZjQ9",
        "notifications_enabled": True,
        "popup_shown": {}
    }

def save_settings(settings):
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=2)

# ============================================================
# USERS DATA
# ============================================================

def load_users():
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, 'r') as f:
                return json.load(f)
        except:
            pass
    return {}

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def get_default_users():
    return {
        "riyaj": {
            "password": "riyaj",
            "role": "owner",
            "created": datetime.now().isoformat(),
            "telegram_id": str(OWNER_ID)
        }
    }

def ensure_default_users():
    users = load_users()
    default_users = get_default_users()
    changed = False
    
    for username, info in default_users.items():
        if username not in users:
            users[username] = info
            changed = True
        else:
            if users[username].get('role') != 'owner':
                users[username]['role'] = 'owner'
                changed = True
            if users[username].get('telegram_id') != str(OWNER_ID):
                users[username]['telegram_id'] = str(OWNER_ID)
                changed = True
            if users[username].get('password') != info.get('password'):
                users[username]['password'] = info.get('password')
                changed = True
    
    for username in list(users.keys()):
        if username != "riyaj" and users[username].get('role') == 'owner':
            users[username]['role'] = 'user'
            changed = True
    
    if changed:
        save_users(users)
        print(f"✅ Users updated: {users}")
    return users

# ============================================================
# AUTH DECORATORS
# ============================================================

def get_username_from_request():
    username = request.headers.get('X-Username')
    if not username and request.is_json:
        username = request.json.get('username') if request.json else None
    return username

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        username = get_username_from_request()
        if not username:
            return jsonify({'error': 'Authentication required'}), 401
        
        users = load_users()
        if username not in users:
            return jsonify({'error': 'User not found'}), 401
        
        return f(username, *args, **kwargs)
    return decorated

def require_owner(f):
    @wraps(f)
    def decorated(username, *args, **kwargs):
        users = load_users()
        if users.get(username, {}).get('role') != 'owner':
            return jsonify({'error': 'Owner access required'}), 403
        return f(username, *args, **kwargs)
    return decorated

def require_admin_or_owner(f):
    @wraps(f)
    def decorated(username, *args, **kwargs):
        users = load_users()
        role = users.get(username, {}).get('role', '')
        if role not in ['owner', 'admin']:
            return jsonify({'error': 'Admin access required'}), 403
        return f(username, *args, **kwargs)
    return decorated

# ============================================================
# BOT FUNCTIONS
# ============================================================

def send_telegram_message(chat_id, text):
    settings = load_settings()
    if not settings.get('notifications_enabled', True):
        return False
    
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = {
            'chat_id': chat_id,
            'text': text,
            'parse_mode': 'Markdown'
        }
        response = requests.post(url, json=data, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error sending message: {e}")
        return False

def send_bot_notification(message_type, username, filename, file_id, pid=None, error=None, traceback_full=None, requirements=None, module_name=None, bot_username=None):
    settings = load_settings()
    if not settings.get('notifications_enabled', True):
        return True
        
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    display_bot = f"@{bot_username}" if bot_username else BOT_USERNAME
    
    if message_type == 'installing':
        text = (
            f"🔄 **Installing Python deps from `requirements.txt`**\n\n"
            f"📦 `{', '.join(requirements) if requirements else 'None'}`\n"
            f"👤 User: `{username}`\n"
            f"📁 File: `{filename}`\n"
            f"🤖 Bot: {display_bot}\n"
            f"📅 Time: {timestamp}"
        )
    elif message_type == 'installed':
        text = (
            f"✅ **Python deps from `requirements.txt` installed.**\n\n"
            f"📦 `{', '.join(requirements) if requirements else 'None'}`\n"
            f"👤 User: `{username}`\n"
            f"📁 File: `{filename}`\n"
            f"🤖 Bot: {display_bot}\n"
            f"📅 Time: {timestamp}"
        )
    elif message_type == 'running':
        text = (
            f"🚀 **File is running**\n\n"
            f"📁 File: `{filename}`\n"
            f"👤 User: `{username}`\n"
            f"🆔 PID: `{pid}`\n"
            f"🤖 Bot: {display_bot}\n"
            f"📅 Time: {timestamp}"
        )
    elif message_type == 'error':
        text = (
            f"❌ **Error in script pre-check for `{filename}`**\n\n"
            f"👤 User: `{username}`\n"
            f"📁 File: `{filename}`\n"
            f"🤖 Bot: {display_bot}\n\n"
            f"```\n{(traceback_full or error)[:1500]}\n```\n\n"
            f"📅 Time: {timestamp}"
        )
    elif message_type == 'module_installing':
        text = (
            f"🐍 **Module `{module_name}` not found. Installing `{module_name}`...**\n\n"
            f"👤 User: `{username}`\n"
            f"📁 File: `{filename}`\n"
            f"🤖 Bot: {display_bot}\n"
            f"📅 Time: {timestamp}"
        )
    elif message_type == 'module_installed':
        text = (
            f"✅ **Package `{module_name}` (for `{module_name}`) installed.**\n\n"
            f"👤 User: `{username}`\n"
            f"📁 File: `{filename}`\n"
            f"🤖 Bot: {display_bot}\n"
            f"📅 Time: {timestamp}"
        )
    elif message_type == 'retrying':
        text = (
            f"🔄 **Install successful. Retrying `{filename}`...**\n\n"
            f"👤 User: `{username}`\n"
            f"📁 File: `{filename}`\n"
            f"🤖 Bot: {display_bot}\n"
            f"📅 Time: {timestamp}"
        )
    else:
        return False
    
    return send_telegram_message(OWNER_ID, text)

# ============================================================
# PACKAGE MAP
# ============================================================

PACKAGE_MAP = {
    'PIL': 'Pillow',
    'cv2': 'opencv-python',
    'sklearn': 'scikit-learn',
    'yaml': 'PyYAML',
    'bs4': 'beautifulsoup4',
    'telegram': 'python-telegram-bot',
    'telebot': 'pyTelegramBotAPI',
    'pyrogram': 'pyrogram',
    'telethon': 'telethon',
    'aiogram': 'aiogram',
    'requests': 'requests',
    'flask': 'Flask',
    'flask_cors': 'Flask-CORS',
    'django': 'Django',
    'numpy': 'numpy',
    'pandas': 'pandas',
    'matplotlib': 'matplotlib',
    'tensorflow': 'tensorflow',
    'torch': 'torch',
    'transformers': 'transformers',
    'scipy': 'scipy',
    'cryptography': 'cryptography',
    'paramiko': 'paramiko',
    'pymongo': 'pymongo',
    'mysql': 'mysql-connector-python',
    'psycopg2': 'psycopg2-binary',
    'redis': 'redis',
    'celery': 'celery',
    'fastapi': 'fastapi',
    'uvicorn': 'uvicorn',
    'gunicorn': 'gunicorn',
    'aiohttp': 'aiohttp',
    'websockets': 'websockets',
    'jinja2': 'Jinja2',
    'markdown': 'Markdown',
    'lxml': 'lxml',
    'soupsieve': 'soupsieve',
    'pytest': 'pytest',
    'mock': 'mock',
    'coverage': 'coverage',
    'flake8': 'flake8',
    'black': 'black',
    'isort': 'isort',
    'mypy': 'mypy',
    'pylint': 'pylint',
    'opencv': 'opencv-python',
    'Pillow': 'Pillow',
    'aiofiles': 'aiofiles',
    'httpx': 'httpx',
    'pydantic': 'pydantic',
    'sqlalchemy': 'sqlalchemy',
    'alembic': 'alembic',
    'click': 'click',
    'colorama': 'colorama',
    'rich': 'rich',
    'tqdm': 'tqdm',
    'python-dotenv': 'python-dotenv',
    'pytz': 'pytz',
    'dateutil': 'python-dateutil',
    'pyyaml': 'PyYAML',
    'beautifulsoup4': 'beautifulsoup4',
    'soup': 'beautifulsoup4',
    'html5lib': 'html5lib',
    'feedparser': 'feedparser',
    'pymysql': 'pymysql',
    'psycopg2': 'psycopg2-binary',
    'sqlite': 'sqlite3',
    'aiosqlite': 'aiosqlite',
    'asyncpg': 'asyncpg',
    'motor': 'motor',
    'aioredis': 'aioredis',
    'kafka': 'kafka-python',
    'rabbitmq': 'pika',
    'grpc': 'grpcio',
    'protobuf': 'protobuf',
    'twilio': 'twilio',
    'stripe': 'stripe',
    'paypal': 'paypalrestsdk',
    'boto3': 'boto3',
    'google-cloud': 'google-cloud',
    'azure': 'azure-storage-blob',
    'pytorch': 'torch',
    'tf': 'tensorflow',
    'keras': 'keras',
    'theano': 'Theano',
    'caffe': 'caffe',
    'mxnet': 'mxnet',
    'dask': 'dask',
    'ray': 'ray',
    'modin': 'modin',
    'vaex': 'vaex',
    'polars': 'polars',
    'duckdb': 'duckdb',
    'blob': 'azure-storage-blob',
    's3': 'boto3',
    'gcs': 'google-cloud-storage',
    'firebase': 'firebase-admin',
    'supabase': 'supabase',
    'pinecone': 'pinecone-client',
    'weaviate': 'weaviate-client',
    'qdrant': 'qdrant-client',
    'chromadb': 'chromadb',
    'langchain': 'langchain',
    'llama': 'llama-cpp-python',
    'openai': 'openai',
    'anthropic': 'anthropic',
    'cohere': 'cohere',
    'mistral': 'mistralai',
    'psutil': 'psutil',
    'requests_toolbelt': 'requests-toolbelt',
    'pytgcalls': 'pytgcalls',
    'python-telegram-bot': 'python-telegram-bot',
    'pyTelegramBotAPI': 'pyTelegramBotAPI',
}

STD_LIB = set([
    'os', 'sys', 'time', 'json', 're', 'random', 'string', 'base64',
    'datetime', 'threading', 'shutil', 'zipfile', 'subprocess', 'signal',
    'math', 'collections', 'itertools', 'functools', 'typing', 'io',
    'pathlib', 'tempfile', 'hashlib', 'hmac', 'uuid', 'csv', 'xml',
    'html', 'urllib', 'http', 'socket', 'ssl', 'email', 'pickle',
    'shelve', 'sqlite3', 'logging', 'traceback', 'inspect',
    'pydoc', 'doctest', 'unittest', 'argparse', 'optparse', 'getopt',
    'configparser', 'dataclasses', 'enum', 'abc', 'contextlib',
    'asyncio', 'concurrent', 'multiprocessing', 'queue', 'weakref',
    'copy', 'pprint', 'textwrap', 'stringprep', 'struct', 'codecs',
    'crypt', 'termios', 'tty', 'pty', 'fcntl', 'grp', 'pwd',
    'resource', 'sysconfig', 'platform', 'ctypes', 'curses',
    'readline', 'rlcompleter', 'telnetlib', 'ftplib', 'poplib',
    'imaplib', 'nntplib', 'smtplib', 'smtpd', 'wsgiref',
    'xmlrpc', 'pickletools', 'dis', 'symbol', 'token',
    'keyword', 'ast', 'compileall', 'py_compile', 'zipimport',
    'pkgutil', 'site', 'atexit', 'sched', 'bisect', 'heapq',
    'array', 'audioop', 'binhex', 'binascii', 'cgi', 'cgitb',
    'chunk', 'cmath', 'cmd', 'code', 'codeop', 'colorsys',
    'compile', 'contextvars', 'cProfile', 'decimal', 'difflib',
    'dircache', 'distutils', 'dummy', 'encodings', 'ensurepip',
    'enum', 'filecmp', 'fileinput', 'fnmatch', 'fractions',
    'getopt', 'getpass', 'gettext', 'glob', 'gzip',
    'heapq', 'hmac', 'imp', 'importlib', 'inspect', 'io', 'ipaddress',
    'linecache', 'locale', 'logging', 'lzma', 'mailbox',
    'mailcap', 'marshal', 'math', 'mimetypes', 'mmap', 'modulefinder',
    'msilib', 'msvcrt', 'multiprocessing', 'netrc', 'nis', 'nntplib',
    'numbers', 'operator', 'optparse', 'parser', 'pathlib',
    'pdb', 'pickle', 'pickletools', 'pipes', 'pkgutil', 'platform',
    'plistlib', 'poplib', 'posix', 'pprint', 'profile', 'pstats',
    'pty', 'pwd', 'py_compile', 'pyclbr', 'pydoc', 'queue', 'quopri',
    'readline', 'reprlib', 'resource', 'rlcompleter',
    'runpy', 'sched', 'secrets', 'select', 'selectors', 'shelve',
    'shlex', 'shutil', 'signal', 'site', 'smtpd', 'smtplib', 'sndhdr',
    'socket', 'socketserver', 'sqlite3', 'ssl', 'stat', 'string',
    'stringprep', 'struct', 'subprocess', 'sunau', 'symbol', 'symtable',
    'sys', 'sysconfig', 'syslog', 'tabnanny', 'tarfile', 'telnetlib',
    'tempfile', 'termios', 'textwrap', 'threading', 'time', 'timeit',
    'tkinter', 'token', 'tokenize', 'trace', 'traceback', 'tracemalloc',
    'tty', 'turtle', 'types', 'typing', 'unicodedata', 'unittest',
    'uu', 'uuid', 'venv', 'warnings', 'wave', 'weakref',
    'webbrowser', 'xdrlib', 'zipapp', 'zipfile', 'zipimport', 'zlib',
    'unittest', 'xml', 'tkinter', 'idlelib', 'pydoc_data', 'ensurepip',
    'flask_cors', 'werkzeug', 'click', 'markupsafe', 'itsdangerous'
])

# ============================================================
# DETECT BOT USERNAME FROM FILE
# ============================================================

def detect_bot_token(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        token_match = re.search(r'[0-9]{9,10}:[A-Za-z0-9_-]{35,}', content)
        if token_match:
            token = token_match.group(0)
            try:
                resp = requests.get(f'https://api.telegram.org/bot{token}/getMe', timeout=5)
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get('ok'):
                        return token, data['result'].get('username')
            except:
                pass
        return None, None
    except:
        return None, None

# ============================================================
# INSTALL MISSING MODULE
# ============================================================

def install_missing_module(module_name, username, filename, file_id, bot_username=None):
    try:
        send_bot_notification('module_installing', username, filename, file_id, module_name=module_name, bot_username=bot_username)
        
        package_name = PACKAGE_MAP.get(module_name, module_name)
        
        result = subprocess.run(
            [sys.executable, '-m', 'pip', 'install', package_name],
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            send_bot_notification('module_installed', username, filename, file_id, module_name=module_name, bot_username=bot_username)
            return True, result.stdout
        else:
            send_bot_notification('error', username, filename, file_id, error=result.stderr, traceback_full=result.stderr, bot_username=bot_username)
            return False, result.stderr
            
    except subprocess.TimeoutExpired:
        error_msg = "Installation timed out after 120 seconds"
        send_bot_notification('error', username, filename, file_id, error=error_msg, traceback_full=error_msg, bot_username=bot_username)
        return False, error_msg
    except Exception as e:
        error_msg = str(e)
        send_bot_notification('error', username, filename, file_id, error=error_msg, traceback_full=traceback.format_exc(), bot_username=bot_username)
        return False, error_msg

# ============================================================
# PROCESS MANAGEMENT
# ============================================================

def load_processes():
    if os.path.exists(PROCESSES_FILE):
        try:
            with open(PROCESSES_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_processes(processes):
    with open(PROCESSES_FILE, 'w') as f:
        json.dump(processes, f, indent=2)

def kill_process(pid):
    try:
        if os.name == 'nt':
            subprocess.run(['taskkill', '/F', '/PID', str(pid)], capture_output=True, timeout=5)
        else:
            subprocess.run(['kill', '-9', str(pid)], capture_output=True, timeout=5)
        return True
    except:
        return False

def is_process_running(pid):
    try:
        if os.name == 'nt':
            result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}'], capture_output=True, text=True, timeout=3)
            return str(pid) in result.stdout
        else:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
    except:
        return False

def stop_file_process(file_id):
    processes = load_processes()
    if file_id in processes:
        pid = processes[file_id]['pid']
        kill_process(pid)
        del processes[file_id]
        save_processes(processes)
        return True
    return False

def cleanup_dead_processes():
    processes = load_processes()
    changed = False
    for file_id, info in list(processes.items()):
        try:
            if not is_process_running(info['pid']):
                del processes[file_id]
                changed = True
        except:
            del processes[file_id]
            changed = True
    if changed:
        save_processes(processes)
    return processes

# ============================================================
# RUN FILE
# ============================================================

def run_file(filepath, file_id, filename, username, cwd=None, attempt=1):
    try:
        dir_path = cwd if cwd else os.path.dirname(filepath)
        log_file = os.path.join(LOGS_DIR, f"{file_id}.log")
        
        _, bot_username = detect_bot_token(filepath)
        
        req_path = os.path.join(dir_path, 'requirements.txt')
        if os.path.exists(req_path):
            with open(req_path, 'r') as f:
                requirements = [line.strip() for line in f if line.strip()]
            
            if requirements:
                send_bot_notification('installing', username, filename, file_id, requirements=requirements, bot_username=bot_username)
                
                result = subprocess.run(
                    [sys.executable, '-m', 'pip', 'install', '-r', req_path],
                    capture_output=True,
                    text=True,
                    timeout=120
                )
                
                if result.returncode == 0:
                    send_bot_notification('installed', username, filename, file_id, requirements=requirements, bot_username=bot_username)
                else:
                    send_bot_notification('error', username, filename, file_id, error=result.stderr, traceback_full=result.stderr, bot_username=bot_username)
                    with open(log_file, 'w') as f:
                        f.write(f"=== INSTALLATION FAILED ===\n{result.stderr}\n\n")
                    return None
        else:
            with open(log_file, 'w') as f:
                f.write(f"=== No requirements.txt found ===\n\n")
        
        with open(log_file, 'a') as f:
            f.write(f"=== RUNNING {filename} (Attempt {attempt}) ===\n")
        
        process = subprocess.Popen(
            [sys.executable, filepath],
            stdout=open(log_file, 'a'),
            stderr=subprocess.PIPE,
            cwd=dir_path,
            text=True,
            bufsize=1
        )
        
        try:
            stdout, stderr = process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            stderr = None
            stdout = None
        
        if stderr and 'ModuleNotFoundError' in stderr:
            match = re.search(r"ModuleNotFoundError: No module named '(.+?)'", stderr)
            if match:
                module_name = match.group(1).strip().strip("'\"")
                
                send_bot_notification('module_installing', username, filename, file_id, module_name=module_name, bot_username=bot_username)
                success, output = install_missing_module(module_name, username, filename, file_id, bot_username)
                
                if success:
                    send_bot_notification('retrying', username, filename, file_id, bot_username=bot_username)
                    return run_file(filepath, file_id, filename, username, cwd, attempt + 1)
                else:
                    return None
        
        processes = load_processes()
        processes[file_id] = {
            'pid': process.pid,
            'filename': filename,
            'filepath': filepath,
            'username': username,
            'started': datetime.now().isoformat(),
            'status': 'running',
            'installed': True,
            'bot_username': bot_username
        }
        save_processes(processes)
        
        send_bot_notification('running', username, filename, file_id, pid=process.pid, bot_username=bot_username)
        
        print(f"✅ File started: {filename} (PID: {process.pid})")
        return process.pid
        
    except Exception as e:
        error_msg = str(e)
        traceback_full = traceback.format_exc()
        
        send_bot_notification('error', username, filename, file_id, error=error_msg, traceback_full=traceback_full, bot_username=bot_username)
        
        with open(log_file, 'w') as f:
            f.write(f"=== ERROR ===\n{error_msg}\n\n{traceback_full}\n")
        
        print(f"❌ Error running file: {e}")
        return None

# ============================================================
# FILE MANAGEMENT
# ============================================================

def get_user_dir(username):
    user_dir = os.path.join(UPLOAD_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    return user_dir

def get_bot_user_dir(username):
    bot_user_dir = os.path.join(BOT_UPLOAD_DIR, str(username))
    os.makedirs(bot_user_dir, exist_ok=True)
    return bot_user_dir

def get_user_files(username):
    user_dir = get_user_dir(username)
    files = []
    processes = load_processes()
    
    if os.path.exists(user_dir):
        for fname in os.listdir(user_dir):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath) and not fname.startswith('.'):
                if fname == 'requirements.txt':
                    continue
                    
                file_id = fname.split('_')[0] if fname.endswith('.py') and '_' in fname else fname
                
                status = 'stopped'
                if file_id in processes:
                    if is_process_running(processes[file_id]['pid']):
                        status = 'running'
                    else:
                        del processes[file_id]
                        save_processes(processes)
                
                has_token = False
                bot_username = None
                token = None
                if fname.endswith('.py'):
                    token, bot_username = detect_bot_token(fpath)
                    if token:
                        has_token = True
                
                files.append({
                    'id': file_id,
                    'filename': fname,
                    'size': os.path.getsize(fpath),
                    'status': status,
                    'path': fpath,
                    'owner': username,
                    'has_token': has_token,
                    'bot_username': bot_username
                })
    return files

def get_all_files():
    all_files = []
    if os.path.exists(UPLOAD_DIR):
        for username in os.listdir(UPLOAD_DIR):
            user_dir = os.path.join(UPLOAD_DIR, username)
            if os.path.isdir(user_dir):
                files = get_user_files(username)
                all_files.extend(files)
    return all_files

# ============================================================
# TERMINAL MANAGEMENT
# ============================================================

class TerminalSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.process = None
        self.master_fd = None
        self.slave_fd = None
        self.output_queue = queue.Queue()
        self.running = False
        self.thread = None
        self.cwd = os.getcwd()
        self.exit_code = None

    def start(self, cwd=None):
        if self.running:
            return False
        
        try:
            if cwd:
                self.cwd = cwd
            
            if os.name == 'nt':
                self.process = subprocess.Popen(
                    ['cmd.exe'],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    cwd=self.cwd
                )
            else:
                self.master_fd, self.slave_fd = pty.openpty()
                self.process = subprocess.Popen(
                    ['/bin/bash', '-i'],
                    stdin=self.slave_fd,
                    stdout=self.slave_fd,
                    stderr=self.slave_fd,
                    text=True,
                    bufsize=1,
                    cwd=self.cwd
                )
                fcntl.fcntl(self.master_fd, fcntl.F_SETFL, os.O_NONBLOCK)
            
            self.running = True
            self.thread = threading.Thread(target=self._read_output, daemon=True)
            self.thread.start()
            return True
        except Exception as e:
            return False

    def _read_output(self):
        while self.running and self.process:
            try:
                if os.name == 'nt':
                    if self.process.stdout:
                        output = self.process.stdout.readline()
                        if output:
                            self.output_queue.put(output)
                        else:
                            break
                else:
                    try:
                        output = os.read(self.master_fd, 1024)
                        if output:
                            self.output_queue.put(output.decode('utf-8', errors='ignore'))
                        else:
                            break
                    except BlockingIOError:
                        time.sleep(0.05)
                    except Exception:
                        break
            except Exception:
                break
        
        if self.process:
            self.process.poll()
            self.exit_code = self.process.returncode

    def write(self, data):
        if self.running:
            try:
                if os.name == 'nt':
                    if self.process.stdin:
                        self.process.stdin.write(data)
                        self.process.stdin.flush()
                else:
                    os.write(self.master_fd, data.encode())
                return True
            except:
                return False
        return False

    def read_output(self):
        output = ''
        while not self.output_queue.empty():
            try:
                output += self.output_queue.get_nowait()
            except:
                break
        return output

    def stop(self):
        self.running = False
        try:
            if self.process:
                self.process.terminate()
                time.sleep(0.3)
                if self.process.poll() is None:
                    self.process.kill()
        except:
            pass
        try:
            if self.master_fd:
                os.close(self.master_fd)
        except:
            pass
        try:
            if self.slave_fd:
                os.close(self.slave_fd)
        except:
            pass

    def is_alive(self):
        if self.process:
            self.process.poll()
            return self.process.returncode is None
        return False

terminal_sessions = {}

def get_terminal_session(session_id):
    if session_id not in terminal_sessions:
        terminal_sessions[session_id] = TerminalSession(session_id)
    return terminal_sessions[session_id]

# ============================================================
# FLASK ROUTES
# ============================================================

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    users = load_users()
    if username in users and users[username]['password'] == password:
        return jsonify({
            'success': True,
            'username': username,
            'role': users[username]['role']
        })
    return jsonify({'error': 'Invalid credentials'}), 401

@app.route('/api/signup', methods=['POST'])
def signup():
    settings = load_settings()
    if not settings.get('signup_enabled', True):
        return jsonify({'error': 'Signup is disabled by admin'}), 403
        
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    users = load_users()
    if username in users:
        return jsonify({'error': 'Username already exists'}), 400
    
    users[username] = {
        'password': password,
        'role': 'user',
        'created': datetime.now().isoformat()
    }
    save_users(users)
    
    user_dir = os.path.join(UPLOAD_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    
    bot_user_dir = get_bot_user_dir(username)
    
    return jsonify({'success': True, 'message': 'Account created successfully'})

@app.route('/api/users')
@require_auth
def get_users(username):
    users = load_users()
    user_role = users[username]['role']
    
    result = []
    for uname, info in users.items():
        if user_role == 'owner':
            result.append({
                'username': uname,
                'password': info['password'],
                'role': info['role'],
                'created': info.get('created', '')
            })
        elif user_role == 'admin':
            if uname == username or info['role'] == 'user':
                result.append({
                    'username': uname,
                    'password': info['password'] if uname == username else '••••••••',
                    'role': info['role'],
                    'created': info.get('created', '')
                })
        elif uname == username:
            result.append({
                'username': uname,
                'password': '••••••••',
                'role': info['role'],
                'created': info.get('created', '')
            })
    
    return jsonify({'users': result})

@app.route('/api/users/remove', methods=['POST'])
@require_admin_or_owner
def remove_user(username):
    data = request.json
    target = data.get('username')
    
    if not target:
        return jsonify({'error': 'Username required'}), 400
    
    users = load_users()
    if target not in users:
        return jsonify({'error': 'User not found'}), 400
    
    if target == username:
        return jsonify({'error': 'Cannot remove yourself'}), 400
    
    if users[target]['role'] == 'owner':
        return jsonify({'error': 'Cannot remove owner'}), 400
    
    if users[username]['role'] == 'admin' and users[target]['role'] == 'admin':
        return jsonify({'error': 'Admins cannot remove other admins'}), 400
    
    user_dir = os.path.join(UPLOAD_DIR, target)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)
    
    bot_user_dir = get_bot_user_dir(target)
    if os.path.exists(bot_user_dir):
        shutil.rmtree(bot_user_dir)
    
    del users[target]
    save_users(users)
    
    return jsonify({'message': f'User {target} removed'})

@app.route('/api/users/add', methods=['POST'])
@require_admin_or_owner
def add_user(username):
    data = request.json
    new_username = data.get('username')
    password = data.get('password')
    role = data.get('role', 'user')
    
    if not new_username or not password:
        return jsonify({'error': 'Username and password required'}), 400
    
    users = load_users()
    
    if new_username in users:
        users[new_username]['role'] = role
        save_users(users)
        return jsonify({'message': f'User {new_username} updated to {role}'})
    
    users[new_username] = {
        'password': password,
        'role': role,
        'created': datetime.now().isoformat()
    }
    save_users(users)
    
    user_dir = os.path.join(UPLOAD_DIR, new_username)
    os.makedirs(user_dir, exist_ok=True)
    
    get_bot_user_dir(new_username)
    
    return jsonify({'message': 'User added successfully'})

@app.route('/api/users/promote', methods=['POST'])
@require_owner
def promote_user(username):
    data = request.json
    target = data.get('username')
    role = data.get('role', 'admin')
    
    if not target:
        return jsonify({'error': 'Username required'}), 400
    
    users = load_users()
    if target not in users:
        return jsonify({'error': 'User does not exist'}), 400
    
    if target == username:
        return jsonify({'error': 'Cannot change own role'}), 400
    
    users[target]['role'] = role
    save_users(users)
    return jsonify({'message': f'User {target} promoted to {role}'})

@app.route('/api/users/update', methods=['PUT'])
@require_auth
def update_user(username):
    data = request.json
    field = data.get('field')
    value = data.get('value')
    old_value = data.get('old_value')
    
    users = load_users()
    
    if field == 'username':
        if value in users:
            return jsonify({'error': 'Username already exists'}), 400
        
        old_dir = os.path.join(UPLOAD_DIR, username)
        new_dir = os.path.join(UPLOAD_DIR, value)
        if os.path.exists(old_dir):
            os.rename(old_dir, new_dir)
        
        old_bot_dir = get_bot_user_dir(username)
        new_bot_dir = get_bot_user_dir(value)
        if os.path.exists(old_bot_dir):
            os.rename(old_bot_dir, new_bot_dir)
        
        processes = load_processes()
        for file_id, info in processes.items():
            if info.get('username') == username:
                info['username'] = value
        save_processes(processes)
        
        users[value] = users.pop(username)
        save_users(users)
        
        return jsonify({'message': 'Username updated'})
    
    elif field == 'password':
        if old_value and users[username]['password'] != old_value:
            return jsonify({'error': 'Old password is incorrect'}), 400
        users[username]['password'] = value
        save_users(users)
        return jsonify({'message': 'Password updated'})
    
    return jsonify({'error': 'Invalid field'}), 400

@app.route('/api/settings', methods=['GET'])
@require_auth
def get_settings(username):
    settings = load_settings()
    return jsonify({'settings': settings})

@app.route('/api/settings', methods=['PUT'])
@require_owner
def update_settings(username):
    data = request.json
    settings = load_settings()
    
    if 'owner_telegram' in data:
        settings['owner_telegram'] = data['owner_telegram']
    if 'contact_owner' in data:
        contact = data['contact_owner']
        if contact.startswith('@'):
            contact = contact[1:]
        settings['contact_owner'] = contact
    if 'signup_enabled' in data:
        settings['signup_enabled'] = data['signup_enabled']
    if 'telegram_popup' in data:
        settings['telegram_popup'] = data['telegram_popup']
    if 'telegram_link' in data:
        settings['telegram_link'] = data['telegram_link']
    if 'notifications_enabled' in data:
        settings['notifications_enabled'] = data['notifications_enabled']
    
    save_settings(settings)
    return jsonify({'message': 'Settings updated'})

@app.route('/api/settings/popup-shown', methods=['POST'])
@require_auth
def popup_shown(username):
    data = request.json
    settings = load_settings()
    
    if 'popup_shown' not in settings:
        settings['popup_shown'] = {}
    
    settings['popup_shown'][username] = {
        'timestamp': datetime.now().isoformat(),
        'shown': data.get('shown', True)
    }
    
    save_settings(settings)
    return jsonify({'message': 'Popup status updated'})

@app.route('/api/files')
@require_auth
def get_user_files_route(username):
    files = get_user_files(username)
    return jsonify({'files': files})

@app.route('/api/all-files')
@require_owner
def get_all_files_route(username):
    files = get_all_files()
    return jsonify({'files': files})

@app.route('/api/upload', methods=['POST'])
@require_auth
def upload_file(username):
    if 'files[]' not in request.files:
        return jsonify({'error': 'No files provided'}), 400

    files = request.files.getlist('files[]')
    if len(files) == 0:
        return jsonify({'error': 'No files selected'}), 400

    user_dir = get_user_dir(username)
    bot_user_dir = get_bot_user_dir(username)
    
    file_id = str(uuid.uuid4())[:6]
    main_py = None
    uploaded_any = False

    for f in files:
        filename = f.filename
        if filename.lower().endswith('.zip'):
            zip_path = os.path.join(user_dir, filename)
            f.save(zip_path)
            try:
                # Create temp directory for extraction
                temp_dir = os.path.join(user_dir, f"temp_{file_id}")
                os.makedirs(temp_dir, exist_ok=True)
                
                with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                    zip_ref.extractall(temp_dir)
                
                # Rename all extracted files with file_id prefix
                for root, dirs, ext_files in os.walk(temp_dir):
                    for ext_file in ext_files:
                        old_path = os.path.join(root, ext_file)
                        new_filename = f"{file_id}_{ext_file}"
                        new_path = os.path.join(user_dir, new_filename)
                        shutil.move(old_path, new_path)
                        
                        # Copy to bot directory
                        bot_new_path = os.path.join(bot_user_dir, new_filename)
                        shutil.copy2(new_path, bot_new_path)
                
                # Remove temp directory
                shutil.rmtree(temp_dir)
                os.remove(zip_path)
                uploaded_any = True
            except Exception as e:
                return jsonify({'error': f'ZIP extraction failed: {str(e)}'}), 400
        else:
            # Save file with file_id prefix if .py file
            if filename.endswith('.py') and not filename.startswith(file_id):
                new_filename = f"{file_id}_{filename}"
            else:
                new_filename = filename
            filepath = os.path.join(user_dir, new_filename)
            f.save(filepath)
            
            bot_filepath = os.path.join(bot_user_dir, new_filename)
            shutil.copy(filepath, bot_filepath)
            uploaded_any = True

    if not uploaded_any:
        return jsonify({'error': 'No files uploaded'}), 400

    # Find main Python file
    for fname in os.listdir(user_dir):
        if fname.endswith('.py') and fname.startswith(file_id):
            base = fname[len(file_id)+1:]
            if base in ['bot.py', 'main.py', 'app.py', 'index.py', 'run.py']:
                main_py = os.path.join(user_dir, fname)
                break

    if not main_py:
        for fname in os.listdir(user_dir):
            if fname.endswith('.py') and fname.startswith(file_id):
                main_py = os.path.join(user_dir, fname)
                break

    if not main_py:
        return jsonify({
            'message': 'Files uploaded, but no Python file found to run.',
            'file_id': file_id,
            'main_file': None,
            'pid': None,
            'files_uploaded': len(files),
            'installed': 'none'
        })

    pid = run_file(main_py, file_id, os.path.basename(main_py), username, cwd=user_dir)

    return jsonify({
        'message': 'Upload successful',
        'file_id': file_id,
        'main_file': os.path.basename(main_py),
        'pid': pid,
        'files_uploaded': len(files),
        'installed': 'done'
    })

@app.route('/api/deploy', methods=['POST'])
@require_auth
def deploy_code(username):
    data = request.json
    filename = data.get('filename', 'main.py')
    code = data.get('code', '')
    
    if not code:
        return jsonify({'error': 'No code provided'}), 400
    
    user_dir = get_user_dir(username)
    bot_user_dir = get_bot_user_dir(username)
    
    file_id = str(uuid.uuid4())[:6]
    full_filename = f"{file_id}_{filename}"
    filepath = os.path.join(user_dir, full_filename)
    bot_filepath = os.path.join(bot_user_dir, full_filename)
    
    processes = load_processes()
    if file_id in processes:
        kill_process(processes[file_id]['pid'])
        del processes[file_id]
        save_processes(processes)
    
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(code)
    
    shutil.copy(filepath, bot_filepath)
    
    pid = run_file(filepath, file_id, filename, username, cwd=user_dir)
    
    return jsonify({
        'message': 'Code deployed',
        'file_id': file_id,
        'filename': full_filename,
        'pid': pid,
        'installed': 'done'
    })

@app.route('/api/files/start/<file_id>', methods=['POST'])
@require_auth
def start_file_route(username, file_id):
    cleanup_dead_processes()
    
    user_dir = get_user_dir(username)
    main_file = None
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id) and fname.endswith('.py'):
            main_file = os.path.join(user_dir, fname)
            break
    
    if not main_file:
        users = load_users()
        if users[username]['role'] == 'owner':
            for dirname in os.listdir(UPLOAD_DIR):
                user_dir = os.path.join(UPLOAD_DIR, dirname)
                for fname in os.listdir(user_dir):
                    if fname.startswith(file_id) and fname.endswith('.py'):
                        main_file = os.path.join(user_dir, fname)
                        break
                if main_file:
                    break
        if not main_file:
            return jsonify({'error': 'File not found'}), 404
    
    processes = load_processes()
    if file_id in processes:
        kill_process(processes[file_id]['pid'])
        del processes[file_id]
        save_processes(processes)
    
    pid = run_file(main_file, file_id, os.path.basename(main_file), username, cwd=os.path.dirname(main_file))
    return jsonify({'message': 'Started', 'pid': pid})

@app.route('/api/files/stop/<file_id>', methods=['POST'])
@require_auth
def stop_file_route(username, file_id):
    if stop_file_process(file_id):
        return jsonify({'message': 'Stopped'})
    return jsonify({'error': 'File not running'}), 404

@app.route('/api/files/delete/<file_id>', methods=['DELETE'])
@require_auth
def delete_file_route(username, file_id):
    stop_file_process(file_id)
    
    processes = load_processes()
    if file_id in processes:
        del processes[file_id]
        save_processes(processes)
    
    user_dir = get_user_dir(username)
    bot_user_dir = get_bot_user_dir(username)
    
    deleted = False
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath):
                os.remove(fpath)
                deleted = True
                break
    
    for fname in os.listdir(bot_user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(bot_user_dir, fname)
            if os.path.isfile(fpath):
                os.remove(fpath)
                break
    
    req_path = os.path.join(user_dir, 'requirements.txt')
    if os.path.exists(req_path):
        py_files = [f for f in os.listdir(user_dir) if f.endswith('.py')]
        if len(py_files) == 0:
            try:
                os.remove(req_path)
            except:
                pass
    
    log_file = os.path.join(LOGS_DIR, f"{file_id}.log")
    if os.path.exists(log_file):
        os.remove(log_file)
    
    if deleted:
        return jsonify({'message': 'Deleted'})
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/files/download/<file_id>')
@require_auth
def download_file_route(username, file_id):
    user_dir = get_user_dir(username)
    
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath):
                return send_file(fpath, as_attachment=True, download_name=fname)
    
    users = load_users()
    if users[username]['role'] == 'owner':
        for dirname in os.listdir(UPLOAD_DIR):
            user_dir = os.path.join(UPLOAD_DIR, dirname)
            for fname in os.listdir(user_dir):
                if fname.startswith(file_id):
                    fpath = os.path.join(user_dir, fname)
                    if os.path.isfile(fpath):
                        return send_file(fpath, as_attachment=True, download_name=fname)
    
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/files/download-all')
@require_auth
def download_all_files(username):
    user_dir = get_user_dir(username)
    if not os.path.exists(user_dir) or not os.listdir(user_dir):
        return jsonify({'error': 'No files to download'}), 404
    
    zip_path = os.path.join(BASE_DIR, f"{username}_files_{int(time.time())}.zip")
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(user_dir):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, user_dir)
                    zipf.write(file_path, arcname)
        
        return send_file(zip_path, as_attachment=True, download_name=f"{username}_files.zip")
    finally:
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except:
                pass

@app.route('/api/folder/download')
@require_auth
def folder_download_route(username):
    path = request.args.get('path', '')
    
    users = load_users()
    user_role = users[username]['role']
    
    if user_role == 'owner':
        base_dir = UPLOAD_DIR
    else:
        base_dir = get_user_dir(username)
    
    full_path = os.path.join(base_dir, path)
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(full_path)
    
    if not real_path.startswith(real_base):
        return jsonify({'error': 'Access denied'}), 403
    
    if not os.path.exists(real_path):
        return jsonify({'error': 'File not found'}), 404
    
    if os.path.isdir(real_path):
        zip_path = os.path.join(BASE_DIR, f"folder_{int(time.time())}.zip")
        try:
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(real_path):
                    for file in files:
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, real_path)
                        zipf.write(file_path, arcname)
            
            return send_file(zip_path, as_attachment=True, download_name=os.path.basename(real_path) + ".zip")
        finally:
            if os.path.exists(zip_path):
                try:
                    os.remove(zip_path)
                except:
                    pass
    else:
        return send_file(real_path, as_attachment=True, download_name=os.path.basename(real_path))

@app.route('/api/files/start-all', methods=['POST'])
@require_auth
def start_all_files(username):
    user_dir = get_user_dir(username)
    started = 0
    
    if os.path.exists(user_dir):
        for fname in os.listdir(user_dir):
            if fname.endswith('.py') and not fname.startswith('.') and fname != 'requirements.txt':
                parts = fname.split('_', 1)
                if len(parts) >= 2:
                    file_id = parts[0]
                else:
                    file_id = fname
                fpath = os.path.join(user_dir, fname)
                if os.path.isfile(fpath):
                    processes = load_processes()
                    if file_id not in processes:
                        run_file(fpath, file_id, fname, username, cwd=user_dir)
                        started += 1
    
    return jsonify({'message': f'Started {started} files'})

@app.route('/api/files/stop-all', methods=['POST'])
@require_auth
def stop_all_files(username):
    processes = load_processes()
    stopped = 0
    
    for file_id, info in list(processes.items()):
        if info.get('username') == username:
            if kill_process(info['pid']):
                del processes[file_id]
                stopped += 1
    
    save_processes(processes)
    return jsonify({'message': f'Stopped {stopped} files'})

@app.route('/api/files/logs/<file_id>')
@require_auth
def get_file_logs(username, file_id):
    log_file = os.path.join(LOGS_DIR, f"{file_id}.log")
    if os.path.exists(log_file):
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        return jsonify({'logs': content})
    return jsonify({'logs': 'No logs found'})

@app.route('/api/files/content/<file_id>')
@require_auth
def get_file_content(username, file_id):
    user_dir = get_user_dir(username)
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath):
                with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                return jsonify({'content': content})
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/files/content/<file_id>', methods=['PUT'])
@require_auth
def update_file_content(username, file_id):
    data = request.json
    content = data.get('content', '')
    user_dir = get_user_dir(username)
    bot_user_dir = get_bot_user_dir(username)
    
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath):
                with open(fpath, 'w', encoding='utf-8') as f:
                    f.write(content)
                bot_fpath = os.path.join(bot_user_dir, fname)
                if os.path.exists(bot_fpath):
                    with open(bot_fpath, 'w', encoding='utf-8') as f:
                        f.write(content)
                return jsonify({'message': 'File updated'})
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/files/rename/<file_id>', methods=['POST'])
@require_auth
def rename_file(username, file_id):
    data = request.json
    new_name = data.get('new_name', '')
    if not new_name:
        return jsonify({'error': 'New name required'}), 400
    
    user_dir = get_user_dir(username)
    bot_user_dir = get_bot_user_dir(username)
    
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            old_path = os.path.join(user_dir, fname)
            parts = fname.split('_', 1)
            if len(parts) >= 2:
                new_fname = f"{parts[0]}_{new_name}"
            else:
                new_fname = new_name
            new_path = os.path.join(user_dir, new_fname)
            os.rename(old_path, new_path)
            
            bot_old_path = os.path.join(bot_user_dir, fname)
            if os.path.exists(bot_old_path):
                bot_new_path = os.path.join(bot_user_dir, new_fname)
                os.rename(bot_old_path, bot_new_path)
            
            return jsonify({'message': 'File renamed'})
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/files/duplicate/<file_id>', methods=['POST'])
@require_auth
def duplicate_file(username, file_id):
    user_dir = get_user_dir(username)
    bot_user_dir = get_bot_user_dir(username)
    
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath):
                # Extract original filename without file_id prefix
                parts = fname.split('_', 1)
                if len(parts) >= 2:
                    original_name = parts[1]
                else:
                    original_name = fname
                
                # Generate new short file_id (6 characters)
                new_file_id = str(uuid.uuid4())[:6]
                new_fname = f"{new_file_id}_{original_name}"
                new_path = os.path.join(user_dir, new_fname)
                shutil.copy2(fpath, new_path)
                
                bot_fpath = os.path.join(bot_user_dir, fname)
                if os.path.exists(bot_fpath):
                    bot_new_path = os.path.join(bot_user_dir, new_fname)
                    shutil.copy2(bot_fpath, bot_new_path)
                
                return jsonify({'message': 'File duplicated', 'new_file_id': new_file_id})
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/files/compress/<file_id>', methods=['POST'])
@require_auth
def compress_file(username, file_id):
    user_dir = get_user_dir(username)
    target_file = None
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            target_file = os.path.join(user_dir, fname)
            break
    
    if not target_file or not os.path.isfile(target_file):
        return jsonify({'error': 'File not found'}), 404
    
    zip_path = target_file + '.zip'
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
        zipf.write(target_file, os.path.basename(target_file))
    
    return jsonify({'message': 'File compressed'})

@app.route('/api/files/details/<file_id>')
@require_auth
def file_details(username, file_id):
    user_dir = get_user_dir(username)
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath):
                stat = os.stat(fpath)
                token, bot_username = detect_bot_token(fpath)
                return jsonify({
                    'details': {
                        'filename': fname,
                        'size': stat.st_size,
                        'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
                        'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        'path': fpath,
                        'owner': username,
                        'has_token': bool(token),
                        'bot_username': bot_username
                    }
                })
    return jsonify({'error': 'File not found'}), 404

@app.route('/api/files/bot-info/<file_id>')
@require_auth
def get_bot_info(username, file_id):
    user_dir = get_user_dir(username)
    for fname in os.listdir(user_dir):
        if fname.startswith(file_id):
            fpath = os.path.join(user_dir, fname)
            if os.path.isfile(fpath):
                token, bot_username = detect_bot_token(fpath)
                return jsonify({
                    'has_token': bool(token),
                    'bot_username': bot_username
                })
    
    users = load_users()
    if users[username]['role'] == 'owner':
        for dirname in os.listdir(UPLOAD_DIR):
            user_dir = os.path.join(UPLOAD_DIR, dirname)
            for fname in os.listdir(user_dir):
                if fname.startswith(file_id):
                    fpath = os.path.join(user_dir, fname)
                    if os.path.isfile(fpath):
                        token, bot_username = detect_bot_token(fpath)
                        return jsonify({
                            'has_token': bool(token),
                            'bot_username': bot_username
                        })
    
    return jsonify({'has_token': False, 'bot_username': None})

@app.route('/api/folder/list', methods=['POST'])
@require_auth
def folder_list(username):
    data = request.json
    path = data.get('path', '')
    
    users = load_users()
    user_role = users[username]['role']
    
    if user_role == 'owner':
        base_dir = UPLOAD_DIR
    else:
        base_dir = get_user_dir(username)
    
    if path:
        full_path = os.path.join(base_dir, path)
    else:
        full_path = base_dir
    
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(full_path)
    if not real_path.startswith(real_base):
        return jsonify({'error': 'Access denied'}), 403
    
    if not os.path.exists(real_path):
        return jsonify({'error': 'Path does not exist'}), 404
    
    if not os.path.isdir(real_path):
        return jsonify({'error': 'Not a directory'}), 400
    
    items = []
    for item in os.listdir(real_path):
        item_path = os.path.join(real_path, item)
        if item.startswith('.'):
            continue
        if item == 'requirements.txt':
            continue
            
        is_dir = os.path.isdir(item_path)
        stat = os.stat(item_path)
        
        has_token = False
        bot_username = None
        if not is_dir and item.endswith('.py'):
            token, bot_username = detect_bot_token(item_path)
            if token:
                has_token = True
        
        items.append({
            'name': item,
            'path': os.path.join(path, item) if path else item,
            'is_dir': is_dir,
            'size': stat.st_size if not is_dir else 0,
            'modified': datetime.fromtimestamp(stat.st_mtime).isoformat(),
            'created': datetime.fromtimestamp(stat.st_ctime).isoformat(),
            'has_token': has_token,
            'bot_username': bot_username
        })
    
    return jsonify({'items': items})

@app.route('/api/folder/create', methods=['POST'])
@require_auth
def folder_create(username):
    data = request.json
    path = data.get('path', '')
    name = data.get('name', '')
    
    if not name:
        return jsonify({'error': 'Folder name required'}), 400
    
    users = load_users()
    user_role = users[username]['role']
    
    if user_role == 'owner':
        base_dir = UPLOAD_DIR
    else:
        base_dir = get_user_dir(username)
    
    if path:
        full_path = os.path.join(base_dir, path)
    else:
        full_path = base_dir
    
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(full_path)
    if not real_path.startswith(real_base):
        return jsonify({'error': 'Access denied'}), 403
    
    new_folder = os.path.join(real_path, name)
    os.makedirs(new_folder, exist_ok=True)
    
    return jsonify({'message': 'Folder created'})

@app.route('/api/folder/create-file', methods=['POST'])
@require_auth
def folder_create_file(username):
    data = request.json
    path = data.get('path', '')
    name = data.get('name', '')
    content = data.get('content', '')
    
    if not name:
        return jsonify({'error': 'File name required'}), 400
    
    users = load_users()
    user_role = users[username]['role']
    
    if user_role == 'owner':
        base_dir = UPLOAD_DIR
    else:
        base_dir = get_user_dir(username)
    
    if path:
        full_path = os.path.join(base_dir, path)
    else:
        full_path = base_dir
    
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(full_path)
    if not real_path.startswith(real_base):
        return jsonify({'error': 'Access denied'}), 403
    
    file_path = os.path.join(real_path, name)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(content)
    
    return jsonify({'message': 'File created'})

@app.route('/api/folder/delete', methods=['POST'])
@require_auth
def folder_delete(username):
    data = request.json
    path = data.get('path', '')
    
    if not path:
        return jsonify({'error': 'Path required'}), 400
    
    users = load_users()
    user_role = users[username]['role']
    
    if user_role == 'owner':
        base_dir = UPLOAD_DIR
    else:
        base_dir = get_user_dir(username)
    
    full_path = os.path.join(base_dir, path)
    real_base = os.path.realpath(base_dir)
    real_path = os.path.realpath(full_path)
    
    if not real_path.startswith(real_base):
        return jsonify({'error': 'Access denied'}), 403
    
    if not os.path.exists(real_path):
        return jsonify({'error': 'Path does not exist'}), 404
    
    if os.path.isdir(real_path):
        shutil.rmtree(real_path)
    else:
        os.remove(real_path)
    
    return jsonify({'message': 'Deleted successfully'})

@app.route('/api/processes')
@require_auth
def get_processes(username):
    cleanup_dead_processes()
    
    processes = load_processes()
    result = []
    users = load_users()
    user_role = users[username]['role']
    
    for file_id, info in processes.items():
        if user_role == 'owner' or info.get('username') == username:
            result.append({
                'id': file_id,
                'pid': info['pid'],
                'filename': info.get('filename', 'Unknown'),
                'started': info.get('started', 'Unknown'),
                'status': 'running',
                'username': info.get('username', 'unknown'),
                'installed': info.get('installed', [])
            })
    
    return jsonify({'processes': result})

@app.route('/api/logs')
@require_auth
def get_logs_route(username):
    logs = []
    for fname in os.listdir(LOGS_DIR):
        fpath = os.path.join(LOGS_DIR, fname)
        if os.path.isfile(fpath):
            with open(fpath, 'r') as f:
                logs.append(f'=== {fname} ===\n{f.read()}\n')
    return jsonify({'logs': '\n'.join(logs) if logs else 'No logs available'})

@app.route('/api/terminal/start', methods=['POST'])
@require_auth
def terminal_start(username):
    session_id = request.json.get('session_id')
    if not session_id:
        session_id = str(uuid.uuid4())[:8]
    
    user_dir = get_user_dir(username)
    
    session = get_terminal_session(session_id)
    if session.start(cwd=user_dir):
        return jsonify({'session_id': session_id, 'status': 'started'})
    return jsonify({'error': 'Failed to start terminal'}), 500

@app.route('/api/terminal/command', methods=['POST'])
@require_auth
def terminal_command(username):
    data = request.json
    session_id = data.get('session_id')
    command = data.get('command', '')
    
    if not session_id:
        return jsonify({'error': 'Session ID required'}), 400
    
    session = get_terminal_session(session_id)
    if not session.running:
        return jsonify({'error': 'Terminal not running'}), 400
    
    if session.write(command + '\n'):
        return jsonify({'status': 'ok'})
    return jsonify({'error': 'Failed to execute command'}), 500

@app.route('/api/terminal/output/<session_id>')
@require_auth
def terminal_output(username, session_id):
    def generate():
        session = get_terminal_session(session_id)
        last_output = ''
        
        while True:
            if not session.running:
                if session.exit_code is not None:
                    yield f"data: {json.dumps({'type': 'end', 'code': session.exit_code})}\n\n"
                else:
                    yield f"data: {json.dumps({'type': 'end'})}\n\n"
                break
            
            output = session.read_output()
            if output:
                yield f"data: {json.dumps({'type': 'output', 'data': output})}\n\n"
            
            time.sleep(0.05)
    
    return Response(stream_with_context(generate()),
                   mimetype='text/event-stream',
                   headers={
                       'Cache-Control': 'no-cache',
                       'X-Accel-Buffering': 'no'
                   })

@app.route('/api/terminal/stop/<session_id>', methods=['POST'])
@require_auth
def terminal_stop(username, session_id):
    if session_id in terminal_sessions:
        terminal_sessions[session_id].stop()
        del terminal_sessions[session_id]
        return jsonify({'status': 'stopped'})
    return jsonify({'error': 'Session not found'}), 404

@app.route('/api/stats')
@require_auth
def get_stats(username):
    users = load_users()
    user_role = users[username]['role']
    
    if user_role == 'owner':
        all_files = get_all_files()
        running = sum(1 for f in all_files if f['status'] == 'running')
        stopped = sum(1 for f in all_files if f['status'] == 'stopped')
        return jsonify({
            'total_users': len(users),
            'total_files': len(all_files),
            'running': running,
            'stopped': stopped
        })
    else:
        user_files = get_user_files(username)
        running = sum(1 for f in user_files if f['status'] == 'running')
        stopped = sum(1 for f in user_files if f['status'] == 'stopped')
        return jsonify({
            'total_users': 1,
            'total_files': len(user_files),
            'running': running,
            'stopped': stopped
        })

@app.route('/health')
def health():
    return jsonify({
        'status': 'healthy',
        'service': 'PYTHON HOSTING - Render',
        'timestamp': datetime.now().isoformat()
    })

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Endpoint not found'}), 404

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'Internal server error'}), 500

# ============================================================
# HTML TEMPLATE
# ============================================================

HTML_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>PYTHON HOSTING</title>
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700;800;900&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            background: #050807; 
            color: #00ff88; 
            font-family: 'Orbitron', 'Courier New', monospace; 
            display: flex; 
            justify-content: center; 
            align-items: flex-start; 
            min-height: 100vh; 
            padding: 20px 10px;
            background-image: radial-gradient(circle at 20% 30%, rgba(0,255,136,0.03) 0%, transparent 60%);
        }
        .auth-container { 
            border: 1.5px solid rgba(0,255,136,0.15); 
            padding: 35px 30px 30px; 
            width: 400px; 
            background: rgba(5,8,7,0.9); 
            text-align: center; 
            border-radius: 12px;
            box-shadow: 0 8px 40px rgba(0,0,0,0.8);
            margin-top: 50px;
        }
        .auth-container .brand h1 { font-size: 1.6rem; font-weight: 700; color: #00ff88; letter-spacing: 3px; text-transform: uppercase; }
        .auth-container .brand .highlight { color: #33ddff; }
        .auth-container .brand .sub { font-size: 0.6rem; letter-spacing: 5px; opacity: 0.3; margin-top: 4px; text-transform: uppercase; }
        .auth-container .divider { height: 1px; background: linear-gradient(90deg, transparent, rgba(0,255,136,0.15), transparent); margin: 16px 0 20px 0; }
        .auth-container h2 { font-size: 0.7rem; letter-spacing: 4px; opacity: 0.4; font-weight: 400; margin-bottom: 18px; text-transform: uppercase; }
        .auth-container .input-group { text-align: left; margin-bottom: 14px; }
        .auth-container .input-group label { display: block; font-size: 0.65rem; letter-spacing: 2px; opacity: 0.5; margin-bottom: 4px; text-transform: uppercase; }
        .auth-container .input-group input { width: 100%; background: rgba(0,0,0,0.6); border: 1.5px solid rgba(0,255,136,0.12); color: #00ff88; padding: 12px 14px; border-radius: 8px; outline: none; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.85rem; }
        .auth-container .input-group input:focus { border-color: rgba(0,255,136,0.4); }
        .auth-container .btn-primary { width: 100%; padding: 14px; margin-top: 8px; cursor: pointer; border: 1.5px solid rgba(0,255,136,0.3); background: rgba(0,255,136,0.04); color: #00ff88; font-weight: 700; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.85rem; border-radius: 8px; transition: all 0.2s ease; letter-spacing: 2px; text-transform: uppercase; }
        .auth-container .btn-primary:hover { background: #00ff88; color: #000; border-color: #00ff88; }
        .auth-container .btn-secondary { width: 100%; padding: 12px; margin-top: 6px; cursor: pointer; border: 1.5px solid rgba(255,255,255,0.3); background: rgba(255,255,255,0.04); color: #ffffff; font-weight: 700; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.75rem; border-radius: 8px; transition: all 0.2s ease; letter-spacing: 1px; text-transform: uppercase; }
        .auth-container .btn-secondary:hover { background: #ffffff; color: #000; border-color: #ffffff; }
        .auth-container .login-error { color: #ff4466; font-size: 0.7rem; margin-top: 8px; display: none; }
        .auth-container .login-error.show { display: block; }
        .auth-container .contact-btn { margin-top: 10px; background: rgba(255,255,255,0.05); border-color: rgba(255,255,255,0.2); }
        .auth-container .contact-btn:hover { background: #ffffff; color: #000; border-color: #ffffff; }
        .toast { position: fixed; top: 25px; left: 50%; transform: translateX(-50%) translateY(-120px); background: rgba(0,255,136,0.06); border: 1.5px solid rgba(0,255,136,0.25); color: #00ff88; padding: 16px 32px; border-radius: 12px; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.85rem; z-index: 9999; backdrop-filter: blur(16px); transition: all 0.5s cubic-bezier(0.22, 1, 0.36, 1); opacity: 0; pointer-events: none; text-align: center; min-width: 280px; }
        .toast.show { opacity: 1; transform: translateX(-50%) translateY(0); }
        .toast.error { border-color: rgba(255,51,85,0.35); color: #ff4466; background: rgba(255,51,85,0.06); }
        .dashboard-btn { background: rgba(0, 255, 136, 0.08); border: 1.5px solid rgba(0, 255, 136, 0.3); color: #00ff88; padding: 12px 14px; cursor: pointer; text-align: center; font-weight: 600; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.7rem; transition: all 0.15s ease; border-radius: 8px; letter-spacing: 0.5px; width: 100%; }
        .dashboard-btn:hover { background: #00ff88; color: #000; border-color: #00ff88; }
        .dashboard-btn.red { border-color: rgba(255, 51, 85, 0.4); color: #ff3355; }
        .dashboard-btn.red:hover { background: #ff3355; color: #000; }
        .dashboard-btn.blue { border-color: rgba(51, 221, 255, 0.4); color: #33ddff; }
        .dashboard-btn.blue:hover { background: #33ddff; color: #000; }
        .dashboard-btn.green { border-color: rgba(0, 255, 136, 0.4); color: #00ff88; }
        .dashboard-btn.green:hover { background: #00ff88; color: #000; }
        .dashboard-btn.orange { border-color: rgba(255, 170, 51, 0.4); color: #ffaa33; }
        .dashboard-btn.orange:hover { background: #ffaa33; color: #000; }
        .dashboard-btn.purple { border-color: rgba(187, 136, 255, 0.4); color: #bb88ff; }
        .dashboard-btn.purple:hover { background: #bb88ff; color: #000; }
        .dashboard-btn.white { border-color: rgba(255,255,255,0.4); color: #ffffff; background: rgba(255,255,255,0.05); }
        .dashboard-btn.white:hover { background: #ffffff; color: #000; }
        .p-small { font-size: 0.6rem; opacity: 0.4; margin: 4px 0 2px 0; letter-spacing: 1px; }
        .box { border: 1px solid rgba(0, 255, 136, 0.15); padding: 16px 14px; margin-bottom: 14px; background: #0a100e; border-radius: 10px; max-width: 520px; margin-left: auto; margin-right: auto; }
        .box h2 { font-size: 0.7rem; margin: 0 0 10px 0; color: #00ff88; letter-spacing: 3px; text-transform: uppercase; opacity: 0.8; font-weight: 500; }
        .stat-grid { display: grid; grid-template-columns: 1fr 1fr 1fr 1fr; gap: 8px; margin-bottom: 12px; }
        .stat-box { border: 1px solid rgba(0, 255, 136, 0.1); padding: 10px 4px; text-align: center; background: rgba(0, 255, 136, 0.03); border-radius: 8px; }
        .stat-num { font-size: 1.5rem; font-weight: 700; display: block; line-height: 1.3; color: #00ff88; }
        .stat-label { font-size: 0.45rem; letter-spacing: 1px; opacity: 0.4; text-transform: uppercase; }
        input, textarea, select { width: 100%; background: rgba(0,0,0,0.6); border: 1.5px solid rgba(0, 255, 136, 0.15); color: #00ff88; padding: 10px 12px; margin: 4px 0; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.7rem; border-radius: 8px; outline: none; }
        input:focus, textarea:focus, select:focus { border-color: rgba(0, 255, 136, 0.5); }
        textarea { resize: vertical; min-height: 100px; }
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.85); backdrop-filter: blur(6px); z-index: 999; justify-content: center; align-items: center; }
        .modal-overlay.active { display: flex; }
        .modal-box { background: #0a100e; border: 1.5px solid rgba(0,255,136,0.2); border-radius: 12px; padding: 25px 20px; width: 420px; max-width: 92%; max-height: 90vh; overflow-y: auto; }
        .modal-box h3 { color: #00ff88; font-size: 0.9rem; letter-spacing: 2px; margin-bottom: 14px; text-transform: uppercase; opacity: 0.8; font-weight: 500; }
        .modal-actions { display: flex; gap: 10px; margin-top: 14px; }
        .modal-actions .dashboard-btn { flex: 1; text-align: center; padding: 10px; margin: 0; font-size: 0.6rem; }
        .modal-actions .dashboard-btn.cancel { background: transparent; border-color: rgba(255,68,102,0.2); color: #ff4466; }
        .modal-actions .dashboard-btn.cancel:hover { background: #ff4466; color: #000; }
        .file-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 10px; margin: 3px 0; border: 1px solid rgba(0,255,136,0.06); border-radius: 6px; background: rgba(0,0,0,0.2); flex-wrap: wrap; gap: 4px; cursor: default; -webkit-touch-callout: none; -webkit-user-select: none; user-select: none; }
        .file-item .file-info { display: flex; align-items: center; gap: 8px; font-size: 0.7rem; flex-wrap: wrap; }
        .file-item .file-info .name { color: #00ff88; }
        .file-item .file-info .size { opacity: 0.3; font-size: 0.55rem; }
        .file-item .file-actions { display: flex; gap: 4px; flex-wrap: wrap; }
        .file-item .file-actions .btn-sm { padding: 2px 8px; font-size: 0.45rem; border: 1px solid rgba(0,255,136,0.15); border-radius: 4px; background: transparent; color: #00ff88; cursor: pointer; font-family: 'Orbitron', 'Courier New', monospace; letter-spacing: 0.5px; }
        .file-item .file-actions .btn-sm:hover { background: #00ff88; color: #000; }
        .file-item .file-actions .btn-sm.red { color: #ff4466; border-color: rgba(255,68,102,0.2); }
        .file-item .file-actions .btn-sm.red:hover { background: #ff4466; color: #000; }
        .file-item .file-actions .btn-sm.orange { color: #ffaa33; border-color: rgba(255,170,51,0.2); }
        .file-item .file-actions .btn-sm.orange:hover { background: #ffaa33; color: #000; }
        .file-item .file-actions .btn-sm.blue { color: #33ddff; border-color: rgba(51,221,255,0.2); }
        .file-item .file-actions .btn-sm.blue:hover { background: #33ddff; color: #000; }
        .file-item .file-actions .btn-sm.purple { color: #bb88ff; border-color: rgba(187,136,255,0.2); }
        .file-item .file-actions .btn-sm.purple:hover { background: #bb88ff; color: #000; }
        .file-item .file-actions .btn-sm.white { color: #ffffff; border-color: rgba(255,255,255,0.2); }
        .file-item .file-actions .btn-sm.white:hover { background: #ffffff; color: #000; }
        .file-item .file-actions .btn-sm.green { color: #00ff88; border-color: rgba(0,255,136,0.2); }
        .file-item .file-actions .btn-sm.green:hover { background: #00ff88; color: #000; }
        .file-item .file-actions .btn-sm.download { color: #33ddff; border-color: rgba(51,221,255,0.2); }
        .file-item .file-actions .btn-sm.download:hover { background: #33ddff; color: #000; }
        .status-badge { font-size: 0.45rem; padding: 2px 10px; border-radius: 12px; border: 1px solid; text-transform: uppercase; letter-spacing: 1px; }
        .status-badge.running { color: #00ff88; border-color: rgba(0,255,136,0.3); }
        .status-badge.stopped { color: #ff4466; border-color: rgba(255,68,102,0.3); }
        .role-badge { font-size: 0.4rem; padding: 2px 10px; border-radius: 12px; border: 1px solid; text-transform: uppercase; letter-spacing: 1px; }
        .role-badge.owner { color: #ffaa33; border-color: rgba(255,170,51,0.3); }
        .role-badge.admin { color: #33ddff; border-color: rgba(51,221,255,0.3); }
        .role-badge.user { color: #00ff88; border-color: rgba(0,255,136,0.3); }
        .top-bar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px; padding-bottom: 12px; border-bottom: 1px solid rgba(0,255,136,0.08); max-width: 520px; margin-left: auto; margin-right: auto; }
        .top-bar .title { font-size: 1.1rem; font-weight: 700; letter-spacing: 2px; }
        .top-bar .title span { color: #33ddff; }
        .profile-icon { width: 44px; height: 44px; border-radius: 50%; background: rgba(0,255,136,0.08); border: 1.5px solid rgba(0,255,136,0.25); color: #00ff88; display: flex; align-items: center; justify-content: center; font-size: 1rem; font-weight: 700; cursor: pointer; text-transform: uppercase; }
        .profile-icon:hover { background: #00ff88; color: #000; border-color: #00ff88; }
        .profile-dropdown { display: none; position: absolute; right: 0; top: 55px; width: 280px; max-height: 80vh; overflow-y: auto; background: #0a100e; border: 1px solid rgba(0,255,136,0.15); padding: 14px; border-radius: 10px; flex-direction: column; gap: 2px; z-index: 20; backdrop-filter: blur(12px); }
        .profile-dropdown.open { display: flex; }
        .profile-dropdown .user-info { display: flex; flex-direction: column; gap: 2px; border-bottom: 1px solid rgba(0,255,136,0.08); padding-bottom: 10px; margin-bottom: 8px; }
        .profile-dropdown .user-name { font-size: 0.9rem; font-weight: 600; color: #00ff88; }
        .profile-dropdown .user-role { font-size: 0.5rem; opacity: 0.35; letter-spacing: 2px; text-transform: uppercase; }
        .profile-dropdown .section-label { font-size: 0.45rem; opacity: 0.3; letter-spacing: 3px; text-transform: uppercase; padding: 6px 4px 2px 4px; border-top: 1px solid rgba(0,255,136,0.05); margin-top: 4px; }
        .profile-dropdown .dashboard-btn { background: transparent; border: 1px solid rgba(0,255,136,0.06); color: #00ff88; padding: 8px 10px; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.6rem; text-align: left; cursor: pointer; border-radius: 6px; margin: 1px 0; transition: all 0.15s ease; width: 100%; }
        .profile-dropdown .dashboard-btn:hover { background: rgba(0,255,136,0.1); border-color: rgba(0,255,136,0.2); }
        .profile-dropdown .dashboard-btn.red { color: #ff4466; border-color: rgba(255,68,102,0.1); }
        .profile-dropdown .dashboard-btn.red:hover { background: rgba(255,68,102,0.1); border-color: rgba(255,68,102,0.3); }
        .btn-vertical { display: flex; flex-direction: column; gap: 8px; }
        #dashboardPage { display: none; }
        .top-bar { position: relative; }
        .status-info { background: rgba(0,255,136,0.05); border: 1px solid rgba(0,255,136,0.1); padding: 8px 12px; border-radius: 6px; margin-bottom: 10px; font-size: 0.6rem; letter-spacing: 1px; }
        .node-list { font-size: 0.7rem; line-height: 2; border-left: 2px solid rgba(0, 255, 136, 0.1); padding-left: 14px; margin: 6px 0 12px 0; }
        .node-item { display: flex; justify-content: space-between; align-items: center; padding: 2px 0; border-bottom: 1px solid rgba(0,255,136,0.04); }
        .node-item .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 8px; }
        .node-item .status-dot.running { background: #00ff88; }
        .node-item .status-dot.stopped { background: #ff4466; }
        .user-item { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; margin: 2px 0; border: 1px solid rgba(0,255,136,0.06); border-radius: 6px; background: rgba(0,0,0,0.2); }
        .user-item .user-info { display: flex; align-items: center; gap: 8px; font-size: 0.7rem; flex-wrap: wrap; }
        .user-item .user-info .uname { color: #00ff88; }
        .user-item .user-info .created { opacity: 0.3; font-size: 0.5rem; }
        .user-item .user-actions { display: flex; gap: 4px; }
        .terminal-container { background: #050807; border: 1px solid rgba(0,255,136,0.1); border-radius: 8px; padding: 12px; max-height: 300px; overflow-y: auto; font-family: 'Courier New', monospace; font-size: 0.7rem; color: #00ff88; white-space: pre-wrap; word-break: break-all; }
        .terminal-container::-webkit-scrollbar { width: 4px; }
        .terminal-container::-webkit-scrollbar-track { background: rgba(0,255,136,0.05); }
        .terminal-container::-webkit-scrollbar-thumb { background: rgba(0,255,136,0.2); border-radius: 2px; }
        .terminal-input-row { display: flex; gap: 8px; margin-top: 8px; }
        .terminal-input-row input { flex: 1; padding: 8px 10px; font-size: 0.7rem; background: rgba(0,0,0,0.6); border: 1.5px solid rgba(0,255,136,0.15); color: #00ff88; border-radius: 6px; font-family: 'Courier New', monospace; }
        .terminal-input-row input:focus { border-color: rgba(0,255,136,0.4); outline: none; }
        .terminal-input-row .dashboard-btn { flex: 0 0 auto; width: auto; padding: 8px 16px; font-size: 0.55rem; }
        .folder-nav { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; padding: 8px; background: rgba(0,0,0,0.3); border-radius: 6px; font-size: 0.6rem; }
        .folder-nav .crumb { color: #33ddff; cursor: pointer; opacity: 0.6; transition: opacity 0.2s; }
        .folder-nav .crumb:hover { opacity: 1; }
        .folder-nav .separator { opacity: 0.2; padding: 0 2px; }
        .folder-nav .current { opacity: 1; color: #00ff88; }
        .folder-item { display: flex; justify-content: space-between; align-items: center; padding: 6px 10px; margin: 2px 0; border: 1px solid rgba(0,255,136,0.06); border-radius: 6px; background: rgba(0,0,0,0.2); cursor: pointer; transition: background 0.15s; }
        .folder-item:hover { background: rgba(0,255,136,0.05); }
        .folder-item .folder-info { display: flex; align-items: center; gap: 8px; font-size: 0.7rem; flex-wrap: wrap; }
        .folder-item .folder-info .icon { font-size: 1rem; }
        .folder-item .folder-info .name { color: #33ddff; }
        .folder-item .folder-actions { display: flex; gap: 4px; flex-wrap: wrap; }
        .folder-item .folder-actions .btn-sm { padding: 2px 8px; font-size: 0.4rem; border: 1px solid rgba(0,255,136,0.15); border-radius: 4px; background: transparent; color: #00ff88; cursor: pointer; font-family: 'Orbitron', 'Courier New', monospace; }
        .folder-item .folder-actions .btn-sm:hover { background: #00ff88; color: #000; }
        .folder-item .folder-actions .btn-sm.red { color: #ff4466; border-color: rgba(255,68,102,0.2); }
        .folder-item .folder-actions .btn-sm.red:hover { background: #ff4466; color: #000; }
        .folder-item .folder-actions .btn-sm.orange { color: #ffaa33; border-color: rgba(255,170,51,0.2); }
        .folder-item .folder-actions .btn-sm.orange:hover { background: #ffaa33; color: #000; }
        .folder-item .folder-actions .btn-sm.blue { color: #33ddff; border-color: rgba(51,221,255,0.2); }
        .folder-item .folder-actions .btn-sm.blue:hover { background: #33ddff; color: #000; }
        .folder-item .folder-actions .btn-sm.purple { color: #bb88ff; border-color: rgba(187,136,255,0.2); }
        .folder-item .folder-actions .btn-sm.purple:hover { background: #bb88ff; color: #000; }
        .folder-item .folder-actions .btn-sm.white { color: #ffffff; border-color: rgba(255,255,255,0.2); }
        .folder-item .folder-actions .btn-sm.white:hover { background: #ffffff; color: #000; }
        .folder-item .folder-actions .btn-sm.green { color: #00ff88; border-color: rgba(0,255,136,0.2); }
        .folder-item .folder-actions .btn-sm.green:hover { background: #00ff88; color: #000; }
        .folder-item .folder-actions .btn-sm.download { color: #33ddff; border-color: rgba(51,221,255,0.2); }
        .folder-item .folder-actions .btn-sm.download:hover { background: #33ddff; color: #000; }
        .folder-manager-toolbar { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; }
        .folder-manager-toolbar .dashboard-btn { flex: 1; min-width: 80px; padding: 8px 10px; font-size: 0.55rem; }
        .search-input { flex: 2; min-width: 120px; padding: 8px 10px; font-size: 0.55rem; background: rgba(0,0,0,0.6); border: 1.5px solid rgba(0,255,136,0.15); color: #00ff88; border-radius: 6px; font-family: 'Orbitron', 'Courier New', monospace; }
        .search-input:focus { border-color: rgba(0,255,136,0.4); outline: none; }
        .context-menu { display: none; position: fixed; background: #0a100e; border: 1px solid rgba(0,255,136,0.15); border-radius: 8px; padding: 6px 0; min-width: 180px; z-index: 1000; backdrop-filter: blur(12px); box-shadow: 0 8px 30px rgba(0,0,0,0.6); }
        .context-menu.open { display: block; }
        .context-menu .menu-item { padding: 8px 16px; font-size: 0.6rem; color: #00ff88; cursor: pointer; transition: background 0.15s; font-family: 'Orbitron', 'Courier New', monospace; letter-spacing: 0.5px; }
        .context-menu .menu-item:hover { background: rgba(0,255,136,0.08); }
        .context-menu .menu-item.red { color: #ff4466; }
        .context-menu .menu-item.red:hover { background: rgba(255,68,102,0.08); }
        .context-menu .menu-item.blue { color: #33ddff; }
        .context-menu .menu-item.blue:hover { background: rgba(51,221,255,0.08); }
        .context-menu .menu-item.green { color: #00ff88; }
        .context-menu .menu-item.green:hover { background: rgba(0,255,136,0.08); }
        .context-menu .menu-item.orange { color: #ffaa33; }
        .context-menu .menu-item.orange:hover { background: rgba(255,170,51,0.08); }
        .context-menu .menu-item.white { color: #ffffff; }
        .context-menu .menu-item.white:hover { background: rgba(255,255,255,0.08); }
        .context-menu .menu-item.purple { color: #bb88ff; }
        .context-menu .menu-item.purple:hover { background: rgba(187,136,255,0.08); }
        .context-menu .menu-divider { height: 1px; background: rgba(0,255,136,0.06); margin: 4px 12px; }
        .file-details-grid { display: grid; grid-template-columns: 1fr 2fr; gap: 6px 12px; font-size: 0.65rem; margin: 10px 0; }
        .file-details-grid .label { opacity: 0.4; }
        .file-details-grid .value { color: #00ff88; word-break: break-all; }
        .table-container { overflow-x: auto; -webkit-overflow-scrolling: touch; }
        table { width: 100%; border-collapse: collapse; font-size: 0.6rem; min-width: 300px; }
        th, td { padding: 6px 8px; text-align: left; border-bottom: 1px solid rgba(0,255,136,0.06); }
        th { color: #33ddff; opacity: 0.6; font-weight: 400; letter-spacing: 1px; }
        td { color: #00ff88; }
        .action-btns { display: flex; gap: 4px; flex-wrap: wrap; }
        .action-btns .btn-sm { padding: 2px 6px; font-size: 0.4rem; border: 1px solid rgba(0,255,136,0.15); border-radius: 4px; background: transparent; color: #00ff88; cursor: pointer; font-family: 'Orbitron', 'Courier New', monospace; }
        .action-btns .btn-sm:hover { background: #00ff88; color: #000; }
        .action-btns .btn-sm.red { color: #ff4466; border-color: rgba(255,68,102,0.2); }
        .action-btns .btn-sm.red:hover { background: #ff4466; color: #000; }
        .action-btns .btn-sm.orange { color: #ffaa33; border-color: rgba(255,170,51,0.2); }
        .action-btns .btn-sm.orange:hover { background: #ffaa33; color: #000; }
        .action-btns .btn-sm.blue { color: #33ddff; border-color: rgba(51,221,255,0.2); }
        .action-btns .btn-sm.blue:hover { background: #33ddff; color: #000; }
        .action-btns .btn-sm.purple { color: #bb88ff; border-color: rgba(187,136,255,0.2); }
        .action-btns .btn-sm.purple:hover { background: #bb88ff; color: #000; }
        .action-btns .btn-sm.white { color: #ffffff; border-color: rgba(255,255,255,0.2); }
        .action-btns .btn-sm.white:hover { background: #ffffff; color: #000; }
        .action-btns .btn-sm.green { color: #00ff88; border-color: rgba(0,255,136,0.2); }
        .action-btns .btn-sm.green:hover { background: #00ff88; color: #000; }
        .action-btns .btn-sm.download { color: #33ddff; border-color: rgba(51,221,255,0.2); }
        .action-btns .btn-sm.download:hover { background: #33ddff; color: #000; }
        .hidden { display: none !important; }
        .mt-2 { margin-top: 8px; }
        .flex { display: flex; gap: 8px; }
        .flex .dashboard-btn { flex: 1; }
        #loader { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: #050807; display: flex; flex-direction: column; justify-content: center; align-items: center; z-index: 1000; transition: opacity 0.6s ease; }
        .loader-ring { width: 80px; height: 80px; border-radius: 50%; border: 3px solid transparent; border-top: 3px solid #00ff88; border-right: 3px solid #33ddff; animation: spin 0.8s linear infinite; position: relative; margin-bottom: 24px; }
        .loader-ring::before { content: ''; position: absolute; top: 8px; left: 8px; right: 8px; bottom: 8px; border-radius: 50%; border: 3px solid transparent; border-bottom: 3px solid #00ff88; border-left: 3px solid #33ddff; animation: spin 0.5s linear infinite reverse; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        #loader .loader-text { font-size: 0.9rem; letter-spacing: 6px; opacity: 0.6; text-transform: uppercase; background: linear-gradient(90deg, #00ff88, #33ddff); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        #loader .loader-progress { margin-top: 16px; width: 200px; height: 2px; background: rgba(0,255,136,0.08); border-radius: 2px; overflow: hidden; }
        #loader .loader-progress .bar { height: 100%; width: 0%; background: linear-gradient(90deg, #00ff88, #33ddff); border-radius: 2px; transition: width 0.15s ease; }
        .file-input-wrapper { position: relative; width: 100%; }
        .file-input-wrapper input[type="file"] { position: absolute; opacity: 0; width: 100%; height: 100%; cursor: pointer; top: 0; left: 0; }
        .file-input-wrapper .file-label { background: rgba(0,0,0,0.6); border: 1.5px solid rgba(0,255,136,0.15); color: #00ff88; padding: 10px 12px; border-radius: 8px; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.6rem; display: flex; align-items: center; justify-content: center; gap: 8px; cursor: pointer; min-height: 44px; text-align: center; width: 100%; }
        .file-input-wrapper .file-label:hover { border-color: rgba(0,255,136,0.4); }
        .file-input-wrapper .file-label .file-count { background: rgba(0,255,136,0.1); border-radius: 12px; padding: 0 10px; font-size: 0.5rem; border: 1px solid rgba(0,255,136,0.15); }
        .code-editor { font-family: 'Courier New', monospace; font-size: 0.65rem; background: #050807; color: #00ff88; border: 1px solid rgba(0,255,136,0.15); border-radius: 6px; padding: 10px; width: 100%; min-height: 200px; resize: vertical; tab-size: 4; }
        .code-editor:focus { border-color: rgba(0,255,136,0.4); outline: none; }
        .user-card { display: flex; justify-content: space-between; align-items: center; padding: 8px 12px; margin: 4px 0; border: 1px solid rgba(0,255,136,0.08); border-radius: 6px; background: rgba(0,0,0,0.2); flex-wrap: wrap; gap: 6px; }
        .user-card .user-data { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 0.65rem; }
        .user-card .user-data .username { color: #00ff88; font-weight: 600; }
        .user-card .user-data .password { opacity: 0.5; font-size: 0.55rem; }
        .user-card .user-data .role-badge { font-size: 0.4rem; }
        .user-card .user-actions .btn-sm { padding: 2px 10px; font-size: 0.45rem; border: 1px solid rgba(255,68,102,0.2); border-radius: 4px; background: transparent; color: #ff4466; cursor: pointer; font-family: 'Orbitron', 'Courier New', monospace; }
        .user-card .user-actions .btn-sm:hover { background: #ff4466; color: #000; }
        .popup-overlay { display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.9); backdrop-filter: blur(10px); z-index: 99999; justify-content: center; align-items: center; }
        .popup-overlay.active { display: flex; }
        .popup-box { background: #0a100e; border: 1.5px solid rgba(0,255,136,0.2); border-radius: 16px; padding: 30px 28px; width: 360px; max-width: 92%; text-align: center; box-shadow: 0 20px 60px rgba(0,0,0,0.8); }
        .popup-box .popup-title { font-size: 1.2rem; font-weight: 700; color: #00ff88; letter-spacing: 2px; margin-bottom: 10px; }
        .popup-box .popup-sub { font-size: 0.65rem; opacity: 0.4; letter-spacing: 1px; margin-bottom: 20px; }
        .popup-box .popup-btn { padding: 12px 30px; cursor: pointer; border: 1.5px solid rgba(0,255,136,0.3); background: rgba(0,255,136,0.05); color: #00ff88; font-weight: 600; font-family: 'Orbitron', 'Courier New', monospace; font-size: 0.7rem; border-radius: 10px; transition: all 0.2s ease; letter-spacing: 1px; margin: 6px 8px; min-width: 120px; }
        .popup-box .popup-btn:hover { background: #00ff88; color: #000; border-color: #00ff88; }
        .popup-box .popup-btn.close-btn { border-color: rgba(255,68,102,0.3); color: #ff4466; background: rgba(255,68,102,0.05); }
        .popup-box .popup-btn.close-btn:hover { background: #ff4466; color: #000; }
        .popup-box .popup-icon { font-size: 3rem; margin-bottom: 10px; }
        @media (max-width: 480px) {
            .auth-container { width: 95%; padding: 25px 16px; }
            .auth-container .brand h1 { font-size: 1.2rem; }
            .stat-grid { grid-template-columns: 1fr 1fr; gap: 6px; }
            .stat-box { padding: 8px 2px; }
            .stat-num { font-size: 1.2rem; }
            .profile-dropdown { width: 220px; right: 0; }
            .modal-box { width: 95%; padding: 20px 14px; }
            .file-item .file-actions .btn-sm { font-size: 0.4rem; padding: 1px 4px; }
            .terminal-input-row { flex-direction: column; }
            .context-menu { min-width: 150px; }
            .table-container { font-size: 0.5rem; }
            .action-btns .btn-sm { font-size: 0.35rem; padding: 1px 4px; }
            .user-card { flex-direction: column; align-items: stretch; }
            .user-card .user-data { justify-content: center; }
            .user-card .user-actions { text-align: center; }
            .folder-manager-toolbar .dashboard-btn { min-width: 60px; font-size: 0.45rem; }
            .folder-item .folder-actions .btn-sm { font-size: 0.35rem; padding: 1px 4px; }
            .popup-box { padding: 25px 16px; width: 95%; }
            .popup-box .popup-btn { min-width: 80px; padding: 10px 18px; font-size: 0.6rem; }
        }
    </style>
</head>
<body>

<div id="loader">
    <div class="loader-ring"></div>
    <div class="loader-text">⚡ Loading...</div>
    <div class="loader-progress"><div class="bar" id="loaderBar"></div></div>
</div>

<div class="toast" id="toast">
    <span id="toastMessage">ACCESS GRANTED</span>
    <div id="toastSub" style="font-size:0.5rem;opacity:0.35;margin-top:3px;letter-spacing:1px;">SECURE CONNECTION ESTABLISHED</div>
</div>

<!-- POPUP -->
<div class="popup-overlay" id="popupOverlay">
    <div class="popup-box">
        <div class="popup-icon">📢</div>
        <div class="popup-title">JOIN OUR CHANNEL</div>
        <div class="popup-sub">Stay updated with latest news and updates</div>
        <div>
            <button class="popup-btn" id="popupJoinBtn">🔗 Join Now</button>
            <button class="popup-btn close-btn" id="popupCloseBtn">❌ Close</button>
        </div>
    </div>
</div>

<!-- LOGIN -->
<div class="auth-container" id="loginBox">
    <div class="brand">
        <h1>🐍 PYTHON <span class="highlight">HOSTING</span></h1>
        <div class="sub">⚡ Render Cloud Shell ⚡</div>
    </div>
    <div class="divider"></div>
    <h2>▶ AUTHENTICATION REQUIRED</h2>
    <div class="input-group">
        <label>👤 USERNAME</label>
        <input type="text" id="user" placeholder="Enter Username">
    </div>
    <div class="input-group">
        <label>🔒 PASSWORD</label>
        <input type="password" id="pass" placeholder="Enter Password">
    </div>
    <div class="login-error" id="loginError">❌ Invalid username or password</div>
    <button class="btn-primary" id="unlock-btn">⚡ UNLOCK DASHBOARD</button>
    <button class="btn-secondary" id="createAccountBtn">➕ CREATE ACCOUNT</button>
    <button class="btn-secondary contact-btn" id="contactOwnerBtn">📧 DM TO BUY</button>
    <div style="margin-top:22px;font-size:0.5rem;opacity:0.15;letter-spacing:2px;text-transform:uppercase;">
        UNAUTHORIZED ACCESS PROHIBITED
    </div>
</div>

<!-- DASHBOARD -->
<div id="dashboardPage">
    <div class="top-bar">
        <div class="title">🐍 <span>PYTHON</span> HOSTING</div>
        <div style="position:relative;">
            <div class="profile-icon" id="profileIcon">R</div>
            <div class="profile-dropdown" id="profileDropdown">
                <div class="user-info">
                    <span class="user-name" id="dropdownUsername">👤 riyaj</span>
                    <span class="user-role" id="dropdownRole">ROLE: OWNER</span>
                </div>
                <div class="section-label">👤 ACCOUNT</div>
                <button class="dashboard-btn" id="menuChangeUsername">✏️ Change Username</button>
                <button class="dashboard-btn" id="menuChangePassword">🔑 Change Password</button>
                <button class="dashboard-btn" id="menuDownloadAll">📦 Download All Files</button>
                
                <div class="section-label">📁 ADMIN</div>
                <button class="dashboard-btn" id="menuMyFiles">📂 My Files</button>
                <button class="dashboard-btn" id="menuMyNodes">🖥 My Nodes</button>
                <button class="dashboard-btn" id="menuAllUsers">👥 All Users</button>
                <button class="dashboard-btn" id="menuAddUser">➕ Add User</button>
                
                <div class="section-label">👑 OWNER</div>
                <button class="dashboard-btn" id="menuAllFiles">📁 All Files</button>
                <button class="dashboard-btn" id="menuAdminsList">👑 Admins List</button>
                <button class="dashboard-btn" id="menuAddAdmin">➕ Add Admin</button>
                <button class="dashboard-btn" id="menuTerminal">💻 Terminal</button>
                <button class="dashboard-btn" id="menuSettings">⚙ Settings</button>
                
                <div class="section-label">⚙ SYSTEM</div>
                <button class="dashboard-btn red" id="menuLogout">🚪 Logout</button>
            </div>
        </div>
    </div>

    <div id="mainContent">
        <div id="pageDashboard">
            <div class="box">
                <div class="status-info" id="statusInfo">
                    🤖 Running Files: <span id="botStatus">0</span> | 📁 Files: <span id="fileCountStatus">0</span>
                </div>
                <div class="stat-grid">
                    <div class="stat-box"><span class="stat-num" id="stoppedCount">0</span><div class="stat-label">Stopped</div></div>
                    <div class="stat-box"><span class="stat-num" id="fileCount">0</span><div class="stat-label">Files</div></div>
                    <div class="stat-box"><span class="stat-num" id="runningCount">0</span><div class="stat-label">Running</div></div>
                    <div class="stat-box"><span class="stat-num" id="processCount">0</span><div class="stat-label">Processes</div></div>
                </div>
                <button class="dashboard-btn red" id="logoutBtn">🚪 Logout</button>
            </div>

            <div class="box">
                <h2>📤 Upload & Deploy</h2>
                <div style="display:flex;gap:8px;flex-wrap:wrap;">
                    <div class="file-input-wrapper" style="flex:2;min-width:150px;">
                        <input type="file" id="fileInput" multiple>
                        <div class="file-label" id="fileLabel">
                            📎 Select Files
                            <span class="file-count" id="fileCountBadge">0 selected</span>
                        </div>
                    </div>
                    <button class="dashboard-btn green" id="uploadBtn" style="flex:1;min-width:100px;">📦 Upload</button>
                </div>
                <p class="p-small" id="uploadStatus">Upload any files – .py, .zip, .json, .txt, etc. All go directly to your folder.</p>
            </div>

            <div class="box">
                <h2>⌨ Deploy Code</h2>
                <input type="text" id="pyFilename" value="main.py" placeholder="filename.py" style="font-size:0.7rem;">
                <textarea rows="5" id="pyCodeArea" placeholder="Paste your Python code here..." style="font-size:0.7rem;"></textarea>
                <button class="dashboard-btn blue" id="deployBtn">▶ Deploy & Run</button>
                <p class="p-small" id="deployStatus">Paste code and deploy (Auto-install missing modules)</p>
            </div>

            <div class="box">
                <h2>🖥 Running Nodes</h2>
                <div class="node-list" id="runningNodesList">
                    <div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">No nodes running</div>
                </div>
                <div class="btn-vertical">
                    <button class="dashboard-btn green" id="startAllBtn">▶ Start All</button>
                    <button class="dashboard-btn red" id="stopAllBtn">■ Stop All</button>
                </div>
            </div>

            <div class="box">
                <h2>📁 My Files</h2>
                <div id="filesList">
                    <div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">No files uploaded</div>
                </div>
            </div>

            <div class="box">
                <h2>⚙ Manage</h2>
                <div class="btn-vertical">
                    <button class="dashboard-btn blue" id="logsBtn">≡ View Logs</button>
                    <button class="dashboard-btn green" id="refreshBtn">🔄 Refresh</button>
                    <button class="dashboard-btn blue" id="fileManagerBtn">📂 File Manager</button>
                </div>
                <p class="p-small" id="manageStatus">Manage your files and processes</p>
            </div>
            
            <div id="contactOwnerDashboard" style="display:none;" class="box">
                <h2>📧 Contact Owner</h2>
                <button class="dashboard-btn" id="contactOwnerDashboardBtn" style="background:rgba(255,255,255,0.05);border-color:rgba(255,255,255,0.2);color:#ffffff;">📧 DM TO BUY</button>
                <p class="p-small">Direct contact to owner</p>
            </div>
        </div>

        <div id="pageAllUsers" class="hidden">
            <div class="box">
                <h2>👥 All Users</h2>
                <div id="allUsersList">
                    <div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">Loading users...</div>
                </div>
                <button class="dashboard-btn blue" id="backFromUsers">← Back</button>
            </div>
        </div>

        <div id="pageAdminsList" class="hidden">
            <div class="box">
                <h2>👑 Admins List</h2>
                <div id="adminsList">
                    <div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">Loading admins...</div>
                </div>
                <button class="dashboard-btn blue" id="backFromAdmins">← Back</button>
            </div>
        </div>

        <div id="pageAllFiles" class="hidden">
            <div class="box">
                <h2>📁 All Files</h2>
                <div class="table-container">
                    <table>
                        <thead>
                            <tr>
                                <th>Filename</th>
                                <th>Username</th>
                                <th>Size</th>
                                <th>Status</th>
                                <th>Actions</th>
                            </tr>
                        </thead>
                        <tbody id="allFilesList">
                            <tr><td colspan="5" style="text-align:center;opacity:0.3;">Loading files...</td></tr>
                        </tbody>
                    </table>
                </div>
                <button class="dashboard-btn blue" id="backFromAllFiles">← Back</button>
            </div>
        </div>

        <div id="pageMyNodes" class="hidden">
            <div class="box">
                <h2>🖥 My Nodes</h2>
                <div class="node-list" id="myNodesList">
                    <div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">No nodes running</div>
                </div>
                <button class="dashboard-btn blue" id="backFromNodes">← Back</button>
            </div>
        </div>

        <div id="pageTerminal" class="hidden">
            <div class="box">
                <h2>💻 Interactive Terminal</h2>
                <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
                    <button class="dashboard-btn green" id="terminalStartBtn" style="flex:1;min-width:80px;padding:8px 10px;font-size:0.55rem;">▶ Start</button>
                    <button class="dashboard-btn red" id="terminalStopBtn" style="flex:1;min-width:80px;padding:8px 10px;font-size:0.55rem;">■ Stop</button>
                    <button class="dashboard-btn orange" id="terminalClearBtn" style="flex:1;min-width:80px;padding:8px 10px;font-size:0.55rem;">✕ Clear</button>
                </div>
                <div class="terminal-container" id="terminalOutput">
                    <div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">Click Start to begin terminal session</div>
                </div>
                <div class="terminal-input-row">
                    <input type="text" id="terminalInput" placeholder="Enter command..." autofocus>
                    <button class="dashboard-btn blue" id="terminalSendBtn" style="flex:0;padding:8px 16px;font-size:0.55rem;">Send</button>
                </div>
                <button class="dashboard-btn blue" id="backFromTerminal" style="margin-top:10px;">← Back</button>
            </div>
        </div>

        <div id="pageFolderManager" class="hidden">
            <div class="box">
                <h2>📂 File Manager</h2>
                <div class="folder-nav" id="folderNav">
                    <span class="crumb" data-path="">📁 Root</span>
                </div>
                <div class="folder-manager-toolbar">
                    <input type="text" class="search-input" id="folderSearch" placeholder="🔍 Search files...">
                    <button class="dashboard-btn blue" id="folderCreateDir">📁 New Folder</button>
                    <button class="dashboard-btn green" id="folderCreateFile">📄 New File</button>
                    <button class="dashboard-btn orange" id="folderRefresh">🔄 Refresh</button>
                </div>
                <div id="folderContent">
                    <div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">Loading...</div>
                </div>
                <button class="dashboard-btn blue" id="backFromFolderManager">← Back</button>
            </div>
        </div>
    </div>
</div>

<!-- MODAL -->
<div class="modal-overlay" id="modalOverlay">
    <div class="modal-box">
        <h3 id="modalTitle">Modal</h3>
        <div id="modalBody"></div>
        <div class="modal-actions">
            <button class="dashboard-btn cancel" id="modalCancel">Cancel</button>
            <button class="dashboard-btn" id="modalConfirm">Confirm</button>
        </div>
    </div>
</div>

<div class="context-menu" id="contextMenu">
    <div class="menu-item" data-action="open">📂 Open</div>
    <div class="menu-item green" data-action="start">▶ Start</div>
    <div class="menu-item orange" data-action="stop">■ Stop</div>
    <div class="menu-item blue" data-action="download">⬇ Download</div>
    <div class="menu-item" data-action="edit">✏️ Edit</div>
    <div class="menu-item" data-action="rename">✏️ Rename</div>
    <div class="menu-item purple" data-action="details">ℹ️ Details</div>
    <div class="menu-divider"></div>
    <div class="menu-item white" data-action="openbot">🤖 Open Bot</div>
    <div class="menu-divider"></div>
    <div class="menu-item red" data-action="delete">🗑 Delete</div>
</div>

<script>
// ============================================================
// COMPLETE JAVASCRIPT
// ============================================================

(function() {
    "use strict";

    let currentUser = null;
    let currentRole = 'user';
    let isLoggedIn = false;
    let files = [];
    let processes = [];
    let allUsers = [];
    let allFiles = [];
    let autoRefreshInterval = null;
    let settings = {};
    let terminalSessionId = null;
    let terminalEventSource = null;
    let currentFolderPath = '';
    let folderContents = [];
    let contextTargetFileId = null;
    let contextTargetFileName = null;
    let contextTargetIsDir = false;
    let terminalCommandHistory = [];
    let terminalHistoryIndex = -1;
    let signupEnabled = true;

    const AUTO_LOGOUT_HOURS = 24;
    const CHECK_INTERVAL_MS = 60000;

    function checkAutoLogout() {
        if (!isLoggedIn || !currentUser) return;
        if (currentRole === 'owner' || currentRole === 'admin') return;
        
        const lastActivity = localStorage.getItem('lastActivity_' + currentUser);
        if (!lastActivity) {
            localStorage.setItem('lastActivity_' + currentUser, Date.now().toString());
            return;
        }
        
        const elapsed = Date.now() - parseInt(lastActivity);
        const maxAge = AUTO_LOGOUT_HOURS * 60 * 60 * 1000;
        
        if (elapsed > maxAge) {
            showToast('⏰ Session expired (24 hours)', true, 'Please login again');
            setTimeout(() => { logout(); }, 2000);
        }
    }

    setInterval(checkAutoLogout, CHECK_INTERVAL_MS);

    function updateLastActivity() {
        if (currentUser) {
            localStorage.setItem('lastActivity_' + currentUser, Date.now().toString());
        }
    }

    document.addEventListener('click', updateLastActivity);
    document.addEventListener('keypress', updateLastActivity);
    document.addEventListener('touchstart', updateLastActivity);

    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMessage');
    const toastSub = document.getElementById('toastSub');
    const loader = document.getElementById('loader');
    const loaderBar = document.getElementById('loaderBar');
    const loginBox = document.getElementById('loginBox');
    const dashboardPage = document.getElementById('dashboardPage');

    const pages = {
        dashboard: document.getElementById('pageDashboard'),
        allUsers: document.getElementById('pageAllUsers'),
        adminsList: document.getElementById('pageAdminsList'),
        allFiles: document.getElementById('pageAllFiles'),
        myNodes: document.getElementById('pageMyNodes'),
        terminal: document.getElementById('pageTerminal'),
        folderManager: document.getElementById('pageFolderManager')
    };

    const fileInput = document.getElementById('fileInput');
    const fileLabel = document.getElementById('fileLabel');
    const fileCountBadge = document.getElementById('fileCountBadge');

    function showToast(message, isError, subText) {
        toast.className = 'toast';
        if (isError) toast.classList.add('error');
        toastMsg.textContent = message || 'SUCCESS';
        toastSub.textContent = subText || 'Operation completed';
        toast.classList.add('show');
        clearTimeout(toast._timeout);
        toast._timeout = setTimeout(() => toast.classList.remove('show'), 3000);
    }

    function updateLoader(progress) {
        loaderBar.style.width = Math.min(progress, 100) + '%';
    }

    function hideLoader() {
        loader.style.opacity = '0';
        setTimeout(() => loader.style.display = 'none', 400);
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    function formatFileSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / 1048576).toFixed(1) + ' MB';
    }

    function showPage(pageId) {
        Object.keys(pages).forEach(key => {
            if (pages[key]) pages[key].classList.add('hidden');
        });
        if (pages[pageId]) pages[pageId].classList.remove('hidden');
    }

    function closeModal() {
        document.getElementById('modalOverlay').classList.remove('active');
    }

    function showCustomModal(title, bodyHTML, confirmText, confirmCallback) {
        document.getElementById('modalTitle').textContent = title;
        document.getElementById('modalBody').innerHTML = bodyHTML;
        document.getElementById('modalConfirm').textContent = confirmText || 'Confirm';
        document.getElementById('modalOverlay').classList.add('active');
        document.getElementById('modalConfirm').onclick = function() {
            if (confirmCallback) confirmCallback();
            else closeModal();
        };
        document.getElementById('modalCancel').onclick = closeModal;
    }

    async function apiCall(endpoint, method, body, headers) {
        let opts = { 
            method: method || 'GET', 
            headers: { 
                'Content-Type': 'application/json',
                'X-Username': currentUser || ''
            } 
        };
        if (body) opts.body = JSON.stringify(body);
        if (headers) Object.assign(opts.headers, headers);
        const res = await fetch(endpoint, opts);
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'API request failed');
        return data;
    }

    async function apiUpload(endpoint, formData) {
        const res = await fetch(endpoint, { 
            method: 'POST', 
            headers: { 'X-Username': currentUser || '' },
            body: formData 
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || 'Upload failed');
        return data;
    }

    async function performLogin(username, password) {
        try {
            const data = await apiCall('/api/login', 'POST', { username, password });
            if (data.success) {
                currentUser = data.username;
                currentRole = data.role;
                isLoggedIn = true;
                localStorage.setItem('loggedInUser', JSON.stringify({ username: currentUser, role: currentRole }));
                localStorage.setItem('lastActivity_' + currentUser, Date.now().toString());
                showToast('⚡ ACCESS GRANTED', false, 'Welcome ' + currentUser + '!');
                checkAndShowPopup();
                return true;
            }
            document.getElementById('loginError').textContent = '❌ ' + data.error;
            document.getElementById('loginError').classList.add('show');
            setTimeout(() => document.getElementById('loginError').classList.remove('show'), 3000);
            return false;
        } catch(e) {
            document.getElementById('loginError').textContent = '❌ ' + e.message;
            document.getElementById('loginError').classList.add('show');
            return false;
        }
    }

    async function performSignup(username, password) {
        try {
            const data = await apiCall('/api/signup', 'POST', { username, password });
            if (data.success) {
                showToast('✅ Account created! You can now login.', false);
                return true;
            }
            showToast('❌ ' + data.error, true);
            return false;
        } catch(e) {
            showToast('❌ ' + e.message, true);
            return false;
        }
    }

    function checkAndShowPopup() {
        settings.telegram_popup = settings.telegram_popup !== undefined ? settings.telegram_popup : true;
        
        if (!settings.telegram_popup) return;
        
        if (settings.popup_shown && settings.popup_shown[currentUser]) {
            const lastShown = new Date(settings.popup_shown[currentUser].timestamp);
            const now = new Date();
            const hoursDiff = (now - lastShown) / (1000 * 60 * 60);
            if (hoursDiff < 8) return;
        }
        
        const popup = document.getElementById('popupOverlay');
        popup.classList.add('active');
        
        const joinBtn = document.getElementById('popupJoinBtn');
        const link = settings.telegram_link || 'https://t.me/+m0R5z1yhmCtiZjQ9';
        joinBtn.onclick = function() {
            window.open(link, '_blank');
            popup.classList.remove('active');
            markPopupShown();
        };
        
        document.getElementById('popupCloseBtn').onclick = function() {
            popup.classList.remove('active');
            markPopupShown();
        };
    }

    async function markPopupShown() {
        try {
            await apiCall('/api/settings/popup-shown', 'POST', { shown: true });
            if (!settings.popup_shown) settings.popup_shown = {};
            settings.popup_shown[currentUser] = { timestamp: new Date().toISOString(), shown: true };
        } catch(e) {
            console.error('Error marking popup:', e);
        }
    }

    function initDashboard() {
        loginBox.style.display = 'none';
        dashboardPage.style.display = 'block';
        document.getElementById('profileIcon').textContent = currentUser.charAt(0).toUpperCase();
        document.getElementById('dropdownUsername').textContent = '👤 ' + currentUser;
        document.getElementById('dropdownRole').textContent = 'ROLE: ' + currentRole.toUpperCase();
        
        const ownerItems = document.querySelectorAll('#menuAllFiles, #menuAdminsList, #menuAddAdmin, #menuTerminal, #menuSettings');
        const adminItems = document.querySelectorAll('#menuAllUsers, #menuAddUser');
        
        if (currentRole === 'owner') {
            ownerItems.forEach(el => { if (el) el.style.display = ''; });
            adminItems.forEach(el => { if (el) el.style.display = ''; });
        } else if (currentRole === 'admin') {
            ownerItems.forEach(el => { if (el) el.style.display = 'none'; });
            adminItems.forEach(el => { if (el) el.style.display = ''; });
        } else {
            ownerItems.forEach(el => { if (el) el.style.display = 'none'; });
            adminItems.forEach(el => { if (el) el.style.display = 'none'; });
        }
        
        showPage('dashboard');
        fetchSettings();
        fetchFiles();
        fetchAllFiles();
        fetchProcesses();
        fetchUsers();
        updateContactOwnerVisibility();
        
        if (autoRefreshInterval) clearInterval(autoRefreshInterval);
        autoRefreshInterval = setInterval(() => { 
            fetchProcesses(); 
            fetchFiles();
            if (currentRole === 'owner') fetchAllFiles();
        }, 15000);
    }

    function logout() {
        if (terminalEventSource) {
            terminalEventSource.close();
            terminalEventSource = null;
        }
        localStorage.removeItem('loggedInUser');
        if (currentUser) {
            localStorage.removeItem('lastActivity_' + currentUser);
        }
        currentUser = null;
        isLoggedIn = false;
        dashboardPage.style.display = 'none';
        loginBox.style.display = 'block';
        showToast('🚪 LOGGED OUT', false);
    }

    async function fetchSettings() {
        try {
            const data = await apiCall('/api/settings');
            settings = data.settings || {};
            updateContactOwnerVisibility();
        } catch(e) {
            console.error('Error fetching settings:', e);
        }
    }

    function updateContactOwnerVisibility() {
    const contactBtn = document.getElementById('contactOwnerBtn');
    const contactOwner = settings.contact_owner || 'Card_hacker_12';
    const displayName = contactOwner.startsWith('@') ? contactOwner.substring(1) : contactOwner;
    
    // BUTTON KA NAAM FIXED RAKHO - "📧 DM TO BUY"
    contactBtn.textContent = '📧 DM TO BUY';
    if (contactOwner && contactOwner !== '') {
        contactBtn.onclick = function() {
            window.open(`https://t.me/${contactOwner}`, '_blank');
            showToast(`📧 Opening @${contactOwner}`, false);
        };
    } else {
        contactBtn.onclick = function() {
            showToast('❌ Owner username not configured. Set it in Settings.', true);
        };
    }
    
    const dashboardContactBtn = document.getElementById('contactOwnerDashboardBtn');
    if (dashboardContactBtn) {
        // DASHBOARD BUTTON KA NAAM BHI FIXED
        dashboardContactBtn.textContent = '📧 DM TO BUY';
        if (contactOwner && contactOwner !== '') {
            dashboardContactBtn.onclick = function() {
                window.open(`https://t.me/${contactOwner}`, '_blank');
                showToast(`📧 Opening @${contactOwner}`, false);
            };
        } else {
            dashboardContactBtn.onclick = function() {
                showToast('❌ Owner username not configured. Set it in Settings.', true);
            };
        }
    }
    
    const createAccountBtn = document.getElementById('createAccountBtn');
    const contactOwnerDashboard = document.getElementById('contactOwnerDashboard');
    
    if (settings.signup_enabled !== undefined) {
        signupEnabled = settings.signup_enabled;
        if (signupEnabled) {
            createAccountBtn.style.display = 'block';
            if (contactOwnerDashboard) contactOwnerDashboard.style.display = 'none';
        } else {
            createAccountBtn.style.display = 'none';
            if (contactOwnerDashboard) contactOwnerDashboard.style.display = 'block';
        }
    }
}

    function openSettings() {
    document.getElementById('profileDropdown').classList.remove('open');
    const currentSignup = settings.signup_enabled !== undefined ? settings.signup_enabled : true;
    const currentPopup = settings.telegram_popup !== undefined ? settings.telegram_popup : true;
    const currentLink = settings.telegram_link || 'https://t.me/+m0R5z1yhmCtiZjQ9';
    
    showCustomModal('⚙ Settings',
        '<div style="margin-bottom:10px;">' +
        '<label style="font-size:0.6rem;opacity:0.4;display:block;margin-bottom:4px;">👑 Set Contact Username</label>' +
        '<input type="text" id="settingsContactOwner" value="' + (settings.contact_owner || '') + '" placeholder="Username (without @)" style="font-size:0.7rem;">' +
        '<p style="font-size:0.45rem;opacity:0.2;margin-top:4px;">This will set the Contact Owner username</p>' +
        '</div>' +
        '<div style="margin-bottom:10px;">' +
        '<label style="font-size:0.6rem;opacity:0.4;display:block;margin-bottom:4px;">Signup Status</label>' +
        '<div style="display:flex;gap:8px;">' +
        '<button class="dashboard-btn ' + (currentSignup ? 'green' : 'red') + '" id="settingsToggleSignup" style="flex:1;padding:8px;font-size:0.55rem;">' + (currentSignup ? '🟢 Signup ON' : '🔴 Signup OFF') + '</button>' +
        '</div>' +
        '</div>' +
        '<div style="margin-bottom:10px;">' +
        '<label style="font-size:0.6rem;opacity:0.4;display:block;margin-bottom:4px;">Telegram Popup</label>' +
        '<div style="display:flex;gap:8px;">' +
        '<button class="dashboard-btn ' + (currentPopup ? 'green' : 'red') + '" id="settingsTogglePopup" style="flex:1;padding:8px;font-size:0.55rem;">' + (currentPopup ? '🟢 Popup ON' : '🔴 Popup OFF') + '</button>' +
        '</div>' +
        '</div>' +
        '<div style="margin-bottom:10px;">' +
        '<label style="font-size:0.6rem;opacity:0.4;display:block;margin-bottom:4px;">Telegram Link</label>' +
        '<input type="text" id="settingsTelegramLink" value="' + currentLink + '" placeholder="https://t.me/..." style="font-size:0.7rem;">' +
        '</div>',
        'SAVE', async function() {
            const contactOwner = document.getElementById('settingsContactOwner').value.trim();
            const newSignup = settings.signup_enabled !== undefined ? !settings.signup_enabled : false;
            const newPopup = settings.telegram_popup !== undefined ? !settings.telegram_popup : false;
            const newLink = document.getElementById('settingsTelegramLink').value.trim();
            
            try {
                let updates = { signup_enabled: newSignup, telegram_popup: newPopup };
                
                if (contactOwner) {
                    updates.contact_owner = contactOwner.replace('@', '').trim();
                }
                
                if (newLink) {
                    updates.telegram_link = newLink;
                }
                
                await apiCall('/api/settings', 'PUT', updates);
                settings = { ...settings, ...updates };
                updateContactOwnerVisibility();
                showToast('✅ Settings saved', false);
                closeModal();
            } catch(e) {
                showToast('❌ ' + e.message, true);
            }
        });
    
    setTimeout(() => {
        const toggleBtn = document.getElementById('settingsToggleSignup');
        if (toggleBtn) {
            toggleBtn.onclick = function() {
                const isOn = this.textContent.includes('ON');
                this.textContent = isOn ? '🔴 Signup OFF' : '🟢 Signup ON';
                this.className = 'dashboard-btn ' + (isOn ? 'red' : 'green');
                settings.signup_enabled = !isOn;
            };
        }
        const popupBtn = document.getElementById('settingsTogglePopup');
        if (popupBtn) {
            popupBtn.onclick = function() {
                const isOn = this.textContent.includes('ON');
                this.textContent = isOn ? '🔴 Popup OFF' : '🟢 Popup ON';
                this.className = 'dashboard-btn ' + (isOn ? 'red' : 'green');
                settings.telegram_popup = !isOn;
            };
        }
    }, 100);
}

    async function fetchFiles() {
        try {
            const data = await apiCall('/api/files');
            files = data.files || [];
            renderFiles();
            updateStats();
        } catch(e) {
            console.error('Error fetching files:', e);
        }
    }

    async function fetchAllFiles() {
        try {
            const data = await apiCall('/api/all-files');
            allFiles = data.files || [];
            renderAllFiles();
        } catch(e) {
            console.error('Error fetching all files:', e);
        }
    }

    async function fetchUsers() {
        try {
            const data = await apiCall('/api/users');
            allUsers = data.users || [];
            renderAllUsers();
            renderAdminsList();
        } catch(e) {
            console.error('Error fetching users:', e);
        }
    }

    async function fetchProcesses() {
        try {
            const data = await apiCall('/api/processes');
            processes = data.processes || [];
            document.getElementById('processCount').textContent = processes.length;
            document.getElementById('botStatus').textContent = processes.length;
            updateStats();
            updateRunningNodes();
            updateMyNodes();
        } catch(e) {
            console.error('Error fetching processes:', e);
        }
    }

    function updateStats() {
        const running = files.filter(f => f.status === 'running').length;
        const stopped = files.filter(f => f.status === 'stopped').length;
        document.getElementById('runningCount').textContent = running;
        document.getElementById('stoppedCount').textContent = stopped;
        document.getElementById('fileCount').textContent = files.length;
        document.getElementById('fileCountStatus').textContent = files.length;
    }

    function renderFiles() {
        const container = document.getElementById('filesList');
        if (files.length === 0) {
            container.innerHTML = '<div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">No files uploaded</div>';
            return;
        }
        let html = '';
        files.forEach(f => {
            const statusClass = f.status === 'running' ? 'running' : 'stopped';
            const hasBot = f.has_token || false;
            const botUsername = f.bot_username || null;
            html += `
                <div class="file-item" data-file-id="${f.id}" data-filename="${f.filename}">
                    <div class="file-info">
                        <span class="name">${escapeHtml(f.filename)}</span>
                        <span class="size">${(f.size / 1024).toFixed(1)} KB</span>
                        <span class="status-badge ${statusClass}">${f.status || 'stopped'}</span>
                    </div>
                    <div class="file-actions">
                        ${f.status !== 'running' ? `<button class="btn-sm green" onclick="window.startFile('${f.id}')">▶ Start</button>` : ''}
                        ${f.status === 'running' ? `<button class="btn-sm orange" onclick="window.stopFile('${f.id}')">■ Stop</button>` : ''}
                        <button class="btn-sm blue" onclick="window.restartFile('${f.id}')">↻ Restart</button>
                        <button class="btn-sm purple" onclick="window.viewLogs('${f.id}')">📄 Logs</button>
                        <button class="btn-sm download" onclick="window.downloadFile('${f.id}')">⬇ Download</button>
                        <button class="btn-sm" onclick="window.editFile('${f.id}')">✏️ Edit</button>
                        <button class="btn-sm red" onclick="window.deleteFile('${f.id}')">🗑</button>
                        ${hasBot && botUsername ? `<button class="btn-sm white" onclick="window.openBot('${botUsername}')">🤖 Open Bot</button>` : ''}
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    }

    function renderAllFiles() {
        const container = document.getElementById('allFilesList');
        if (allFiles.length === 0) {
            container.innerHTML = '<tr><td colspan="5" style="text-align:center;opacity:0.3;">No files found</td></tr>';
            return;
        }
        let html = '';
        allFiles.forEach(f => {
            const statusClass = f.status === 'running' ? 'running' : 'stopped';
            const hasBot = f.has_token || false;
            const botUsername = f.bot_username || null;
            html += `
                <tr>
                    <td>${escapeHtml(f.filename)}</td>
                    <td><span style="color:#33ddff;">${escapeHtml(f.owner || 'unknown')}</span></td>
                    <td>${(f.size / 1024).toFixed(1)} KB</td>
                    <td><span class="status-badge ${statusClass}">${f.status || 'stopped'}</span></td>
                    <td>
                        <div class="action-btns">
                            ${f.status !== 'running' ? `<button class="btn-sm green" onclick="window.startFile('${f.id}')">▶</button>` : ''}
                            ${f.status === 'running' ? `<button class="btn-sm orange" onclick="window.stopFile('${f.id}')">■</button>` : ''}
                            <button class="btn-sm blue" onclick="window.restartFile('${f.id}')">↻</button>
                            <button class="btn-sm purple" onclick="window.viewLogs('${f.id}')">📄</button>
                            <button class="btn-sm download" onclick="window.downloadFile('${f.id}')">⬇</button>
                            <button class="btn-sm" onclick="window.editFile('${f.id}')">✏️</button>
                            <button class="btn-sm" onclick="window.renameFile('${f.id}')">✏️</button>
                            <button class="btn-sm" onclick="window.duplicateFile('${f.id}')">📋</button>
                            <button class="btn-sm blue" onclick="window.fileDetails('${f.id}')">ℹ️</button>
                            <button class="btn-sm red" onclick="window.deleteFile('${f.id}')">🗑</button>
                            ${hasBot && botUsername ? `<button class="btn-sm white" onclick="window.openBot('${botUsername}')">🤖</button>` : ''}
                        </div>
                    </td>
                </tr>
            `;
        });
        container.innerHTML = html;
    }

    function renderAllUsers() {
        const container = document.getElementById('allUsersList');
        if (!allUsers || allUsers.length === 0) {
            container.innerHTML = '<div style="opacity:0.3;text-align:center;padding:20px;font-size:0.7rem;">No users found</div>';
            return;
        }
        let html = '';
        allUsers.forEach(u => {
            const roleClass = u.role || 'user';
            const isOwner = u.role === 'owner';
            const isSelf = u.username === currentUser;
            html += `
                <div class="user-card">
                    <div class="user-data">
                        <span class="username">${escapeHtml(u.username)}</span>
                        <span class="password">${escapeHtml(u.password)}</span>
                        <span class="role-badge ${roleClass}">${(u.role || 'user').toUpperCase()}</span>
                        ${u.created ? `<span style="opacity:0.3;font-size:0.45rem;">${new Date(u.created).toLocaleDateString()}</span>` : ''}
                        ${isSelf ? '<span style="opacity:0.2;font-size:0.4rem;">(You)</span>' : ''}
                    </div>
                    <div class="user-actions">
                        ${!isOwner && !isSelf && (currentRole === 'owner' || (currentRole === 'admin' && u.role !== 'admin')) ? 
                            `<button class="btn-sm" onclick="window.removeUser('${u.username}')">Remove</button>` : ''}
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    }

    function renderAdminsList() {
        const container = document.getElementById('adminsList');
        const admins = allUsers.filter(u => u.role === 'owner' || u.role === 'admin');
        if (admins.length === 0) {
            container.innerHTML = '<div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">No admins found</div>';
            return;
        }
        let html = '';
        admins.forEach(u => {
            const roleClass = u.role || 'admin';
            html += `
                <div class="user-item">
                    <div class="user-info">
                        <span class="uname">${escapeHtml(u.username)}</span>
                        <span class="role-badge ${roleClass}">${(u.role || 'admin').toUpperCase()}</span>
                        <span class="created">${u.created ? new Date(u.created).toLocaleDateString() : ''}</span>
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
    }

    function updateRunningNodes() {
        const container = document.getElementById('runningNodesList');
        if (processes.length === 0) {
            container.innerHTML = '<div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">No nodes running</div>';
            return;
        }
        let html = '';
        processes.forEach(n => {
            html += `<div class="node-item">
                <span><span class="status-dot running"></span> ${escapeHtml(n.filename)}</span>
                <span style="opacity:0.4;font-size:0.55rem;">PID: ${n.pid}</span>
                <span>
                    <button class="btn-sm orange" onclick="window.stopNode('${n.id}')">■ Stop</button>
                    <button class="btn-sm red" onclick="window.deleteNode('${n.id}')">🗑</button>
                </span>
            </div>`;
        });
        container.innerHTML = html;
    }

    function updateMyNodes() {
        const container = document.getElementById('myNodesList');
        if (processes.length === 0) {
            container.innerHTML = '<div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">No nodes running</div>';
            return;
        }
        let html = '';
        processes.forEach(n => {
            html += `<div class="node-item">
                <span><span class="status-dot running"></span> ${escapeHtml(n.filename)}</span>
                <span style="opacity:0.4;font-size:0.55rem;">PID: ${n.pid}</span>
                <span>
                    <button class="btn-sm orange" onclick="window.stopNode('${n.id}')">■ Stop</button>
                    <button class="btn-sm red" onclick="window.deleteNode('${n.id}')">🗑</button>
                </span>
            </div>`;
        });
        container.innerHTML = html;
    }

    window.startFile = async function(fileId) {
        try {
            showToast('▶ Starting...', false);
            await apiCall(`/api/files/start/${fileId}`, 'POST');
            showToast('✅ Started', false);
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.stopFile = async function(fileId) {
        try {
            showToast('■ Stopping...', false);
            await apiCall(`/api/files/stop/${fileId}`, 'POST');
            showToast('✅ Stopped', false);
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.restartFile = async function(fileId) {
        try {
            showToast('↻ Restarting...', false);
            await apiCall(`/api/files/stop/${fileId}`, 'POST');
            await apiCall(`/api/files/start/${fileId}`, 'POST');
            showToast('✅ Restarted', false);
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.viewLogs = async function(fileId) {
        try {
            const data = await apiCall(`/api/files/logs/${fileId}`, 'GET');
            const logContent = data.logs || 'No logs available';
            showCustomModal(`📄 Logs for ${fileId}`,
                `<pre style="background:#050807;padding:10px;border-radius:6px;font-size:0.65rem;color:#00ff88;max-height:300px;overflow-y:auto;white-space:pre-wrap;font-family:'Courier New',monospace;">${escapeHtml(logContent)}</pre>`,
                'Close');
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.downloadFile = async function(fileId) {
        try {
            const response = await fetch(`/api/files/download/${fileId}`, {
                headers: { 'X-Username': currentUser }
            });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Download failed');
            }
            const blob = await response.blob();
            const contentDisposition = response.headers.get('Content-Disposition');
            let filename = 'downloaded_file';
            if (contentDisposition) {
                const match = contentDisposition.match(/filename="(.+)"/);
                if (match) filename = match[1];
            }
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            showToast('⬇ Download started', false);
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.deleteFile = async function(fileId) {
        showCustomModal('🗑 Delete File', 
            '<p style="opacity:0.6;text-align:center;font-size:0.8rem;">This will STOP the process and DELETE the file.<br>Are you sure?</p>',
            'YES, DELETE', async function() {
                try {
                    showToast('🗑 Deleting...', false);
                    await apiCall(`/api/files/delete/${fileId}`, 'DELETE');
                    showToast('✅ Deleted', false);
                    closeModal();
                    await fetchFiles();
                    await fetchAllFiles();
                    await fetchProcesses();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    };

    window.editFile = async function(fileId) {
        try {
            const data = await apiCall(`/api/files/content/${fileId}`, 'GET');
            const content = data.content || '';
            showCustomModal('✏️ Edit File',
                '<div style="position:relative;">' +
                '<div style="display:flex;gap:6px;margin-bottom:6px;flex-wrap:wrap;">' +
                '<button class="btn-sm" id="clearEditorBtn" style="padding:4px 12px;font-size:0.45rem;border:1px solid rgba(255,68,102,0.2);color:#ff4466;background:transparent;border-radius:4px;cursor:pointer;">✕ Clear</button>' +
                '<button class="btn-sm" id="copyEditorBtn" style="padding:4px 12px;font-size:0.45rem;border:1px solid rgba(51,221,255,0.2);color:#33ddff;background:transparent;border-radius:4px;cursor:pointer;">📋 Copy</button>' +
                '</div>' +
                '<textarea id="editFileContent" rows="12" class="code-editor" style="min-height:200px;width:100%;">' + 
                escapeHtml(content) + '</textarea></div>',
                'SAVE', async function() {
                    const content = document.getElementById('editFileContent').value;
                    try {
                        await apiCall(`/api/files/content/${fileId}`, 'PUT', { content: content });
                        showToast('✅ File saved', false);
                        closeModal();
                        fetchFiles();
                        if (currentRole === 'owner') fetchAllFiles();
                    } catch(e) {
                        showToast('❌ ' + e.message, true);
                    }
                });
            setTimeout(() => {
                const clearBtn = document.getElementById('clearEditorBtn');
                if (clearBtn) {
                    clearBtn.onclick = function() {
                        document.getElementById('editFileContent').value = '';
                        showToast('🧹 Editor cleared', false);
                    };
                }
                const copyBtn = document.getElementById('copyEditorBtn');
                if (copyBtn) {
                    copyBtn.onclick = function() {
                        const textarea = document.getElementById('editFileContent');
                        textarea.select();
                        try {
                            navigator.clipboard.writeText(textarea.value).then(() => {
                                showToast('📋 Copied to clipboard!', false);
                            }).catch(() => {
                                document.execCommand('copy');
                                showToast('📋 Copied!', false);
                            });
                        } catch(e) {
                            document.execCommand('copy');
                            showToast('📋 Copied!', false);
                        }
                    };
                }
            }, 100);
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.renameFile = async function(fileId) {
        const file = files.find(f => f.id === fileId) || allFiles.find(f => f.id === fileId);
        const oldName = file ? file.filename : 'file';
        showCustomModal('✏️ Rename File',
            '<div><input type="text" id="renameFileInput" value="' + escapeHtml(oldName) + '" style="font-size:0.7rem;"></div>',
            'RENAME', async function() {
                const newName = document.getElementById('renameFileInput').value.trim();
                if (!newName) { showToast('❌ Name required', true); return; }
                try {
                    await apiCall(`/api/files/rename/${fileId}`, 'POST', { new_name: newName });
                    showToast('✅ Renamed', false, newName);
                    closeModal();
                    fetchFiles();
                    if (currentRole === 'owner') fetchAllFiles();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    };

    window.duplicateFile = async function(fileId) {
        try {
            await apiCall(`/api/files/duplicate/${fileId}`, 'POST');
            showToast('✅ Duplicated', false);
            fetchFiles();
            if (currentRole === 'owner') fetchAllFiles();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.fileDetails = async function(fileId) {
        try {
            const data = await apiCall(`/api/files/details/${fileId}`, 'GET');
            const details = data.details || {};
            showCustomModal('ℹ️ File Details',
                '<div class="file-details-grid">' +
                '<span class="label">Name:</span><span class="value">' + escapeHtml(details.filename || '') + '</span>' +
                '<span class="label">Size:</span><span class="value">' + formatFileSize(details.size || 0) + '</span>' +
                '<span class="label">Created:</span><span class="value">' + (details.created ? new Date(details.created).toLocaleString() : '') + '</span>' +
                '<span class="label">Modified:</span><span class="value">' + (details.modified ? new Date(details.modified).toLocaleString() : '') + '</span>' +
                '<span class="label">Path:</span><span class="value" style="font-size:0.5rem;">' + escapeHtml(details.path || '') + '</span>' +
                '<span class="label">Owner:</span><span class="value">' + escapeHtml(details.owner || '') + '</span>' +
                (details.has_token ? '<span class="label">Bot:</span><span class="value">@' + escapeHtml(details.bot_username || '') + '</span>' : '') +
                '</div>',
                'Close');
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.openBot = function(botUsername) {
        if (botUsername) {
            window.open(`https://t.me/${botUsername}`, '_blank');
            showToast(`🤖 Opening @${botUsername}`, false);
        } else {
            showToast('❌ No bot username available', true);
        }
    };

    window.stopNode = async function(fileId) {
        try {
            showToast('■ Stopping...', false);
            await apiCall(`/api/files/stop/${fileId}`, 'POST');
            showToast('✅ Stopped', false);
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    };

    window.deleteNode = async function(fileId) {
        showCustomModal('🗑 Delete Process', 
            '<p style="opacity:0.6;text-align:center;font-size:0.8rem;">This will STOP the process.<br>Are you sure?</p>',
            'YES, STOP', async function() {
                try {
                    showToast('■ Stopping...', false);
                    await apiCall(`/api/files/stop/${fileId}`, 'POST');
                    showToast('✅ Stopped', false);
                    closeModal();
                    await fetchFiles();
                    await fetchAllFiles();
                    await fetchProcesses();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    };

    window.removeUser = async function(username) {
        if (username === currentUser) {
            showToast('❌ Cannot remove yourself', true);
            return;
        }
        showCustomModal('🗑 Remove User', 
            '<p style="opacity:0.6;text-align:center;font-size:0.8rem;">Are you sure you want to remove user <strong>' + escapeHtml(username) + '</strong>?<br>This will delete all their files.</p>',
            'YES, REMOVE', async function() {
                try {
                    await apiCall('/api/users/remove', 'POST', { username: username });
                    showToast('✅ User removed', false, username);
                    closeModal();
                    fetchUsers();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    };

    document.addEventListener('contextmenu', function(e) {
        e.preventDefault();
        return false;
    });

    let longPressTimer = null;
    let longPressTarget = null;

    document.addEventListener('touchstart', function(e) {
        const target = e.target.closest('.folder-item, .file-item');
        if (target) {
            longPressTarget = target;
            longPressTimer = setTimeout(function() {
                if (longPressTarget) {
                    const fileId = longPressTarget.dataset.fileId;
                    const filename = longPressTarget.dataset.filename;
                    const isDir = longPressTarget.dataset.isDir === 'true';
                    const hasToken = longPressTarget.dataset.hasToken === 'true';
                    const botUsername = longPressTarget.dataset.botUsername || null;
                    
                    contextTargetFileId = fileId;
                    contextTargetFileName = filename;
                    contextTargetIsDir = isDir;
                    
                    const menu = document.getElementById('contextMenu');
                    const touch = e.touches ? e.touches[0] : e;
                    const x = touch.clientX || 0;
                    const y = touch.clientY || 0;
                    menu.style.left = Math.min(x, window.innerWidth - 200) + 'px';
                    menu.style.top = Math.min(y, window.innerHeight - 300) + 'px';
                    menu.classList.add('open');
                    
                    const openBotItem = menu.querySelector('[data-action="openbot"]');
                    if (hasToken && botUsername) {
                        openBotItem.style.display = '';
                        openBotItem.textContent = `🤖 Open @${botUsername}`;
                    } else {
                        openBotItem.style.display = 'none';
                    }
                    
                    const openItem = menu.querySelector('[data-action="open"]');
                    if (isDir) {
                        openItem.style.display = '';
                    } else {
                        openItem.style.display = 'none';
                    }
                    
                    if (navigator.vibrate) {
                        navigator.vibrate(20);
                    }
                }
            }, 500);
        }
    }, { passive: true });

    document.addEventListener('touchmove', function(e) {
        clearTimeout(longPressTimer);
        longPressTarget = null;
    }, { passive: true });

    document.addEventListener('touchend', function(e) {
        clearTimeout(longPressTimer);
        longPressTarget = null;
    }, { passive: true });

    document.getElementById('contextMenu').addEventListener('click', function(e) {
        const action = e.target.dataset.action;
        if (!action || !contextTargetFileId) {
            document.getElementById('contextMenu').classList.remove('open');
            return;
        }
        
        const fileId = contextTargetFileId;
        const isDir = contextTargetIsDir;
        document.getElementById('contextMenu').classList.remove('open');
        
        switch(action) {
            case 'open':
                if (isDir) {
                    window.openFolder(contextTargetFileName);
                }
                break;
            case 'start':
                window.startFile(fileId);
                break;
            case 'stop':
                window.stopFile(fileId);
                break;
            case 'download':
                window.downloadFile(fileId);
                break;
            case 'edit':
                window.editFile(fileId);
                break;
            case 'rename':
                window.renameFile(fileId);
                break;
            case 'details':
                window.fileDetails(fileId);
                break;
            case 'openbot':
                const botItem = document.getElementById('contextMenu').querySelector('[data-action="openbot"]');
                const botUsername = botItem ? botItem.textContent.replace('🤖 Open @', '').trim() : null;
                if (botUsername) {
                    window.openBot(botUsername);
                } else {
                    showToast('❌ No bot info available', true);
                }
                break;
            case 'delete':
                window.deleteFile(fileId);
                break;
        }
    });

    document.addEventListener('click', function(e) {
        const menu = document.getElementById('contextMenu');
        if (menu && !menu.contains(e.target)) {
            menu.classList.remove('open');
        }
    });

    async function loadFolder(path) {
        try {
            const data = await apiCall('/api/folder/list', 'POST', { path: path || '' });
            folderContents = data.items || [];
            currentFolderPath = path || '';
            renderFolderContents();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    }

    function renderFolderContents() {
        const container = document.getElementById('folderContent');
        const search = document.getElementById('folderSearch').value.toLowerCase();
        
        let items = folderContents;
        if (search) {
            items = items.filter(item => item.name.toLowerCase().includes(search));
        }
        
        items.sort((a, b) => {
            if (a.is_dir && !b.is_dir) return -1;
            if (!a.is_dir && b.is_dir) return 1;
            return a.name.localeCompare(b.name);
        });
        
        if (items.length === 0) {
            container.innerHTML = '<div style="opacity:0.3;text-align:center;padding:20px;font-size:0.7rem;">📂 Empty folder</div>';
            return;
        }
        
        let html = '';
        items.forEach(item => {
            const icon = item.is_dir ? '📁' : '📄';
            const size = item.is_dir ? '' : formatFileSize(item.size);
            const modified = item.modified ? new Date(item.modified).toLocaleDateString() : '';
            const fileId = item.path.split('/').pop().split('_')[0] || item.path;
            
            html += `
                <div class="folder-item" 
                     data-file-id="${fileId}" 
                     data-filename="${item.path}"
                     data-is-dir="${item.is_dir}"
                     data-has-token="${item.has_token || false}"
                     data-bot-username="${item.bot_username || ''}">
                    <div class="folder-info">
                        <span class="icon">${icon}</span>
                        <span class="name">${escapeHtml(item.name)}</span>
                        <span style="opacity:0.3;font-size:0.5rem;">${size}</span>
                        <span style="opacity:0.2;font-size:0.45rem;">${modified}</span>
                        ${item.has_token ? '<span style="color:#33ddff;font-size:0.4rem;">🤖 BOT</span>' : ''}
                    </div>
                    <div class="folder-actions">
                        ${item.is_dir ? 
                            `<button class="btn-sm blue" onclick="window.openFolder('${item.path}')">📂 Open</button>` : 
                            `<button class="btn-sm download" onclick="window.downloadFile('${fileId}')">⬇ Download</button>
                             <button class="btn-sm blue" onclick="window.fileDetails('${fileId}')">ℹ️</button>
                             <button class="btn-sm red" onclick="window.deleteFile('${fileId}')">🗑</button>`
                        }
                    </div>
                </div>
            `;
        });
        container.innerHTML = html;
        updateBreadcrumb();
        
        container.querySelectorAll('.folder-item[data-is-dir="true"]').forEach(el => {
            el.addEventListener('click', function(e) {
                if (!e.target.closest('.folder-actions') && !e.target.closest('.context-menu')) {
                    window.openFolder(this.dataset.filename);
                }
            });
        });
    }

    function updateBreadcrumb() {
        const nav = document.getElementById('folderNav');
        const parts = currentFolderPath.split('/').filter(p => p);
        let html = '<span class="crumb" data-path="">📁 Root</span>';
        let path = '';
        parts.forEach((part, index) => {
            path += (index > 0 ? '/' : '') + part;
            const isLast = index === parts.length - 1;
            html += `<span class="separator">/</span>`;
            html += `<span class="crumb ${isLast ? 'current' : ''}" data-path="${path}">${escapeHtml(part)}</span>`;
        });
        nav.innerHTML = html;
        
        nav.querySelectorAll('.crumb').forEach(el => {
            el.addEventListener('click', function() {
                loadFolder(this.dataset.path);
            });
        });
    }

    window.openFolder = function(path) {
        loadFolder(path);
    };

    async function createFolder() {
        showCustomModal('📁 Create Folder',
            '<div><input type="text" id="newFolderName" placeholder="Folder name" style="font-size:0.7rem;"></div>',
            'CREATE', async function() {
                const name = document.getElementById('newFolderName').value.trim();
                if (!name) { showToast('❌ Folder name required', true); return; }
                try {
                    await apiCall('/api/folder/create', 'POST', { 
                        path: currentFolderPath, 
                        name: name 
                    });
                    showToast('✅ Folder created', false, name);
                    closeModal();
                    loadFolder(currentFolderPath);
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    }

    async function createFile() {
        showCustomModal('📄 Create File',
            '<div style="margin-bottom:8px;"><input type="text" id="newFileName" placeholder="File name" style="font-size:0.7rem;"></div>' +
            '<div><textarea id="newFileContent" rows="6" placeholder="File content..." style="font-size:0.65rem;min-height:80px;"></textarea></div>',
            'CREATE', async function() {
                const name = document.getElementById('newFileName').value.trim();
                const content = document.getElementById('newFileContent').value;
                if (!name) { showToast('❌ File name required', true); return; }
                try {
                    await apiCall('/api/folder/create-file', 'POST', { 
                        path: currentFolderPath, 
                        name: name,
                        content: content
                    });
                    showToast('✅ File created', false, name);
                    closeModal();
                    loadFolder(currentFolderPath);
                    fetchFiles();
                    if (currentRole === 'owner') fetchAllFiles();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    }

    async function startTerminal() {
        try {
            const data = await apiCall('/api/terminal/start', 'POST', {});
            terminalSessionId = data.session_id;
            showToast('✅ Terminal started', false);
            document.getElementById('terminalOutput').innerHTML = '<div style="opacity:0.3;text-align:center;padding:10px;font-size:0.7rem;">Terminal ready. Type commands below.</div>';
            
            if (terminalEventSource) {
                terminalEventSource.close();
            }
            
            terminalEventSource = new EventSource(`/api/terminal/output/${terminalSessionId}?username=${currentUser}`);
            terminalEventSource.onmessage = function(event) {
                try {
                    const data = JSON.parse(event.data);
                    if (data.type === 'output') {
                        const output = document.getElementById('terminalOutput');
                        const div = document.createElement('div');
                        div.textContent = data.data;
                        output.appendChild(div);
                        output.scrollTop = output.scrollHeight;
                    } else if (data.type === 'end') {
                        const output = document.getElementById('terminalOutput');
                        const div = document.createElement('div');
                        div.textContent = '--- Terminal session ended ---';
                        div.style.opacity = '0.3';
                        output.appendChild(div);
                        terminalEventSource.close();
                    }
                } catch(e) {
                    console.error('Terminal parse error:', e);
                }
            };
            
            document.getElementById('terminalInput').disabled = false;
            document.getElementById('terminalInput').focus();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    }

    async function stopTerminal() {
        if (terminalSessionId) {
            try {
                await apiCall(`/api/terminal/stop/${terminalSessionId}`, 'POST');
            } catch(e) {}
        }
        if (terminalEventSource) {
            terminalEventSource.close();
            terminalEventSource = null;
        }
        terminalSessionId = null;
        document.getElementById('terminalInput').disabled = true;
        showToast('🔌 Terminal stopped', false);
        const output = document.getElementById('terminalOutput');
        const div = document.createElement('div');
        div.textContent = '--- Terminal stopped ---';
        div.style.opacity = '0.3';
        output.appendChild(div);
    }

    function clearTerminal() {
        document.getElementById('terminalOutput').innerHTML = '';
        const div = document.createElement('div');
        div.textContent = '--- Terminal cleared ---';
        div.style.opacity = '0.3';
        document.getElementById('terminalOutput').appendChild(div);
    }

    async function sendCommand() {
        const input = document.getElementById('terminalInput');
        const command = input.value.trim();
        if (!command || !terminalSessionId) {
            if (!terminalSessionId) showToast('❌ Start terminal first', true);
            return;
        }
        
        try {
            const output = document.getElementById('terminalOutput');
            const div = document.createElement('div');
            div.textContent = '$ ' + command;
            div.style.color = '#33ddff';
            output.appendChild(div);
            output.scrollTop = output.scrollHeight;
            
            terminalCommandHistory.push(command);
            terminalHistoryIndex = -1;
            
            await apiCall('/api/terminal/command', 'POST', {
                session_id: terminalSessionId,
                command: command
            });
            input.value = '';
            input.focus();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    }

    // ============================================================
    // EVENT LISTENERS
    // ============================================================

    document.getElementById('unlock-btn').addEventListener('click', async function() {
        const user = document.getElementById('user').value.trim();
        const pass = document.getElementById('pass').value.trim();
        if (!user || !pass) {
            document.getElementById('loginError').textContent = '❌ Please enter username and password';
            document.getElementById('loginError').classList.add('show');
            setTimeout(() => document.getElementById('loginError').classList.remove('show'), 3000);
            return;
        }
        if (await performLogin(user, pass)) initDashboard();
    });

    document.getElementById('createAccountBtn').addEventListener('click', function() {
        showCustomModal('➕ Create Account',
            '<div style="margin-bottom:12px;">' +
            '<label style="font-size:0.6rem;opacity:0.4;display:block;margin-bottom:4px;">👤 Username</label>' +
            '<input type="text" id="signupUser" placeholder="Choose a username" style="font-size:0.7rem;">' +
            '</div>' +
            '<div style="margin-bottom:12px;">' +
            '<label style="font-size:0.6rem;opacity:0.4;display:block;margin-bottom:4px;">🔒 Password</label>' +
            '<input type="password" id="signupPass" placeholder="Choose a password" style="font-size:0.7rem;">' +
            '</div>' +
            '<div>' +
            '<label style="font-size:0.6rem;opacity:0.4;display:block;margin-bottom:4px;">🔒 Confirm Password</label>' +
            '<input type="password" id="signupPassConfirm" placeholder="Confirm password" style="font-size:0.7rem;">' +
            '</div>',
            'CREATE ACCOUNT', async function() {
                const user = document.getElementById('signupUser').value.trim();
                const pass = document.getElementById('signupPass').value.trim();
                const confirm = document.getElementById('signupPassConfirm').value.trim();
                if (!user || !pass || !confirm) {
                    showToast('❌ All fields required', true);
                    return;
                }
                if (pass !== confirm) {
                    showToast('❌ Passwords do not match', true);
                    return;
                }
                if (await performSignup(user, pass)) {
                    closeModal();
                    document.getElementById('user').value = user;
                    document.getElementById('pass').value = pass;
                    showToast('✅ Account created! Login to continue.', false);
                }
            });
    });

    document.addEventListener('keydown', function(e) {
        if (e.key === 'Enter' && loginBox.style.display !== 'none') {
            document.getElementById('unlock-btn').click();
        }
    });

    document.getElementById('logoutBtn').addEventListener('click', logout);
    document.getElementById('menuLogout').addEventListener('click', logout);

    document.getElementById('profileIcon').addEventListener('click', function(e) {
        e.stopPropagation();
        document.getElementById('profileDropdown').classList.toggle('open');
    });
    document.addEventListener('click', function(e) {
        if (!e.target.closest('.profile-icon') && !e.target.closest('.profile-dropdown')) {
            document.getElementById('profileDropdown').classList.remove('open');
        }
    });

    document.getElementById('menuMyFiles').addEventListener('click', function() {
        showPage('dashboard');
        document.getElementById('profileDropdown').classList.remove('open');
        fetchFiles();
    });

    document.getElementById('menuMyNodes').addEventListener('click', function() {
        showPage('myNodes');
        document.getElementById('profileDropdown').classList.remove('open');
        fetchProcesses();
    });

    document.getElementById('menuAllUsers').addEventListener('click', function() {
        showPage('allUsers');
        document.getElementById('profileDropdown').classList.remove('open');
        fetchUsers();
    });

    document.getElementById('menuAddUser').addEventListener('click', function() {
        document.getElementById('profileDropdown').classList.remove('open');
        showCustomModal('➕ Add User',
            '<div style="margin-bottom:10px;"><input type="text" id="addUsername" placeholder="Username" style="font-size:0.7rem;"></div>' +
            '<div><input type="password" id="addPassword" placeholder="Password" style="font-size:0.7rem;"></div>' +
            '<div style="margin-top:10px;"><select id="addUserRole" style="font-size:0.6rem;">' +
            '<option value="user">User</option>' +
            '<option value="admin">Admin</option>' +
            '</select></div>',
            'ADD USER', async function() {
                const username = document.getElementById('addUsername').value.trim();
                const password = document.getElementById('addPassword').value.trim();
                const role = document.getElementById('addUserRole').value;
                if (!username || !password) {
                    showToast('❌ Username and password required', true);
                    return;
                }
                try {
                    await apiCall('/api/users/add', 'POST', { username, password, role });
                    showToast('✅ User added', false, username);
                    closeModal();
                    fetchUsers();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    });

    document.getElementById('menuAllFiles').addEventListener('click', function() {
        showPage('allFiles');
        document.getElementById('profileDropdown').classList.remove('open');
        fetchAllFiles();
    });

    document.getElementById('menuAdminsList').addEventListener('click', function() {
        showPage('adminsList');
        document.getElementById('profileDropdown').classList.remove('open');
        fetchUsers();
    });

    document.getElementById('menuAddAdmin').addEventListener('click', function() {
        document.getElementById('profileDropdown').classList.remove('open');
        showCustomModal('➕ Add Admin',
            '<div style="margin-bottom:10px;"><input type="text" id="addAdminUsername" placeholder="Existing Username" style="font-size:0.7rem;"></div>' +
            '<p style="font-size:0.5rem;opacity:0.3;">User must already exist. If user exists, they will be promoted to admin.</p>',
            'PROMOTE TO ADMIN', async function() {
                const username = document.getElementById('addAdminUsername').value.trim();
                if (!username) {
                    showToast('❌ Username required', true);
                    return;
                }
                try {
                    await apiCall('/api/users/promote', 'POST', { username, role: 'admin' });
                    showToast('✅ User promoted to admin', false, username);
                    closeModal();
                    fetchUsers();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    });

    document.getElementById('menuTerminal').addEventListener('click', function() {
        showPage('terminal');
        document.getElementById('profileDropdown').classList.remove('open');
    });

    document.getElementById('menuSettings').addEventListener('click', function() {
        openSettings();
    });

    document.getElementById('menuChangeUsername').addEventListener('click', function() {
        document.getElementById('profileDropdown').classList.remove('open');
        showCustomModal('✏️ Change Username',
            '<div><input type="text" id="newUsername" placeholder="New Username" style="font-size:0.7rem;"></div>',
            'UPDATE', async function() {
                const newUsername = document.getElementById('newUsername').value.trim();
                if (!newUsername) {
                    showToast('❌ Username required', true);
                    return;
                }
                try {
                    await apiCall('/api/users/update', 'PUT', { field: 'username', value: newUsername });
                    showToast('✅ Username updated', false, newUsername);
                    closeModal();
                    currentUser = newUsername;
                    localStorage.setItem('loggedInUser', JSON.stringify({ username: currentUser, role: currentRole }));
                    localStorage.setItem('lastActivity_' + currentUser, Date.now().toString());
                    document.getElementById('profileIcon').textContent = currentUser.charAt(0).toUpperCase();
                    document.getElementById('dropdownUsername').textContent = '👤 ' + currentUser;
                    fetchUsers();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    });

    document.getElementById('menuChangePassword').addEventListener('click', function() {
        document.getElementById('profileDropdown').classList.remove('open');
        showCustomModal('🔑 Change Password',
            '<div style="margin-bottom:10px;"><input type="password" id="oldPassword" placeholder="Old Password" style="font-size:0.7rem;"></div>' +
            '<div><input type="password" id="newPassword" placeholder="New Password" style="font-size:0.7rem;"></div>',
            'UPDATE', async function() {
                const oldPass = document.getElementById('oldPassword').value.trim();
                const newPass = document.getElementById('newPassword').value.trim();
                if (!oldPass || !newPass) {
                    showToast('❌ Both passwords required', true);
                    return;
                }
                try {
                    await apiCall('/api/users/update', 'PUT', { field: 'password', old_value: oldPass, value: newPass });
                    showToast('✅ Password updated', false);
                    closeModal();
                } catch(e) {
                    showToast('❌ ' + e.message, true);
                }
            });
    });

    document.getElementById('menuDownloadAll').addEventListener('click', async function() {
        document.getElementById('profileDropdown').classList.remove('open');
        try {
            showToast('📦 Preparing download...', false);
            const response = await fetch('/api/files/download-all', {
                headers: { 'X-Username': currentUser }
            });
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.error || 'Download failed');
            }
            const blob = await response.blob();
            const contentDisposition = response.headers.get('Content-Disposition');
            let filename = 'all_files.zip';
            if (contentDisposition) {
                const match = contentDisposition.match(/filename="(.+)"/);
                if (match) filename = match[1];
            }
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.href = url;
            a.download = filename;
            document.body.appendChild(a);
            a.click();
            a.remove();
            window.URL.revokeObjectURL(url);
            showToast('✅ Download started', false);
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    });

    document.getElementById('backFromUsers').addEventListener('click', () => showPage('dashboard'));
    document.getElementById('backFromAdmins').addEventListener('click', () => showPage('dashboard'));
    document.getElementById('backFromAllFiles').addEventListener('click', () => showPage('dashboard'));
    document.getElementById('backFromNodes').addEventListener('click', () => showPage('dashboard'));
    document.getElementById('backFromTerminal').addEventListener('click', () => {
        stopTerminal();
        showPage('dashboard');
    });
    document.getElementById('backFromFolderManager').addEventListener('click', () => showPage('dashboard'));

    document.getElementById('terminalStartBtn').addEventListener('click', startTerminal);
    document.getElementById('terminalStopBtn').addEventListener('click', stopTerminal);
    document.getElementById('terminalClearBtn').addEventListener('click', clearTerminal);
    document.getElementById('terminalSendBtn').addEventListener('click', sendCommand);
    document.getElementById('terminalInput').addEventListener('keypress', function(e) {
        if (e.key === 'Enter') sendCommand();
    });
    document.getElementById('terminalInput').addEventListener('keydown', function(e) {
        if (e.key === 'ArrowUp') {
            e.preventDefault();
            if (terminalCommandHistory.length > 0) {
                terminalHistoryIndex = Math.min(terminalHistoryIndex + 1, terminalCommandHistory.length - 1);
                this.value = terminalCommandHistory[terminalCommandHistory.length - 1 - terminalHistoryIndex] || '';
            }
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            if (terminalHistoryIndex > 0) {
                terminalHistoryIndex--;
                this.value = terminalCommandHistory[terminalCommandHistory.length - 1 - terminalHistoryIndex] || '';
            } else {
                terminalHistoryIndex = -1;
                this.value = '';
            }
        }
    });

    document.getElementById('uploadBtn').addEventListener('click', async function() {
        const input = document.getElementById('fileInput');
        if (input.files.length === 0) { showToast('Select files first', true); return; }
        
        const formData = new FormData();
        for (let i = 0; i < input.files.length; i++) {
            formData.append('files[]', input.files[i]);
        }
        
        try {
            document.getElementById('uploadStatus').textContent = '⏳ Uploading...';
            const data = await apiUpload('/api/upload', formData);
            showToast('📤 Uploaded ' + data.files_uploaded + ' files', false);
            document.getElementById('uploadStatus').textContent = '✅ Uploaded: ' + data.files_uploaded + ' files';
            input.value = '';
            fileLabel.innerHTML = '📎 Select Files <span class="file-count" id="fileCountBadge">0 selected</span>';
            document.getElementById('fileCountBadge') && (document.getElementById('fileCountBadge').textContent = '0 selected');
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
            loadFolder(currentFolderPath);
        } catch(e) {
            document.getElementById('uploadStatus').textContent = '❌ ' + e.message;
            showToast('Upload failed', true);
        }
    });

    document.getElementById('deployBtn').addEventListener('click', async function() {
        const code = document.getElementById('pyCodeArea').value.trim();
        const filename = document.getElementById('pyFilename').value.trim() || 'main.py';
        if (!code) { showToast('Enter code', true); return; }
        try {
            document.getElementById('deployStatus').textContent = '⏳ Deploying...';
            const data = await apiCall('/api/deploy', 'POST', { filename, code });
            showToast('✅ Deployed', false, filename);
            document.getElementById('deployStatus').textContent = '✅ Deployed: ' + filename;
            document.getElementById('pyCodeArea').value = '';
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
        } catch(e) {
            document.getElementById('deployStatus').textContent = '❌ ' + e.message;
            showToast('Deploy failed', true);
        }
    });

    document.getElementById('startAllBtn').addEventListener('click', async function() {
        try {
            showToast('▶ Starting all...', false);
            await apiCall('/api/files/start-all', 'POST');
            showToast('✅ All started', false);
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    });

    document.getElementById('stopAllBtn').addEventListener('click', async function() {
        try {
            showToast('■ Stopping all...', false);
            await apiCall('/api/files/stop-all', 'POST');
            showToast('✅ All stopped', false);
            await fetchFiles();
            await fetchAllFiles();
            await fetchProcesses();
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    });

    document.getElementById('logsBtn').addEventListener('click', async function() {
        try {
            const data = await apiCall('/api/logs', 'GET');
            const logs = data.logs || 'No logs available';
            showCustomModal('📜 System Logs', 
                `<pre style="background:#050807;padding:10px;border-radius:6px;font-size:0.65rem;color:#00ff88;max-height:300px;overflow-y:auto;white-space:pre-wrap;font-family:'Courier New',monospace;">${escapeHtml(logs)}</pre>`,
                'Close');
        } catch(e) {
            showToast('❌ ' + e.message, true);
        }
    });

    document.getElementById('refreshBtn').addEventListener('click', function() {
        fetchFiles();
        fetchAllFiles();
        fetchProcesses();
        fetchUsers();
        showToast('🔄 Refreshed', false);
    });

    document.getElementById('fileManagerBtn').addEventListener('click', function() {
        showPage('folderManager');
        loadFolder('');
    });

    document.getElementById('folderCreateDir').addEventListener('click', createFolder);
    document.getElementById('folderCreateFile').addEventListener('click', createFile);
    document.getElementById('folderRefresh').addEventListener('click', function() {
        loadFolder(currentFolderPath);
        showToast('🔄 Refreshed', false);
    });

    document.getElementById('folderSearch').addEventListener('input', function() {
        renderFolderContents();
    });

    fileInput.addEventListener('change', function() {
        const count = this.files.length;
        if (count === 0) {
            fileCountBadge.textContent = '0 selected';
            fileLabel.innerHTML = '📎 Select Files <span class="file-count" id="fileCountBadge">0 selected</span>';
        } else {
            const names = Array.from(this.files).map(f => f.name).join(', ');
            fileLabel.innerHTML = '📎 ' + names + ' <span class="file-count" id="fileCountBadge">' + count + ' files</span>';
        }
        document.getElementById('fileCountBadge') && (document.getElementById('fileCountBadge').textContent = count + ' files');
    });

    function init() {
        loader.style.display = 'flex';
        let progress = 0;
        const interval = setInterval(() => {
            progress += 5;
            updateLoader(progress);
            if (progress >= 100) { clearInterval(interval); hideLoader(); }
        }, 150);

        const savedUser = localStorage.getItem('loggedInUser');
        if (savedUser) {
            try { 
                const data = JSON.parse(savedUser);
                currentUser = data.username;
                currentRole = data.role || 'user';
                isLoggedIn = true;
                if (currentRole !== 'owner' && currentRole !== 'admin') {
                    localStorage.setItem('lastActivity_' + currentUser, Date.now().toString());
                }
            } catch(e) {}
        }

        setTimeout(() => {
            hideLoader();
            if (isLoggedIn && currentUser) initDashboard();
            else { 
                loginBox.style.display = 'block'; 
                dashboardPage.style.display = 'none';
                document.getElementById('user').value = '';
                document.getElementById('pass').value = '';
            }
        }, 2000);
    }

    window.addEventListener('load', init);
})();
</script>

</body>
</html>
'''

# ============================================================
# TELEGRAM BOT HANDLERS - REPLY KEYBOARD + AUTO CANCEL + OWNER PASSWORD
# ============================================================

# Store user states for multi-step operations with timers
user_states = {}
user_timers = {}
owner_session = {}

def get_main_keyboard():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    keyboard.add(
        types.KeyboardButton("📂 My Files"),
        types.KeyboardButton("🤖 Bot List")
    )
    keyboard.add(
        types.KeyboardButton("👥 User List"),
        types.KeyboardButton("👑 Admin Panel")
    )
    keyboard.add(
        types.KeyboardButton("🔔 Notifications"),
        types.KeyboardButton("⚙ Settings")
    )
    keyboard.add(
        types.KeyboardButton("➕ Add User"),
        types.KeyboardButton("👤 Owner Info")
    )
    keyboard.add(
        types.KeyboardButton("✏️ Edit Contact Owner")
    )
    return keyboard

def get_owner_keyboard():
    keyboard = types.ReplyKeyboardMarkup(row_width=2, resize_keyboard=True)
    keyboard.add(
        types.KeyboardButton("📂 My Files"),
        types.KeyboardButton("🤖 Bot List")
    )
    keyboard.add(
        types.KeyboardButton("👥 User List"),
        types.KeyboardButton("👑 Admin Panel")
    )
    keyboard.add(
        types.KeyboardButton("🔔 Notifications"),
        types.KeyboardButton("⚙ Settings")
    )
    keyboard.add(
        types.KeyboardButton("➕ Add User"),
        types.KeyboardButton("👤 Owner Info")
    )
    keyboard.add(
        types.KeyboardButton("✏️ Edit Contact Owner")
    )
    keyboard.add(
        types.KeyboardButton("🔑 Change Owner Password"),
        types.KeyboardButton("✏️ Change Owner Username")
    )
    return keyboard

def clear_user_state(user_id):
    if user_id in user_timers:
        try:
            user_timers[user_id].cancel()
        except:
            pass
        del user_timers[user_id]
    if user_id in user_states:
        del user_states[user_id]
    if user_id in owner_session:
        del owner_session[user_id]

def set_user_state(user_id, state_data):
    clear_user_state(user_id)
    user_states[user_id] = state_data
    # Auto cancel after 60 seconds
    timer = threading.Timer(60.0, lambda: clear_user_state(user_id))
    timer.daemon = True
    user_timers[user_id] = timer
    timer.start()

def is_owner_authenticated(user_id):
    return user_id in owner_session and owner_session[user_id].get('authenticated', False)

@bot.message_handler(commands=['start', 'help'])
def start_message(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    clear_user_state(user_id)
    
    # ✅ FIX: Ensure default users first
    ensure_default_users()
    
    users = load_users()
    
    # Check if user is owner by Telegram ID
    if user_id == OWNER_ID:
        owner_session[user_id] = {'authenticated': True}
        bot.reply_to(
            message, 
            f"🤖 **Hosting Bot Control Panel**\n\n👤 Owner\n🤖 Bot: {BOT_USERNAME}\n\nSelect an option from the keyboard below:",
            reply_markup=get_owner_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    # Check if user is already authenticated as owner
    if is_owner_authenticated(user_id):
        bot.reply_to(
            message, 
            f"🤖 **Hosting Bot Control Panel**\n\n👤 Owner\n🤖 Bot: {BOT_USERNAME}\n\nSelect an option from the keyboard below:",
            reply_markup=get_owner_keyboard(),
            parse_mode='Markdown'
        )
        return
    
    # Check if user exists in users
    user_exists = False
    username = None
    
    for uname, info in users.items():
        if str(user_id) == info.get('telegram_id', ''):
            user_exists = True
            username = uname
            break
    
    if not user_exists:
        # Check if user_id directly matches any username
        for uname, info in users.items():
            if str(user_id) == uname:
                user_exists = True
                username = uname
                break
    
    if not user_exists:
        bot.reply_to(message, "⚠️ You are not registered. Contact owner.", reply_markup=types.ReplyKeyboardRemove())
        return
    
    # Store telegram_id if not already stored
    users_changed = False
    if users.get(username, {}).get('telegram_id') != str(user_id):
        users[username]['telegram_id'] = str(user_id)
        users_changed = True
    
    if users_changed:
        save_users(users)
    
    role = users.get(username, {}).get('role', 'user')
    
    if role == 'owner':
        owner_session[user_id] = {'authenticated': True}
        bot.reply_to(
            message, 
            f"🤖 **Hosting Bot Control Panel**\n\n👤 Owner\n🤖 Bot: {BOT_USERNAME}\n\nSelect an option from the keyboard below:",
            reply_markup=get_owner_keyboard(),
            parse_mode='Markdown'
        )
    else:
        bot.reply_to(
            message, 
            f"🤖 **Hosting Bot Control Panel**\n\n👤 User: `{username}`\n🤖 Bot: {BOT_USERNAME}\n\nSelect an option from the keyboard below:",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )

@bot.message_handler(commands=['owner'])
def owner_command(message):
    user_id = message.from_user.id
    chat_id = message.chat.id
    
    # Check if already authenticated
    if is_owner_authenticated(user_id):
        bot.reply_to(message, "✅ You are already authenticated as Owner!", reply_markup=get_owner_keyboard())
        return
    
    # Check if user is owner by role
    users = load_users()
    is_owner_user = False
    for uname, info in users.items():
        if info.get('role') == 'owner' and str(user_id) == info.get('telegram_id', ''):
            is_owner_user = True
            break
    
    if is_owner_user or user_id == OWNER_ID:
        owner_session[user_id] = {'authenticated': True}
        bot.reply_to(message, "✅ Owner access granted!", reply_markup=get_owner_keyboard())
        return
    
    # Ask for password
    msg = bot.reply_to(message, "🔑 **Enter Owner Password:**", parse_mode='Markdown', reply_markup=types.ReplyKeyboardRemove())
    set_user_state(user_id, {'state': 'owner_password', 'message_id': msg.message_id})

def process_owner_password(message):
    user_id = message.from_user.id
    password = message.text.strip()
    
    if password == OWNER_PASSWORD:
        owner_session[user_id] = {'authenticated': True}
        bot.reply_to(message, "✅ Owner access granted! You now have full access.", reply_markup=get_owner_keyboard())
        clear_user_state(user_id)
    else:
        bot.reply_to(message, "❌ Wrong password! Use /owner to try again.", reply_markup=get_main_keyboard())
        clear_user_state(user_id)

@bot.message_handler(func=lambda message: True)
def handle_text_messages(message):
    user_id = message.from_user.id
    text = message.text
    
    if user_id in user_states:
        state = user_states[user_id].get('state')
        if state == 'add_user_username':
            process_add_user_username(message)
            return
        elif state == 'add_user_password':
            process_add_user_password(message)
            return
        elif state == 'edit_contact_owner':
            process_edit_contact_owner(message)
            return
        elif state == 'add_admin':
            process_add_admin(message)
            return
        elif state == 'change_owner_username':
            process_change_owner_username(message)
            return
        elif state == 'change_owner_password':
            process_change_owner_password(message)
            return
        elif state == 'owner_password':
            process_owner_password(message)
            return
    
    clear_user_state(user_id)
    
    users = load_users()
    
    # Check if user is owner (authenticated)
    is_owner = is_owner_authenticated(user_id)
    
    if user_id == OWNER_ID:
        is_owner = True
    
    if not is_owner:
        # Check if user exists
        user_exists = False
        for username, info in users.items():
            if str(user_id) == info.get('telegram_id', ''):
                user_exists = True
                break
        
        if not user_exists:
            bot.reply_to(message, "⚠️ You are not registered. Contact owner.", reply_markup=types.ReplyKeyboardRemove())
            return
    
    if text == "📂 My Files":
        my_files_handler(message)
    elif text == "🤖 Bot List":
        bot_list_handler(message)
    elif text == "👥 User List":
        user_list_handler(message)
    elif text == "👑 Admin Panel":
        admin_panel_handler(message)
    elif text == "🔔 Notifications":
        notifications_handler(message)
    elif text == "⚙ Settings":
        settings_handler(message)
    elif text == "➕ Add User":
        add_user_handler(message)
    elif text == "👤 Owner Info":
        owner_info_handler(message)
    elif text == "✏️ Edit Contact Owner":
        edit_contact_owner_handler(message)
    elif text == "🔑 Change Owner Password" and is_owner:
        change_owner_password_handler(message)
    elif text == "✏️ Change Owner Username" and is_owner:
        change_owner_username_handler(message)
    else:
        bot.reply_to(message, "❌ Unknown command. Use /start to see menu.", reply_markup=get_main_keyboard())

# ============================================================
# HANDLER FUNCTIONS
# ============================================================

def my_files_handler(message):
    user_id = message.from_user.id
    users = load_users()
    
    # Pehle check karo ki user owner hai ya nahi
    if user_id == OWNER_ID or is_owner_authenticated(user_id):
        username = "riyaj"
    else:
        # Find username for this telegram_id
        username = None
        for uname, info in users.items():
            if str(user_id) == info.get('telegram_id', ''):
                username = uname
                break
        
        # Agar username nahi mila toh check karo ki user exist karta hai
        if not username:
            # Check if user_id directly matches any username
            for uname, info in users.items():
                if str(user_id) == uname:
                    username = uname
                    break
    
    if not username:
        bot.reply_to(message, "⚠️ You are not registered. Contact owner.", reply_markup=get_main_keyboard())
        return
    
    user_files = get_user_files(username)
    
    if not user_files:
        bot.reply_to(message, f"📂 **No files found for `{username}`**\n\nUpload files from website first.", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    response = f"📂 **Files for `{username}`:**\n\n"
    for f in user_files:
        status_icon = "🟢" if f['status'] == 'running' else "🔴"
        bot_name = f" @{f['bot_username']}" if f.get('bot_username') else ""
        response += f"{status_icon} `{f['filename']}` ({f['size']//1024} KB){bot_name}\n"
    
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=get_main_keyboard())

def bot_list_handler(message):
    all_files = get_all_files()
    
    bots = {}
    for f in all_files:
        if f.get('bot_username'):
            if f['bot_username'] not in bots:
                bots[f['bot_username']] = []
            bots[f['bot_username']].append(f)
    
    if not bots:
        bot.reply_to(message, "🤖 **No bots found**\n\nUpload files with bot tokens first.", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    response = "🤖 **All Bots:**\n\n"
    markup = types.InlineKeyboardMarkup(row_width=1)
    
    for bot_username, files in bots.items():
        response += f"🤖 @{bot_username} ({len(files)} files)\n"
        for f in files:
            status_icon = "🟢" if f['status'] == 'running' else "🔴"
            response += f"  {status_icon} `{f['filename']}` (👤{f['owner']})\n"
            markup.add(types.InlineKeyboardButton(
                f"{status_icon} {f['filename'][:25]}", 
                callback_data=f'viewfile_{f["id"]}_{f["owner"]}'
            ))
    
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_start'))
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)

def user_list_handler(message):
    user_id = message.from_user.id
    users = load_users()
    
    # Owner ko sab users dikhao
    if user_id == OWNER_ID or is_owner_authenticated(user_id):
        user_list = users
    else:
        # Non-owner ko sirf users dikhao (owner nahi)
        user_list = {u: info for u, info in users.items() if info.get('role') != 'owner'}
    
    if not user_list:
        bot.reply_to(message, "👥 **No users found**", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    response = "👥 **User List:**\n\n"
    for username, info in user_list.items():
        role = info.get('role', 'user')
        if role == 'owner':
            response += f"👑 `{username}` (Owner)\n"
        else:
            response += f"👤 `{username}` (🔑 `{info['password']}`)\n"
    
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=get_main_keyboard())

def admin_panel_handler(message):
    user_id = message.from_user.id
    users = load_users()
    
    # Check if user is owner
    if user_id == OWNER_ID or is_owner_authenticated(user_id):
        role = 'owner'
        username = 'riyaj'
    else:
        # Check in users
        username = None
        for uname, info in users.items():
            if str(user_id) == info.get('telegram_id', ''):
                username = uname
                break
        
        if not username:
            # Check if user_id directly matches username
            for uname, info in users.items():
                if str(user_id) == uname:
                    username = uname
                    break
        
        if not username:
            bot.reply_to(message, "⚠️ You are not registered. Contact owner.", reply_markup=get_main_keyboard())
            return
        
        role = users.get(username, {}).get('role', 'user')
    
    if role not in ['admin', 'owner']:
        bot.reply_to(message, "⚠️ Admin only", reply_markup=get_main_keyboard())
        return
    
    # Create inline keyboard for Admin Panel
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("👥 User List", callback_data='admin_userlist'),
        types.InlineKeyboardButton("➕ Add User", callback_data='admin_adduser')
    )
    markup.add(
        types.InlineKeyboardButton("👑 Admins List", callback_data='admin_adminslist'),
        types.InlineKeyboardButton("➕ Add Admin", callback_data='admin_addadmin')
    )
    if role == 'owner':
        markup.add(
            types.InlineKeyboardButton("👤 Owner Info", callback_data='admin_ownerinfo')
        )
        markup.add(
            types.InlineKeyboardButton("🔑 Change Owner Password", callback_data='admin_change_owner_password')
        )
        markup.add(
            types.InlineKeyboardButton("✏️ Change Owner Username", callback_data='admin_change_owner_username')
        )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_start'))
    
    response = "👑 **Admin Panel**\n\nSelect an option below:"
    
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)

def notifications_handler(message):
    settings = load_settings()
    current = settings.get('notifications_enabled', True)
    
    status = "🟢 ON" if current else "🔴 OFF"
    response = f"🔔 **Notifications**\n\nCurrent Status: {status}"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔔 Turn ON", callback_data='notif_on'),
        types.InlineKeyboardButton("🔕 Turn OFF", callback_data='notif_off')
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_start'))
    
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)

def settings_handler(message):
    settings = load_settings()
    
    signup_status = "🟢 ON" if settings.get('signup_enabled', True) else "🔴 OFF"
    notif_status = "🟢 ON" if settings.get('notifications_enabled', True) else "🔴 OFF"
    popup_status = "🟢 ON" if settings.get('telegram_popup', True) else "🔴 OFF"
    
    response = "⚙ **Settings**\n\n"
    response += f"🔔 Notifications: {notif_status}\n"
    response += f"📢 Telegram Popup: {popup_status}\n"
    response += f"🔓 Signup: {signup_status}\n"
    response += f"📎 Telegram Link: {settings.get('telegram_link', 'Not set')}\n"
    response += f"👤 Contact Owner: {settings.get('contact_owner', 'Not set')}\n\n"
    response += "Use the buttons below to change settings:"
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(
        types.InlineKeyboardButton("🔔 Notif ON", callback_data='notif_on'),
        types.InlineKeyboardButton("🔕 Notif OFF", callback_data='notif_off')
    )
    markup.add(
        types.InlineKeyboardButton("📢 Popup ON", callback_data='popup_on'),
        types.InlineKeyboardButton("🔇 Popup OFF", callback_data='popup_off')
    )
    markup.add(
        types.InlineKeyboardButton("🔓 Signup ON", callback_data='signup_on'),
        types.InlineKeyboardButton("🔒 Signup OFF", callback_data='signup_off')
    )
    markup.add(
        types.InlineKeyboardButton("📎 Set Link", callback_data='set_link'),
        types.InlineKeyboardButton("✏️ Edit Contact", callback_data='edit_contact')
    )
    markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_start'))
    
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=markup)

def add_user_handler(message):
    user_id = message.from_user.id
    clear_user_state(user_id)
    
    cancel_keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    cancel_keyboard.add(types.KeyboardButton("❌ Cancel"))
    
    msg = bot.reply_to(message, "👤 **Enter username:**\n\nType /cancel to cancel", parse_mode='Markdown', reply_markup=cancel_keyboard)
    set_user_state(user_id, {'state': 'add_user_username', 'message_id': msg.message_id, 'step': 'username'})

def process_add_user_username(message):
    user_id = message.from_user.id
    text = message.text.strip()
    
    if text == "❌ Cancel" or text == "/cancel":
        bot.reply_to(message, "❌ Operation cancelled", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    if not text:
        bot.reply_to(message, "❌ Invalid username. Try again or type /cancel", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    state = user_states.get(user_id, {})
    state['username'] = text
    state['state'] = 'add_user_password'
    state['step'] = 'password'
    user_states[user_id] = state
    
    cancel_keyboard = types.ReplyKeyboardMarkup(row_width=1, resize_keyboard=True)
    cancel_keyboard.add(types.KeyboardButton("❌ Cancel"))
    
    msg = bot.reply_to(message, f"🔑 **Enter password for `{text}`:**\n\nType /cancel to cancel", parse_mode='Markdown', reply_markup=cancel_keyboard)
    user_states[user_id]['message_id'] = msg.message_id

def process_add_user_password(message):
    user_id = message.from_user.id
    state = user_states.get(user_id, {})
    text = message.text.strip()
    
    if text == "❌ Cancel" or text == "/cancel":
        bot.reply_to(message, "❌ Operation cancelled", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    username = state.get('username')
    password = text
    
    if not password or not username:
        bot.reply_to(message, "❌ Invalid input. Try again.", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    users = load_users()
    if username in users:
        bot.reply_to(message, f"⚠️ User `{username}` already exists!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    users[username] = {
        'password': password,
        'role': 'user',
        'created': datetime.now().isoformat()
    }
    save_users(users)
    
    user_dir = os.path.join(UPLOAD_DIR, username)
    os.makedirs(user_dir, exist_ok=True)
    
    bot.reply_to(message, f"✅ User `{username}` added successfully!", parse_mode='Markdown', reply_markup=get_main_keyboard())
    clear_user_state(user_id)

def process_add_admin(message):
    user_id = message.from_user.id
    username = message.text.strip()
    
    if not username:
        bot.reply_to(message, "❌ Invalid username", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    users = load_users()
    
    if username not in users:
        bot.reply_to(message, f"⚠️ User `{username}` does not exist!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    if users[username].get('role') == 'admin':
        bot.reply_to(message, f"⚠️ User `{username}` is already admin!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    if users[username].get('role') == 'owner':
        bot.reply_to(message, f"⚠️ User `{username}` is owner!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    users[username]['role'] = 'admin'
    save_users(users)
    bot.reply_to(message, f"✅ User `{username}` promoted to admin!", parse_mode='Markdown', reply_markup=get_main_keyboard())
    clear_user_state(user_id)

def owner_info_handler(message):
    users = load_users()
    owner = users.get('riyaj', {'password': 'riyaj', 'role': 'owner'})
    
    response = "👑 **Owner Info**\n\n"
    response += f"Username: `riyaj`\n"
    response += f"Password: `{owner.get('password', 'riyaj')}`\n\n"
    response += "⚠️ Keep this safe!"
    
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=get_main_keyboard())

def edit_contact_owner_handler(message):
    msg = bot.reply_to(
        message, 
        "✏️ **Edit Contact Owner**\n\nSend new username (without @) or `0` to reset to default.",
        parse_mode='Markdown',
        reply_markup=types.ReplyKeyboardRemove()
    )
    set_user_state(message.from_user.id, {'state': 'edit_contact_owner', 'message_id': msg.message_id})

def process_edit_contact_owner(message):
    user_id = message.from_user.id
    username = message.text.strip()
    
    if not username:
        bot.reply_to(message, "❌ Invalid username", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    settings = load_settings()
    if username == '0':
        settings['contact_owner'] = 'Card_hacker_12'
        bot.reply_to(message, "✅ Contact owner reset to default: Card_hacker_12", reply_markup=get_main_keyboard())
    else:
        username = username.replace('@', '').strip()
        settings['contact_owner'] = username
        bot.reply_to(message, f"✅ Contact owner set to: {username}", reply_markup=get_main_keyboard())
    
    save_settings(settings)
    clear_user_state(user_id)

# ============================================================
# OWNER CHANGE USERNAME/PASSWORD HANDLERS (BOT BUTTONS)
# ============================================================

def change_owner_username_handler(message):
    msg = bot.reply_to(
        message, 
        "✏️ **Change Owner Username**\n\nSend new username:", 
        parse_mode='Markdown',
        reply_markup=types.ReplyKeyboardRemove()
    )
    set_user_state(message.from_user.id, {'state': 'change_owner_username', 'message_id': msg.message_id})

def change_owner_password_handler(message):
    msg = bot.reply_to(
        message, 
        "🔑 **Change Owner Password**\n\nSend new password:", 
        parse_mode='Markdown',
        reply_markup=types.ReplyKeyboardRemove()
    )
    set_user_state(message.from_user.id, {'state': 'change_owner_password', 'message_id': msg.message_id})

def process_change_owner_username(message):
    user_id = message.from_user.id
    new_username = message.text.strip()
    
    if not new_username:
        bot.reply_to(message, "❌ Invalid username", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    users = load_users()
    
    # Check if username already exists
    if new_username in users:
        bot.reply_to(message, f"⚠️ Username `{new_username}` already exists!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    # Find owner by role
    old_username = None
    for uname, info in users.items():
        if info.get('role') == 'owner':
            old_username = uname
            break
    
    if not old_username:
        bot.reply_to(message, "❌ Owner not found! Please run /start and try again.", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    # Get owner data
    owner_password = users[old_username].get('password', 'riyaj')
    owner_telegram_id = users[old_username].get('telegram_id', str(OWNER_ID))
    owner_created = users[old_username].get('created', datetime.now().isoformat())
    
    # Update user - Change username, keep everything else
    users[new_username] = {
        'password': owner_password,
        'role': 'owner',
        'created': owner_created,
        'telegram_id': owner_telegram_id
    }
    del users[old_username]
    save_users(users)
    
    # Rename directory
    old_dir = os.path.join(UPLOAD_DIR, old_username)
    new_dir = os.path.join(UPLOAD_DIR, new_username)
    if os.path.exists(old_dir):
        os.rename(old_dir, new_dir)
    
    old_bot_dir = get_bot_user_dir(old_username)
    new_bot_dir = get_bot_user_dir(new_username)
    if os.path.exists(old_bot_dir):
        os.rename(old_bot_dir, new_bot_dir)
    
    bot.reply_to(message, f"✅ Owner username changed from `{old_username}` to `{new_username}`!\n\nNow login with:\n👤 Username: `{new_username}`\n🔑 Password: `{owner_password}`", parse_mode='Markdown', reply_markup=get_main_keyboard())
    clear_user_state(user_id)

def process_change_owner_password(message):
    user_id = message.from_user.id
    new_password = message.text.strip()
    
    if not new_password:
        bot.reply_to(message, "❌ Invalid password", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    users = load_users()
    
    # Find owner by role
    owner_username = None
    for uname, info in users.items():
        if info.get('role') == 'owner':
            owner_username = uname
            break
    
    if not owner_username:
        bot.reply_to(message, "❌ Owner not found! Please run /start and try again.", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    # Update password
    users[owner_username]['password'] = new_password
    save_users(users)
    
    bot.reply_to(message, f"✅ Owner password changed successfully!\n\nNow login with:\n👤 Username: `{owner_username}`\n🔑 Password: `{new_password}`", parse_mode='Markdown', reply_markup=get_main_keyboard())
    clear_user_state(user_id)

# ============================================================
# INLINE CALLBACK HANDLERS - COMPLETE
# ============================================================

@bot.callback_query_handler(func=lambda call: True)
def handle_settings_callbacks(call):
    user_id = call.from_user.id
    data = call.data
    
    clear_user_state(user_id)
    
    # ============================================================
    # VIEW FILE DETAILS + ACTIONS
    # ============================================================
    if data.startswith('viewfile_'):
        file_data = data.split('_')
        if len(file_data) < 3:
            bot.answer_callback_query(call.id, "Invalid")
            return
        
        file_id = file_data[1]
        username = file_data[2]
        
        user_files = get_user_files(username)
        target_file = None
        for f in user_files:
            if f['id'] == file_id:
                target_file = f
                break
        
        if not target_file:
            bot.answer_callback_query(call.id, "File not found")
            return
        
        status_icon = "🟢" if target_file['status'] == 'running' else "🔴"
        bot_name = f" @{target_file['bot_username']}" if target_file.get('bot_username') else ""
        
        response = f"📄 **File: `{target_file['filename']}`**\n"
        response += f"Status: {status_icon} {target_file['status']}\n"
        response += f"Size: {target_file['size']//1024} KB\n"
        response += f"Owner: `{target_file['owner']}`\n"
        if bot_name:
            response += f"Bot: {bot_name}\n"
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        if target_file['status'] != 'running':
            markup.add(types.InlineKeyboardButton("▶ Start", callback_data=f'start_{file_id}_{username}'))
        if target_file['status'] == 'running':
            markup.add(types.InlineKeyboardButton("■ Stop", callback_data=f'stop_{file_id}_{username}'))
        markup.add(types.InlineKeyboardButton("↻ Restart", callback_data=f'restart_{file_id}_{username}'))
        markup.add(types.InlineKeyboardButton("📄 Logs", callback_data=f'logs_{file_id}_{username}'))
        markup.add(types.InlineKeyboardButton("⬇ Download", callback_data=f'download_{file_id}_{username}'))
        markup.add(types.InlineKeyboardButton("🗑 Delete", callback_data=f'delete_{file_id}_{username}'))
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_start'))
        
        bot.edit_message_text(response, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        bot.answer_callback_query(call.id)
        return
    
    # ============================================================
    # FILE ACTION CALLBACKS
    # ============================================================
    if data.startswith('start_'):
        file_data = data.split('_')
        if len(file_data) < 3:
            bot.answer_callback_query(call.id, "Invalid")
            return
        file_id = file_data[1]
        username = file_data[2]
        
        try:
            response = requests.post(
                f"{request.host_url}api/files/start/{file_id}",
                headers={'X-Username': username}
            )
            if response.status_code == 200:
                bot.answer_callback_query(call.id, "✅ File started")
                call.data = f'viewfile_{file_id}_{username}'
                handle_settings_callbacks(call)
            else:
                bot.answer_callback_query(call.id, "❌ Failed to start")
        except Exception as e:
            print(f"Start error: {e}")
            bot.answer_callback_query(call.id, "❌ Error")
        return
    
    if data.startswith('stop_'):
        file_data = data.split('_')
        if len(file_data) < 3:
            bot.answer_callback_query(call.id, "Invalid")
            return
        file_id = file_data[1]
        username = file_data[2]
        
        try:
            response = requests.post(
                f"{request.host_url}api/files/stop/{file_id}",
                headers={'X-Username': username}
            )
            if response.status_code == 200:
                bot.answer_callback_query(call.id, "✅ File stopped")
                call.data = f'viewfile_{file_id}_{username}'
                handle_settings_callbacks(call)
            else:
                bot.answer_callback_query(call.id, "❌ Failed to stop")
        except Exception as e:
            print(f"Stop error: {e}")
            bot.answer_callback_query(call.id, "❌ Error")
        return
    
    if data.startswith('restart_'):
        file_data = data.split('_')
        if len(file_data) < 3:
            bot.answer_callback_query(call.id, "Invalid")
            return
        file_id = file_data[1]
        username = file_data[2]
        
        try:
            requests.post(
                f"{request.host_url}api/files/stop/{file_id}",
                headers={'X-Username': username}
            )
            response = requests.post(
                f"{request.host_url}api/files/start/{file_id}",
                headers={'X-Username': username}
            )
            if response.status_code == 200:
                bot.answer_callback_query(call.id, "✅ File restarted")
                call.data = f'viewfile_{file_id}_{username}'
                handle_settings_callbacks(call)
            else:
                bot.answer_callback_query(call.id, "❌ Failed to restart")
        except Exception as e:
            print(f"Restart error: {e}")
            bot.answer_callback_query(call.id, "❌ Error")
        return
    
    if data.startswith('logs_'):
        file_data = data.split('_')
        if len(file_data) < 3:
            bot.answer_callback_query(call.id, "Invalid")
            return
        file_id = file_data[1]
        username = file_data[2]
        
        try:
            response = requests.get(
                f"{request.host_url}api/files/logs/{file_id}",
                headers={'X-Username': username}
            )
            if response.status_code == 200:
                logs = response.json().get('logs', 'No logs available')
                if len(logs) > 4000:
                    logs = logs[:4000] + '\n... (truncated)'
                bot.answer_callback_query(call.id)
                bot.send_message(call.message.chat.id, f"📄 **Logs for file:**\n\n```\n{logs}\n```", parse_mode='Markdown')
            else:
                bot.answer_callback_query(call.id, "❌ Failed to get logs")
        except Exception as e:
            print(f"Logs error: {e}")
            bot.answer_callback_query(call.id, "❌ Error")
        return
    
    if data.startswith('download_'):
        file_data = data.split('_')
        if len(file_data) < 3:
            bot.answer_callback_query(call.id, "Invalid")
            return
        file_id = file_data[1]
        username = file_data[2]
        
        try:
            response = requests.get(
                f"{request.host_url}api/files/download/{file_id}",
                headers={'X-Username': username}
            )
            if response.status_code == 200:
                bot.answer_callback_query(call.id, "⬇ Download started from website")
            else:
                bot.answer_callback_query(call.id, "❌ Failed to download")
        except Exception as e:
            print(f"Download error: {e}")
            bot.answer_callback_query(call.id, "❌ Error")
        return
    
    if data.startswith('delete_'):
        file_data = data.split('_')
        if len(file_data) < 3:
            bot.answer_callback_query(call.id, "Invalid")
            return
        file_id = file_data[1]
        username = file_data[2]
        
        try:
            response = requests.delete(
                f"{request.host_url}api/files/delete/{file_id}",
                headers={'X-Username': username}
            )
            if response.status_code == 200:
                bot.answer_callback_query(call.id, "🗑 File deleted")
                bot.edit_message_text("✅ File deleted successfully", call.message.chat.id, call.message.message_id)
                class FakeMessage:
                    def __init__(self, chat_id, from_user):
                        self.chat = type('obj', (object,), {'id': chat_id})()
                        self.from_user = type('obj', (object,), {'id': from_user})()
                fake_msg = FakeMessage(call.message.chat.id, call.from_user.id)
                bot_list_handler(fake_msg)
            else:
                bot.answer_callback_query(call.id, "❌ Failed to delete")
        except Exception as e:
            print(f"Delete error: {e}")
            bot.answer_callback_query(call.id, "❌ Error")
        return
    
    # ============================================================
    # ADMIN PANEL CALLBACKS
    # ============================================================
    if data == 'admin_userlist':
        users = load_users()
        user_list = {u: info for u, info in users.items() if info.get('role') != 'owner'}
        
        if not user_list:
            bot.edit_message_text("👥 **No users found**", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=get_main_keyboard())
            bot.answer_callback_query(call.id)
            return
        
        response = "👥 **User List:**\n\n"
        for username, info in user_list.items():
            role = info.get('role', 'user')
            if role == 'owner':
                response += f"👑 `{username}` (Owner)\n"
            else:
                response += f"👤 `{username}` (🔑 `{info['password']}`)\n"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_panel'))
        bot.edit_message_text(response, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        bot.answer_callback_query(call.id)
        return
    
    elif data == 'admin_adduser':
        bot.edit_message_text("➕ **Add User**\n\nSend username:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        set_user_state(call.from_user.id, {'state': 'add_user_username', 'chat_id': call.message.chat.id, 'message_id': call.message.message_id})
        bot.answer_callback_query(call.id)
        return
    
    elif data == 'admin_adminslist':
        users = load_users()
        admin_list = {u: info for u, info in users.items() if info.get('role') in ['admin', 'owner']}
        
        user_id = call.from_user.id
        if user_id != OWNER_ID:
            admin_list = {u: info for u, info in admin_list.items() if u != 'riyaj'}
        
        if not admin_list:
            bot.edit_message_text("👑 **No admins found**", call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=get_main_keyboard())
            bot.answer_callback_query(call.id)
            return
        
        response = "👑 **Admins List:**\n\n"
        for username, info in admin_list.items():
            if username == 'riyaj':
                response += f"👑 {username} (Owner)\n"
            else:
                response += f"🛡️ `{username}` (🔑 `{info['password']}`)\n"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_panel'))
        bot.edit_message_text(response, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        bot.answer_callback_query(call.id)
        return
    
    elif data == 'admin_addadmin':
        bot.edit_message_text("➕ **Add Admin**\n\nSend existing username to promote to admin:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        set_user_state(call.from_user.id, {'state': 'add_admin', 'chat_id': call.message.chat.id, 'message_id': call.message.message_id})
        bot.answer_callback_query(call.id)
        return
    
    elif data == 'admin_ownerinfo':
        users = load_users()
        owner = users.get('riyaj', {'password': 'riyaj', 'role': 'owner'})
        
        response = "👑 **Owner Info**\n\n"
        response += f"Username: `riyaj`\n"
        response += f"Password: `{owner.get('password', 'riyaj')}`\n\n"
        response += "⚠️ Keep this safe!"
        
        markup = types.InlineKeyboardMarkup()
        markup.add(types.InlineKeyboardButton("🔙 Back to Admin Panel", callback_data='admin_panel'))
        bot.edit_message_text(response, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        bot.answer_callback_query(call.id)
        return
    
    elif data == 'admin_change_owner_password':
        bot.edit_message_text("🔑 **Change Owner Password**\n\nSend new password:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        set_user_state(call.from_user.id, {'state': 'change_owner_password', 'chat_id': call.message.chat.id, 'message_id': call.message.message_id})
        bot.answer_callback_query(call.id)
        return
    
    elif data == 'admin_change_owner_username':
        bot.edit_message_text("✏️ **Change Owner Username**\n\nSend new username:", call.message.chat.id, call.message.message_id, parse_mode='Markdown')
        set_user_state(call.from_user.id, {'state': 'change_owner_username', 'chat_id': call.message.chat.id, 'message_id': call.message.message_id})
        bot.answer_callback_query(call.id)
        return
    
    elif data == 'admin_panel':
        # Go back to admin panel
        users = load_users()
        user_id = call.from_user.id
        
        if user_id == OWNER_ID:
            role = 'owner'
        else:
            username = None
            for uname, info in users.items():
                if str(user_id) == info.get('telegram_id', ''):
                    username = uname
                    break
            if not username:
                for uname, info in users.items():
                    if str(user_id) == uname:
                        username = uname
                        break
            role = users.get(username, {}).get('role', 'user') if username else 'user'
        
        markup = types.InlineKeyboardMarkup(row_width=2)
        markup.add(
            types.InlineKeyboardButton("👥 User List", callback_data='admin_userlist'),
            types.InlineKeyboardButton("➕ Add User", callback_data='admin_adduser')
        )
        markup.add(
            types.InlineKeyboardButton("👑 Admins List", callback_data='admin_adminslist'),
            types.InlineKeyboardButton("➕ Add Admin", callback_data='admin_addadmin')
        )
        if role == 'owner':
            markup.add(
                types.InlineKeyboardButton("👤 Owner Info", callback_data='admin_ownerinfo')
            )
            markup.add(
                types.InlineKeyboardButton("🔑 Change Owner Password", callback_data='admin_change_owner_password')
            )
            markup.add(
                types.InlineKeyboardButton("✏️ Change Owner Username", callback_data='admin_change_owner_username')
            )
        markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='back_to_start'))
        
        response = "👑 **Admin Panel**\n\nSelect an option below:"
        bot.edit_message_text(response, call.message.chat.id, call.message.message_id, parse_mode='Markdown', reply_markup=markup)
        bot.answer_callback_query(call.id)
        return
    
    # ============================================================
    # ORIGINAL SETTINGS CALLBACKS
    # ============================================================
    if data == 'notif_on':
        settings = load_settings()
        settings['notifications_enabled'] = True
        save_settings(settings)
        bot.answer_callback_query(call.id, "🔔 Notifications ON")
        bot.edit_message_text("✅ Notifications turned ON", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
    
    elif data == 'notif_off':
        settings = load_settings()
        settings['notifications_enabled'] = False
        save_settings(settings)
        bot.answer_callback_query(call.id, "🔕 Notifications OFF")
        bot.edit_message_text("✅ Notifications turned OFF", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
    
    elif data == 'popup_on':
        settings = load_settings()
        settings['telegram_popup'] = True
        save_settings(settings)
        bot.answer_callback_query(call.id, "📢 Popup ON")
        bot.edit_message_text("✅ Telegram Popup turned ON", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
    
    elif data == 'popup_off':
        settings = load_settings()
        settings['telegram_popup'] = False
        save_settings(settings)
        bot.answer_callback_query(call.id, "🔇 Popup OFF")
        bot.edit_message_text("✅ Telegram Popup turned OFF", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
    
    elif data == 'signup_on':
        settings = load_settings()
        settings['signup_enabled'] = True
        save_settings(settings)
        bot.answer_callback_query(call.id, "🔓 Signup ON")
        bot.edit_message_text("✅ Signup ENABLED", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
    
    elif data == 'signup_off':
        settings = load_settings()
        settings['signup_enabled'] = False
        save_settings(settings)
        bot.answer_callback_query(call.id, "🔒 Signup OFF")
        bot.edit_message_text("✅ Signup DISABLED", call.message.chat.id, call.message.message_id, reply_markup=get_main_keyboard())
    
    elif data == 'set_link':
        msg = bot.send_message(call.message.chat.id, "📎 **Enter Telegram channel/group link:**\nExample: `https://t.me/+m0R5z1yhmCtiZjQ9`", parse_mode='Markdown', reply_markup=types.ReplyKeyboardRemove())
        set_user_state(user_id, {'state': 'set_link', 'message': call.message})
        bot.register_next_step_handler(msg, process_set_link, call.message)
    
    elif data == 'edit_contact':
        msg = bot.send_message(call.message.chat.id, "✏️ **Enter new contact owner username (without @):**", parse_mode='Markdown', reply_markup=types.ReplyKeyboardRemove())
        set_user_state(user_id, {'state': 'edit_contact', 'message': call.message})
        bot.register_next_step_handler(msg, process_edit_contact_inline, call.message)
    
    elif data == 'back_to_start':
        class FakeMessage:
            def __init__(self, chat_id, from_user):
                self.chat = type('obj', (object,), {'id': chat_id})()
                self.from_user = type('obj', (object,), {'id': from_user})()
                self.text = '/start'
        fake_msg = FakeMessage(call.message.chat.id, user_id)
        start_message(fake_msg)

def process_set_link(message, original_message):
    user_id = message.from_user.id
    link = message.text.strip()
    
    if not link:
        bot.reply_to(message, "❌ Invalid link", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    settings = load_settings()
    settings['telegram_link'] = link
    save_settings(settings)
    bot.reply_to(message, f"✅ Telegram link set to:\n{link}", reply_markup=get_main_keyboard())
    clear_user_state(user_id)

def process_edit_contact_inline(message, original_message):
    user_id = message.from_user.id
    username = message.text.strip()
    
    if not username:
        bot.reply_to(message, "❌ Invalid username", reply_markup=get_main_keyboard())
        clear_user_state(user_id)
        return
    
    settings = load_settings()
    if username == '0':
        settings['contact_owner'] = 'Card_hacker_12'
        bot.reply_to(message, "✅ Contact owner reset to default: Card_hacker_12", reply_markup=get_main_keyboard())
    else:
        username = username.replace('@', '').strip()
        settings['contact_owner'] = username
        bot.reply_to(message, f"✅ Contact owner set to: {username}", reply_markup=get_main_keyboard())
    
    save_settings(settings)
    clear_user_state(user_id)

# ============================================================
# COMMAND HANDLERS
# ============================================================

@bot.message_handler(commands=['notif_on'])
def notif_on_cmd(message):
    settings = load_settings()
    settings['notifications_enabled'] = True
    save_settings(settings)
    bot.reply_to(message, "🔔 Notifications ON", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['notif_off'])
def notif_off_cmd(message):
    settings = load_settings()
    settings['notifications_enabled'] = False
    save_settings(settings)
    bot.reply_to(message, "🔕 Notifications OFF", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['popup_on'])
def popup_on_cmd(message):
    settings = load_settings()
    settings['telegram_popup'] = True
    save_settings(settings)
    bot.reply_to(message, "📢 Telegram Popup ON", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['popup_off'])
def popup_off_cmd(message):
    settings = load_settings()
    settings['telegram_popup'] = False
    save_settings(settings)
    bot.reply_to(message, "📢 Telegram Popup OFF", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['signup_on'])
def signup_on_cmd(message):
    settings = load_settings()
    settings['signup_enabled'] = True
    save_settings(settings)
    bot.reply_to(message, "🔓 Signup ENABLED", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['signup_off'])
def signup_off_cmd(message):
    settings = load_settings()
    settings['signup_enabled'] = False
    save_settings(settings)
    bot.reply_to(message, "🔒 Signup DISABLED", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['set_link'])
def set_link_cmd(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: /set_link <telegram_link>", reply_markup=get_main_keyboard())
        return
    
    link = parts[1].strip()
    settings = load_settings()
    settings['telegram_link'] = link
    save_settings(settings)
    bot.reply_to(message, f"✅ Telegram link set to:\n{link}", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['edit_contact'])
def edit_contact_cmd(message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: /edit_contact <username> (without @)", reply_markup=get_main_keyboard())
        return
    
    username = parts[1].strip().replace('@', '')
    settings = load_settings()
    settings['contact_owner'] = username
    save_settings(settings)
    bot.reply_to(message, f"✅ Contact owner set to: {username}", reply_markup=get_main_keyboard())

@bot.message_handler(commands=['remove_user'])
def remove_user_cmd(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: /remove_user <username>", reply_markup=get_main_keyboard())
        return
    
    username = parts[1]
    users = load_users()
    
    if username not in users:
        bot.reply_to(message, "❌ User not found", reply_markup=get_main_keyboard())
        return
    
    if users[username].get('role') in ['owner', 'admin']:
        bot.reply_to(message, "⚠️ Cannot remove admin/owner", reply_markup=get_main_keyboard())
        return
    
    user_dir = os.path.join(UPLOAD_DIR, username)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)
    
    bot_user_dir = get_bot_user_dir(username)
    if os.path.exists(bot_user_dir):
        shutil.rmtree(bot_user_dir)
    
    del users[username]
    save_users(users)
    bot.reply_to(message, f"✅ User `{username}` removed", parse_mode='Markdown', reply_markup=get_main_keyboard())

@bot.message_handler(commands=['add_admin'])
def add_admin_cmd(message):
    parts = message.text.split()
    if len(parts) < 2:
        bot.reply_to(message, "❌ Usage: /add_admin <username>", reply_markup=get_main_keyboard())
        return
    
    username = parts[1]
    users = load_users()
    
    if username not in users:
        bot.reply_to(message, f"⚠️ User `{username}` does not exist!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    if users[username].get('role') == 'admin':
        bot.reply_to(message, f"⚠️ User `{username}` is already admin!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    if users[username].get('role') == 'owner':
        bot.reply_to(message, f"⚠️ User `{username}` is owner!", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    users[username]['role'] = 'admin'
    save_users(users)
    bot.reply_to(message, f"✅ User `{username}` promoted to admin!", parse_mode='Markdown', reply_markup=get_main_keyboard())

@bot.message_handler(commands=['admins_list'])
def admins_list_cmd(message):
    users = load_users()
    admin_list = {u: info for u, info in users.items() if info.get('role') in ['admin', 'owner']}
    
    user_id = message.from_user.id
    if str(user_id) != OWNER_ID:
        admin_list = {u: info for u, info in admin_list.items() if u != 'riyaj'}
    
    if not admin_list:
        bot.reply_to(message, "👑 **No admins found**", parse_mode='Markdown', reply_markup=get_main_keyboard())
        return
    
    response = "👑 **Admins List:**\n\n"
    for username, info in admin_list.items():
        if username == 'riyaj':
            response += f"👑 {username} (Owner)\n"
        else:
            response += f"🛡️ `{username}` (🔑 `{info['password']}`)\n"
    
    bot.reply_to(message, response, parse_mode='Markdown', reply_markup=get_main_keyboard())

# ============================================================
# START APPLICATION
# ============================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 3000))
    
    print("="*60)
    print(f"🐍 PYTHON HOSTING PANEL + BOT")
    print(f"🤖 Bot Username: {BOT_USERNAME}")
    print(f"🔐 Secret Owner: {OWNER_ID}")
    print(f"🔑 Owner Password: {OWNER_PASSWORD}")
    print(f"🌐 Website Port: {port}")
    print("="*60)
    
    ensure_default_users()
    
    # Remove webhook first
    try:
        bot.remove_webhook()
        print("✅ Webhook removed")
    except Exception as e:
        print(f"⚠️ Webhook error: {e}")
    
    def run_bot():
        print("🚀 Bot starting polling...")
        while True:
            try:
                bot.polling(none_stop=True, interval=0, timeout=20)
            except Exception as e:
                print(f"❌ Bot polling error: {e}")
                time.sleep(5)
                print("🔄 Restarting bot polling...")
    
    bot_thread = threading.Thread(target=run_bot, daemon=True)
    bot_thread.start()
    print("✅ Bot polling thread started")
    
    print("🌐 Starting Flask server...")
    app.run(host='0.0.0.0', port=port, debug=False)