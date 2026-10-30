import json
import sqlite3

import httpx
import pytest

from app.assistant import Assistant
from app.config import Settings
from app.languages import requested_language
from app.store import Store


@pytest.mark.parametrize("phrase,code", [
    ("Говори по-английски", "en"),
    ("Переключись на армянский", "hy"),
    ("Говори по-русски", "ru"),
    ("Speak Armenian", "hy"),
    ("Խոսիր հայերեն", "hy"),
])
def test_spoken_language_switches(phrase, code):
    assert requested_language(phrase) == code


def test_translation_question_does_not_change_language():
    assert requested_language("Переведи фразу на английский") is None


def test_existing_profile_database_gets_language_column(tmp_path):
    path = tmp_path / "existing.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE profiles(id TEXT PRIMARY KEY, model TEXT, prompt TEXT, daily_limit INTEGER)")
        db.execute("INSERT INTO profiles VALUES(?,?,?,?)", ("42", "gpt-6-luna", "Моя инструкция", 50))
    store = Store(path)
    assert store.settings("42")["prompt"] == "Моя инструкция"
    assert store.settings("42")["language"] == "ru"
    store.db.close()


@pytest.mark.asyncio
async def test_language_persists_and_guides_model_after_restart(tmp_path):
    path = tmp_path / "language.sqlite3"
    store = Store(path)
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "Բարև։"}}]})

    settings = Settings(_env_file=None, alisagpt_api_key="key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings, store, upstream)
        assert "հայերեն" in await assistant.reply("42", "Говори по-армянски")
        assert requests == []
    store.db.close()

    store = Store(path)
    assert store.settings("42")["language"] == "hy"
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings, store, upstream)
        assert await assistant.reply("42", "Привет") == "Բարև։"
    assert "արևելահայերենով" in requests[-1]["messages"][1]["content"]
    store.db.close()

