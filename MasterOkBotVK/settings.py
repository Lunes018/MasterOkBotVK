import re
import json
import os

SETTINGS_FILE = "settings.json"

_DEFAULT = {
    "template": "detailed",   # minimal | detailed | discount
    "blacklist": ["б/у", "дефект", "брак", "поврежден", "царапин"],
    "hashtags_enabled": True,
}

_STOP_WORDS = {
    "для", "и", "с", "в", "на", "от", "по", "из", "к", "у", "о", "а", "но",
    "или", "не", "же", "бы", "то", "как", "так", "это", "все", "еще",
    "черный", "белый", "красный", "синий", "зеленый", "желтый", "серый",
    "новый", "новая", "новое", "универсальный", "универсальная",
    "купить", "цена", "товар",
}


def _load() -> dict:
    if not os.path.exists(SETTINGS_FILE):
        _save(_DEFAULT.copy())
        return _DEFAULT.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        for k, v in _DEFAULT.items():
            data.setdefault(k, v)
        return data
    except Exception as e:
        print(f"[SETTINGS] ⚠ Ошибка чтения {SETTINGS_FILE}: {e}")
        return _DEFAULT.copy()


def _save(data: dict):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[SETTINGS] ❌ Не удалось сохранить: {e}")


def get(key, default=None):
    return _load().get(key, default)


def set(key, value):
    data = _load()
    data[key] = value
    _save(data)


# ---------- Шаблоны ----------

def get_template() -> str:
    return get("template", "detailed")


def set_template(t: str):
    if t not in ("minimal", "detailed", "discount"):
        raise ValueError(f"Неизвестный шаблон: {t}")
    set("template", t)


# ---------- Стоп-слова ----------

def get_blacklist() -> list:
    return get("blacklist", []) or []


def add_blacklist(word: str) -> bool:
    word = word.strip().lower()
    if not word:
        return False
    data = _load()
    bl = data.get("blacklist", [])
    if word in bl:
        return False
    bl.append(word)
    data["blacklist"] = bl
    _save(data)
    return True


def remove_blacklist(word: str) -> bool:
    word = word.strip().lower()
    data = _load()
    bl = data.get("blacklist", [])
    if word not in bl:
        return False
    bl.remove(word)
    data["blacklist"] = bl
    _save(data)
    return True


def has_blacklist_word(text: str):
    """Возвращает первое найденное стоп-слово или None."""
    if not text:
        return None
    lower = text.lower()
    for w in get_blacklist():
        if w in lower:
            return w
    return None


# ---------- Хештеги ----------

def hashtags_enabled() -> bool:
    return bool(get("hashtags_enabled", True))


def set_hashtags_enabled(enabled: bool):
    set("hashtags_enabled", bool(enabled))


def generate_hashtags(title: str, extra: list = None) -> str:
    """Собирает хештеги из названия + extra. Возвращает строку '#tag1 #tag2'."""
    if not title:
        return ""

    tags = ["#ozon"]

    words = re.findall(r"[а-яёa-z]{4,}", title.lower())
    unique = []
    for w in words:
        if w in _STOP_WORDS:
            continue
        if w in unique:
            continue
        unique.append(w)
        if len(unique) >= 4:
            break

    for w in unique:
        tags.append(f"#{w}")

    if extra:
        for e in extra:
            e = e.strip().lower().lstrip("#")
            if e and f"#{e}" not in tags:
                tags.append(f"#{e}")

    return " ".join(tags[:6])