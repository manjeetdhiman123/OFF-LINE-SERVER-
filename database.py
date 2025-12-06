import sqlite3
import hashlib
from pathlib import Path
from cryptography.fernet import Fernet
import os
import json
from datetime import datetime
import pytz

# Configuration
DB_PATH = Path(__file__).parent / 'users.db'
ENCRYPTION_KEY_FILE = Path(__file__).parent / '.encryption_key'
KOLKATA_TZ = pytz.timezone('Asia/Kolkata')

def get_encryption_key():
    """Get or create encryption key for cookie storage"""
    if ENCRYPTION_KEY_FILE.exists():
        with open(ENCRYPTION_KEY_FILE, 'rb') as f:
            return f.read()
    else:
        key = Fernet.generate_key()
        with open(ENCRYPTION_KEY_FILE, 'wb') as f:
            f.write(key)
        return key

ENCRYPTION_KEY = get_encryption_key()
cipher_suite = Fernet(ENCRYPTION_KEY)

def encrypt_cookies(cookies):
    return cipher_suite.encrypt(cookies.encode()).decode()

def decrypt_cookies(encrypted_cookies):
    if encrypted_cookies:
        try:
            return cipher_suite.decrypt(encrypted_cookies.encode()).decode()
        except:
            return ""
    return ""

def init_db():
    """Initialize database with tables, including the new tasks table"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Users Table (for login)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 2. User Configs Table (for settings, cookies, etc.)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_configs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL UNIQUE,
            chat_id TEXT,
            name_prefix TEXT,
            delay INTEGER DEFAULT 30,
            cookies_encrypted TEXT,
            messages TEXT,
            locked_group_name TEXT,
            locked_nicknames TEXT,
            lock_enabled INTEGER DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            admin_e2ee_thread_id TEXT,
            admin_e2ee_chat_type TEXT DEFAULT 'E2EE',
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    # 3. New Tasks Table (for multi-task management) 
    # task_id will be auto-incrementing, acting as the unique identifier (PID substitute)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            task_id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            is_running INTEGER DEFAULT 1,
            started_at TEXT, 
            message_count INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users(id)
        )
    ''')
    
    conn.commit()
    conn.close()

# Ensure DB is initialized when the file is imported
init_db()

# --- User Management Functions (Unchanged, simplified here) ---

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def create_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        password_hash = hash_password(password)
        cursor.execute('INSERT INTO users (username, password_hash) VALUES (?, ?)', (username, password_hash))
        user_id = cursor.lastrowid
        
        # Initialize user config with default values
        cursor.execute('''
            INSERT INTO user_configs (user_id, chat_id, name_prefix, delay, cookies_encrypted, messages)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (user_id, '', '', 30, '', 'Hello!'))
        
        conn.commit()
        conn.close()
        return True, "Account created successfully!"
    except sqlite3.IntegrityError:
        conn.close()
        return False, "Username already exists."
    except Exception as e:
        conn.close()
        return False, f"An error occurred: {str(e)}"

def verify_user(username, password):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    password_hash = hash_password(password)
    
    cursor.execute('SELECT id FROM users WHERE username = ? AND password_hash = ?', (username, password_hash))
    user = cursor.fetchone()
    
    conn.close()
    return user[0] if user else None

def get_username(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT username FROM users WHERE id = ?', (user_id,))
    username = cursor.fetchone()
    conn.close()
    return username[0] if username else "Unknown User"

# --- User Config Functions (Modified to include admin tracking and use timezone) ---

def get_user_config(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT chat_id, name_prefix, delay, cookies_encrypted, messages, lock_enabled, admin_e2ee_thread_id, admin_e2ee_chat_type FROM user_configs WHERE user_id = ?', (user_id,))
    config = cursor.fetchone()
    conn.close()
    
    if config:
        # Decrypt cookies before returning
        decrypted_cookies = decrypt_cookies(config[3])
        return {
            'chat_id': config[0] or '',
            'name_prefix': config[1] or '',
            'delay': config[2],
            'cookies': decrypted_cookies,
            'messages': config[4] or '',
            'lock_enabled': bool(config[5]),
            'admin_e2ee_thread_id': config[6],
            'admin_e2ee_chat_type': config[7],
        }
    
    return None

def update_user_config(user_id, chat_id, name_prefix, delay, cookies, messages):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    encrypted_cookies = encrypt_cookies(cookies)
    
    cursor.execute('''
        UPDATE user_configs 
        SET chat_id = ?, name_prefix = ?, delay = ?, 
            cookies_encrypted = ?, messages = ?, updated_at = ?
        WHERE user_id = ?
    ''', (chat_id, name_prefix, delay, encrypted_cookies, messages, datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d %H:%M:%S"), user_id))
    
    conn.commit()
    conn.close()


# --- Admin E2EE Thread Tracking Functions ---

def get_admin_e2ee_thread_id(user_id, current_cookies):
    """Retrieve saved admin thread ID and check if cookies match."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT admin_e2ee_thread_id, cookies_encrypted, admin_e2ee_chat_type FROM user_configs WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    conn.close()

    if result and result[0]:
        saved_encrypted_cookies = result[1]
        
        # Check if saved cookies match current cookies (after encryption)
        if saved_encrypted_cookies == encrypt_cookies(current_cookies):
            return result[0], result[2]  # Return thread_id and chat_type
    
    return None, None

def set_admin_e2ee_thread_id(user_id, thread_id, current_cookies, chat_type='E2EE'):
    """Save the admin E2EE thread ID and the cookies used to find it."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    encrypted_cookies = encrypt_cookies(current_cookies)
    
    cursor.execute('''
        UPDATE user_configs 
        SET admin_e2ee_thread_id = ?, admin_e2ee_chat_type = ?, cookies_encrypted = ?
        WHERE user_id = ?
    ''', (thread_id, chat_type, encrypted_cookies, user_id))
    
    conn.commit()
    conn.close()

def clear_admin_e2ee_thread_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE user_configs 
        SET admin_e2ee_thread_id = NULL, admin_e2ee_chat_type = NULL
        WHERE user_id = ?
    ''', (user_id,))
    conn.commit()
    conn.close()


# ===============================================
# --- NEW MULTI-TASK MANAGEMENT FUNCTIONS ---
# ===============================================

def create_task_record(user_id):
    """Create a new task record and return its unique task_id (PID substitute)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    started_at = datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    
    cursor.execute('''
        INSERT INTO tasks (user_id, is_running, started_at)
        VALUES (?, 1, ?)
    ''', (user_id, started_at))
    
    task_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return task_id

def get_tasks_for_user(user_id):
    """Retrieve all running tasks for a specific user."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # We retrieve all tasks that are marked as running for this user
    cursor.execute('''
        SELECT task_id, is_running, started_at, message_count
        FROM tasks 
        WHERE user_id = ? AND is_running = 1
    ''', (user_id,))
    
    tasks = []
    for row in cursor.fetchall():
        tasks.append({
            'task_id': row[0],
            'is_running': bool(row[1]),
            'started_at': row[2],
            'message_count': row[3]
        })
        
    conn.close()
    return tasks

def get_task(task_id):
    """Retrieve the full record for a specific task ID."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT task_id, is_running, started_at, message_count, user_id
        FROM tasks 
        WHERE task_id = ?
    ''', (task_id,))
    
    row = cursor.fetchone()
    conn.close()
    
    if row:
        return {
            'task_id': row[0],
            'is_running': bool(row[1]),
            'started_at': row[2],
            'message_count': row[3],
            'user_id': row[4]
        }
    return {'is_running': False} # Return False if task not found

def stop_task_by_id(user_id, task_id):
    """Mark a specific task as stopped (is_running = 0)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Ensure the user only stops their own tasks
    cursor.execute('''
        UPDATE tasks 
        SET is_running = 0 
        WHERE task_id = ? AND user_id = ? AND is_running = 1
    ''', (task_id, user_id))
    
    rows_affected = cursor.rowcount
    conn.commit()
    conn.close()
    
    return rows_affected > 0 # Returns True if the task was running and stopped

def set_task_message_count(task_id, count):
    """Update the message count for a running task."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE tasks 
        SET message_count = ?
        WHERE task_id = ?
    ''', (count, task_id))
    
    conn.commit()
    conn.close()

# --- Lock System Functions (Simplified) ---

def set_lock_enabled(user_id, enabled):
    """Enable or disable the lock system"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('''
        UPDATE user_configs 
        SET lock_enabled = ?, updated_at = ?
        WHERE user_id = ?
    ''', (1 if enabled else 0, datetime.now(KOLKATA_TZ).strftime("%Y-%m-%d %H:%M:%S"), user_id))
    
    conn.commit()
    conn.close()
    
def get_lock_enabled(user_id):
    """Check if lock is enabled for a user"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT lock_enabled FROM user_configs WHERE user_id = ?', (user_id,))
    result = cursor.fetchone()
    
    conn.close()
    return bool(result[0]) if result else False