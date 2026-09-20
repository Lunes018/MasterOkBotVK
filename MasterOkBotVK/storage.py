import json
import os
import time
from datetime import datetime

STORAGE_DIR = "storage"
os.makedirs(STORAGE_DIR, exist_ok=True)

PRICES_FILE = os.path.join(STORAGE_DIR, "tracked_prices.json")
PUBLISHED_FILE = os.path.join(STORAGE_DIR, "published.json")
SCHEDULE_FILE = os.path.join(STORAGE_DIR, "schedule.json")
GIVEAWAYS_FILE = os.path.join(STORAGE_DIR, "giveaways.json")


def _load(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"[STORAGE] ⚠ {path}: {e}")
        return default


def _save(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[STORAGE] ❌ {path}: {e}")


# ---------- Отслеживание цен ----------

def get_tracked() -> dict:
    return _load(PRICES_FILE, {})


def add_tracked(url: str, price, name: str) -> bool:
    data = get_tracked()
    if url in data:
        return False
    data[url] = {
        "price": price,
        "name": name,
        "added": datetime.now().isoformat(timespec="seconds"),
        "last_check": datetime.now().isoformat(timespec="seconds"),
    }
    _save(PRICES_FILE, data)
    return True


def remove_tracked(url: str) -> bool:
    data = get_tracked()
    if url in data:
        del data[url]
        _save(PRICES_FILE, data)
        return True
    return False


def update_tracked_price(url: str, price):
    data = get_tracked()
    if url in data:
        data[url]["price"] = price
        data[url]["last_check"] = datetime.now().isoformat(timespec="seconds")
        _save(PRICES_FILE, data)


# ---------- История публикаций ----------

def is_published(url: str, days: int = 7) -> bool:
    data = _load(PUBLISHED_FILE, [])
    cutoff = time.time() - days * 86400
    for entry in data:
        if entry.get("url") == url and entry.get("ts", 0) > cutoff:
            return True
    return False


def mark_published(url: str, sku: str = None):
    data = _load(PUBLISHED_FILE, [])
    data.append({"url": url, "sku": sku, "ts": time.time()})
    if len(data) > 5000:
        data = data[-5000:]
    _save(PUBLISHED_FILE, data)


def clear_published():
    _save(PUBLISHED_FILE, [])


def published_stats() -> dict:
    data = _load(PUBLISHED_FILE, [])
    now = time.time()
    week = sum(1 for e in data if e.get("ts", 0) > now - 7 * 86400)
    month = sum(1 for e in data if e.get("ts", 0) > now - 30 * 86400)
    return {"total": len(data), "week": week, "month": month}


# ---------- Расписание ----------

_DEFAULT_SCHEDULE = {
    "morning_top": {"enabled": False, "time": "09:00", "last_run": None},
    "daily_report": {"enabled": False, "time": "20:00", "last_run": None},
}


def get_schedule() -> dict:
    data = _load(SCHEDULE_FILE, {})
    for k, v in _DEFAULT_SCHEDULE.items():
        data.setdefault(k, v.copy())
    return data


def save_schedule(data: dict):
    _save(SCHEDULE_FILE, data)


def set_schedule(task: str, enabled=None, time_str=None) -> dict:
    data = get_schedule()
    if task not in data:
        data[task] = {"enabled": False, "time": "09:00", "last_run": None}
    if enabled is not None:
        data[task]["enabled"] = bool(enabled)
    if time_str is not None:
        data[task]["time"] = time_str
    _save(SCHEDULE_FILE, data)
    return data[task]


def mark_run(task: str):
    data = get_schedule()
    if task in data:
        data[task]["last_run"] = datetime.now().strftime("%Y-%m-%d")
        _save(SCHEDULE_FILE, data)


# ---------- Розыгрыши ----------

def get_giveaways() -> dict:
    return _load(GIVEAWAYS_FILE, {})


def add_giveaway(post_id: int, url: str, title: str, end_date: str) -> dict:
    data = get_giveaways()
    key = str(post_id)
    data[key] = {
        "post_id": post_id,
        "url": url,
        "title": title,
        "end_date": end_date,
        "created": datetime.now().isoformat(timespec="seconds"),
        "finished": False,
        "winner_id": None,
    }
    _save(GIVEAWAYS_FILE, data)
    return data[key]


def update_giveaway(post_id: int, **kwargs):
    data = get_giveaways()
    key = str(post_id)
    if key in data:
        data[key].update(kwargs)
        _save(GIVEAWAYS_FILE, data)


def get_active_giveaways() -> list:
    data = get_giveaways()
    return [g for g in data.values() if not g.get("finished")]