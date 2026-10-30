import asyncio
import json

import httpx
import pytest

from app.assistant import Assistant
from app.config import Settings
from app.store import Store
from app.recipes import recipe_title


def test_recipe_title_removes_full_spoken_prompt():
    assert recipe_title("Дай рецепт овсяной каши") == "Овсяной каши"
    assert recipe_title("Как приготовить борщ?") == "Борщ"


@pytest.mark.asyncio
async def test_station_recipe_is_saved_and_copied_to_owner_telegram(tmp_path):
    store = Store(tmp_path / "recipes.sqlite3")
    sent = []

    def provider(request):
        if request.url.host == "api.telegram.org":
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ингредиенты: картофель. Приготовление: сварите."}}]})

    settings = Settings(_env_file=None, alisagpt_api_key="key", telegram_bot_token="test-token", telegram_owner_id=42)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings, store, upstream)
        answer = await assistant.reply("42", "Дай рецепт картофельного супа", channel="voice")
        assert answer.startswith("Ингредиенты:")
        assert len(store.recipes("42")) == 1
        await asyncio.gather(*assistant.delivery_tasks)
    assert sent[0]["chat_id"] == "42"
    assert "Сохранён рецепт" in sent[0]["text"]
    assert "картофель" in sent[0]["text"]
    assert store.recipe("other", 1) is None
    store.db.close()


@pytest.mark.asyncio
async def test_station_recipe_is_sent_to_invited_guest_too(tmp_path):
    store = Store(tmp_path / "recipes-shared.sqlite3")
    code = store.create_telegram_invite("42")
    assert store.redeem_telegram_invite("99", code, "42")
    recipients = []

    def provider(request):
        if request.url.host == "api.telegram.org":
            recipients.append(json.loads(request.content)["chat_id"])
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(200, json={"choices": [{"message": {"content": "Ингредиенты и шаги"}}]})

    settings = Settings(_env_file=None, alisagpt_api_key="key", telegram_bot_token="test-token", telegram_owner_id=42)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings, store, upstream)
        await assistant.reply("42", "Дай рецепт супа", channel="voice")
        await asyncio.gather(*assistant.delivery_tasks)
    assert recipients == ["42", "99"]
    store.db.close()


@pytest.mark.asyncio
async def test_telegram_recipe_answer_is_not_duplicated(tmp_path):
    store = Store(tmp_path / "recipes.sqlite3")
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "Рецепт"}}]})

    settings = Settings(_env_file=None, alisagpt_api_key="key", telegram_bot_token="test-token", telegram_owner_id=42)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings, store, upstream)
        assert await assistant.reply("42", "Рецепт супа", channel="telegram") == "Рецепт"
    assert len(requests) == 1
    assert store.recipes("42") == []
    store.db.close()

