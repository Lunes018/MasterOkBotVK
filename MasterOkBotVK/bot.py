import os
import re
import sys
import time
import json
import random
from datetime import datetime
import vk_api
from vk_api.longpoll import VkLongPoll, VkEventType
from vk_api.keyboard import VkKeyboard, VkKeyboardColor

from config import VK_TOKEN, VK_GROUP_ID, ADMIN_ID
from ozon_parser import OzonParser
from ozon_seller import OzonSellerAPI, RU_MONTHS
from vk_publisher import VKPublisher
from ai_generator import generate_report_text
import notifier
import settings
import storage
import bot_scheduler

# Инициализация
vk_session = vk_api.VkApi(token=VK_TOKEN)
vk = vk_session.get_api()

notifier.install(vk, ADMIN_ID)
longpoll = VkLongPoll(vk_session)

parser = OzonParser()
seller_api = OzonSellerAPI()
publisher = VKPublisher()

OZON_URL_RE = re.compile(r"https?://(?:www\.)?ozon\.ru/product/[^\s]+")
MAX_PODBORKA = 5

AUTOPOSTING_ENABLED = True


# ---------- Утилиты ----------

def fmt_number(x) -> str:
    try:
        if isinstance(x, float):
            return f"{x:,.2f}".replace(",", " ")
        return f"{int(x):,}".replace(",", " ")
    except Exception:
        return str(x)


def fmt_delta(current: float, previous: float) -> str:
    if previous == 0:
        if current == 0:
            return "без изменений"
        return "↑ новый"
    delta = (current - previous) / previous * 100
    sign = "↑" if delta > 0 else ("↓" if delta < 0 else "=")
    return f"{sign} {delta:+.1f}%"


def _parse_price(s):
    if not s:
        return None
    digits = "".join(c for c in str(s) if c.isdigit())
    return int(digits) if digits else None


def build_post_text(product: dict, template: str, with_hashtags: bool) -> str:
    title = product["title"]
    price = product["price"]
    old_price = product.get("old_price")
    desc = product["description"]
    url = product["url"]

    if template == "minimal":
        lines = [f"🛍 {title}", "", f"💰 {price}"]
        if old_price:
            lines.append(f"❌ Было: {old_price}")
        lines += ["", f"🔗 {url}"]
    elif template == "discount":
        lines = [f"🔥 СКИДКА", "", f"🛍 {title}", "",
                 f"❌ Было: {old_price or '—'}", f"✅ Стало: {price}", "",
                 f"🔗 Купить: {url}"]
    else:
        lines = [f"🛍 {title}", "", f"💰 Цена: {price}"]
        if old_price:
            lines.append(f"❌ Было: {old_price}")
        lines += ["", f"📝 {desc}", "", f"🔗 Купить: {url}"]

    text = "\n".join(lines)
    if with_hashtags:
        tags = settings.generate_hashtags(title)
        if tags:
            text += "\n\n" + tags
    return text


# ---------- Публикация ----------

def handle_post_command(message: str, skip_dupe_check: bool = False) -> str:
    match = OZON_URL_RE.search(message)
    if not match:
        return "❌ Не найдена ссылка на товар Ozon."
    url = match.group(0)

    if not skip_dupe_check and storage.is_published(url, days=7):
        return ("🔁 Этот товар уже публиковался за последние 7 дней.\n"
                "Если нужно всё равно опубликовать: !пост! <url>")

    try:
        product = parser.parse_product(url)
    except Exception as e:
        print(f"[PARSER] ❌ Ошибка парсинга: {e}")
        return f"❌ Ошибка парсинга товара: {e}"

    blocked = settings.has_blacklist_word(
        product["title"] + " " + product.get("description", "")
    )
    if blocked:
        return f"🚫 В товаре найдено стоп-слово «{blocked}» — пост не опубликован"

    template = settings.get_template()
    text = build_post_text(product, template, settings.hashtags_enabled())

    try:
        time.sleep(2)
        post_id = publisher.publish_post(text, product["images"])
        storage.mark_published(url)
        return f"✅ Пост опубликован! ID: {post_id}"
    except Exception as e:
        print(f"[VK] ❌ Ошибка публикации: {e}")
        return f"❌ Ошибка публикации: {e}"


def handle_podborka_command(message: str) -> str:
    urls = OZON_URL_RE.findall(message)
    if not urls:
        return "❌ Не найдено ни одной ссылки на Ozon."
    if len(urls) > MAX_PODBORKA:
        urls = urls[:MAX_PODBORKA]

    products = []
    blocked_items = []
    dupe_items = []
    for url in urls:
        try:
            if storage.is_published(url, days=7):
                dupe_items.append(url)
                continue
            product = parser.parse_product(url)
            blocked = settings.has_blacklist_word(
                product["title"] + " " + product.get("description", "")
            )
            if blocked:
                blocked_items.append((product["title"], blocked))
                continue
            products.append(product)
        except Exception as e:
            print(f"[PODBORKA] ❌ {url[:80]}: {e}")

    if not products:
        parts = ["❌ Ни один товар не прошёл фильтры."]
        if blocked_items:
            parts.append(f"⛔ Отфильтровано: {len(blocked_items)}")
        if dupe_items:
            parts.append(f"🔁 Уже публиковались: {len(dupe_items)}")
        return "\n".join(parts)

    lines = [f"🛍 Подборка товаров ({len(products)} шт.)", ""]
    all_titles = []
    for i, p in enumerate(products, 1):
        all_titles.append(p["title"])
        lines.append(f"{i}. {p['title']}")
        price_line = f"   💰 {p['price']}"
        if p.get("old_price"):
            price_line += f"  ❌ {p['old_price']}"
        lines.append(price_line)
        lines.append(f"   🔗 {p['url']}")
        lines.append("")

    if settings.hashtags_enabled():
        all_tags = []
        for title in all_titles:
            tags = settings.generate_hashtags(title, ["подборка", "скидки"])
            if tags:
                all_tags.extend(tags.split())
        seen = set()
        uniq = [t for t in all_tags if not (t in seen or seen.add(t))]
        if uniq:
            lines.append(" ".join(uniq[:8]))

    text = "\n".join(lines)

    attachments = []
    for p in products:
        if p.get("images"):
            att = publisher.upload_photo(p["images"][0])
            if att:
                attachments.append(att)

    try:
        post_id = publisher.publish_post_with_attachments(text, attachments)
        for p in products:
            storage.mark_published(p["url"])
        result = f"✅ Подборка опубликована! ID: {post_id}"
        if blocked_items:
            result += f"\n⛔ Отфильтровано: {len(blocked_items)}"
        if dupe_items:
            result += f"\n🔁 Пропущено дублей: {len(dupe_items)}"
        return result
    except Exception as e:
        return f"❌ Ошибка публикации подборки: {e}"


def handle_report_command() -> str:
    try:
        stats = seller_api.get_weekly_analytics()
    except Exception as e:
        return f"❌ Ошибка аналитики: {e}"
    report_text = generate_report_text(stats)
    try:
        post_id = publisher.publish_post(report_text)
        return f"✅ Отчёт опубликован! ID: {post_id}"
    except Exception as e:
        return f"❌ Ошибка публикации отчёта: {e}"


def handle_top_command(peer_id: int, scheduled: bool = False) -> str:
    try:
        top = seller_api.get_top_products("week", limit=5)
    except Exception as e:
        return f"❌ Ошибка топ товаров: {e}"
    if not top:
        return "❌ Нет данных о продажах за неделю"

    fee_pct = 0.20
    try:
        rep = seller_api.get_sales_report("week")
        if rep["revenue"] > 0:
            fee_pct = rep["fees_total"] / rep["revenue"]
            fee_pct = max(0.0, min(fee_pct, 0.95))
    except Exception:
        pass

    public_lines = ["🔥 Топ-5 товаров за неделю", ""]
    for i, p in enumerate(top, 1):
        name = p["name"]
        if len(name) > 70:
            name = name[:67] + "..."
        public_lines.append(f"{i}. {name}")
        public_lines.append(f"   🛒 {p['orders']} заказов")
        public_lines.append(f"   🔗 https://www.ozon.ru/product/{p['sku']}/")
        public_lines.append("")

    private_lines = ["📊 Топ-5 (с выручкой)", ""]
    for i, p in enumerate(top, 1):
        after_fees = p["revenue"] * (1 - fee_pct)
        private_lines.append(f"{i}. {p['name']}")
        private_lines.append(
            f"   🛒 {p['orders']} · 💰 {fmt_number(p['revenue'])} ₽ "
            f"(после вычетов: ~{fmt_number(after_fees)} ₽)"
        )
        private_lines.append("")

    try:
        post_id = publisher.publish_post("\n".join(public_lines))
        print(f"[TOP] ✅ Пост ID: {post_id}")
    except Exception as e:
        return f"❌ Ошибка публикации: {e}"

    try:
        vk.messages.send(peer_id=peer_id, message="\n".join(private_lines),
                         random_id=int(time.time() * 1000))
    except Exception:
        pass

    return "✅ Топ опубликован, детали — в ЛС"


def handle_forecast_command() -> str:
    try:
        fc = seller_api.get_forecast(days_back=30, forecast_days=7)
    except Exception as e:
        return f"❌ Ошибка прогноза: {e}"
    if not fc.get("ok"):
        return f"⚠ {fc.get('reason', 'Не удалось построить прогноз')}"
    return (
        f"📈 Прогноз на следующую неделю\n"
        f"(на основе {fc['days_back']} дней)\n\n"
        f"📊 Средние в день:\n"
        f"   🛒 {fc['avg_orders']} заказов\n"
        f"   💰 {fmt_number(fc['avg_revenue'])} ₽\n\n"
        f"🔮 Ожидаемо за {fc['forecast_days']} дней:\n"
        f"   🛒 ~{fc['forecast_orders']} заказов\n"
        f"   💰 ~{fmt_number(fc['forecast_revenue'])} ₽"
    )


def handle_anons_command(message: str) -> str:
    text = re.sub(r"^!анонс\s*", "", message, flags=re.IGNORECASE).strip()
    if not text:
        return "❌ Пример: !анонс Сегодня скидки 20%!"
    try:
        post_id = publisher.publish_post(text)
        return f"✅ Анонс опубликован! ID: {post_id}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


# ---------- Настройки ----------

def handle_template_command(message: str) -> str:
    text = re.sub(r"^!шаблон\s*", "", message, flags=re.IGNORECASE).strip().lower()
    if not text:
        return (f"🎨 Текущий шаблон: {settings.get_template()}\n\n"
                f"• минимал — название, цена, ссылка\n"
                f"• подробный — с описанием\n"
                f"• скидка — было/стало\n\n"
                f"Сменить: !шаблон минимал")
    aliases = {
        "минимал": "minimal", "minimal": "minimal", "мин": "minimal",
        "подробный": "detailed", "detailed": "detailed", "полный": "detailed",
        "скидка": "discount", "discount": "discount",
    }
    key = aliases.get(text)
    if not key:
        return f"❌ Неизвестный: {text}. Доступно: минимал, подробный, скидка"
    settings.set_template(key)
    return f"✅ Шаблон: {key}"


def handle_filter_command(message: str) -> str:
    text = re.sub(r"^!фильтр\s*", "", message, flags=re.IGNORECASE).strip()
    if not text:
        bl = settings.get_blacklist()
        if not bl:
            return "📝 Стоп-слова не заданы.\nДобавить: !фильтр дефект"
        return "📝 Стоп-слова:\n" + "\n".join(f"• {w}" for w in bl) + \
               "\n\nУдалить: !фильтр удалить <слово>"
    if text.lower().startswith("удалить "):
        word = text[8:].strip()
        return (f"✅ Удалено: «{word.lower()}»" if settings.remove_blacklist(word)
                else f"❌ Нет слова «{word.lower()}»")
    return (f"✅ Добавлено: «{text.lower()}»" if settings.add_blacklist(text)
            else "⚠ Уже в списке")


def handle_hashtags_command(message: str) -> str:
    text = re.sub(r"^!хештеги\s*", "", message, flags=re.IGNORECASE).strip().lower()
    if not text:
        state = "включены" if settings.hashtags_enabled() else "выключены"
        return f"🏷 Авто-хештеги {state}.\n!хештеги вкл / выкл / тест <название>"
    if text in ("вкл", "on"):
        settings.set_hashtags_enabled(True)
        return "✅ Включены"
    if text in ("выкл", "off"):
        settings.set_hashtags_enabled(False)
        return "✅ Выключены"
    if text.startswith("тест "):
        return f"🏷 {settings.generate_hashtags(text[5:].strip())}"
    return "❓ !хештеги вкл | выкл | тест <название>"


# ---------- Аналитика ----------

def handle_stocks_command() -> str:
    try:
        stocks = seller_api.get_low_stocks(threshold=5)
    except Exception as e:
        return f"❌ {e}"
    if not stocks:
        return "✅ Все товары в наличии (≥ 5 шт.)"
    lines = [f"⚠ Товары на исходе ({len(stocks)} шт.)", ""]
    for s in stocks[:20]:
        name = s["name"][:62] + ("..." if len(s["name"]) > 65 else "")
        lines.append(f"• {name} — {s['present']} шт.")
    if len(stocks) > 20:
        lines.append(f"\n...и ещё {len(stocks) - 20}")
    return "\n".join(lines)


def handle_balance_command() -> str:
    try:
        b = seller_api.get_balance()
    except Exception as e:
        return f"❌ {e}"
    return (
        f"💰 Финансы Ozon ({b['date_from']} — {b['date_to']})\n\n"
        f"📥 Продажи: {fmt_number(b['sales'])} ₽\n"
        f"➖ Комиссия: −{fmt_number(b['fee'])} ₽\n"
        f"➖ Услуги: −{fmt_number(b['services'])} ₽\n"
        f"➖ Возвраты: −{fmt_number(b['returns'])} ₽\n\n"
        f"✅ К перечислению: {fmt_number(b['accrued'])} ₽"
    )


def handle_product_command(message: str) -> str:
    m = re.search(r"\d{5,}", message)
    if not m:
        return "❌ Укажи SKU. Пример: !товар 15120854317"
    sku = m.group(0)
    try:
        r = seller_api.get_product_report(sku, "week")
    except Exception as e:
        return f"❌ {e}"
    name = r.get("name") or f"SKU {sku}"
    return (
        f"📦 {name}\n📅 {r['date_from']} — {r['date_to']}\n\n"
        f"🛒 Заказов: {r['orders']}\n"
        f"💰 Выручка: {fmt_number(r['revenue'])} ₽\n"
        f"🔗 https://www.ozon.ru/product/{sku}/"
    )


def handle_reviews_command() -> str:
    try:
        data = seller_api.get_reviews_summary(days=7)
    except Exception as e:
        return f"❌ Ошибка отзывов: {e}"

    if not data.get("ok"):
        return (
            f"⚠ Отзывы недоступны: {data.get('reason', 'неизвестная ошибка')}\n\n"
            f"Метод /v1/review/list требует подписки Premium Plus."
        )

    lines = [
        f"⭐ Отзывы за 7 дней",
        f"📊 Всего: {data['total']} · Средний рейтинг: {data['avg_rating']} / 5",
        "",
    ]

    if data["good"]:
        lines.append("👍 Хорошие отзывы:")
        for r in data["good"]:
            lines.append(f"  {r['rating']}★ {r['text'][:120]}...")
        lines.append("")

    if data["bad"]:
        lines.append("👎 Плохие отзывы:")
        for r in data["bad"]:
            lines.append(f"  {r['rating']}★ {r['text'][:120]}...")
        lines.append("")

    if not data["good"] and not data["bad"]:
        lines.append("Новых развёрнутых отзывов нет.")

    return "\n".join(lines)


def handle_questions_command() -> str:
    try:
        data = seller_api.get_questions(only_new=True)
    except Exception as e:
        return f"❌ Ошибка вопросов: {e}"

    if not data.get("ok"):
        return (
            f"⚠ Вопросы недоступны: {data.get('reason', 'неизвестная ошибка')}\n\n"
            f"Метод /v1/question/list требует подписки Premium Plus."
        )

    items = data.get("items", [])
    if not items:
        return "✅ Новых вопросов нет"

    lines = [f"❓ Неотвеченные вопросы ({len(items)}):", ""]
    for i, q in enumerate(items[:10], 1):
        lines.append(f"{i}. {q['text']}")
        if q.get("sku"):
            lines.append(f"   SKU: {q['sku']}")
        lines.append("")

    return "\n".join(lines)


def handle_actions_command() -> str:
    try:
        data = seller_api.get_actions()
    except Exception as e:
        return f"❌ Ошибка акций: {e}"

    if not data.get("ok"):
        return f"⚠ Акции недоступны: {data.get('reason', '—')}"

    items = data.get("items", [])
    if not items:
        return "📭 Активных акций нет"

    lines = [f"🎁 Активные акции Ozon ({len(items)}):", ""]
    for i, a in enumerate(items[:10], 1):
        lines.append(f"{i}. {a['title']}")
        if a.get("date_from") and a.get("date_to"):
            lines.append(f"   📅 {a['date_from'][:10]} — {a['date_to'][:10]}")
        if a.get("potential"):
            lines.append(f"   📦 товаров: {a['potential']}")
        lines.append("")

    return "\n".join(lines)


# ---------- Скидки ----------

def _on_price_drop_callback(url, info, product, old_price, new_price):
    name = info.get("name") or product["title"]
    diff = old_price - new_price
    pct = diff / old_price * 100 if old_price else 0
    text = (
        f"🔥 СКИДКА\n\n📦 {name}\n\n"
        f"❌ Было: {fmt_number(old_price)} ₽\n"
        f"✅ Стало: {fmt_number(new_price)} ₽\n"
        f"📉 −{fmt_number(diff)} ₽ ({pct:.1f}%)\n\n"
        f"🔗 Купить: {url}"
    )
    try:
        post_id = publisher.publish_post(text, product.get("images", [])[:1])
        print(f"[SCHEDULER] ✅ Пост о скидке ID: {post_id}")
    except Exception as e:
        print(f"[SCHEDULER] ❌ {e}")


def handle_discount_command(message: str, peer_id: int) -> str:
    text = re.sub(r"^!скидк[аи]\s*", "", message, flags=re.IGNORECASE).strip()
    lower = text.lower()

    if not text:
        tracked = storage.get_tracked()
        if not tracked:
            return ("📉 Список пуст.\n"
                    "!скидка <url> — добавить\n"
                    "!скидка удалить <url>\n"
                    "!скидка проверить")
        lines = [f"📉 Отслеживаемых ({len(tracked)}):", ""]
        for url, info in list(tracked.items())[:15]:
            name = info.get("name", "—")[:55]
            lines.append(f"• {name}")
            lines.append(f"  {info.get('price')} ₽ · {url[:50]}")
        return "\n".join(lines)

    if lower == "проверить":
        vk.messages.send(peer_id=peer_id, message="⏳ Проверяю...",
                         random_id=int(time.time() * 1000))
        from bot_scheduler import _check_prices
        _check_prices({"parser": parser, "on_price_drop": _on_price_drop_callback})
        return "✅ Готово"

    if lower.startswith("удалить"):
        m = OZON_URL_RE.search(text)
        if not m:
            return "❌ Укажи ссылку"
        return (f"✅ Удалено" if storage.remove_tracked(m.group(0))
                else "⚠ Нет в списке")

    m = OZON_URL_RE.search(text)
    if not m:
        return "❌ Пример: !скидка https://ozon.ru/product/..."
    url = m.group(0)

    if url in storage.get_tracked():
        return "⚠ Уже отслеживается"

    vk.messages.send(peer_id=peer_id, message="⏳ Парсинг...",
                     random_id=int(time.time() * 1000))
    try:
        product = parser.parse_product(url)
    except Exception as e:
        return f"❌ {e}"

    price = _parse_price(product.get("price"))
    if not price:
        return "❌ Не удалось определить цену"

    storage.add_tracked(url, price, product["title"])
    return (f"✅ Добавлено\n\n📦 {product['title']}\n💰 {price} ₽\n\n"
            f"Проверка раз в час. При снижении ≥5% или ≥100 ₽ — публикую пост.")


def handle_dupes_command(message: str) -> str:
    text = re.sub(r"^!дубли\s*", "", message, flags=re.IGNORECASE).strip().lower()
    if text == "очистить":
        storage.clear_published()
        return "✅ История очищена"
    stats = storage.published_stats()
    return (
        f"📚 История публикаций:\n\n"
        f"• Всего: {stats['total']}\n"
        f"• За 7 дней: {stats['week']}\n"
        f"• За 30 дней: {stats['month']}\n\n"
        f"Публикация товара повторно в течение 7 дней блокируется.\n"
        f"Очистить: !дубли очистить\n"
        f"Обойти: !пост! <url>"
    )


def handle_schedule_command(message: str) -> str:
    text = re.sub(r"^!расписание\s*", "", message, flags=re.IGNORECASE).strip().lower()
    data = storage.get_schedule()
    if not text:
        lines = ["⏰ Расписание:"]
        for task, cfg in data.items():
            title = "Утренний топ" if task == "morning_top" else "Вечерний отчёт"
            state = "вкл" if cfg.get("enabled") else "выкл"
            lines.append(f"• {title}: {state} в {cfg.get('time')}")
        lines.append("")
        lines.append("!расписание топ 09:00")
        lines.append("!расписание отчет 20:00")
        lines.append("!расписание выкл топ")
        return "\n".join(lines)

    if "выкл" in text:
        task = "morning_top" if "топ" in text else "daily_report"
        storage.set_schedule(task, enabled=False)
        return f"⏸ Выключено: {task}"

    if "топ" in text:
        task = "morning_top"
    elif "отчет" in text or "отчёт" in text:
        task = "daily_report"
    else:
        return "❌ Не понял. Смотри !расписание"

    m = re.search(r"(\d{1,2}:\d{2})", text)
    if not m:
        return "❌ Пример: !расписание топ 09:00"

    cfg = storage.set_schedule(task, enabled=True, time_str=m.group(1))
    return f"✅ {task} → {cfg['time']}"


# ---------- Опросы и розыгрыши ----------

def handle_poll_command(message: str) -> str:
    """!опрос Вопрос? | вариант1 | вариант2 | ..."""
    text = re.sub(r"^!опрос\s*", "", message, flags=re.IGNORECASE).strip()
    if not text or "|" not in text:
        return ("❌ Формат: !опрос Вопрос? | вариант1 | вариант2 | ...\n"
                "Пример: !опрос Какой цвет лучше? | Чёрный | Хром | Латунь")

    parts = [p.strip() for p in text.split("|") if p.strip()]
    if len(parts) < 3:
        return "❌ Нужно: вопрос + минимум 2 варианта"

    question = parts[0]
    answers = parts[1:11]  # ВК разрешает до 10 вариантов

    try:
        # Создаём опрос через VK API
        poll = vk.polls.create(
            question=question,
            is_anonymous=1,
            is_multiple=0,
            owner_id=-VK_GROUP_ID,
            add_answers=json.dumps(
                [{"text": a} for a in answers],
                ensure_ascii=False
            ),
        )
        poll_id = poll["id"]
        attachment = f"poll-{VK_GROUP_ID}_{poll_id}"

        response = vk.wall.post(
            owner_id=f"-{VK_GROUP_ID}",
            message=f"📊 {question}",
            attachments=attachment,
            from_group=1,
        )
        return f"✅ Опрос опубликован! ID: {response['post_id']}"
    except Exception as e:
        print(f"[VK] ❌ Опрос: {e}")
        return f"❌ Ошибка публикации опроса: {e}"


def handle_giveaway_command(message: str) -> str:
    """!розыгрыш <url> YYYY-MM-DD"""
    text = re.sub(r"^!розыгрыш\s*", "", message, flags=re.IGNORECASE).strip()

    # Проверяем розыгрыши, готовые к завершению
    if text.lower() in ("итоги", "завершить"):
        return _finish_ready_giveaways()

    if text.lower() in ("список", "list"):
        active = storage.get_active_giveaways()
        if not active:
            return "📭 Активных розыгрышей нет"
        lines = ["🎁 Активные розыгрыши:", ""]
        for g in active:
            lines.append(f"• {g['title'][:60]}")
            lines.append(f"  до {g['end_date']} · post_id {g['post_id']}")
        return "\n".join(lines)

    m = OZON_URL_RE.search(text)
    if not m:
        return ("❌ Формат: !розыгрыш <url> YYYY-MM-DD\n"
                "Пример: !розыгрыш https://ozon.ru/product/... 2026-10-01\n\n"
                "Дополнительно:\n"
                "!розыгрыш список\n"
                "!розыгрыш итоги — подвести итоги завершившихся")
    url = m.group(0)

    dm = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if not dm:
        return "❌ Укажи дату окончания в формате YYYY-MM-DD"
    end_date = dm.group(1)

    try:
        product = parser.parse_product(url)
    except Exception as e:
        return f"❌ Ошибка парсинга товара: {e}"

    title = product["title"]
    price = product["price"]

    text_post = (
        f"🎁 РОЗЫГРЫШ\n\n"
        f"Разыгрываем: {title}\n"
        f"💰 Стоимость: {price}\n\n"
        f"📌 Условия участия:\n"
        f"1. Подписаться на наше сообщество\n"
        f"2. Сделать репост этой записи\n"
        f"3. Не удалять репост до подведения итогов\n\n"
        f"📅 Итоги: {end_date}\n"
        f"Победитель определится случайным образом.\n\n"
        f"🔗 Товар: {url}\n\n"
        f"#розыгрыш #ozon #приз"
    )

    try:
        post_id = publisher.publish_post(text_post, product.get("images", [])[:1])
        storage.add_giveaway(post_id, url, title, end_date)
        return (
            f"✅ Розыгрыш опубликован! ID: {post_id}\n"
            f"Дата итогов: {end_date}\n\n"
            f"После даты напиши: !розыгрыш итоги"
        )
    except Exception as e:
        return f"❌ Ошибка публикации: {e}"


def _finish_ready_giveaways() -> str:
    """Определяет победителей для розыгрышей, у которых прошла дата."""
    active = storage.get_active_giveaways()
    if not active:
        return "📭 Нет активных розыгрышей"

    today = datetime.now().strftime("%Y-%m-%d")
    ready = [g for g in active if g["end_date"] <= today]

    if not ready:
        return f"⏳ Ни один розыгрыш ещё не завершился.\nАктивных: {len(active)}"

    results = []
    for g in ready:
        try:
            # Получаем список репостов записи
            offset = 0
            reposters = []
            while True:
                resp = vk.wall.getReposts(
                    owner_id=-VK_GROUP_ID,
                    post_id=g["post_id"],
                    count=1000,
                    offset=offset,
                )
                items = resp.get("items", [])
                if not items:
                    break
                for item in items:
                    rid = item.get("from_id")
                    if rid and rid > 0:  # только пользователи
                        reposters.append(rid)
                if len(items) < 1000:
                    break
                offset += 1000
                if offset > 10000:
                    break

            # Уникальные ID
            reposters = list(set(reposters))

            if not reposters:
                results.append(f"❌ {g['title'][:50]} — нет репостов")
                storage.update_giveaway(g["post_id"], finished=True)
                continue

            winner = random.choice(reposters)

            # Публикуем результат
            result_text = (
                f"🎉 РОЗЫГРЫШ ЗАВЕРШЁН\n\n"
                f"Разыгрывали: {g['title']}\n\n"
                f"🏆 Победитель: [id{winner}|пользователь]\n"
                f"Участников: {len(reposters)}\n\n"
                f"Победителю — написать нам в личные сообщения для получения приза!"
            )
            try:
                post_id = publisher.publish_post(result_text)
            except Exception:
                post_id = None

            try:
                vk.messages.send(
                    user_id=winner,
                    message=(f"🎉 Поздравляем! Вы выиграли в розыгрыше: {g['title']}\n\n"
                             f"Напишите нам для получения приза."),
                    random_id=int(time.time() * 1000),
                )
            except Exception as e:
                print(f"[GIVEAWAY] Не удалось написать победителю: {e}")

            storage.update_giveaway(g["post_id"], finished=True, winner_id=winner)
            results.append(
                f"✅ {g['title'][:50]} — победитель id{winner} "
                f"(из {len(reposters)} участников)"
            )
        except Exception as e:
            results.append(f"❌ {g['title'][:50]} — ошибка: {e}")

    return "🎁 Итоги розыгрышей:\n\n" + "\n".join(results)


# ---------- Клавиатуры ----------

def handle_rotchet_command(peer_id: int):
    keyboard = VkKeyboard(one_time=False)
    keyboard.add_button("📅 Вчера", color=VkKeyboardColor.PRIMARY,
                        payload={"cmd": "sales_report", "period": "yesterday"})
    keyboard.add_button("📅 Сегодня", color=VkKeyboardColor.PRIMARY,
                        payload={"cmd": "sales_report", "period": "today"})
    keyboard.add_line()
    keyboard.add_button("📅 Неделя", color=VkKeyboardColor.PRIMARY,
                        payload={"cmd": "sales_report", "period": "week"})
    keyboard.add_button("📅 Месяц", color=VkKeyboardColor.PRIMARY,
                        payload={"cmd": "month_menu"})
    vk.messages.send(peer_id=peer_id, message="📊 Выберите период:",
                     keyboard=keyboard.get_keyboard(),
                     random_id=int(time.time() * 1000))


def handle_month_menu(peer_id: int):
    today = datetime.now().date()
    months = []
    y, m = today.year, today.month
    for _ in range(3):
        months.append((y, m))
        m -= 1
        if m <= 0:
            m = 12
            y -= 1

    keyboard = VkKeyboard(one_time=False)
    y1, m1 = months[0]
    keyboard.add_button(f"{RU_MONTHS[m1 - 1]} {y1}", color=VkKeyboardColor.PRIMARY,
                        payload={"cmd": "sales_report", "period": f"month_{y1}_{m1:02d}"})
    y2, m2 = months[1]
    keyboard.add_button(f"{RU_MONTHS[m2 - 1]} {y2}", color=VkKeyboardColor.SECONDARY,
                        payload={"cmd": "sales_report", "period": f"month_{y2}_{m2:02d}"})
    keyboard.add_line()
    y3, m3 = months[2]
    keyboard.add_button(f"{RU_MONTHS[m3 - 1]} {y3}", color=VkKeyboardColor.SECONDARY,
                        payload={"cmd": "sales_report", "period": f"month_{y3}_{m3:02d}"})
    keyboard.add_line()
    keyboard.add_button("⬅️ Назад", color=VkKeyboardColor.NEGATIVE,
                        payload={"cmd": "back_to_periods"})
    vk.messages.send(peer_id=peer_id, message="📅 Выберите месяц:",
                     keyboard=keyboard.get_keyboard(),
                     random_id=int(time.time() * 1000))


def handle_report_period(period: str, peer_id: int) -> str:
    try:
        data = seller_api.get_sales_report(period)
    except Exception as e:
        return f"❌ {e}"
    d_orders = fmt_delta(data["ordered_units"], data.get("previous_orders", 0))
    d_rev = fmt_delta(data["revenue"], data.get("previous_revenue", 0))
    return (
        f"📊 Отчёт — {data['label']}\n"
        f"📅 {data['date_from']} — {data['date_to']}\n\n"
        f"🛒 Заказов: {data['ordered_units']}\n"
        f"💰 Выручка: {fmt_number(data['revenue'])} ₽\n"
        f"➖ Удержания: {fmt_number(data['fees_total'])} ₽\n"
        f"✅ После вычетов: {fmt_number(data['revenue_after_fees'])} ₽\n\n"
        f"📊 К прошлому:\n   🛒 {d_orders}\n   💰 {d_rev}"
    )


def handle_restart_command(peer_id: int):
    print("[BOT] 🔄 !рестарт")
    try:
        vk.messages.send(peer_id=peer_id, message="🔄 Перезапускаюсь...",
                         random_id=int(time.time() * 1000))
    except Exception:
        pass
    try:
        parser.close()
    except Exception:
        pass
    try:
        notifier.flush_now()
    except Exception:
        pass
    time.sleep(3)
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
    except Exception as e:
        print(f"[BOT] ❌ {e}")
        sys.exit(1)


def handle_log_command(message: str) -> str:
    m = re.search(r"\d+", message)
    n = int(m.group(0)) if m else 30
    n = max(5, min(n, 200))
    text = notifier.read_last_lines(n)
    if len(text) > 3900:
        text = "...(обрезано)\n" + text[-3900:]
    return f"📜 {n} строк:\n\n{text}"


def scheduled_morning_top():
    print("[SCHEDULER] Утренний топ")
    try:
        handle_top_command(ADMIN_ID, scheduled=True)
    except Exception as e:
        print(f"[SCHEDULER] ❌ {e}")


def scheduled_daily_report():
    print("[SCHEDULER] Вечерний отчёт")
    try:
        handle_report_command()
    except Exception as e:
        print(f"[SCHEDULER] ❌ {e}")


# ---------- Основной цикл ----------

def main():
    global AUTOPOSTING_ENABLED
    print("[BOT] Запущен. Ожидаю команды...")

    bot_scheduler.start({
        "parser": parser,
        "on_price_drop": _on_price_drop_callback,
        "on_morning_top": scheduled_morning_top,
        "on_daily_report": scheduled_daily_report,
    })

    for event in longpoll.listen():
        if event.type != VkEventType.MESSAGE_NEW or not event.to_me:
            continue

        message = event.text.strip()
        lower = message.lower()
        notifier.set_peer(event.peer_id)

        raw_payload = getattr(event, "payload", None)
        if not raw_payload and hasattr(event, "obj") and isinstance(event.obj, dict):
            raw_payload = event.obj.get("payload")

        if raw_payload:
            try:
                payload = json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            except Exception:
                payload = {}
            cmd = payload.get("cmd")

            if cmd == "month_menu":
                handle_month_menu(event.peer_id)
                continue
            if cmd == "back_to_periods":
                handle_rotchet_command(event.peer_id)
                continue
            if cmd == "sales_report":
                period = payload.get("period", "today")
                vk.messages.send(peer_id=event.peer_id, message="⏳ Собираю...",
                                 random_id=int(time.time() * 1000))
                text = handle_report_period(period, event.peer_id)
                vk.messages.send(peer_id=event.peer_id, message=text,
                                 random_id=int(time.time() * 1000))
                continue

        try:
            # --- Служебные ---
            if lower.startswith("!рестарт"):
                handle_restart_command(event.peer_id)
                continue
            if lower.startswith("!лог"):
                vk.messages.send(peer_id=event.peer_id, message=handle_log_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!стоп"):
                AUTOPOSTING_ENABLED = False
                vk.messages.send(peer_id=event.peer_id, message="⏸ Остановлен.",
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!старт"):
                AUTOPOSTING_ENABLED = True
                vk.messages.send(peer_id=event.peer_id, message="▶ Возобновлён.",
                                 random_id=int(time.time() * 1000))
                continue

            # --- Настройки ---
            if lower.startswith("!шаблон"):
                vk.messages.send(peer_id=event.peer_id, message=handle_template_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!фильтр"):
                vk.messages.send(peer_id=event.peer_id, message=handle_filter_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!хештеги"):
                vk.messages.send(peer_id=event.peer_id, message=handle_hashtags_command(message),
                                 random_id=int(time.time() * 1000))
                continue

            # --- Скидки / расписание / дубли ---
            if lower.startswith("!расписание"):
                vk.messages.send(peer_id=event.peer_id, message=handle_schedule_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!скидка") or lower.startswith("!скидки"):
                vk.messages.send(peer_id=event.peer_id, message=handle_discount_command(message, event.peer_id),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!дубли"):
                vk.messages.send(peer_id=event.peer_id, message=handle_dupes_command(message),
                                 random_id=int(time.time() * 1000))
                continue

            # --- Отчёты ---
            if lower.startswith("!отзывы"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Собираю отзывы...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_reviews_command(),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!вопросы"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Собираю вопросы...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_questions_command(),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!акции"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Загружаю акции...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_actions_command(),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!остатки"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Собираю остатки...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_stocks_command(),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!баланс"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Считаю...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_balance_command(),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!товар"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Собираю...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_product_command(message),
                                 random_id=int(time.time() * 1000))
                continue

            # --- Опросы / розыгрыши ---
            if lower.startswith("!опрос"):
                vk.messages.send(peer_id=event.peer_id, message=handle_poll_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!розыгрыш"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Обрабатываю...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_giveaway_command(message),
                                 random_id=int(time.time() * 1000))
                continue

            # --- Посты ---
            if lower.startswith("!пост!"):
                vk.messages.send(peer_id=event.peer_id,
                                 message=handle_post_command(message, skip_dupe_check=True),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!подборка"):
                count = min(len(OZON_URL_RE.findall(message)), MAX_PODBORKA)
                vk.messages.send(peer_id=event.peer_id,
                                 message=f"⏳ Собираю подборку из {count}...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_podborka_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!ротчет"):
                handle_rotchet_command(event.peer_id)
                continue
            if lower.startswith("!топ"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Собираю топ...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_top_command(event.peer_id),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!прогноз"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Считаю...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_forecast_command(),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!анонс"):
                vk.messages.send(peer_id=event.peer_id, message=handle_anons_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!пост"):
                vk.messages.send(peer_id=event.peer_id, message=handle_post_command(message),
                                 random_id=int(time.time() * 1000))
                continue
            if lower.startswith("!отчет"):
                vk.messages.send(peer_id=event.peer_id, message="⏳ Собираю...",
                                 random_id=int(time.time() * 1000))
                vk.messages.send(peer_id=event.peer_id, message=handle_report_command(),
                                 random_id=int(time.time() * 1000))
                continue

            if lower.startswith("!помощь") or lower.startswith("!help"):
                help_text = (
                    "📖 Команды бота:\n\n"
                    "📢 Постинг:\n"
                    "!пост <url> — карточка (с защитой от дублей)\n"
                    "!пост! <url> — принудительно\n"
                    "!подборка <url1> <url2> ... — до 5 товаров\n"
                    "!анонс <текст> — ручной пост\n"
                    "!топ — топ-5 товаров недели\n"
                    "!опрос Вопрос? | вар1 | вар2\n"
                    "!розыгрыш <url> YYYY-MM-DD\n"
                    "!розыгрыш итоги / список\n\n"
                    "💰 Скидки:\n"
                    "!скидка <url> / !скидка / !скидка удалить <url>\n\n"
                    "📊 Аналитика:\n"
                    "!Ротчет — отчёт по продажам\n"
                    "!прогноз — прогноз выручки\n"
                    "!остатки / !баланс / !товар <sku>\n"
                    "!отзывы / !вопросы / !акции\n"
                    "!отчет — аналитика за неделю\n\n"
                    "⏰ Автоматизация:\n"
                    "!расписание — автопосты\n"
                    "!дубли — история публикаций\n\n"
                    "⚙️ Настройки:\n"
                    "!шаблон / !фильтр / !хештеги\n\n"
                    "🔧 Служебное:\n"
                    "!лог N / !стоп / !старт / !рестарт"
                )
                vk.messages.send(peer_id=event.peer_id, message=help_text,
                                 random_id=int(time.time() * 1000))
                continue

        except Exception as e:
            print(f"[BOT] ❌ Необработанная ошибка: {e}")


if __name__ == "__main__":
    main()