import asyncio
import json

import httpx
import pytest

from app.assistant import Assistant
from app.config import Settings
from app.main import create_app
from app.store import Store


def settings():
    return Settings(_env_file=None, alisagpt_api_key="test-key", alice_skill_id="skill", alice_webhook_token="secret")


@pytest.mark.asyncio
async def test_auto_memory_survives_restart_and_informs_next_answer(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = Store(path)
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        if body.get("response_format"):
            content = json.dumps({"facts": ["Пользователя зовут Ирина."]})
        else:
            content = "Ответ"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        assert await assistant.reply("owner", "Меня зовут Ирина") == "Ответ"
        await asyncio.gather(*assistant.learning_tasks)
        assert len(requests) == 2
    store.db.close()

    store = Store(path)
    assert [item["fact"] for item in store.memories("owner")] == ["Пользователя зовут Ирина."]
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        await assistant.reply("owner", "Как меня зовут?")
        await asyncio.gather(*assistant.learning_tasks)
    answer_request = next(request for request in reversed(requests) if "response_format" not in request)
    assert "Пользователя зовут Ирина" in answer_request["messages"][1]["content"]
    store.db.close()


@pytest.mark.asyncio
async def test_fact_extraction_does_not_delay_spoken_answer(tmp_path):
    store = Store(tmp_path / "fast.sqlite3")
    release = asyncio.Event()

    async def provider(request):
        if json.loads(request.content).get("response_format"):
            await release.wait()
            content = '{"facts": []}'
        else:
            content = "Готовый ответ"
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        assert await asyncio.wait_for(assistant.reply("owner", "Я люблю чай"), 0.5) == "Готовый ответ"
        release.set()
        await asyncio.gather(*assistant.learning_tasks)
    store.db.close()


@pytest.mark.asyncio
async def test_alice_can_save_review_and_delete_memory(tmp_path):
    store = Store(tmp_path / "alice.sqlite3")
    store.redeem_code("alice-user", store.create_link_code("owner"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500))) as upstream:
        assistant = Assistant(settings(), store, upstream)
        app = create_app(settings(), store, assistant)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            async def send(command, number):
                payload = {"version": "1.0", "session": {"skill_id": "skill", "session_id": "s", "message_id": number, "user": {"user_id": "alice-user"}}, "request": {"command": command}}
                return (await client.post("/alice/secret", json=payload)).json()["response"]["text"]

            assert await send("запомни я люблю джаз", 1) == "Запомнила."
            assert "люблю джаз" in await send("что ты помнишь", 2)
            assert "Удалено фактов: 1" in await send("забудь всё", 3)
            assert store.memories("owner") == []
            assert store.usage("owner") == 0
    store.db.close()


def test_memory_rejects_keys_and_is_isolated_by_user(tmp_path):
    store = Store(tmp_path / "private.sqlite3")
    with pytest.raises(ValueError):
        store.remember("owner", "Мой пароль от сервера: abc")
    store.remember("owner", "Я люблю чай")
    assert store.memories("other") == []
    assert store.forget("other") == 0
    assert len(store.memories("owner")) == 1
    store.db.close()
