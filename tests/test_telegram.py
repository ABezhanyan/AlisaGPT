from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app import telegram


@pytest.fixture
async def bot_handlers(monkeypatch):
    """Capture real handlers without polling Telegram or requiring credentials."""
    handlers = {}

    class Dispatcher:
        def message(self, *filters):
            def register(callback):
                handlers[callback.__name__] = callback
                return callback
            return register

        def callback_query(self, *filters):
            def register(callback):
                handlers[callback.__name__] = callback
                return callback
            return register

        async def start_polling(self, *args, **kwargs):
            pass

    class Bot:
        def __init__(self, token, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def set_my_commands(self, *args):
            pass

    monkeypatch.setattr(telegram, "Dispatcher", Dispatcher)
    monkeypatch.setattr(telegram, "Bot", Bot)
    monkeypatch.setattr(telegram, "bot_session", Mock())
    settings = SimpleNamespace(
        telegram_bot_token="unused", telegram_owner_id=42,
        allowed_models=("gpt-4.1-mini", "gpt-4.1"),
    )
    store = Mock()
    assistant = SimpleNamespace(reply=AsyncMock(return_value="Ответ"))
    await telegram.run_bot(settings, store, assistant)
    return handlers, settings, store, assistant


def message(user=42, chat_type="private", text="Привет"):
    return SimpleNamespace(
        from_user=SimpleNamespace(id=user),
        chat=SimpleNamespace(type=chat_type),
        text=text, answer=AsyncMock(),
    )


@pytest.mark.parametrize("user,chat_type", [(99, "private"), (42, "group")])
async def test_unauthorized_messages_cannot_read_or_change_anything(bot_handlers, user, chat_type):
    handlers, _, store, assistant = bot_handlers
    incoming = message(user, chat_type)
    for name, callback in handlers.items():
        if name == "menu_callback":
            continue
        if name in {"model_command", "prompt_command", "limit_command", "remember_command", "forget_command", "recipe_command"}:
            await callback(incoming, SimpleNamespace(args="gpt-4.1-mini"))
        else:
            await callback(incoming)
    assert store.mock_calls == []
    assistant.reply.assert_not_awaited()
    incoming.answer.assert_not_awaited()


@pytest.mark.parametrize("model", [None, "unknown-model", "gpt-4.1-mini extra"])
async def test_model_rejects_unlisted_models(bot_handlers, model):
    handlers, _, store, _ = bot_handlers
    incoming = message()
    await handlers["model_command"](incoming, SimpleNamespace(args=model))
    store.update_settings.assert_not_called()
    assert "gpt-4.1-mini" in incoming.answer.await_args.args[0]


async def test_model_saves_allowlisted_model_for_owner(bot_handlers):
    handlers, _, store, _ = bot_handlers
    incoming = message()
    await handlers["model_command"](incoming, SimpleNamespace(args=" gpt-4.1-mini "))
    store.update_settings.assert_called_once_with("42", model="gpt-4.1-mini")
    incoming.answer.assert_awaited_once()


async def test_unlink_revokes_owner_links(bot_handlers):
    handlers, _, store, _ = bot_handlers
    incoming = message()
    await handlers["unlink_command"](incoming)
    store.unlink.assert_called_once_with("42")
    incoming.answer.assert_awaited_once()


async def test_recipe_buttons_open_saved_recipe_for_owner(bot_handlers):
    handlers, _, store, _ = bot_handlers
    store.recipes.return_value = [{"id": 7, "request": "Овсяной каши"}]
    store.recipe.return_value = {"request": "Овсяной каши", "answer": "Ингредиенты и шаги"}
    incoming = message()
    await handlers["recipes_command"](incoming)
    assert incoming.answer.await_args.kwargs["reply_markup"].inline_keyboard[0][0].callback_data == "recipe:7"
    callback = SimpleNamespace(message=incoming, from_user=SimpleNamespace(id=42), data="recipe:7", answer=AsyncMock())
    await handlers["menu_callback"](callback)
    store.recipe.assert_called_once_with("42", 7)
    assert "Ингредиенты" in incoming.answer.await_args.args[0]


@pytest.mark.parametrize("owner,token", [(0, "unused"), (42, "")])
async def test_missing_configuration_never_starts_polling(owner, token):
    settings = SimpleNamespace(telegram_owner_id=owner, telegram_bot_token=token)
    with pytest.raises(ValueError, match="Configure"):
        await telegram.run_bot(settings, Mock(), Mock())


def test_bot_session_preserves_proxy_and_platform_ca(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:8080")
    monkeypatch.setenv("SSL_CERT_FILE", "/platform/ca.pem")
    connector = SimpleNamespace(_connector_init={})
    factory = Mock(return_value=connector)
    tls_context = object()
    tls_factory = Mock(return_value=tls_context)
    monkeypatch.setattr(telegram, "AiohttpSession", factory)
    monkeypatch.setattr(telegram.ssl, "create_default_context", tls_factory)
    assert telegram.bot_session() is connector
    factory.assert_called_once_with(proxy="http://proxy:8080")
    tls_factory.assert_called_once_with(cafile="/platform/ca.pem")
    assert connector._connector_init["ssl"] is tls_context

