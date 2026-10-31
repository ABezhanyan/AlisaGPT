import json
import sqlite3
import asyncio

import httpx
import pytest

from app.assistant import Assistant, UserError
from app.config import Settings
from app.languages import answer_language_request, requested_language
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


@pytest.mark.parametrize("phrase,code", [
    ("Ответь на английском: Как дела?", "en"),
    ("Переведи на армянский: Добрый день", "hy"),
    ("Answer in English: What happened?", "en"),
])
def test_single_answer_language_request(phrase, code):
    assert answer_language_request(phrase) == code


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


@pytest.mark.asyncio
async def test_voice_audio_is_on_demand_without_daily_quota(tmp_path):
    store = Store(tmp_path / "voice.sqlite3")
    store.update_settings("42", language="hy")
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, content=b"OggS-audio")

    settings = Settings(_env_file=None, alisagpt_api_key="key")
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings, store, upstream)
        assert await assistant.synthesize_voice("42", "Բարև։") == b"OggS-audio"
        assert await assistant.synthesize_voice("42", "Կրկին։") == b"OggS-audio"
    assert len(requests) == 2
    assert requests[0]["response_format"] == "opus"
    assert "Armenian" in requests[0]["instructions"]
    assert store.voice_usage("42") == 2
    store.db.close()


@pytest.mark.asyncio
async def test_station_sends_non_russian_voice_to_telegram_in_background(tmp_path):
    store = Store(tmp_path / "station-voice.sqlite3")
    requests = []

    def provider(request):
        requests.append(request)
        if request.url.path == "/v1/chat/completions":
            body = json.loads(request.content)
            assert "արևելահայերենով" in body["messages"][1]["content"]
            return httpx.Response(200, json={"choices": [{"message": {"content": "Բարև ձեզ։"}}]})
        if request.url.path == "/v1/audio/speech":
            assert json.loads(request.content)["input"] == "Բարև ձեզ։"
            return httpx.Response(200, content=b"OggS-audio")
        if request.url.path.endswith("/sendVoice"):
            assert b"OggS-audio" in request.content
            assert b"chat_id" in request.content
            return httpx.Response(200, json={"ok": True})
        raise AssertionError(request.url)

    settings = Settings(_env_file=None, alisagpt_api_key="key", telegram_bot_token="bot-token", telegram_owner_id=42)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings, store, upstream)
        assert await assistant.reply("42", "Ответь на армянском: привет", channel="voice") == "Բարև ձեզ։"
        assert [request.url.path for request in requests] == ["/v1/chat/completions"]
        await asyncio.gather(*assistant.delivery_tasks)
    assert any(request.url.path.endswith("/sendVoice") for request in requests)
    assert store.voice_usage("42") == 1
    assert store.settings("42")["language"] == "ru"
    store.db.close()
