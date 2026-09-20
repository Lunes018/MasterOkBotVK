import os
import matplotlib
matplotlib.use("Agg")  # без GUI — для сервера/консоли
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from datetime import datetime


CHART_DIR = "charts"
os.makedirs(CHART_DIR, exist_ok=True)


def build_orders_chart(daily_data: list, label: str, date_from: str, date_to: str) -> str:
    """
    Строит график заказов и выручки по дням.
    daily_data: [{"date": "2026-09-14", "orders": 12, "revenue": 34567.0}, ...]
    Возвращает путь к PNG-файлу или None при ошибке.
    """
    if not daily_data:
        return None

    try:
        dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in daily_data]
        orders = [d["orders"] for d in daily_data]
        revenues = [d["revenue"] for d in daily_data]

        fig, ax1 = plt.subplots(figsize=(10, 5), dpi=120)
        fig.patch.set_facecolor("#1e1e2e")
        ax1.set_facecolor("#282a36")

        # Столбцы — заказы
        bars = ax1.bar(dates, orders, color="#7aa2f7", alpha=0.85, width=0.6, label="Заказы")
        ax1.set_xlabel("Дата", color="#cdd6f4", fontsize=11)
        ax1.set_ylabel("Заказы", color="#7aa2f7", fontsize=11)
        ax1.tick_params(axis="y", labelcolor="#7aa2f7")
        ax1.tick_params(axis="x", labelcolor="#cdd6f4", rotation=30)

        # Подписи значений над столбцами
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax1.text(
                    bar.get_x() + bar.get_width() / 2,
                    h,
                    f"{int(h)}",
                    ha="center", va="bottom",
                    color="#cdd6f4", fontsize=9,
                )

        # Вторая ось — выручка
        ax2 = ax1.twinx()
        ax2.plot(dates, revenues, color="#f7768e", marker="o", linewidth=2, label="Выручка")
        ax2.set_ylabel("Выручка, ₽", color="#f7768e", fontsize=11)
        ax2.tick_params(axis="y", labelcolor="#f7768e")

        # Красивые даты на оси X
        ax1.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax1.xaxis.set_major_formatter(mdates.DateFormatter("%d.%m"))

        # Заголовок
        plt.title(
            f"{label}\n{date_from} — {date_to}",
            color="#cdd6f4", fontsize=13, pad=15,
        )

        # Легенда
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(
            lines1 + lines2, labels1 + labels2,
            loc="upper left", facecolor="#1e1e2e",
            labelcolor="#cdd6f4", edgecolor="#45475a",
        )

        for spine in ax1.spines.values():
            spine.set_color("#45475a")
        for spine in ax2.spines.values():
            spine.set_color("#45475a")

        ax1.grid(axis="y", linestyle="--", alpha=0.2)

        plt.tight_layout()

        # Имя файла — уникальное, чтобы не перезаписывалось
        fname = f"chart_{date_from}_{date_to}_{int(datetime.now().timestamp())}.png"
        fpath = os.path.join(CHART_DIR, fname)
        plt.savefig(fpath, facecolor=fig.get_facecolor())
        plt.close(fig)

        print(f"[CHART] График сохранён: {fpath}")
        return fpath

    except Exception as e:
        print(f"[CHART] ❌ Ошибка построения графика: {e}")
        return None