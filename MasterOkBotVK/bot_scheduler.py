import threading
import time
from datetime import datetime

from storage import (
    get_tracked, update_tracked_price, get_schedule, mark_run,
)


_stop_flag = threading.Event()


def start(context: dict):
    """Запускает фоновый поток планировщика.
    context — словарь с ключами:
      parser          — OzonParser
      on_price_drop   — callable(url, info, product, old_price, new_price)
      on_morning_top  — callable()
      on_daily_report — callable()
    """
    t = threading.Thread(target=_loop, args=(context,), daemon=True)
    t.start()
    print("[SCHEDULER] Запущен фоновый поток")


def stop():
    _stop_flag.set()


def _loop(ctx):
    last_price_check = 0
    while not _stop_flag.is_set():
        try:
            now = time.time()
            if now - last_price_check > 3600:
                last_price_check = now
                _check_prices(ctx)

            _check_schedule(ctx)
        except Exception as e:
            print(f"[SCHEDULER] ❌ {e}")

        time.sleep(60)


def _check_prices(ctx):
    tracked = get_tracked()
    if not tracked:
        return
    print(f"[SCHEDULER] Проверяю цены: {len(tracked)} товаров")
    parser = ctx["parser"]
    for url, info in list(tracked.items()):
        try:
            product = parser.parse_product(url)
            new_price = _parse_price(product.get("price"))
            old_price = info.get("price")

            if old_price and new_price and new_price < old_price:
                diff = old_price - new_price
                pct = diff / old_price * 100 if old_price else 0
                print(f"[SCHEDULER] 💰 Снижение: {info.get('name','')[:50]} "
                      f"{old_price} → {new_price} ({pct:.1f}%)")
                if pct >= 5 or diff >= 100:
                    if ctx.get("on_price_drop"):
                        ctx["on_price_drop"](url, info, product, old_price, new_price)
                update_tracked_price(url, new_price)
            else:
                if new_price:
                    update_tracked_price(url, new_price)
        except Exception as e:
            print(f"[SCHEDULER] ❌ Ошибка проверки {url[:60]}: {e}")


def _parse_price(s):
    if not s:
        return None
    digits = "".join(c for c in str(s) if c.isdigit())
    return int(digits) if digits else None


def _check_schedule(ctx):
    data = get_schedule()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    current_hm = now.strftime("%H:%M")

    for task, cfg in data.items():
        if not cfg.get("enabled"):
            continue
        if cfg.get("time") != current_hm:
            continue
        if cfg.get("last_run") == today:
            continue

        print(f"[SCHEDULER] ⏰ Запускаю задачу: {task}")
        try:
            if task == "morning_top" and ctx.get("on_morning_top"):
                ctx["on_morning_top"]()
            elif task == "daily_report" and ctx.get("on_daily_report"):
                ctx["on_daily_report"]()
        except Exception as e:
            print(f"[SCHEDULER] ❌ Ошибка {task}: {e}")
        mark_run(task)