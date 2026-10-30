import hashlib
import re
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
        CREATE TABLE IF NOT EXISTS web_usage(user_id TEXT, day TEXT, count INTEGER, PRIMARY KEY(user_id, day));
        CREATE TABLE IF NOT EXISTS pending_weather(user_id TEXT PRIMARY KEY, question TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, fact TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL, UNIQUE(user_id, fact));
        CREATE INDEX IF NOT EXISTS memories_user ON memories(user_id, created DESC);
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

    def remember(self, user_id, fact, source="manual"):
        fact = " ".join(fact.strip().split())
        if not fact or len(fact) > 300:
            raise ValueError("Fact must contain 1-300 characters")
        if re.search(r"(?i)(sk-[a-z0-9_-]{12,}|парол|токен|api.?key|секрет|\b\d{12,}\b)", fact):
            raise ValueError("Secrets cannot be saved")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO memories(user_id, fact, source, created) VALUES(?,?,?,?)", (user_id, fact, source, time.time()))

    def memories(self, user_id):
        return [dict(row) for row in self.db.execute("SELECT id, fact, source FROM memories WHERE user_id=? ORDER BY created DESC, id DESC", (user_id,))]

    def forget(self, user_id, memory_id=None):
        with self.db:
            if memory_id is None:
                return self.db.execute("DELETE FROM memories WHERE user_id=?", (user_id,)).rowcount
            return self.db.execute("DELETE FROM memories WHERE user_id=? AND id=?", (user_id, memory_id)).rowcount

    def relevant_memories(self, user_id, text, limit=20):
        facts = self.memories(user_id)
        words = set(re.findall(r"[\wа-яё]{4,}", text.lower()))
        ranked = sorted(facts, key=lambda row: (
            len(words & set(re.findall(r"[\wа-яё]{4,}", row["fact"].lower()))),
            row["source"] == "manual",
            row["id"]), reverse=True)
        return ranked[:limit]

    def weather_city(self, user_id):
        row = self.db.execute("SELECT fact FROM memories WHERE user_id=? AND fact LIKE 'Город для прогноза погоды: %' ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        return row[0].split(": ", 1)[1] if row else None

    def set_weather_city(self, user_id, city):
        city = " ".join(city.strip().split())
        if not re.fullmatch(r"[А-Яа-яЁёA-Za-z\s-]{2,60}", city):
            raise ValueError("Invalid city")
        with self.db:
            self.db.execute("DELETE FROM memories WHERE user_id=? AND fact LIKE 'Город для прогноза погоды: %'", (user_id,))
            self.db.execute("INSERT INTO memories(user_id, fact, source, created) VALUES(?,?,?,?)", (user_id, f"Город для прогноза погоды: {city}", "manual", time.time()))

    def ask_weather_city(self, user_id, question):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO pending_weather VALUES(?,?,?)", (user_id, question, time.time()))

    def take_pending_weather(self, user_id):
        with self.db:
            row = self.db.execute("SELECT question, created FROM pending_weather WHERE user_id=?", (user_id,)).fetchone()
            self.db.execute("DELETE FROM pending_weather WHERE user_id=?", (user_id,))
        return row[0] if row and time.time() - row[1] < 300 else None

    def web_usage(self, user_id):
        row = self.db.execute("SELECT count FROM web_usage WHERE user_id=? AND day=?", (user_id, datetime.now(timezone.utc).date().isoformat())).fetchone()
        return row[0] if row else 0

    def reserve_web_search(self, user_id, limit):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO web_usage VALUES(?,?,0)", (user_id, day))
            return self.db.execute("UPDATE web_usage SET count=count+1 WHERE user_id=? AND day=? AND count<?", (user_id, day, limit)).rowcount == 1

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
