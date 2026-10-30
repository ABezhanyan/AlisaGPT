import re
from urllib.parse import urlparse


CURRENT_INFO = re.compile(
    r"(?i)(погод|новост|прогноз|курс(?: валют| доллара| евро)|котировк|"
    r"расписани|результат(?:ы| матча)|сч[её]т матч|"
    r"\bсегодня\b|\bсейчас\b|\bзавтра\b|"
    r"последн(?:ие|их) событи|свеж(?:ие|их) событи|"
    r"поищи|найди в интернете|проверь в интернете|актуальн|что нового|что произошло сегодня)"
)
WEATHER = re.compile(r"(?i)(погод|прогноз погоды|температур|осадк)")
CITY_IN_QUESTION = re.compile(r"(?i)\b(?:в|во|для)\s+(?!ближайш|следующ|каком|мо[её]м|городе|мир[еа])([а-яёa-z][а-яёa-z-]{1,})")
CITY_REPLY = re.compile(r"[А-Яа-яЁёA-Za-z\s-]{2,60}")


def needs_web_search(text):
    return bool(CURRENT_INFO.search(text))


def is_weather_question(text):
    return bool(WEATHER.search(text))


def is_general_news_question(text):
    return bool(re.fullmatch(r"(?i)\s*(?:(?:расскажи(?: мне)?|какие|что там с|дай|последние|свежие)?\s*(?:главные\s+)?новости(?:\s+(?:сегодня|за сегодня|мира|в мире))?|что нового(?:\s+в мире)?|что произошло сегодня)\s*[?.!]*\s*", text))


def clean_voice_answer(text):
    text = re.split(r"(?i)\b(?:источники|ссылки|sources)\s*[:—-]", text, maxsplit=1)[0]
    text = re.sub(r"(?i)https?://\S+|www\.\S+", "", text)
    text = re.sub(r"(?i)\b(?:[a-z0-9-]+\.)+(?:com|ru|org|net|co\.uk)(?:/\S*)?", "", text)
    text = re.sub(r"(?i)\b(?:источник|подробнее)\s*[:—-]\s*[^.!?]+[.!?]?", "", text)
    text = re.sub(r"(?i)\b(?:по данным|согласно)\s+[^,.!?]{3,90},\s*", "", text)
    return " ".join(text.split()).strip()


def has_explicit_city(text):
    return bool(CITY_IN_QUESTION.search(text))


def city_reply(text):
    candidate = re.sub(r"(?i)^(?:в|во|город|в городе)\s+", "", text.strip()).strip()
    if len(candidate.split()) > 3 or not CITY_REPLY.fullmatch(candidate):
        return None
    if candidate.lower() in {"сегодня", "завтра", "сейчас", "потом", "спасибо", "продолжи"}:
        return None
    return candidate


def parse_web_answer(data):
    if data.get("status") != "completed" or not any(item.get("type") == "web_search_call" for item in data.get("output", [])):
        raise ValueError("Web search was not completed")
    parts, sources = [], []
    for item in data["output"]:
        if item.get("type") == "web_search_call":
            for source in item.get("action", {}).get("sources", []):
                url = source.get("url", "") if isinstance(source, dict) else ""
                if urlparse(url).scheme in {"http", "https"} and url not in sources:
                    sources.append(url)
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") != "output_text":
                continue
            parts.append(content.get("text", ""))
            for annotation in content.get("annotations", []):
                if annotation.get("type") != "url_citation":
                    continue
                url = annotation.get("url", "")
                if urlparse(url).scheme in {"http", "https"} and url not in sources:
                    sources.append(url)
    answer = re.sub(r"cite[^]+|【[^】]+】", "", " ".join(parts)).strip()
    if not answer:
        raise ValueError("Empty web answer")
    varied, domains = [], set()
    for url in sources:
        domain = urlparse(url).netloc.removeprefix("www.")
        if domain not in domains:
            varied.append(url)
            domains.add(domain)
        if len(varied) == 5:
            break
    return answer, varied
