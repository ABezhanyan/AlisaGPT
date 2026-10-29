import asyncio
import httpx

class UserError(Exception):
    pass

class Assistant:
    def __init__(self, settings, store, client=None):
        self.settings, self.store = settings, store
        self.client = client or httpx.AsyncClient(timeout=25, trust_env=True)
        self.locks = {}

    async def reply(self, user_id, text):
        if not self.settings.alisagpt_api_key:
            raise UserError("Администратор ещё не подключил OpenAI API.")
        if len(text) > 4000:
            raise UserError("Сократите сообщение до 4000 символов.")
        async with self.locks.setdefault(user_id, asyncio.Lock()):
            profile = self.store.settings(user_id)
            if not self.store.reserve_request(user_id, profile["daily_limit"]):
                raise UserError("Дневной лимит запросов исчерпан.")
            try:
                response = await self.client.post("https://api.openai.com/v1/chat/completions", headers={"Authorization": f"Bearer {self.settings.alisagpt_api_key}"}, json={"model":profile["model"],"messages":[{"role":"system","content":profile["prompt"]},*self.store.history(user_id),{"role":"user","content":text}],"max_completion_tokens":300})
                response.raise_for_status()
                answer = response.json()["choices"][0]["message"]["content"]
                if not isinstance(answer, str) or not answer.strip(): raise ValueError("Empty answer")
            except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
                raise UserError("Сервис ответа временно недоступен. Попробуйте позже.") from exc
            self.store.add_exchange(user_id, text, answer)
            return answer
