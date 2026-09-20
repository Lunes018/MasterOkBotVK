import requests
from config import YANDEX_AUTH_HEADER, YANDEX_FOLDER_ID


TEMPLATE_HEADER = "Почему стоит выбирать нас?"


def generate_report_text(stats: dict) -> str:
    if not YANDEX_AUTH_HEADER or not YANDEX_FOLDER_ID:
        return _fallback_report(stats)

    prompt = (
        f"Напиши пост для ВК о результатах недели интернет-магазина "
        f"(с {stats['date_from']} по {stats['date_to']}). "
        f"Заказов: {stats['total_orders']}. "
        f"Действующий рейтинг магазина: {stats['seller_rating']} из 5. "
        f"Жалоб на товары: {stats['complaints']}. "
        f"Начни пост фразой «{TEMPLATE_HEADER}». "
        f"Далее оформи как:\n"
        f"📊 Итоги недели (даты)\n\n"
        f"🛒 Заказов: ...\n"
        f"⭐ Рейтинг магазина: ... / 5\n"
        f"Жалоб на товары: ...\n\n"
        f"Спасибо за ваши заказы! Продолжаем радовать вас качественными товарами. 🚀\n"
        f"БЕЗ markdown-разметки."
    )

    payload = {
        "modelUri": f"gpt://{YANDEX_FOLDER_ID}/yandexgpt-lite",
        "completionOptions": {
            "stream": False,
            "temperature": 0.7,
            "maxTokens": 500,
        },
        "messages": [
            {"role": "system",
             "text": "Ты пишешь посты для ВКонтакте. Не используй markdown."},
            {"role": "user", "text": prompt},
        ],
    }

    try:
        response = requests.post(
            "https://llm.api.cloud.yandex.net/foundationModels/v1/completion",
            headers={
                "Authorization": YANDEX_AUTH_HEADER,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        return result["result"]["alternatives"][0]["message"]["text"]
    except Exception as e:
        print(f"[AI] Ошибка генерации: {e}. Использую шаблон.")
        return _fallback_report(stats)


def _fallback_report(stats: dict) -> str:
    """Шаблон отчёта: только заказы, рейтинг магазина и жалобы."""
    return (
        f"{TEMPLATE_HEADER}\n"
        f"📊 Итоги недели ({stats['date_from']} — {stats['date_to']})\n"
        f"\n"
        f"🛒 Заказов: {stats['total_orders']}\n"
        f"⭐ Рейтинг магазина: {stats['seller_rating']} / 5\n"
        f"Жалоб на товары: {stats['complaints']}\n"
        f"\n"
        f"Спасибо за ваши заказы! Продолжаем радовать вас качественными товарами. 🚀"
    )