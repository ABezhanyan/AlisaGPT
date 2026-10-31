import asyncio
import sys
from .config import Settings
from .store import Store
from .assistant import Assistant
from .telegram import run_bot

async def bot():
    settings = Settings()
    store = Store(settings.database_path, settings.default_model)
    assistant = Assistant(settings, store)
    try:
        await run_bot(settings, store, assistant)
    finally:
        await assistant.close()
        store.db.close()

if __name__ == '__main__':
    if sys.argv[1:] != ['bot']:
        raise SystemExit('Usage: python -m app bot')
    asyncio.run(bot())
