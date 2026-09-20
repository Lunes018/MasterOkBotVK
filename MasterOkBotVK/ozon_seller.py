import time
import calendar
import requests
from datetime import datetime, timedelta
from config import OZON_CLIENT_ID, OZON_API_KEY


RU_MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


class OzonSellerAPI:
    BASE_URL = "https://api-seller.ozon.ru"

    def __init__(self):
        self.headers = {
            "Client-Id": OZON_CLIENT_ID,
            "Api-Key": OZON_API_KEY,
            "Content-Type": "application/json",
        }
        self._sku_name_cache = {}

    # ---------- Недельный отчёт ----------

    def get_weekly_analytics(self) -> dict:
        date_to = datetime.now().strftime("%Y-%m-%d")
        date_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        total_orders = 0
        returns = 0

        try:
            payload = {
                "date_from": date_from,
                "date_to": date_to,
                "metrics": ["ordered_units", "returns"],
                "dimension": ["day"],
                "limit": 1000,
            }
            r = requests.post(
                f"{self.BASE_URL}/v1/analytics/data",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            for item in data.get("result", {}).get("data", []):
                metrics = item.get("metrics", [])
                if metrics and metrics[0]:
                    total_orders += int(metrics[0])
                if len(metrics) > 1 and metrics[1]:
                    returns += int(metrics[1])
        except Exception as e:
            print(f"[SELLER] ❌ analytics error: {e}")

        seller_rating = 0.0
        try:
            r = requests.post(
                f"{self.BASE_URL}/v1/seller/info",
                headers=self.headers,
                json={},
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                for rating_group in data.get("ratings", []):
                    current_value = rating_group.get("current_value", {})
                    val = current_value.get("value")
                    if val is not None:
                        try:
                            seller_rating = round(float(val), 2)
                            break
                        except (TypeError, ValueError):
                            continue
        except Exception as e:
            print(f"[SELLER] ❌ seller/info error: {e}")

        complaints = max(0, returns // 2)

        return {
            "date_from": date_from,
            "date_to": date_to,
            "total_orders": total_orders,
            "seller_rating": seller_rating,
            "complaints": complaints,
        }

    # ---------- Периоды ----------

    def _resolve_period(self, period: str) -> tuple:
        today = datetime.now().date()

        if period == "today":
            d = today.strftime("%Y-%m-%d")
            return d, d, "Сегодня"

        if period == "yesterday":
            y = (today - timedelta(days=1)).strftime("%Y-%m-%d")
            return y, y, "Вчера"

        if period == "week":
            date_from = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            date_to = today.strftime("%Y-%m-%d")
            return date_from, date_to, "Последние 7 дней"

        if period.startswith("month_"):
            try:
                _, year_s, month_s = period.split("_")
                year = int(year_s)
                month = int(month_s)
            except Exception:
                raise ValueError(f"Некорректный период месяца: {period}")

            first_day = datetime(year, month, 1).date()
            last_day_num = calendar.monthrange(year, month)[1]
            last_day = datetime(year, month, last_day_num).date()
            if last_day > today:
                last_day = today

            label = f"{RU_MONTHS[month - 1]} {year}"
            return (
                first_day.strftime("%Y-%m-%d"),
                last_day.strftime("%Y-%m-%d"),
                label,
            )

        raise ValueError(f"Неизвестный период: {period}")

    def _get_previous_period_dates(self, period: str) -> tuple:
        date_from, date_to, _ = self._resolve_period(period)
        d_from = datetime.strptime(date_from, "%Y-%m-%d").date()
        d_to = datetime.strptime(date_to, "%Y-%m-%d").date()
        delta = (d_to - d_from).days

        prev_to = d_from - timedelta(days=1)
        prev_from = prev_to - timedelta(days=delta)

        return prev_from.strftime("%Y-%m-%d"), prev_to.strftime("%Y-%m-%d")

    def _get_period_totals(self, date_from: str, date_to: str) -> dict:
        result = {"orders": 0, "revenue": 0.0}
        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": ["ordered_units", "revenue"],
            "dimension": ["day"],
            "limit": 1000,
        }
        for attempt in range(1, 4):
            try:
                r = requests.post(
                    f"{self.BASE_URL}/v1/analytics/data",
                    headers=self.headers,
                    json=payload,
                    timeout=30,
                )
                if r.status_code == 429:
                    time.sleep(3)
                    continue
                if r.status_code != 200:
                    return result
                data = r.json()
                for item in data.get("result", {}).get("data", []):
                    metrics = item.get("metrics", [])
                    if metrics and metrics[0]:
                        result["orders"] += int(metrics[0])
                    if len(metrics) > 1 and metrics[1]:
                        result["revenue"] += float(metrics[1])
                result["revenue"] = round(result["revenue"], 2)
                return result
            except Exception as e:
                print(f"[SELLER] ❌ _get_period_totals: {e}")
                time.sleep(2)
        return result

    # ---------- Отчёт с продажами + сравнение ----------

    def get_sales_report(self, period: str) -> dict:
        date_from, date_to, label = self._resolve_period(period)

        result = {
            "period": period,
            "label": label,
            "date_from": date_from,
            "date_to": date_to,
            "ordered_units": 0,
            "revenue": 0.0,
            "revenue_after_fees": 0.0,
            "fees_total": 0.0,
        }

        current = self._get_period_totals(date_from, date_to)
        result["ordered_units"] = current["orders"]
        result["revenue"] = current["revenue"]

        time.sleep(1)

        try:
            payload = {"date_from": date_from, "date_to": date_to}
            r = requests.post(
                f"{self.BASE_URL}/v1/finance/balance",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            if r.status_code == 200:
                data = r.json()
                cashflows = data.get("cashflows", {})
                sales = cashflows.get("sales", {})
                services = cashflows.get("services", [])
                returns = cashflows.get("returns", {})
                total = data.get("total", {})

                sales_fee = abs(float(sales.get("fee", {}).get("value") or 0))
                services_total = sum(
                    abs(float(s.get("amount", {}).get("value") or 0)) for s in services
                )
                returns_amount = abs(float(returns.get("amount", {}).get("value") or 0))

                result["fees_total"] = round(
                    sales_fee + services_total + returns_amount, 2
                )
                accrued = total.get("accrued", {}).get("value")
                if accrued is not None:
                    result["revenue_after_fees"] = round(abs(float(accrued)), 2)
                else:
                    result["revenue_after_fees"] = round(
                        max(0.0, result["revenue"] - result["fees_total"]), 2
                    )
            else:
                result["fees_total"] = round(result["revenue"] * 0.2, 2)
                result["revenue_after_fees"] = round(result["revenue"] * 0.8, 2)
        except Exception as e:
            print(f"[SELLER] ❌ finance/balance error: {e}")
            result["fees_total"] = round(result["revenue"] * 0.2, 2)
            result["revenue_after_fees"] = round(result["revenue"] * 0.8, 2)

        prev_from, prev_to = self._get_previous_period_dates(period)
        time.sleep(1)
        previous = self._get_period_totals(prev_from, prev_to)

        result["previous_orders"] = previous["orders"]
        result["previous_revenue"] = previous["revenue"]
        result["prev_date_from"] = prev_from
        result["prev_date_to"] = prev_to

        return result

    # ---------- Дневная детализация ----------

    def get_daily_breakdown(self, period: str) -> list:
        date_from, date_to, _ = self._resolve_period(period)
        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": ["ordered_units", "revenue"],
            "dimension": ["day"],
            "limit": 1000,
        }
        for attempt in range(1, 4):
            try:
                r = requests.post(
                    f"{self.BASE_URL}/v1/analytics/data",
                    headers=self.headers,
                    json=payload,
                    timeout=30,
                )
                if r.status_code == 429:
                    time.sleep(3)
                    continue
                if r.status_code != 200:
                    return []

                data = r.json()
                result = []
                for item in data.get("result", {}).get("data", []):
                    dims = item.get("dimensions", [])
                    day_str = None
                    if dims:
                        d0 = dims[0]
                        if isinstance(d0, dict):
                            day_str = d0.get("id") or d0.get("value")
                        elif isinstance(d0, list) and d0:
                            day_str = d0[0]
                        elif isinstance(d0, str):
                            day_str = d0
                    if not day_str:
                        continue
                    metrics = item.get("metrics", [])
                    orders = int(metrics[0]) if metrics and metrics[0] else 0
                    revenue = float(metrics[1]) if len(metrics) > 1 and metrics[1] else 0.0
                    result.append({
                        "date": day_str,
                        "orders": orders,
                        "revenue": round(revenue, 2),
                    })
                result.sort(key=lambda x: x["date"])
                return result
            except Exception as e:
                print(f"[SELLER] ❌ daily breakdown: {e}")
                time.sleep(2)
        return []

    # ---------- Прогноз ----------

    def get_forecast(self, days_back: int = 30, forecast_days: int = 7) -> dict:
        today = datetime.now().date()
        date_from = (today - timedelta(days=days_back)).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")

        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": ["ordered_units", "revenue"],
            "dimension": ["day"],
            "limit": 1000,
        }

        daily = []
        for attempt in range(1, 4):
            try:
                r = requests.post(
                    f"{self.BASE_URL}/v1/analytics/data",
                    headers=self.headers,
                    json=payload,
                    timeout=30,
                )
                if r.status_code == 429:
                    time.sleep(3)
                    continue
                if r.status_code != 200:
                    break
                data = r.json()
                for item in data.get("result", {}).get("data", []):
                    metrics = item.get("metrics", [])
                    orders = int(metrics[0]) if metrics and metrics[0] else 0
                    revenue = float(metrics[1]) if len(metrics) > 1 and metrics[1] else 0.0
                    daily.append({"orders": orders, "revenue": revenue})
                break
            except Exception as e:
                print(f"[SELLER] ❌ forecast: {e}")
                time.sleep(2)

        if len(daily) < 3:
            return {"ok": False, "reason": "мало данных для прогноза"}

        orders_series = [d["orders"] for d in daily]
        revenue_series = [d["revenue"] for d in daily]

        orders_forecast = self._linear_forecast_sum(orders_series, forecast_days)
        revenue_forecast = self._linear_forecast_sum(revenue_series, forecast_days)

        avg_orders = sum(orders_series) / len(orders_series)
        avg_revenue = sum(revenue_series) / len(revenue_series)

        return {
            "ok": True,
            "days_back": days_back,
            "forecast_days": forecast_days,
            "avg_orders": round(avg_orders, 1),
            "avg_revenue": round(avg_revenue, 2),
            "forecast_orders": round(orders_forecast),
            "forecast_revenue": round(revenue_forecast, 2),
        }

    @staticmethod
    def _linear_forecast_sum(values: list, forecast_days: int) -> float:
        n = len(values)
        if n < 2:
            return (values[-1] if values else 0) * forecast_days

        x = list(range(n))
        x_mean = sum(x) / n
        y_mean = sum(values) / n

        num = sum((x[i] - x_mean) * (values[i] - y_mean) for i in range(n))
        den = sum((x[i] - x_mean) ** 2 for i in range(n))
        if den == 0:
            return values[-1] * forecast_days

        slope = num / den
        intercept = y_mean - slope * x_mean

        total = 0.0
        for i in range(forecast_days):
            pred = intercept + slope * (n + i)
            total += max(0.0, pred)
        return total

    # ---------- Топ товаров ----------

    def get_top_products(self, period: str, limit: int = 5) -> list:
        date_from, date_to, _ = self._resolve_period(period)
        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": ["ordered_units", "revenue"],
            "dimension": ["sku"],
            "limit": 1000,
        }

        skus = []
        for attempt in range(1, 4):
            try:
                r = requests.post(
                    f"{self.BASE_URL}/v1/analytics/data",
                    headers=self.headers,
                    json=payload,
                    timeout=30,
                )
                if r.status_code == 429:
                    time.sleep(3)
                    continue
                if r.status_code != 200:
                    print(f"[SELLER] ⚠ analytics (top) {r.status_code}: {r.text[:200]}")
                    return []
                data = r.json()
                for item in data.get("result", {}).get("data", []):
                    dims = item.get("dimensions", [])
                    sku = None
                    if dims:
                        d0 = dims[0]
                        if isinstance(d0, dict):
                            sku = d0.get("id") or d0.get("value")
                        elif isinstance(d0, list) and d0:
                            sku = d0[0]
                        elif isinstance(d0, str):
                            sku = d0
                    if not sku:
                        continue
                    metrics = item.get("metrics", [])
                    orders = int(metrics[0]) if metrics and metrics[0] else 0
                    revenue = float(metrics[1]) if len(metrics) > 1 and metrics[1] else 0.0
                    skus.append({
                        "sku": str(sku),
                        "orders": orders,
                        "revenue": round(revenue, 2),
                    })
                break
            except Exception as e:
                print(f"[SELLER] ❌ top products: {e}")
                time.sleep(2)

        if not skus:
            return []

        skus.sort(key=lambda x: x["orders"], reverse=True)
        top = skus[:limit]

        print(f"[SELLER] top raw skus: {[t['sku'] for t in top]}")

        self._enrich_product_names(top)

        for t in top:
            print(f"[SELLER] top item: sku={t['sku']} name={t.get('name', '')[:60]!r}")

        return top

    def _enrich_product_names(self, items: list):
        if not items:
            return

        for it in items:
            if "name" not in it:
                it["name"] = None

        sku_values = []
        for it in items:
            s = str(it.get("sku", "")).strip()
            if s.isdigit():
                sku_values.append(int(s))

        if not sku_values:
            for it in items:
                it["name"] = it["name"] or f"SKU {it['sku']}"
            return

        # Попытка 1: v3/product/info/list с ключом sku
        try:
            r = requests.post(
                f"{self.BASE_URL}/v3/product/info/list",
                headers=self.headers,
                json={"sku": sku_values},
                timeout=30,
            )
            print(f"[SELLER] v3/product/info/list (sku) status={r.status_code}")
            if r.status_code == 200:
                by_sku = {}
                for entry in r.json().get("items", []):
                    s = entry.get("sku")
                    if s:
                        by_sku[str(s)] = entry.get("name")
                print(f"[SELLER] v3 (sku): имён {len(by_sku)}")
                for it in items:
                    name = by_sku.get(str(it["sku"]))
                    if name:
                        it["name"] = name
                if all(it.get("name") for it in items):
                    return
            else:
                print(f"[SELLER] ⚠ v3 (sku): {r.text[:200]}")
        except Exception as e:
            print(f"[SELLER] ❌ v3 (sku): {e}")

        # Попытка 2: product_id
        try:
            r = requests.post(
                f"{self.BASE_URL}/v3/product/info/list",
                headers=self.headers,
                json={"product_id": sku_values},
                timeout=30,
            )
            print(f"[SELLER] v3/product/info/list (product_id) status={r.status_code}")
            if r.status_code == 200:
                by_pid = {}
                for entry in r.json().get("items", []):
                    pid = entry.get("id") or entry.get("product_id")
                    sku = entry.get("sku")
                    name = entry.get("name")
                    if pid:
                        by_pid[str(pid)] = name
                    if sku:
                        by_pid[str(sku)] = name
                print(f"[SELLER] v3 (product_id): имён {len(by_pid)}")
                for it in items:
                    if it.get("name"):
                        continue
                    name = by_pid.get(str(it["sku"]))
                    if name:
                        it["name"] = name
                if all(it.get("name") for it in items):
                    return
            else:
                print(f"[SELLER] ⚠ v3 (product_id): {r.text[:200]}")
        except Exception as e:
            print(f"[SELLER] ❌ v3 (product_id): {e}")

        # Попытка 3: offer_id
        try:
            r = requests.post(
                f"{self.BASE_URL}/v3/product/info/list",
                headers=self.headers,
                json={"offer_id": [str(s) for s in sku_values]},
                timeout=30,
            )
            print(f"[SELLER] v3/product/info/list (offer_id) status={r.status_code}")
            if r.status_code == 200:
                by_offer = {}
                for entry in r.json().get("items", []):
                    offer = entry.get("offer_id")
                    name = entry.get("name")
                    if offer:
                        by_offer[str(offer)] = name
                print(f"[SELLER] v3 (offer_id): имён {len(by_offer)}")
                for it in items:
                    if it.get("name"):
                        continue
                    name = by_offer.get(str(it["sku"]))
                    if name:
                        it["name"] = name
            else:
                print(f"[SELLER] ⚠ v3 (offer_id): {r.text[:200]}")
        except Exception as e:
            print(f"[SELLER] ❌ v3 (offer_id): {e}")

        for it in items:
            if not it.get("name"):
                it["name"] = f"SKU {it['sku']}"

    # ---------- Остатки ----------

    def get_low_stocks(self, threshold: int = 5) -> list:
        result = []
        payload = {
            "filter": {"visibility": "ALL"},
            "limit": 1000,
        }

        items_raw = []
        try:
            r = requests.post(
                f"{self.BASE_URL}/v4/product/info/stocks",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            print(f"[SELLER] stocks status={r.status_code}")
            if r.status_code != 200:
                print(f"[SELLER] ⚠ stocks: {r.text[:200]}")
                return []
            data = r.json()
            items_raw = data.get("items", [])
            print(f"[SELLER] stocks: получено {len(items_raw)} товаров")
            if items_raw:
                print(f"[SELLER] stocks sample: {items_raw[0]}")
        except Exception as e:
            print(f"[SELLER] ❌ stocks error: {e}")
            return []

        candidates = []
        for item in items_raw:
            stocks = item.get("stocks", []) or []
            total_present = sum(int(s.get("present", 0) or 0) for s in stocks)
            if total_present < threshold:
                candidates.append({
                    "product_id": item.get("product_id"),
                    "offer_id": item.get("offer_id"),
                    "present": total_present,
                    "sku": None,
                    "name": None,
                })

        if not candidates:
            return []

        candidates.sort(key=lambda x: x["present"])
        to_enrich = candidates[:100]
        self._enrich_stock_names(to_enrich)

        for c in candidates:
            result.append({
                "sku": c.get("sku") or str(c.get("product_id") or "—"),
                "name": c.get("name") or f"product_id {c.get('product_id')}",
                "present": c["present"],
            })

        print(f"[SELLER] найдено {len(result)} товаров с остатком < {threshold}")
        return result

    def _enrich_stock_names(self, items: list):
        product_ids = [int(it["product_id"]) for it in items if it.get("product_id")]
        if not product_ids:
            return

        try:
            r = requests.post(
                f"{self.BASE_URL}/v3/product/info/list",
                headers=self.headers,
                json={"product_id": product_ids},
                timeout=30,
            )
            print(f"[SELLER] v3/product/info/list (stocks) status={r.status_code}")
            if r.status_code != 200:
                print(f"[SELLER] ⚠ v3 (stocks): {r.text[:200]}")
                return

            data = r.json()
            by_pid = {}
            for entry in data.get("items", []):
                pid = entry.get("id") or entry.get("product_id")
                if pid is None:
                    continue
                by_pid[int(pid)] = {
                    "name": entry.get("name"),
                    "sku": entry.get("sku"),
                }
            print(f"[SELLER] v3 (stocks): получено {len(by_pid)} записей")

            for it in items:
                pid = it.get("product_id")
                if pid is None:
                    continue
                info = by_pid.get(int(pid))
                if info:
                    it["name"] = info.get("name")
                    it["sku"] = info.get("sku")
        except Exception as e:
            print(f"[SELLER] ❌ v3 (stocks): {e}")

    # ---------- Баланс ----------

    def get_balance(self) -> dict:
        today = datetime.now().date()
        date_from = today.replace(day=1).strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")

        result = {
            "date_from": date_from,
            "date_to": date_to,
            "sales": 0.0,
            "fee": 0.0,
            "services": 0.0,
            "returns": 0.0,
            "accrued": 0.0,
        }
        try:
            r = requests.post(
                f"{self.BASE_URL}/v1/finance/balance",
                headers=self.headers,
                json={"date_from": date_from, "date_to": date_to},
                timeout=30,
            )
            print(f"[SELLER] balance status={r.status_code}")
            if r.status_code != 200:
                print(f"[SELLER] ⚠ balance: {r.text[:200]}")
                return result
            data = r.json()
            cashflows = data.get("cashflows", {})
            sales = cashflows.get("sales", {})
            services = cashflows.get("services", [])
            returns = cashflows.get("returns", {})
            total = data.get("total", {})

            result["sales"] = abs(float(sales.get("amount", {}).get("value") or 0))
            result["fee"] = abs(float(sales.get("fee", {}).get("value") or 0))
            result["services"] = sum(
                abs(float(s.get("amount", {}).get("value") or 0)) for s in services
            )
            result["returns"] = abs(float(returns.get("amount", {}).get("value") or 0))
            result["accrued"] = float(total.get("accrued", {}).get("value") or 0)
        except Exception as e:
            print(f"[SELLER] ❌ balance error: {e}")
        return result

    # ---------- Отчёт по одному товару ----------

    def get_product_report(self, sku: str, period: str = "week") -> dict:
        date_from, date_to, label = self._resolve_period(period)

        result = {
            "sku": sku,
            "name": None,
            "date_from": date_from,
            "date_to": date_to,
            "label": label,
            "orders": 0,
            "revenue": 0.0,
        }

        try:
            sku_int = int(sku)
            r = requests.post(
                f"{self.BASE_URL}/v3/product/info/list",
                headers=self.headers,
                json={"sku": [sku_int]},
                timeout=30,
            )
            if r.status_code == 200:
                items = r.json().get("items", [])
                if items:
                    result["name"] = items[0].get("name")
        except Exception as e:
            print(f"[SELLER] ⚠ name для sku={sku}: {e}")

        payload = {
            "date_from": date_from,
            "date_to": date_to,
            "metrics": ["ordered_units", "revenue"],
            "dimension": ["sku"],
            "limit": 1000,
        }
        try:
            r = requests.post(
                f"{self.BASE_URL}/v1/analytics/data",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            if r.status_code == 200:
                for item in r.json().get("result", {}).get("data", []):
                    dims = item.get("dimensions", [])
                    sku_val = None
                    if dims:
                        d0 = dims[0]
                        if isinstance(d0, dict):
                            sku_val = d0.get("id") or d0.get("value")
                        elif isinstance(d0, list) and d0:
                            sku_val = d0[0]
                        elif isinstance(d0, str):
                            sku_val = d0
                    if str(sku_val) != str(sku):
                        continue
                    metrics = item.get("metrics", [])
                    result["orders"] = int(metrics[0]) if metrics and metrics[0] else 0
                    result["revenue"] = float(metrics[1]) if len(metrics) > 1 and metrics[1] else 0.0
                    break
        except Exception as e:
            print(f"[SELLER] ❌ product report: {e}")

        return result

    # ---------- Отзывы (Premium Plus) ----------

    def get_reviews_summary(self, days: int = 7) -> dict:
        """Сводка отзывов за N дней. Требует Premium Plus."""
        result = {
            "ok": False,
            "reason": None,
            "total": 0,
            "avg_rating": 0.0,
            "good": [],
            "bad": [],
        }
        try:
            payload = {
                "limit": 100,
                "sort": {"sort_by": "created_at", "sort_dir": "DESC"},
            }
            r = requests.post(
                f"{self.BASE_URL}/v1/review/list",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            print(f"[SELLER] review/list status={r.status_code}")
            if r.status_code == 403:
                result["reason"] = "нет подписки Premium Plus"
                return result
            if r.status_code != 200:
                result["reason"] = f"HTTP {r.status_code}: {r.text[:200]}"
                return result

            data = r.json()
            reviews = data.get("reviews", [])
            ratings = []
            cutoff = time.time() - days * 86400

            for rev in reviews:
                created = rev.get("created_at")
                if created:
                    try:
                        t = datetime.fromisoformat(
                            created.replace("Z", "+00:00")
                        ).timestamp()
                        if t < cutoff:
                            continue
                    except Exception:
                        pass

                rating = int(rev.get("rating", 0))
                if rating:
                    ratings.append(rating)

                text = (rev.get("text") or "").strip()
                if len(text) < 10:
                    continue

                item = {
                    "rating": rating,
                    "text": text[:300],
                    "product_id": rev.get("product_id") or rev.get("sku"),
                }

                if rating >= 4 and len(result["good"]) < 3:
                    result["good"].append(item)
                elif rating <= 3 and len(result["bad"]) < 3:
                    result["bad"].append(item)

            result["total"] = len(ratings)
            result["avg_rating"] = (
                round(sum(ratings) / len(ratings), 2) if ratings else 0.0
            )
            result["ok"] = True
        except Exception as e:
            print(f"[SELLER] ❌ review/list: {e}")
            result["reason"] = str(e)
        return result

    # ---------- Вопросы покупателей (Premium Plus) ----------

    def get_questions(self, only_new: bool = True) -> dict:
        result = {"ok": False, "reason": None, "items": []}
        try:
            payload = {"limit": 100}
            r = requests.post(
                f"{self.BASE_URL}/v1/question/list",
                headers=self.headers,
                json=payload,
                timeout=30,
            )
            print(f"[SELLER] question/list status={r.status_code}")
            if r.status_code == 403:
                result["reason"] = "нет подписки Premium Plus"
                return result
            if r.status_code != 200:
                result["reason"] = f"HTTP {r.status_code}: {r.text[:200]}"
                return result

            data = r.json()
            for q in data.get("questions", []):
                status = (q.get("status") or "").lower()
                if only_new and status and status != "new":
                    continue
                text = (q.get("text") or "").strip()
                if not text:
                    continue
                result["items"].append({
                    "text": text[:300],
                    "sku": q.get("product_id") or q.get("sku"),
                    "created": q.get("created_at"),
                })
                if len(result["items"]) >= 20:
                    break
            result["ok"] = True
        except Exception as e:
            print(f"[SELLER] ❌ question/list: {e}")
            result["reason"] = str(e)
        return result

    # ---------- Акции Ozon ----------

    def get_actions(self) -> dict:
        result = {"ok": False, "reason": None, "items": []}
        try:
            r = requests.get(
                f"{self.BASE_URL}/v1/actions",
                headers=self.headers,
                timeout=30,
            )
            print(f"[SELLER] actions status={r.status_code}")
            if r.status_code != 200:
                r = requests.get(
                    f"{self.BASE_URL}/v2/actions",
                    headers=self.headers,
                    timeout=30,
                )
                print(f"[SELLER] actions v2 status={r.status_code}")
            if r.status_code != 200:
                result["reason"] = f"HTTP {r.status_code}: {r.text[:200]}"
                return result

            data = r.json()
            actions = data.get("result") or data.get("actions") or []
            for a in actions:
                if isinstance(a, dict):
                    result["items"].append({
                        "title": a.get("title") or a.get("name") or "—",
                        "date_from": a.get("date_start") or a.get("date_from"),
                        "date_to": a.get("date_end") or a.get("date_to"),
                        "potential": a.get("potential_products_count"),
                    })
            result["ok"] = True
        except Exception as e:
            print(f"[SELLER] ❌ actions: {e}")
            result["reason"] = str(e)
        return result