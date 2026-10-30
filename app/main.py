import asyncio
import hmac
import re
import time
from collections import OrderedDict, deque
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from .assistant import Assistant, UserError
from .config import Settings
from .store import Store
from .memory_commands import handle_memory_command


def create_app(settings=None, store=None, assistant=None):
    settings = settings or Settings()
    store = store or Store(settings.database_path, settings.default_model, settings.daily_limit)
    assistant = assistant or Assistant(settings, store)
    pending, cache, attempts = {}, OrderedDict(), deque()

    @asynccontextmanager
    async def lifespan(app):
        yield
        tasks = list(pending.values())
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await assistant.close()
        store.db.close()

    app = FastAPI(lifespan=lifespan)

    @app.get('/healthz')
    async def health():
        return {"status":"ok", "openai_configured":bool(settings.alisagpt_api_key), "alice_configured":bool(settings.alice_skill_id and settings.alice_webhook_token)}

    def reply(text, end_session=False):
        return {"version":"1.0", "response":{"text":text[:1024], "end_session":end_session}}

    async def take_result(user_id, wait):
        task = pending[user_id]
        done, _ = await asyncio.wait([task], timeout=wait)
        if not done: return reply('Ответ готовится. Скажите «продолжи» через несколько секунд.')
        pending.pop(user_id, None)
        try: return reply(task.result())
        except UserError as exc: return reply(str(exc))
        except Exception: return reply('Не удалось подготовить ответ. Попробуйте позже.')

    @app.post('/alice/{token}')
    async def alice(token: str, request: Request):
        if not settings.alice_webhook_token or not hmac.compare_digest(token, settings.alice_webhook_token):
            raise HTTPException(404)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 65536: raise HTTPException(413)
        import json
        try:
            body = json.loads(raw)
            session = body['session']
            if body['version'] != '1.0' or session['skill_id'] != settings.alice_skill_id or not settings.alice_skill_id:
                raise ValueError()
            session_id, message_id = session['session_id'], session['message_id']
            command = body['request'].get('command', '')
            if not isinstance(command, str) or len(command)>4000 or not isinstance(session_id,str) or not isinstance(message_id,int): raise ValueError()
            alice_id = session.get('user', {}).get('user_id')
            if alice_id is not None and not isinstance(alice_id,str): raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise HTTPException(400, 'Invalid Alice request')
        key = (session_id, message_id)
        if key in cache: return cache[key]
        if not alice_id:
            return reply('Для личного помощника войдите в аккаунт Яндекса и повторите запрос.')
        command = command.strip()
        user_id = store.linked_user(alice_id)
        digits = re.sub(r'\D', '', command)
        if command.lower() in {'выход', 'выйти', 'вернись', 'хватит', 'обычная алиса', 'верни алису', 'закрой навык'}:
            if user_id in pending:
                pending.pop(user_id).cancel()
            result = reply('Возвращаюсь к Алисе.', end_session=True)
        elif not user_id and len(digits) == 8:
            now = time.monotonic()
            while attempts and attempts[0]<now-60: attempts.popleft()
            if len(attempts)>=5: return reply('Слишком много попыток. Подождите минуту.')
            attempts.append(now)
            if store.redeem_code(alice_id, digits):
                result = reply('Привязка выполнена. Теперь задайте вопрос.')
            else: result = reply('Код неверный или истёк. Получите новый код командой /link в Telegram.')
        elif not user_id:
            result = reply('Откройте своего Telegram-бота, отправьте /link и произнесите восьмизначный код цифрами.')
        elif (memory_reply := handle_memory_command(store, user_id, command, max_items=10)) is not None:
            result = reply(memory_reply)
        elif user_id in pending:
            result = await take_result(user_id, 0 if command.lower() in {'продолжи','ответ','готово'} else 3.5)
        elif not command or session.get('new') and command.lower() in {'продолжи','ответ'}:
            result = reply('Я слушаю. Задайте вопрос.')
        elif command.lower() in {'продолжи','ответ'}:
            result = reply('Ожидающего ответа нет. Задайте новый вопрос.')
        else:
            pending[user_id] = asyncio.create_task(assistant.reply(user_id, command))
            result = await take_result(user_id, 3.5)
        cache[key] = result
        if len(cache)>1000: cache.popitem(last=False)
        return result
    return app
