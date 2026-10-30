import asyncio
import json

import httpx
import pytest

from app.assistant import Assistant, UserError
from app.config import Settings
from app.main import create_app
from app.store import Store


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / 'test.sqlite3')
    yield value
    value.db.close()


def settings(**overrides):
    return Settings(_env_file=None, alisagpt_api_key='test-key',
                    alice_webhook_token='test-webhook-secret',
                    alice_skill_id='test-skill', **overrides)


def payload(command='', message_id=1, alice_id='alice-user'):
    session = {'skill_id': 'test-skill', 'session_id': 'session',
               'message_id': message_id, 'new': False}
    if alice_id is not None:
        session['user'] = {'user_id': alice_id}
    return {'version': '1.0', 'session': session,
            'request': {'command': command}}


def linked(store):
    assert store.redeem_code('alice-user', store.create_link_code('telegram-owner'))


@pytest.mark.asyncio
async def test_alice_auth_validation_and_anonymous_user(store):
    assistant = Assistant(settings(), store)
    app = create_app(settings(), store, assistant)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        assert (await client.post('/alice/wrong', json=payload())).status_code == 404
        invalid = payload()
        invalid['session']['skill_id'] = 'another-skill'
        assert (await client.post('/alice/test-webhook-secret', json=invalid)).status_code == 400
        response = await client.post('/alice/test-webhook-secret', json=payload(alice_id=None))
        assert response.status_code == 200
        assert 'аккаунт' in response.json()['response']['text']
        assert store.usage('telegram-owner') == 0
    await assistant.client.aclose()


def test_link_codes_are_single_use_and_expire(store):
    code = store.create_link_code('owner')
    assert len(code) == 8 and code.isdigit()
    assert not store.redeem_code('alice-user', '00000000')
    assert store.redeem_code('alice-user', code)
    assert store.linked_user('alice-user') == 'owner'
    assert not store.redeem_code('other-alice', code)
    expired = store.create_link_code('another-owner')
    store.db.execute('UPDATE codes SET expires=0')
    store.db.commit()
    assert not store.redeem_code('other-alice', expired)


@pytest.mark.asyncio
async def test_alice_link_endpoint(store):
    code = store.create_link_code('telegram-owner')
    assistant = Assistant(settings(), store)
    app = create_app(settings(), store, assistant)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        result = await client.post('/alice/test-webhook-secret', json=payload(' '.join(code)))
        assert 'Привязка выполнена' in result.json()['response']['text']
        assert store.linked_user('alice-user') == 'telegram-owner'
    await assistant.client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['выход', 'верни алису', 'обычная Алиса'])
async def test_alice_quick_exit_returns_to_alice_without_openai(store, command):
    linked(store)
    calls = []

    def provider(request):
        calls.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ответ'}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        app = create_app(settings(), store, assistant)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            response = await client.post('/alice/test-webhook-secret', json=payload(command))

    assert response.status_code == 200
    assert response.json()['response'] == {'text': 'Возвращаюсь к Алисе.', 'end_session': True}
    assert calls == []
    assert store.history('telegram-owner') == []
    assert store.usage('telegram-owner') == 0


@pytest.mark.asyncio
async def test_shared_history_and_alice_deduplication(store):
    requests = []

    def provider(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {'content': f'Ответ {len(requests)}'}}]})

    linked(store)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        assert await assistant.reply('telegram-owner', 'Вопрос из Telegram') == 'Ответ 1'
        app = create_app(settings(), store, assistant)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            body = payload('Вопрос со Станции')
            first = await client.post('/alice/test-webhook-secret', json=body)
            duplicate = await client.post('/alice/test-webhook-secret', json=body)
            assert first.json() == duplicate.json()
            assert first.json()['response']['text'] == 'Ответ 2'
        assert len(requests) == 2
        assert requests[0]['model'] == 'gpt-6-luna'
        assert requests[0]['reasoning_effort'] == 'none'
        assert requests[1]['messages'][2:4] == [
            {'role': 'user', 'content': 'Вопрос из Telegram'},
            {'role': 'assistant', 'content': 'Ответ 1'}]
        assert store.history('telegram-owner')[-1]['content'] == 'Ответ 2'
        assert store.history('another-user') == []


@pytest.mark.asyncio
async def test_quota_prevents_provider_call(store):
    requests = []

    def provider(request):
        requests.append(request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Ответ'}}]})

    store.update_settings('owner', daily_limit=1)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        await assistant.reply('owner', 'Первый вопрос')
        with pytest.raises(UserError, match='лимит'):
            await assistant.reply('owner', 'Второй вопрос')
    assert len(requests) == 1
    assert store.usage('owner') == 1
    assert len(store.history('owner')) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('provider_response', [
    httpx.Response(429, json={'error': {'message': 'rate limited'}}),
    httpx.Response(200, json={'choices': []}),
    httpx.Response(200, json={'choices': [{'message': {'content': ''}}]}),
])
async def test_failed_provider_does_not_pollute_history(store, provider_response):
    def provider(request):
        return provider_response

    store.add_exchange('owner', 'Старый вопрос', 'Старый ответ')
    previous = store.history('owner')
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        with pytest.raises(UserError, match='временно недоступен'):
            await assistant.reply('owner', 'Новый вопрос')
    assert store.history('owner') == previous


@pytest.mark.asyncio
async def test_alice_slow_answer_is_retrieved_without_second_generation(store):
    release = asyncio.Event()
    calls = []

    async def provider(request):
        calls.append(request)
        await release.wait()
        return httpx.Response(200, json={'choices': [{'message': {'content': 'Готовый ответ'}}]})

    linked(store)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as upstream:
        assistant = Assistant(settings(), store, upstream)
        app = create_app(settings(), store, assistant)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            first = await client.post('/alice/test-webhook-secret', json=payload('Длинный вопрос'))
            assert 'готовится' in first.json()['response']['text']
            release.set()
            await asyncio.sleep(0.01)
            result = await client.post('/alice/test-webhook-secret', json=payload('продолжи', message_id=2))
            assert result.json()['response']['text'] == 'Готовый ответ'
            assert len(calls) == 1
            assert len(store.history('telegram-owner')) == 2

