import re


def handle_memory_command(store, user_id, text, max_items=20):
    command = text.strip()
    lower = command.lower()
    if lower.startswith("запомни") and (len(command) == 7 or command[7].isspace()):
        fact = command[7:].strip()
        if not fact:
            return "Скажите: запомни, затем факт."
        try:
            store.remember(user_id, fact)
        except ValueError:
            return "Не могу сохранить это. Назовите короткий факт без паролей и ключей."
        return "Запомнила."
    if lower in {"что ты помнишь", "что ты обо мне помнишь", "моя память", "память"}:
        memories = store.memories(user_id)[:max_items]
        if not memories:
            return "Постоянная память пока пуста. Скажите «запомни» и нужный факт."
        return "Я помню:\n" + "\n".join(f'{item["id"]}. {item["fact"]}' for item in memories)
    if lower in {"забудь всё", "забудь все", "очисти память"}:
        count = store.forget(user_id)
        return f"Постоянная память очищена. Удалено фактов: {count}."
    match = re.fullmatch(r"забудь(?: факт)?(?: номер)?\s+(\d+)", lower)
    if match:
        return "Факт удалён." if store.forget(user_id, int(match.group(1))) else "Такого факта в памяти нет."
    return None
