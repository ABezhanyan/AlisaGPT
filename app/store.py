import hashlib
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROMPT = "Ты голосовой помощник. Отвечай по-русски, кратко и понятно, без Markdown."

class Store:
    def __init__(self, path, model="gpt-6-luna", daily_limit=100):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.model, self.daily_limit = model, daily_limit
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS profiles(id TEXT PRIMARY KEY, model TEXT, prompt TEXT, daily_limit INTEGER);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, user_id TEXT, role TEXT, content TEXT);
        CREATE TABLE IF NOT EXISTS links(alice_id TEXT PRIMARY KEY, user_id TEXT);
        CREATE TABLE IF NOT EXISTS codes(hash TEXT PRIMARY KEY, user_id TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS usage(user_id TEXT, day TEXT, count INTEGER, PRIMARY KEY(user_id, day));
        ''')
        self.db.commit()

    def settings(self, user_id):
        self.db.execute("INSERT OR IGNORE INTO profiles VALUES(?,?,?,?)", (user_id, self.model, DEFAULT_PROMPT, self.daily_limit))
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM profiles WHERE id=?", (user_id,)).fetchone())

    def update_settings(self, user_id, **kwargs):
        self.settings(user_id)
        for key, value in kwargs.items():
            if key not in {"model", "prompt", "daily_limit"}:
                raise ValueError("Unknown setting")
            self.db.execute(f"UPDATE profiles SET {key}=? WHERE id=?", (value, user_id))
        self.db.commit()

    def history(self, user_id):
        rows = self.db.execute("SELECT role, content FROM history WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def add_exchange(self, user_id, text, answer):
        self.db.executemany("INSERT INTO history(user_id,role,content) VALUES(?,?,?)", [(user_id,"user",text),(user_id,"assistant",answer)])
        self.db.execute("DELETE FROM history WHERE user_id=? AND id NOT IN (SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 12)", (user_id,user_id))
        self.db.commit()

    def clear_history(self, user_id):
        self.db.execute("DELETE FROM history WHERE user_id=?", (user_id,)); self.db.commit()

    def usage(self, user_id):
        row = self.db.execute("SELECT count FROM usage WHERE user_id=? AND day=?", (user_id,datetime.now(timezone.utc).date().isoformat())).fetchone()
        return row[0] if row else 0

    def reserve_request(self, user_id, limit):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO usage VALUES(?,?,0)", (user_id,day))
            return self.db.execute("UPDATE usage SET count=count+1 WHERE user_id=? AND day=? AND count<?", (user_id,day,limit)).rowcount == 1

    def create_link_code(self, user_id):
        code = str(secrets.randbelow(90000000) + 10000000)
        with self.db:
            self.db.execute("DELETE FROM codes WHERE user_id=? OR expires<?", (user_id,time.time()))
            self.db.execute("INSERT INTO codes VALUES(?,?,?)", (hashlib.sha256(code.encode()).hexdigest(),user_id,time.time()+300))
        return code

    def redeem_code(self, alice_id, code):
        with self.db:
            row = self.db.execute("SELECT * FROM codes WHERE hash=? AND expires>?", (hashlib.sha256(code.encode()).hexdigest(),time.time())).fetchone()
            if not row: return False
            self.db.execute("DELETE FROM codes WHERE hash=?", (row["hash"],))
            self.db.execute("INSERT OR REPLACE INTO links VALUES(?,?)", (alice_id,row["user_id"]))
        return True

    def linked_user(self, alice_id):
        row = self.db.execute("SELECT user_id FROM links WHERE alice_id=?", (alice_id,)).fetchone()
        return row[0] if row else None

    def unlink(self, user_id):
        with self.db:
            self.db.execute("DELETE FROM links WHERE user_id=?", (user_id,))
            self.db.execute("DELETE FROM codes WHERE user_id=?", (user_id,))
