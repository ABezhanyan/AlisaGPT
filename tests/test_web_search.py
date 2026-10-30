import json
import asyncio

import httpx
import pytest

from app.assistant import Assistant, UserError
from app.config import Settings
from app.store import Store
from app.web_search import clean_voice_answer


def settings(**kwargs):
    return Settings(_env_file=None, alisagpt_api_key="test-key", **kwargs)


def provider(request):
    if request.url.path == "/v1/responses":
        return httpx.Response(200, json={
            "status": "completed",
            "output": [
                {"type": "web_search_call", "action": {"type": "search"}},
                {"type": "message", "content": [{"type": "output_text", "text": "В городе солнечно. citeabc", "annotations": [{"type": "url_citation", "url": "https://weather.example/forecast"}]}]},
            ],
        })
    return httpx.Response(200, json={"choices": [{"message": {"content": "Обычный ответ"}}]})


def test_voice_answer_does_not_read_source_names_or_links():
    text = "По данным BBC, сегодня тепло. Источники: https://bbc.com/news и www.example.org"
    assert clean_voice_answer(text) == "сегодня тепло."


@pytest.mark.asyncio
async def test_news_is_prepared_in_background_and_reused_on_station(tmp_path):
    store = Store(tmp_path / "cached-news.sqlite3")
    requests = []

    def record(request):
        requests.append(request)
        return provider(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as upstream:
        assistant = Assistant(settings(telegram_owner_id=42), store, upstream)
        await assistant.start()
        await asyncio.gather(*assistant.refresh_tasks)
        assert len(requests) == 1
        assert json.loads(requests[0].content)["max_tool_calls"] == 1
        assert await assistant.reply("42", "Какие новости сегодня?") == "В городе солнечно."
        assert len(requests) == 1
        assert store.web_usage("42") == 1
    store.db.close()


@pytest.mark.asyncio
async def test_weather_city_is_asked_then_remembered_and_web_search_is_limited(tmp_path):
    store = Store(tmp_path / "weather.sqlite3")
    requests = []

    def record(request):
        requests.append(request)
        return provider(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as upstream:
        assistant = Assistant(settings(web_search_daily_limit=1), store, upstream)
        assert "Для какого города" in await assistant.reply("owner", "Какая погода?")
        assert requests == []
        assert store.usage("owner") == 0
        assert await assistant.reply("owner", "Москва") == "В городе солнечно."
        assert store.weather_city("owner") == "Москва"
        assert store.web_usage("owner") == 1
        assert requests[0].url.path == "/v1/responses"
        body = json.loads(requests[0].content)
        assert body["tools"] == [{"type": "web_search", "search_context_size": "low"}]
        assert body["max_tool_calls"] == 1
        with pytest.raises(UserError, match="лимит поиска"):
            await assistant.reply("owner", "Какая погода сегодня?")
        assert len(requests) == 1
    store.db.close()


@pytest.mark.asyncio
async def test_telegram_gets_clickable_source_and_normal_questions_do_not_search(tmp_path):
    store = Store(tmp_path / "news.sqlite3")
    requests = []

    def record(request):
        requests.append(request)
        return provider(request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        assert await assistant.reply("owner", "Сколько будет два плюс два?", channel="telegram") == "Обычный ответ"
        answer = await assistant.reply("owner", "Какие новости сегодня?", channel="telegram")
        assert "https://weather.example/forecast" in answer
        assert "cite" not in answer
        assert [request.url.path for request in requests] == ["/v1/chat/completions", "/v1/responses"]
    store.db.close()


@pytest.mark.asyncio
async def test_web_search_must_have_actual_search_call(tmp_path):
    store = Store(tmp_path / "fail.sqlite3")
    def no_search(request):
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "Вчера было солнечно", "annotations": []}]}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_search)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        with pytest.raises(UserError, match="временно недоступен"):
            await assistant.reply("owner", "Какие новости сейчас?")
    store.db.close()
