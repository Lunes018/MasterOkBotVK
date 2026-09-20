import re
import json
import time
import os
import threading
import undetected_chromedriver as uc
from bs4 import BeautifulSoup


DEBUG_DIR = "debug"


class CustomChrome(uc.Chrome):
    def quit(self):
        try:
            super().quit()
        except OSError:
            pass
        except Exception as e:
            print(f"[PARSER] Chrome.quit() предупреждение: {e}")

    def __del__(self):
        try:
            if hasattr(self, "service") and self.service and self.service.process:
                try:
                    self.service.process.kill()
                except Exception:
                    pass
            self.quit()
        except Exception:
            pass


class OzonParser:
    def __init__(self):
        self.driver = None
        self.version_main = None
        self._lock = threading.Lock()  # защита от параллельного парсинга
        os.makedirs(DEBUG_DIR, exist_ok=True)

    # ---------- Управление браузером ----------

    def _create_driver(self):
        options = uc.ChromeOptions()
        # options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-blink-features=AutomationControlled")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-first-run")
        options.add_argument("--no-default-browser-check")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=ru-RU")

        common_kwargs = dict(
            options=options,
            use_subprocess=True,
            version_main=self.version_main,
        )

        try:
            return CustomChrome(**common_kwargs)
        except Exception as e:
            print(f"[PARSER] Chrome без version_main: {e}")
            common_kwargs.pop("version_main", None)
            return CustomChrome(**common_kwargs)

    def _ensure_driver(self):
        if self.driver is None:
            self.driver = self._create_driver()
            return self.driver
        try:
            _ = self.driver.current_url
            return self.driver
        except Exception as e:
            print(f"[PARSER] Драйвер мёртв ({e}), пересоздаю...")
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = self._create_driver()
            return self.driver

    def _restart_driver(self):
        try:
            if self.driver is not None:
                self.driver.quit()
        except Exception:
            pass
        self.driver = self._create_driver()

    # ---------- Публичный парсинг (с блокировкой) ----------

    def parse_product(self, url: str) -> dict:
        with self._lock:
            return self._parse_locked(url)

    # ---------- Основной парсинг ----------

    def _parse_locked(self, url: str) -> dict:
        driver = self._ensure_driver()

        try:
            driver.get(url)
        except Exception as e:
            print(f"[PARSER] get() упал: {e}. Перезапускаю Chrome.")
            self._restart_driver()
            driver = self.driver
            driver.get(url)

        for _ in range(40):
            try:
                if driver.find_elements("tag name", "h1"):
                    break
            except Exception:
                self._restart_driver()
                driver = self.driver
                driver.get(url)
            time.sleep(0.5)

        time.sleep(4)

        try:
            for y in (400, 800, 1200, 1600, 2000, 2400):
                driver.execute_script(f"window.scrollTo(0, {y});")
                time.sleep(0.7)
            driver.execute_script("window.scrollTo(0, 0);")
            time.sleep(1.5)
        except Exception as e:
            print(f"[PARSER] scroll упал: {e}")

        try:
            html = driver.page_source
            current_url = driver.current_url
            page_title = driver.title
        except Exception as e:
            print(f"[PARSER] Не удалось получить page_source: {e}")
            self._restart_driver()
            raise

        with open(f"{DEBUG_DIR}/page.html", "w", encoding="utf-8") as f:
            f.write(html)
        try:
            driver.save_screenshot(f"{DEBUG_DIR}/page.png")
        except Exception:
            pass

        print(f"[PARSER] URL: {current_url}")
        print(f"[PARSER] HTML size: {len(html)}")

        soup = BeautifulSoup(html, "html.parser")

        h1_found = bool(soup.find("h1"))
        if not h1_found:
            lower = html.lower()
            for marker in ("доступ ограничен", "captcha",
                           "слишком много запросов", "подтвердите, что вы"):
                if marker in lower:
                    print(f"[PARSER] ⚠ Похоже на блокировку: '{marker}'")
                    break

        result = {
            "title": None, "price": None, "old_price": None,
            "description": None, "images": [], "url": url,
        }

        self._parse_jsonld(soup, result)
        if not result["title"]:
            h1 = soup.find("h1")
            if h1:
                result["title"] = h1.get_text(strip=True)
        self._parse_inline_json(html, result)
        if not result["price"]:
            self._parse_price_dom(soup, result)
        if not result["description"]:
            self._parse_desc_dom(soup, result)

        self._parse_images_from_jsonld(soup, result)
        self._parse_images_from_meta(soup, result)
        self._parse_images_from_dom(soup, result)
        self._parse_images_from_html(html, result)

        seen = set()
        unique = []
        for u in result["images"]:
            if u not in seen:
                seen.add(u)
                unique.append(u)
            if len(unique) >= 5:
                break
        result["images"] = unique

        result["title"] = result["title"] or "Товар с Ozon"
        result["price"] = result["price"] or "Цена не указана"
        result["description"] = result["description"] or "Описание отсутствует."

        print(f"[PARSER] RESULT: title={result['title'][:50]!r} "
              f"price={result['price']!r} images={len(result['images'])}")
        return result

    # ---------- Текст и цены ----------

    def _parse_jsonld(self, soup, result):
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict) or item.get("@type") != "Product":
                    continue
                if item.get("name") and not result["title"]:
                    result["title"] = item["name"]
                if item.get("description") and not result["description"]:
                    result["description"] = item["description"][:800]
                offers = item.get("offers")
                if isinstance(offers, dict) and offers.get("price"):
                    try:
                        p = int(float(offers["price"]))
                        result["price"] = f"{p:,} ₽".replace(",", " ")
                    except Exception:
                        pass

    def _parse_inline_json(self, html, result):
        if not result["price"]:
            m = re.search(r'"cardPrice"\s*:\s*"(\d+)"', html) or \
                re.search(r'"price"\s*:\s*"(\d+)"', html)
            if m:
                result["price"] = f"{int(m.group(1)):,} ₽".replace(",", " ")
        if not result["old_price"]:
            o = re.search(r'"originalPrice"\s*:\s*"(\d+)"', html)
            if o:
                result["old_price"] = f"{int(o.group(1)):,} ₽".replace(",", " ")
        if not result["description"]:
            d = re.search(r'"description"\s*:\s*"([^"]{40,3000})"', html)
            if d:
                desc = d.group(1)
                try:
                    desc = desc.encode().decode("unicode_escape", errors="ignore")
                except Exception:
                    pass
                result["description"] = desc[:800]

    def _parse_price_dom(self, soup, result):
        for widget in ("webPrice", "webSale", "webOutOfStock"):
            block = soup.find("div", {"data-widget": widget})
            if block:
                nums = re.findall(r"\d[\d\s]*", block.get_text(" ", strip=True))
                if nums:
                    result["price"] = f"{nums[0].strip()} ₽"
                    return

    def _parse_desc_dom(self, soup, result):
        block = soup.find("div", {"data-widget": "webDescription"})
        if block:
            text = block.get_text(separator="\n", strip=True)
            if text:
                result["description"] = text[:800]
                return
        for tag in soup.find_all(["div", "section"]):
            text = tag.get_text(strip=True)
            if 200 < len(text) < 2000:
                result["description"] = text[:800]
                return

    # ---------- Фото ----------

    def _is_ozon_image(self, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        if not url.startswith("http"):
            return False
        domains = ("ozone.ru", "ozon.ru", "ozon-st.cdn.ozon.ru",
                   "ir.ozone.ru", "ir-1.ozone.ru", "ir-2.ozone.ru",
                   "ir-3.ozone.ru", "ir-4.ozone.ru", "ir-5.ozone.ru",
                   "ir-6.ozone.ru", "ir-7.ozone.ru")
        return any(d in url for d in domains)

    def _clean_image_url(self, url: str) -> str:
        return url.split("?")[0] if url else url

    def _looks_like_thumbnail(self, url: str) -> bool:
        return bool(re.search(r'/w?c{1,2}\d+/', url))

    def _parse_images_from_jsonld(self, soup, result):
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "{}")
            except Exception:
                continue
            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                imgs = item.get("image")
                if not imgs:
                    continue
                if isinstance(imgs, str):
                    imgs = [imgs]
                for u in imgs:
                    if isinstance(u, str):
                        cu = self._clean_image_url(u)
                        if self._looks_like_thumbnail(cu):
                            continue
                        if cu and cu not in result["images"]:
                            result["images"].append(cu)

    def _parse_images_from_meta(self, soup, result):
        for prop in ("og:image", "og:image:secure_url", "twitter:image"):
            tag = soup.find("meta", {"property": prop}) or \
                  soup.find("meta", {"name": prop})
            if tag and tag.get("content"):
                cu = self._clean_image_url(tag["content"])
                if self._looks_like_thumbnail(cu):
                    continue
                if cu and cu not in result["images"]:
                    result["images"].append(cu)

    def _parse_images_from_dom(self, soup, result):
        gallery = soup.find("div", {"data-widget": "webGallery"})
        candidates = []
        if gallery:
            candidates.extend(gallery.find_all("img"))
            candidates.extend(gallery.find_all("source"))
        candidates.extend(soup.find_all("img"))

        for tag in candidates:
            for attr in ("src", "data-src", "data-original", "srcset"):
                val = tag.get(attr) if hasattr(tag, "get") else None
                if not val:
                    continue
                parts = [v.strip().split(" ")[0] for v in val.split(",")]
                for u in parts:
                    if not self._is_ozon_image(u):
                        continue
                    cu = self._clean_image_url(u)
                    if self._looks_like_thumbnail(cu):
                        continue
                    if cu and cu not in result["images"]:
                        result["images"].append(cu)
                    if len(result["images"]) >= 20:
                        return

    def _parse_images_from_html(self, html: str, result):
        patterns = [
            r'"(https://[a-z0-9\-]*\.?ozone\.ru/[^"\\]+?\.(?:jpg|jpeg|png|webp))',
            r'"(https://[a-z0-9\-]*\.?ozon\.ru/[^"\\]+?\.(?:jpg|jpeg|png|webp))',
            r'"(https://ozon-st\.cdn\.ozon\.ru/[^"\\]+?\.(?:jpg|jpeg|png|webp))',
        ]
        for pattern in patterns:
            for m in re.findall(pattern, html):
                cu = self._clean_image_url(m)
                if self._looks_like_thumbnail(cu):
                    continue
                if cu and cu not in result["images"]:
                    result["images"].append(cu)
                if len(result["images"]) >= 20:
                    return

    # ---------- Закрытие ----------

    def close(self):
        try:
            if self.driver is not None:
                self.driver.quit()
        except Exception:
            pass
        self.driver = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass