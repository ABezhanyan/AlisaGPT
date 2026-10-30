"""Language selection shared by the Station skill and Telegram chat."""

import re

LANGUAGE_NAMES = {"ru": "русский", "en": "English", "hy": "հայերեն"}
LANGUAGE_INSTRUCTIONS = {
    "ru": "Отвечай на естественном русском языке.",
    "en": "Reply in fluent, natural English. Use English spelling and sentence structure; do not transliterate English into Cyrillic.",
    "hy": "Պատասխանիր բնական, գրագետ արևելահայերենով՝ հայերեն գրերով։ Մի՛ տառադարձիր հայերենը ռուսերեն կամ լատիներեն։",
}
CONFIRMATIONS = {
    "ru": "Хорошо, теперь буду отвечать по-русски.",
    "en": "Okay, I'll speak English from now on.",
    "hy": "Լավ, այսուհետ կպատասխանեմ հայերեն։",
}
ALIASES = {
    "ru": "ru", "русский": "ru", "русском": "ru", "русски": "ru", "russian": "ru", "ռուսերեն": "ru",
    "en": "en", "английский": "en", "английском": "en", "английски": "en", "english": "en", "անգլերեն": "en",
    "hy": "hy", "армянский": "hy", "армянском": "hy", "армянски": "hy", "armenian": "hy", "հայերեն": "hy",
}


def parse_language(value):
    return ALIASES.get(value.strip().lower())


def requested_language(text):
    phrase = re.sub(r"[.!?։]+$", "", text.strip().lower()).strip()
    phrase = re.sub(r"^(?:пожалуйста[,.]?\s+|please\s+)", "", phrase)
    patterns = (
        r"(?:теперь\s+)?(?:говори|отвечай|общайся|переключись|давай\s+говорить)(?:\s+со\s+мной)?\s+(?:на\s+|по[-\s]?)?(?P<language>[а-яё]+)(?:\s+языке)?",
        r"(?:please\s+)?(?:speak|reply|answer|switch\s+to)(?:\s+in)?\s+(?P<language>english|russian|armenian)",
        r"(?:խոսիր|պատասխանիր|անցիր)\s+(?P<language>հայերեն|անգլերեն|ռուսերեն)",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, phrase, re.IGNORECASE)
        if match and (language := parse_language(match.group("language"))):
            return language
    return None

