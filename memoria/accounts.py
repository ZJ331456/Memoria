"""Server-owned users and opaque, revocable login sessions."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import threading
import time
import uuid
from pathlib import Path

USERNAME = re.compile(r"^[a-z][a-z0-9_]{2,31}$")
SESSION_TTL = 12 * 3600


def normalize_username(value: str) -> str:
    value = value.strip().lower()
    if not USERNAME.fullmatch(value):
        raise ValueError("用户名须为 3–32 位英文小写字母、数字或下划线，以字母开头")
    return value


def password_hash(password: str, salt: bytes) -> bytes:
    if not 12 <= len(password) <= 128:
        raise ValueError("密码须为 12–128 个字符")
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**17, r=8, p=1,
                          maxmem=256 * 1024 * 1024, dklen=32)


class AccountStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = path
        self.db = sqlite3.connect(path, check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                salt BLOB, password_hash BLOB, created_at REAL NOT NULL,
                role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','superadmin')));
            CREATE TABLE IF NOT EXISTS login_sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                expires_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS login_attempts (
                bucket TEXT NOT NULL, attempted_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS login_attempt_bucket ON login_attempts(bucket, attempted_at);
            CREATE TABLE IF NOT EXISTS account_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at REAL NOT NULL,
                actor_user_id TEXT REFERENCES users(id), target_user_id TEXT NOT NULL REFERENCES users(id),
                action TEXT NOT NULL);
        """)
        # Existing accounts retain their IDs, hashes and tenant directories. No
        # username or first public registration implicitly becomes an administrator.
        if 'role' not in {row['name'] for row in self.db.execute('PRAGMA table_info(users)')}:
            with self.db:
                self.db.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user' CHECK(role IN ('user','superadmin'))")
        self._lock = threading.RLock()

    def close(self):
        self.db.close()

    def user(self, username: str) -> dict | None:
        with self._lock:
            row = self.db.execute("SELECT id, username, role FROM users WHERE username=?", (normalize_username(username),)).fetchone()
        return dict(row) if row else None

    def reserve(self, username: str) -> dict:
        username = normalize_username(username)
        with self._lock, self.db:
            self.db.execute("INSERT OR IGNORE INTO users(id,username,created_at) VALUES(?,?,?)",
                            (uuid.uuid4().hex, username, time.time()))
        return self.user(username)

    def register(self, username: str, password: str) -> dict:
        username = normalize_username(username)
        salt = secrets.token_bytes(16)
        hashed = password_hash(password, salt)
        with self._lock, self.db:
            self.db.execute("INSERT INTO users(id,username,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
                            (uuid.uuid4().hex, username, salt, hashed, time.time()))
        return self.user(username)

    def set_password(self, username: str, password: str) -> None:
        salt = secrets.token_bytes(16)
        hashed = password_hash(password, salt)
        with self._lock, self.db:
            user = self.user(username)
            if not user:
                raise ValueError("账号不存在")
            self.db.execute("UPDATE users SET salt=?,password_hash=? WHERE id=?", (salt, hashed, user["id"]))
            self.db.execute("DELETE FROM login_sessions WHERE user_id=?", (user["id"],))
            self._event('local_password_reset', user['id'])

    def _event(self, action: str, target: str, actor: str | None = None):
        self.db.execute('INSERT INTO account_events(occurred_at,actor_user_id,target_user_id,action) VALUES(?,?,?,?)',
                        (time.time(),actor,target,action))

    def set_role(self, username: str, role: str) -> dict:
        """Local administrator operation; public HTTP cannot grant roles."""
        username = normalize_username(username)
        if role not in {'user','superadmin'}:
            raise ValueError('Unknown account role')
        with self._lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            user = self.user(username)
            if not user:
                raise ValueError('账号不存在')
            if user['role'] == role:
                return user
            if user['role']=='superadmin' and self.db.execute("SELECT COUNT(*) FROM users WHERE role='superadmin'").fetchone()[0]<=1:
                raise ValueError('不能降级最后一个超级管理员')
            self.db.execute('UPDATE users SET role=? WHERE id=?',(role,user['id']))
            self.db.execute('DELETE FROM login_sessions WHERE user_id=?',(user['id'],))
            self._event('local_role_'+role,user['id'])
            return {**user,'role':role}

    def disable_login(self, username: str):
        """Disable a local account without deleting its identity or personal files."""
        with self._lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            user=self.user(username)
            if not user:
                raise ValueError('账号不存在')
            if user['role']=='superadmin':
                raise ValueError('请先降级超级管理员再停用登录')
            self.db.execute('UPDATE users SET salt=NULL,password_hash=NULL WHERE id=?',(user['id'],))
            self.db.execute('DELETE FROM login_sessions WHERE user_id=?',(user['id'],))
            self._event('local_login_disabled',user['id'])

    def list_accounts(self) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self.db.execute(
                'SELECT id,username,role,created_at,password_hash IS NOT NULL AS login_enabled FROM users ORDER BY username')]

    def reset_password_as_superadmin(self, actor_token: str, username: str, password: str):
        salt=secrets.token_bytes(16)
        hashed=password_hash(password,salt)
        with self._lock, self.db:
            self.db.execute('BEGIN IMMEDIATE')
            actor=self.authenticate(actor_token)
            if not actor or actor['role']!='superadmin':
                raise PermissionError('需要超级管理员权限')
            user=self.user(username)
            if not user:
                raise ValueError('账号不存在')
            self.db.execute('UPDATE users SET salt=?,password_hash=? WHERE id=?',(salt,hashed,user['id']))
            self.db.execute('DELETE FROM login_sessions WHERE user_id=?',(user['id'],))
            self._event('superadmin_password_reset',user['id'],actor['id'])

    def throttle(self, bucket: str, *, maximum: int = 10) -> bool:
        # Store a hash of IP/name buckets, never a password or bearer token.
        bucket = hashlib.sha256(bucket.encode()).hexdigest()
        now = time.time()
        with self._lock, self.db:
            self.db.execute("DELETE FROM login_attempts WHERE attempted_at<?", (now - 600,))
            count = self.db.execute("SELECT COUNT(*) FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()[0]
            if count >= maximum:
                return False
            self.db.execute("INSERT INTO login_attempts VALUES(?,?)", (bucket, now))
        return True

    def login(self, username: str, password: str) -> tuple[dict, str] | None:
        username = normalize_username(username)
        with self._lock:
            row = self.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        salt = bytes(row["salt"]) if row and row["salt"] else b"memoria-dummy-v1!"
        hashed = password_hash(password, salt)
        if not row or not row["password_hash"] or not hmac.compare_digest(hashed, bytes(row["password_hash"])):
            return None
        token = secrets.token_urlsafe(32)
        with self._lock, self.db:
            # A concurrent password reset must not issue a session for the old hash.
            current = self.db.execute("SELECT password_hash,role FROM users WHERE id=?", (row["id"],)).fetchone()
            if not current or not current["password_hash"] or not hmac.compare_digest(bytes(current["password_hash"]), bytes(row["password_hash"])):
                return None
            self.db.execute("DELETE FROM login_sessions WHERE expires_at<=?", (time.time(),))
            self.db.execute("INSERT INTO login_sessions VALUES(?,?,?)",
                            (hashlib.sha256(token.encode()).hexdigest(), row["id"], time.time() + SESSION_TTL))
        return {"id": row["id"], "username": row["username"], "role": current['role']}, token

    def authenticate(self, token: str) -> dict | None:
        if not token or len(token) > 200:
            return None
        with self._lock:
            row = self.db.execute("""SELECT u.id,u.username,u.role FROM login_sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>?""", (hashlib.sha256(token.encode()).hexdigest(), time.time())).fetchone()
        return dict(row) if row else None

    def logout(self, token: str) -> None:
        with self._lock, self.db:
            self.db.execute("DELETE FROM login_sessions WHERE token_hash=?", (hashlib.sha256(token.encode()).hexdigest(),))


def user_directory(data_root: Path, user_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", user_id):
        raise ValueError("Invalid server-owned user ID")
    base = data_root.resolve() / "users"
    path = base / user_id
    if path.is_symlink() or not path.resolve().is_relative_to(base):
        raise ValueError("User directory escapes private storage")
    return path
