"""Private Telegram chat with owner-controlled access to shared assistant data."""

import logging
import os
import ssl

from aiogram import Bot, Dispatcher, F
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import Command
from aiogram.filters.command import CommandObject
from aiogram.types import Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, BufferedInputFile

from app.assistant import UserError
from app.memory_commands import handle_memory_command
from app.languages import CONFIRMATIONS, LANGUAGE_NAMES, parse_language


logger = logging.getLogger(__name__)


def buttons(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=action) for label, action in row]
        for row in rows
    ])


MENU = buttons(
    [("📖 Рецепты", "recipes"), ("🧠 Память", "memories")],
    [("📊 Расход", "usage"), ("🌐 Язык", "language")],
    [("⚙️ Команды", "help")],
)
OWNER_MENU = buttons(
    [("📖 Рецепты", "recipes"), ("🧠 Память", "memories")],
    [("📊 Расход", "usage"), ("🌐 Язык", "language")],
    [("🔗 Привязка", "link")],
    [("👥 Доступ", "access"), ("⚙️ Команды", "help")],
)
LANGUAGE_MENU = buttons([("🇷🇺 Русский", "lang:ru"), ("🇬🇧 English", "lang:en"), ("🇦🇲 Հայերեն", "lang:hy")])


def bot_session():
    session = AiohttpSession(
        proxy=os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    )
    # aiogram has no public TLS-context option; preserve the platform's CA bundle
    # through this compatibility hook after proxy connector initialization.
    session._connector_init["ssl"] = ssl.create_default_context(
        cafile=os.environ.get("SSL_CERT_FILE") or None
    )
    return session

HELP = """Я — ваш помощник и панель управления навыком Алисы.

/link — получить одноразовый код привязки Станции
/unlink — отвязать Алису и аннулировать коды привязки
/model <модель> — выбрать модель
/language <ru|en|hy> — переключить язык ответов (общий для Станции и Telegram)
/prompt <инструкция> — задать стиль ответов
/limit <число> — установить дневной лимит запросов
/usage — настройки и расход запросов, поиска и озвучивания за сегодня
/reset — очистить общую историю диалога
/remember <факт> — сохранить факт надолго
/memories — показать постоянную память
/forget <номер|all> — удалить факт или всю память
/recipes — список рецептов, сохранённых со Станции
/recipe <номер> — открыть сохранённый рецепт
/help — показать команды

Только владелец: /invite — одноразовая ссылка на сутки, /access — список гостей, /revoke <Telegram ID> — отозвать доступ. История, память и рецепты общие для всех приглашённых.

Или просто напишите сообщение: история общая с привязанной Алисой."""


async def run_bot(settings, store, assistant):
    """Run long polling; do not call until Telegram credentials are configured."""
    if not settings.telegram_bot_token or settings.telegram_owner_id <= 0:
        raise ValueError("Configure TELEGRAM_BOT_TOKEN and TELEGRAM_OWNER_ID first")

    dispatcher = Dispatcher()

    def owner(message: Message) -> str | None:
        if (
            message.chat.type == "private"
            and message.from_user
            and message.from_user.id == settings.telegram_owner_id
        ):
            return str(message.from_user.id)
        return None

    owner_id = str(settings.telegram_owner_id)

    def authorized(message: Message) -> str | None:
        if not message.chat or message.chat.type != "private" or not message.from_user:
            return None
        if owner(message) or store.has_telegram_access(str(message.from_user.id), owner_id):
            return owner_id
        return None

    def menu_for(message):
        return OWNER_MENU if owner(message) else MENU

    async def send(message: Message, text: str, reply_markup=None):
        # Telegram allows 4096 characters per plain-text message.
        for offset in range(0, len(text), 4000):
            await message.answer(text[offset : offset + 4000], parse_mode=None, reply_markup=reply_markup if offset == 0 else None)

    async def show_recipes(target, user_id):
        items = store.recipes(user_id)
        if not items:
            await target.answer("Сохранённых рецептов пока нет.", reply_markup=menu_for(target))
            return
        markup = buttons(*[[(item["request"][:45], f'recipe:{item["id"]}')] for item in items], [("◀️ Меню", "menu")])
        await target.answer("Выберите рецепт:", reply_markup=markup)

    async def show_recipe(target, user_id, recipe_id):
        item = store.recipe(user_id, recipe_id)
        if not item:
            await target.answer("Рецепт не найден.", reply_markup=menu_for(target))
            return
        await send(target, f'{item["request"]}\n\n{item["answer"]}')

    @dispatcher.message(Command("start", "help"))
    async def help_command(message: Message, command: CommandObject | None = None):
        if message.chat.type != "private" or not message.from_user:
            return
        code = (command.args or "").strip() if command else ""
        if code and not authorized(message):
            if store.redeem_telegram_invite(str(message.from_user.id), code, owner_id):
                await message.answer("Доступ открыт. Вы пользуетесь общей историей, памятью и рецептами владельца.", reply_markup=MENU)
                return
        if authorized(message):
            await message.answer(HELP, reply_markup=menu_for(message))
        else:
            await message.answer("Доступ по приглашению владельца. Попросите у него личную ссылку.")

    @dispatcher.message(Command("invite"))
    async def invite_command(message: Message):
        if not owner(message):
            return
        code = store.create_telegram_invite(owner_id)
        bot_info = await message.bot.get_me()
        await message.answer(f"Отправьте человеку эту одноразовую ссылку (действует 24 часа):\nhttps://t.me/{bot_info.username}?start={code}\nДоступ можно отозвать командой /revoke <Telegram ID>.")

    @dispatcher.message(Command("access"))
    async def access_command(message: Message):
        if not owner(message):
            return
        guests = store.telegram_guests(owner_id)
        await message.answer("Доступ имеют: " + (", ".join(guests) if guests else "только вы") + "\nНовая ссылка: /invite\nОтозвать: /revoke <Telegram ID>")

    @dispatcher.message(Command("revoke"))
    async def revoke_command(message: Message, command: CommandObject):
        if not owner(message):
            return
        guest_id = (command.args or "").strip()
        if not guest_id.isdigit():
            await message.answer("Используйте /revoke <Telegram ID> из списка /access.")
            return
        await message.answer("Доступ отозван." if store.revoke_telegram_access(owner_id, guest_id) else "Такого гостя нет.")

    @dispatcher.message(Command("link"))
    async def link_command(message: Message):
        if user_id := owner(message):
            code = store.create_link_code(user_id)
            await message.answer(
                f"Скажите Алисе в навыке: «Привязать {code}». "
                "Код одноразовый и действует ограниченное время. "
                "Не передавайте его другим людям."
            )

    @dispatcher.message(Command("model"))
    async def model_command(message: Message, command: CommandObject):
        if not (user_id := owner(message)):
            return
        model = (command.args or "").strip()
        if model not in settings.allowed_models:
            await message.answer(
                "Используйте /model <модель>. Доступны: "
                + ", ".join(settings.allowed_models)
            )
            return
        store.update_settings(user_id, model=model)
        await message.answer(f"Модель: {model}")

    @dispatcher.message(Command("unlink"))
    async def unlink_command(message: Message):
        if user_id := owner(message):
            store.unlink(user_id)
            await message.answer("Алиса отвязана. Все коды привязки аннулированы.")

    @dispatcher.message(Command("prompt"))
    async def prompt_command(message: Message, command: CommandObject):
        if not (user_id := owner(message)):
            return
        prompt = (command.args or "").strip()
        if not prompt or len(prompt) > 4000:
            await message.answer("Используйте /prompt <инструкция до 4000 символов>.")
            return
        store.update_settings(user_id, prompt=prompt)
        await message.answer("Инструкция для помощника сохранена.")

    @dispatcher.message(Command("limit"))
    async def limit_command(message: Message, command: CommandObject):
        if not (user_id := owner(message)):
            return
        try:
            daily_limit = int((command.args or "").strip())
            if not 1 <= daily_limit <= 10000:
                raise ValueError
        except ValueError:
            await message.answer("Используйте /limit <целое число от 1 до 10000>.")
            return
        store.update_settings(user_id, daily_limit=daily_limit)
        await message.answer(f"Дневной лимит: {daily_limit} запросов.")

    @dispatcher.message(Command("usage"))
    async def usage_command(message: Message):
        if user_id := authorized(message):
            current = store.settings(user_id)
            await message.answer(
                f"Модель: {current['model']}\n"
                f"Язык ответов: {LANGUAGE_NAMES[current.get('language', 'ru')]}\n"
                f"Запросов сегодня (UTC): {store.usage(user_id)}\n"
                f"Поисков в интернете сегодня (UTC): {store.web_usage(user_id)}/{settings.web_search_daily_limit}\n"
                f"Озвучиваний сегодня (UTC): {store.voice_usage(user_id)}/{settings.voice_daily_limit}\n"
                f"Дневной лимит: {current['daily_limit']}\n"
                "Это счётчик запросов, не сумма списаний OpenAI."
            )

    @dispatcher.message(Command("language"))
    async def language_command(message: Message, command: CommandObject):
        if not (user_id := authorized(message)):
            return
        choice = parse_language(command.args or "")
        if not choice:
            await message.answer("Выберите язык ответов. Выбор общий для Станции и Telegram:", reply_markup=LANGUAGE_MENU)
            return
        store.update_settings(user_id, language=choice)
        await message.answer(CONFIRMATIONS[choice], reply_markup=menu_for(message))

    @dispatcher.message(Command("reset"))
    async def reset_command(message: Message):
        if user_id := owner(message):
            store.clear_history(user_id)
            await message.answer("Общая история Алисы и Telegram очищена.")

    @dispatcher.message(Command("remember"))
    async def remember_command(message: Message, command: CommandObject):
        if user_id := authorized(message):
            await message.answer(handle_memory_command(store, user_id, "запомни " + (command.args or "")))

    @dispatcher.message(Command("memories"))
    async def memories_command(message: Message):
        if user_id := authorized(message):
            await send(message, handle_memory_command(store, user_id, "что ты помнишь", max_items=1000))

    @dispatcher.message(Command("forget"))
    async def forget_command(message: Message, command: CommandObject):
        if user_id := owner(message):
            arg = (command.args or "").strip().lower()
            if arg == "all":
                arg = "всё"
            await message.answer(handle_memory_command(store, user_id, "забудь " + arg) or "Используйте /forget <номер|all>.")

    @dispatcher.message(Command("recipes"))
    async def recipes_command(message: Message):
        if user_id := authorized(message):
            await show_recipes(message, user_id)

    @dispatcher.message(Command("recipe"))
    async def recipe_command(message: Message, command: CommandObject):
        if not (user_id := authorized(message)):
            return
        try:
            recipe_id = int((command.args or "").strip())
        except ValueError:
            await message.answer("Используйте /recipe <номер>.")
            return
        await show_recipe(message, user_id, recipe_id)

    @dispatcher.callback_query(F.data)
    async def menu_callback(callback: CallbackQuery):
        message = callback.message
        if not message or message.chat.type != "private" or not callback.from_user or not (callback.from_user.id == settings.telegram_owner_id or store.has_telegram_access(str(callback.from_user.id), owner_id)):
            await callback.answer("Недоступно")
            return
        user_id = owner_id
        await callback.answer()
        action = callback.data or ""
        if action == "recipes":
            await show_recipes(message, user_id)
        elif action.startswith("recipe:") and action[7:].isdigit():
            await show_recipe(message, user_id, int(action[7:]))
        elif action == "memories":
            await send(message, handle_memory_command(store, user_id, "что ты помнишь", max_items=1000))
        elif action == "usage":
            current = store.settings(user_id)
            await message.answer(f"Модель: {current['model']}\nЯзык ответов: {LANGUAGE_NAMES[current.get('language', 'ru')]}\nЗапросов сегодня (UTC): {store.usage(user_id)}\nПоисков сегодня (UTC): {store.web_usage(user_id)}/{settings.web_search_daily_limit}\nОзвучиваний сегодня (UTC): {store.voice_usage(user_id)}/{settings.voice_daily_limit}\nДневной лимит: {current['daily_limit']}", reply_markup=MENU)
        elif action.startswith("voice:") and action[6:].isdigit():
            answer = store.answer_for_voice(user_id, int(action[6:]))
            if not answer:
                await message.answer("Этот ответ уже удалён из недавней истории.")
                return
            try:
                audio = await assistant.synthesize_voice(user_id, answer)
                await message.answer_voice(BufferedInputFile(audio, filename="alisagpt.ogg"), caption="Голос сгенерирован ИИ.")
            except UserError as exc:
                await message.answer(str(exc))
            except Exception:
                logger.error("Voice delivery failed")
                await message.answer("Не удалось отправить голосовое сообщение. Попробуйте позже.")
        elif action == "language":
            await message.answer("Выберите язык ответов. Выбор общий для Станции и Telegram:", reply_markup=LANGUAGE_MENU)
        elif action.startswith("lang:") and action[5:] in LANGUAGE_NAMES:
            choice = action[5:]
            store.update_settings(user_id, language=choice)
            await message.answer(CONFIRMATIONS[choice], reply_markup=OWNER_MENU if callback.from_user.id == settings.telegram_owner_id else MENU)
        elif action == "link":
            if callback.from_user.id != settings.telegram_owner_id:
                await message.answer("Привязкой Станции управляет владелец.")
                return
            code = store.create_link_code(user_id)
            await message.answer(f"Скажите Алисе в навыке: «Привязать {code}». Код одноразовый и действует 5 минут.")
        elif action == "access" and callback.from_user.id == settings.telegram_owner_id:
            guests = store.telegram_guests(owner_id)
            await message.answer("Доступ имеют: " + (", ".join(guests) if guests else "только вы") + "\nНовая ссылка: /invite\nОтозвать: /revoke <Telegram ID>")
        elif action == "help":
            await message.answer(HELP, reply_markup=OWNER_MENU if callback.from_user.id == settings.telegram_owner_id else MENU)
        elif action == "menu":
            await message.answer("Главное меню:", reply_markup=OWNER_MENU if callback.from_user.id == settings.telegram_owner_id else MENU)

    @dispatcher.message(F.text)
    async def chat(message: Message):
        if not (user_id := authorized(message)):
            return
        text = message.text or ""
        if text.startswith("/"):
            await message.answer("Неизвестная команда. Список команд: /help")
            return
        if len(text) > 4000:
            await message.answer("Сократите сообщение до 4000 символов.")
            return
        if (memory_reply := handle_memory_command(store, user_id, text)) is not None:
            await send(message, memory_reply)
            return
        try:
            response = await assistant.reply(user_id, text, channel="telegram")
        except UserError as exc:
            await send(message, str(exc))
            return
        except Exception:
            # Never send upstream exception details or credentials to Telegram.
            logger.error("Assistant request failed")
            await message.answer("Не удалось получить ответ. Попробуйте чуть позже.")
            return
        spoken_answer = response.split("\nИсточники: ", 1)[0]
        answer_id = store.answer_id(user_id, spoken_answer)
        markup = buttons([("🔊 Озвучить", f"voice:{answer_id}")]) if answer_id else None
        await send(message, response or "Помощник вернул пустой ответ.", reply_markup=markup)

    async with Bot(settings.telegram_bot_token, session=bot_session()) as bot:
        await bot.set_my_commands([BotCommand(command=name, description=label) for name, label in [
            ("start", "Открыть меню"), ("recipes", "Сохранённые рецепты"),
            ("memories", "Постоянная память"), ("usage", "Расход и модель"),
            ("link", "Привязать Станцию"), ("help", "Все команды"),
            ("invite", "Пригласить человека"), ("access", "Кому открыт доступ"),
            ("language", "Язык ответов"),
        ]])
        await dispatcher.start_polling(bot, allowed_updates=["message", "callback_query"])

