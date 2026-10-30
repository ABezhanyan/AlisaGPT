import hashlib
import json
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
        CREATE TABLE IF NOT EXISTS voice_usage(user_id TEXT, day TEXT, count INTEGER, PRIMARY KEY(user_id, day));
        CREATE TABLE IF NOT EXISTS pending_weather(user_id TEXT PRIMARY KEY, question TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS web_cache(cache_key TEXT PRIMARY KEY, answer TEXT NOT NULL, sources TEXT NOT NULL, fetched REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS recipes(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, request TEXT NOT NULL, answer TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS recipes_user ON recipes(user_id, created DESC);
        CREATE TABLE IF NOT EXISTS telegram_invites(hash TEXT PRIMARY KEY, owner_id TEXT NOT NULL, expires REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS telegram_access(user_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS memories(id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, fact TEXT NOT NULL, source TEXT NOT NULL, created REAL NOT NULL, UNIQUE(user_id, fact));
        CREATE INDEX IF NOT EXISTS memories_user ON memories(user_id, created DESC);
        ''')
        if not any(row[1] == "language" for row in self.db.execute("PRAGMA table_info(profiles)")):
            self.db.execute("ALTER TABLE profiles ADD COLUMN language TEXT NOT NULL DEFAULT 'ru'")
        self.db.commit()

    def settings(self, user_id):
        self.db.execute("INSERT OR IGNORE INTO profiles(id,model,prompt,daily_limit,language) VALUES(?,?,?,?,?)", (user_id, self.model, DEFAULT_PROMPT, self.daily_limit, "ru"))
        self.db.commit()
        return dict(self.db.execute("SELECT * FROM profiles WHERE id=?", (user_id,)).fetchone())

    def update_settings(self, user_id, **kwargs):
        self.settings(user_id)
        for key, value in kwargs.items():
            if key not in {"model", "prompt", "daily_limit", "language"}:
                raise ValueError("Unknown setting")
            if key == "language" and value not in {"ru", "en", "hy"}:
                raise ValueError("Unknown language")
            self.db.execute(f"UPDATE profiles SET {key}=? WHERE id=?", (value, user_id))
        self.db.commit()

    def history(self, user_id):
        rows = self.db.execute("SELECT role, content FROM history WHERE user_id=? ORDER BY id DESC LIMIT 12", (user_id,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    def add_exchange(self, user_id, text, answer):
        self.db.executemany("INSERT INTO history(user_id,role,content) VALUES(?,?,?)", [(user_id,"user",text),(user_id,"assistant",answer)])
        self.db.execute("DELETE FROM history WHERE user_id=? AND id NOT IN (SELECT id FROM history WHERE user_id=? ORDER BY id DESC LIMIT 12)", (user_id,user_id))
        self.db.commit()

    def answer_id(self, user_id, answer):
        row = self.db.execute("SELECT id FROM history WHERE user_id=? AND role='assistant' AND content=? ORDER BY id DESC LIMIT 1", (user_id, answer)).fetchone()
        return row[0] if row else None

    def answer_for_voice(self, user_id, answer_id):
        row = self.db.execute("SELECT content FROM history WHERE user_id=? AND role='assistant' AND id=?", (user_id, answer_id)).fetchone()
        return row[0] if row else None

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
        if not re.fullmatch(r"[А-Яа-яЁёA-Za-zԱ-Ֆա-ֆ\s-]{2,60}", city):
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

    def voice_usage(self, user_id):
        row = self.db.execute("SELECT count FROM voice_usage WHERE user_id=? AND day=?", (user_id, datetime.now(timezone.utc).date().isoformat())).fetchone()
        return row[0] if row else 0

    def reserve_voice(self, user_id, limit):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO voice_usage VALUES(?,?,0)", (user_id, day))
            return self.db.execute("UPDATE voice_usage SET count=count+1 WHERE user_id=? AND day=? AND count<?", (user_id, day, limit)).rowcount == 1

    def reserve_web_search(self, user_id, limit):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO web_usage VALUES(?,?,0)", (user_id, day))
            return self.db.execute("UPDATE web_usage SET count=count+1 WHERE user_id=? AND day=? AND count<?", (user_id, day, limit)).rowcount == 1

    def web_cache(self, cache_key):
        row = self.db.execute("SELECT answer, sources, fetched FROM web_cache WHERE cache_key=?", (cache_key,)).fetchone()
        return {"answer": row[0], "sources": json.loads(row[1]), "fetched": row[2]} if row else None

    def save_web_cache(self, cache_key, answer, sources):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO web_cache VALUES(?,?,?,?)", (cache_key, answer, json.dumps(sources), time.time()))

    def save_recipe(self, user_id, request, answer):
        with self.db:
            cursor = self.db.execute("INSERT INTO recipes(user_id, request, answer, created) VALUES(?,?,?,?)", (user_id, request[:300], answer[:5000], time.time()))
            return cursor.lastrowid

    def recipes(self, user_id, limit=20):
        from .recipes import recipe_title
        return [{**dict(row), "request": recipe_title(row["request"])} for row in self.db.execute("SELECT id, request FROM recipes WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))]

    def recipe(self, user_id, recipe_id):
        row = self.db.execute("SELECT id, request, answer FROM recipes WHERE user_id=? AND id=?", (user_id, recipe_id)).fetchone()
        if not row:
            return None
        from .recipes import recipe_title
        return {**dict(row), "request": recipe_title(row["request"])}

    def known_weather_cities(self):
        rows = self.db.execute("SELECT DISTINCT substr(fact, length('Город для прогноза погоды: ') + 1) FROM memories WHERE fact LIKE 'Город для прогноза погоды: %'").fetchall()
        return [row[0] for row in rows if row[0]]

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

    def create_telegram_invite(self, owner_id):
        code = secrets.token_urlsafe(12)
        with self.db:
            self.db.execute("DELETE FROM telegram_invites WHERE expires<?", (time.time(),))
            self.db.execute("INSERT INTO telegram_invites VALUES(?,?,?)", (hashlib.sha256(code.encode()).hexdigest(), owner_id, time.time()+86400))
        return code

    def redeem_telegram_invite(self, user_id, code, owner_id):
        if not code or len(code) > 100 or user_id == owner_id:
            return False
        digest = hashlib.sha256(code.encode()).hexdigest()
        with self.db:
            row = self.db.execute("SELECT owner_id FROM telegram_invites WHERE hash=? AND expires>?", (digest, time.time())).fetchone()
            if not row or row[0] != owner_id:
                return False
            self.db.execute("DELETE FROM telegram_invites WHERE hash=?", (digest,))
            self.db.execute("INSERT OR REPLACE INTO telegram_access VALUES(?,?,?)", (user_id, owner_id, time.time()))
        return True

    def has_telegram_access(self, user_id, owner_id):
        return self.db.execute("SELECT 1 FROM telegram_access WHERE user_id=? AND owner_id=?", (user_id, owner_id)).fetchone() is not None

    def telegram_guests(self, owner_id):
        return [row[0] for row in self.db.execute("SELECT user_id FROM telegram_access WHERE owner_id=? ORDER BY created DESC", (owner_id,))]

    def revoke_telegram_access(self, owner_id, user_id):
        with self.db:
            return self.db.execute("DELETE FROM telegram_access WHERE owner_id=? AND user_id=?", (owner_id, user_id)).rowcount > 0

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

