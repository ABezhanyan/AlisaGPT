import re


RECIPE_REQUEST = re.compile(r"(?i)(рецепт|как приготовить|как сварить|как испечь|как запечь|как пожарить|ингредиент[ыов]* для)")


def is_recipe_request(text):
    return bool(RECIPE_REQUEST.search(text))
