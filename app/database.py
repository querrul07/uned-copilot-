import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Any, Optional
from app.config import DB_PATH

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        
        # Subjects table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS subjects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                code TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Conversations table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
        """)

        # Messages table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                sources TEXT DEFAULT '[]',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
            )
        """)

        # Files metadata table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS files_meta (
                id TEXT PRIMARY KEY,
                subject_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_size INTEGER DEFAULT 0,
                source TEXT DEFAULT 'manual',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (subject_id) REFERENCES subjects(id) ON DELETE CASCADE
            )
        """)
        conn.commit()

# --- Subject CRUD ---
def get_all_subjects() -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM subjects ORDER BY name ASC")
        return [dict(row) for row in cursor.fetchall()]

def get_subject_by_id(subject_id: str) -> Optional[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

def upsert_subject(subject_id: str, name: str, code: str = "") -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO subjects (id, name, code, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                code = CASE WHEN excluded.code != '' THEN excluded.code ELSE subjects.code END,
                updated_at = CURRENT_TIMESTAMP
        """, (subject_id, name, code))
        conn.commit()
    return {"id": subject_id, "name": name, "code": code}

def delete_subject(subject_id: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE conversation_id IN (SELECT id FROM conversations WHERE subject_id = ?)", (subject_id,))
        cursor.execute("DELETE FROM conversations WHERE subject_id = ?", (subject_id,))
        cursor.execute("DELETE FROM files_meta WHERE subject_id = ?", (subject_id,))
        cursor.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
        conn.commit()

# --- Conversation CRUD ---
def get_conversations_for_subject(subject_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM conversations WHERE subject_id = ? ORDER BY updated_at DESC", (subject_id,))
        return [dict(row) for row in cursor.fetchall()]

def create_conversation(conversation_id: str, subject_id: str, title: str) -> Dict[str, Any]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO conversations (id, subject_id, title)
            VALUES (?, ?, ?)
        """, (conversation_id, subject_id, title))
        conn.commit()
    return {"id": conversation_id, "subject_id": subject_id, "title": title}

def update_conversation_title(conversation_id: str, title: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE conversations SET title = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?", (title, conversation_id))
        conn.commit()

def delete_conversation(conversation_id: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        cursor.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        conn.commit()

# --- Message CRUD ---
def get_messages(conversation_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC", (conversation_id,))
        rows = cursor.fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d["sources"])
            except Exception:
                d["sources"] = []
            result.append(d)
        return result

def add_message(conversation_id: str, role: str, content: str, sources: List[Dict[str, Any]] = None) -> int:
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO messages (conversation_id, role, content, sources)
            VALUES (?, ?, ?, ?)
        """, (conversation_id, role, content, sources_json))
        msg_id = cursor.lastrowid
        cursor.execute("UPDATE conversations SET updated_at = CURRENT_TIMESTAMP WHERE id = ?", (conversation_id,))
        conn.commit()
        return msg_id

# --- Files Meta CRUD ---
def record_file(file_id: str, subject_id: str, filename: str, file_path: str, file_size: int, source: str = "manual"):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO files_meta (id, subject_id, filename, file_path, file_size, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                filename = excluded.filename,
                file_path = excluded.file_path,
                file_size = excluded.file_size,
                source = excluded.source
        """, (file_id, subject_id, filename, file_path, file_size, source))
        conn.commit()

def get_files_for_subject(subject_id: str) -> List[Dict[str, Any]]:
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM files_meta WHERE subject_id = ? ORDER BY filename ASC", (subject_id,))
        return [dict(row) for row in cursor.fetchall()]

def delete_file_meta(file_id: str):
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM files_meta WHERE id = ?", (file_id,))
        conn.commit()
