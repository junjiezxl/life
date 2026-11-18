import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

SESSION_COOKIE_NAME = "lifehub_session"
REMEMBER_DAYS = 30
SHORT_SESSION_HOURS = 12
PBKDF2_ITERATIONS = 310_000


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PBKDF2_ITERATIONS,
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iter_str, salt, expected_hex = stored_hash.split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_str)
        dk = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt.encode("utf-8"),
            iterations,
        )
        return hmac.compare_digest(dk.hex(), expected_hex)
    except Exception:
        return False


def hash_session_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def has_any_user(conn: Any) -> bool:
    row = conn.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone()
    return row is not None


def create_user(conn: Any, username: str, password: str) -> Dict[str, Any]:
    clean_name = username.strip()
    if not clean_name:
        raise ValueError("用户名不能为空")
    if len(password) < 6:
        raise ValueError("密码长度至少需要 6 位")
    ts = _now_dt().isoformat(timespec="seconds")
    pw_hash = hash_password(password)
    cur = conn.execute(
        "INSERT INTO auth_users (username, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (clean_name, pw_hash, ts, ts),
    )
    return {"id": cur.lastrowid, "username": clean_name, "created_at": ts}


def ensure_env_auth_user(conn: Any) -> None:
    if has_any_user(conn):
        return
    env_password = (os.environ.get("LIFEHUB_AUTH_PASSWORD") or "").strip()
    if not env_password:
        return
    env_username = (os.environ.get("LIFEHUB_AUTH_USERNAME") or "admin").strip() or "admin"
    ts = _now_dt().isoformat(timespec="seconds")
    pw_hash = hash_password(env_password)
    conn.execute(
        "INSERT INTO auth_users (username, password_hash, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (env_username, pw_hash, ts, ts),
    )


def authenticate_user(conn: Any, username: str, password: str) -> Optional[Dict[str, Any]]:
    clean_name = username.strip()
    row = conn.execute(
        "SELECT id, username, password_hash FROM auth_users WHERE username = ?",
        (clean_name,),
    ).fetchone()
    if not row:
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    return {"id": row["id"], "username": row["username"]}


def update_user_credentials(
    conn: Any,
    user_id: int,
    current_password: str,
    new_password: Optional[str] = None,
    new_username: Optional[str] = None,
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT id, username, password_hash FROM auth_users WHERE id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        raise ValueError("用户不存在")
    if not verify_password(current_password, row["password_hash"]):
        raise PermissionError("当前密码不正确")

    target_username = row["username"]
    if new_username is not None and new_username.strip():
        target_username = new_username.strip()

    target_hash = row["password_hash"]
    if new_password is not None and new_password != "":
        if len(new_password) < 6:
            raise ValueError("新密码长度至少需要 6 位")
        target_hash = hash_password(new_password)

    if target_username == row["username"] and target_hash == row["password_hash"]:
        raise ValueError("请填写新的用户名或新密码")

    ts = _now_dt().isoformat(timespec="seconds")
    conn.execute(
        "UPDATE auth_users SET username = ?, password_hash = ?, updated_at = ? WHERE id = ?",
        (target_username, target_hash, ts, user_id),
    )
    conn.execute(
        "UPDATE auth_sessions SET username = ? WHERE user_id = ?",
        (target_username, user_id),
    )
    return {"id": user_id, "username": target_username}


def create_session(
    conn: Any,
    user_id: int,
    username: str,
    remember_me: bool = True,
    user_agent: str = "",
) -> Tuple[str, str, int]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hash_session_token(raw_token)
    now = _now_dt()
    delta = timedelta(days=REMEMBER_DAYS) if remember_me else timedelta(hours=SHORT_SESSION_HOURS)
    expires_at = (now + delta).isoformat(timespec="seconds")
    max_age = int(delta.total_seconds())
    conn.execute(
        """INSERT INTO auth_sessions (token_hash, user_id, username, remember_me, user_agent, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (token_hash, user_id, username, 1 if remember_me else 0, user_agent[:240], now.isoformat(timespec="seconds"), expires_at),
    )
    return raw_token, expires_at, max_age


def get_session_user(conn: Any, raw_token: str) -> Optional[Dict[str, Any]]:
    if not raw_token:
        return None
    token_hash = hash_session_token(raw_token)
    row = conn.execute(
        """SELECT s.token_hash, s.user_id, u.username, s.remember_me, s.expires_at
        FROM auth_sessions s
        JOIN auth_users u ON u.id = s.user_id
        WHERE s.token_hash = ?""",
        (token_hash,),
    ).fetchone()
    if not row:
        return None
    try:
        exp = datetime.fromisoformat(row["expires_at"])
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=_now_dt().tzinfo)
        if exp <= _now_dt():
            conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,))
            return None
    except Exception:
        return None
    return {
        "user_id": row["user_id"],
        "username": row["username"],
        "remember_me": bool(row["remember_me"]),
        "expires_at": row["expires_at"],
        "token_hash": row["token_hash"],
    }


def delete_session(conn: Any, raw_token: str) -> None:
    if not raw_token:
        return
    conn.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (hash_session_token(raw_token),))


def delete_other_sessions(conn: Any, user_id: int, except_raw_token: Optional[str] = None) -> int:
    if except_raw_token:
        cur = conn.execute(
            "DELETE FROM auth_sessions WHERE user_id = ? AND token_hash != ?",
            (user_id, hash_session_token(except_raw_token)),
        )
    else:
        cur = conn.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    return cur.rowcount or 0


def count_active_sessions(conn: Any, user_id: int) -> int:
    now_str = _now_dt().isoformat(timespec="seconds")
    conn.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now_str,))
    row = conn.execute(
        "SELECT COUNT(*) AS cnt FROM auth_sessions WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    return int(row["cnt"]) if row else 0


class LoginRateLimiter:
    """In-memory sliding-window rate limiter against login brute-force attempts."""

    def __init__(self, max_attempts: int = 5, window_seconds: int = 300, lock_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lock_seconds = lock_seconds
        self._failures: Dict[str, List[float]] = defaultdict(list)
        self._locked_until: Dict[str, float] = {}

    def check_blocked(self, key: str) -> int:
        now = time.monotonic()
        until = self._locked_until.get(key)
        if until and until > now:
            return int(until - now) + 1
        if until:
            self._locked_until.pop(key, None)
        return 0

    def record_failure(self, key: str) -> int:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        attempts = [t for t in self._failures[key] if t > cutoff]
        attempts.append(now)
        self._failures[key] = attempts
        if len(attempts) >= self.max_attempts:
            self._locked_until[key] = now + self.lock_seconds
            self._failures[key] = []
            return self.lock_seconds
        return 0

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)
        self._locked_until.pop(key, None)
