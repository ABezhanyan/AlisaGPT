import re


RECIPE_REQUEST = re.compile(r"(?i)(рецепт|как приготовить|как сварить|как испечь|как запечь|как пожарить|ингредиент[ыов]* для)")


def is_recipe_request(text):
    return bool(RECIPE_REQUEST.search(text))


def recipe_title(text):
    """Short, stable label for a spoken recipe request."""
    match = RECIPE_REQUEST.search(text)
    title = text[match.end():] if match else text
    title = re.sub(r"(?i)^\s*(?:мне|пожалуйста|для|как|приготовить|сделать|сварить|испечь|запечь|пожарить)\b", "", title).strip()
    title = re.split(r"(?i)\b(?:и расскажи|пошагово|с подробностями|на сегодня)\b", title, maxsplit=1)[0]
    title = title.strip(" .,!?:;—-«»\"' ")[:70].strip()
    return (title[:1].upper() + title[1:]) if title else "Без названия"

