import io
import os
import time
import requests as req
import vk_api
from config import VK_TOKEN, VK_USER_TOKEN, VK_GROUP_ID


class VKPublisher:
    """Публикует посты в группе и грузит фото в сообщения.
    Использует разные эндпоинты ВК для разных задач:
    - getWallUploadServer/saveWallPhoto — для постов на стене;
    - getMessagesUploadServer/saveMessagesPhoto — для вложений в личные сообщения.
    Загрузка идёт напрямую через requests, минуя vk_api.upload."""

    MAX_UPLOAD_RETRIES = 6
    INITIAL_DELAY = 3
    RETRY_DELAYS = [5, 8, 12, 15, 20]

    def __init__(self):
        self.vk_session = vk_api.VkApi(token=VK_TOKEN)
        self.vk = self.vk_session.get_api()

        self.upload_ok = False
        self.user_vk = None

        if not VK_USER_TOKEN:
            print("[VK] ⚠ VK_USER_TOKEN не задан — фото грузиться не будут")
            return

        try:
            self.user_session = vk_api.VkApi(token=VK_USER_TOKEN)
            self.user_vk = self.user_session.get_api()
            self.user_vk.users.get()
            self.upload_ok = True
            print("[VK] ✅ Пользовательский токен рабочий, фото будут грузиться")
        except Exception as e:
            print(f"[VK] ⚠ VK_USER_TOKEN невалиден: {e}")
            print("[VK] Пост будет без фото. Получи новый токен: https://vkhost.github.io/")

    # ---------- Публикация на стене группы ----------

    def publish_post(self, text: str, image_urls: list = None) -> int:
        attachments = []

        if image_urls and self.upload_ok:
            for url in image_urls[:5]:
                att = self.upload_photo(url)
                if att:
                    attachments.append(att)

        return self.publish_post_with_attachments(text, attachments)

    def publish_post_with_attachments(self, text: str, attachments: list) -> int:
        response = self.vk.wall.post(
            owner_id=f"-{VK_GROUP_ID}",
            message=text,
            attachments=",".join(attachments) if attachments else None,
            from_group=1,
        )
        return response["post_id"]

    # ---------- Загрузка фото из URL (для стены) ----------

    def upload_photo(self, url: str) -> str:
        if not self.upload_ok:
            return None
        try:
            photo = self._upload_photo_from_url(url)
            if photo and photo.get("id") and photo.get("owner_id"):
                print(f"[VK] ✅ Фото загружено: {url[:80]}")
                return f"photo{photo['owner_id']}_{photo['id']}"
        except Exception as e:
            print(f"[VK] ❌ Не удалось загрузить фото {url[:80]}: {e}")
        return None

    def _download_image(self, url: str) -> bytes:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://www.ozon.ru/",
            "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        }

        resp = req.get(url, timeout=30, headers=headers, allow_redirects=True)
        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code} при скачивании фото")

        content_type = resp.headers.get("Content-Type", "").lower()
        size = len(resp.content)
        print(f"[VK] Скачано: {size} байт, Content-Type: {content_type}")

        if size < 1000:
            raise RuntimeError(f"Файл слишком маленький ({size} байт)")

        head = resp.content[:12]
        is_jpeg = head.startswith(b"\xff\xd8\xff")
        is_png = head.startswith(b"\x89PNG")
        is_gif = head.startswith(b"GIF8")
        is_webp = head[:4] == b"RIFF" and head[8:12] == b"WEBP"
        if not (is_jpeg or is_png or is_gif or is_webp):
            raise RuntimeError(f"Скачанный файл не является изображением")

        return resp.content

    def _detect_format(self, content: bytes) -> tuple:
        head = content[:12]
        if head.startswith(b"\xff\xd8\xff"):
            return "image.jpg", "image/jpeg"
        if head.startswith(b"\x89PNG"):
            return "image.png", "image/png"
        if head.startswith(b"GIF8"):
            return "image.gif", "image/gif"
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return "image.webp", "image/webp"
        return "image.jpg", "image/jpeg"

    def _upload_photo_from_url(self, url: str) -> dict:
        content = self._download_image(url)
        filename, mime = self._detect_format(content)
        return self._upload_content(content, filename, mime)

    # ---------- Загрузка фото из файла — для ПОСТОВ (стена) ----------

    def upload_photo_from_file(self, file_path: str) -> str:
        """Загружает локальный файл для поста на стене группы."""
        if not self.upload_ok:
            return None

        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except Exception as e:
            print(f"[VK] ❌ Не удалось прочитать файл {file_path}: {e}")
            return None

        filename = os.path.basename(file_path)
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")

        try:
            photo = self._upload_content(content, filename, mime)
        except Exception as e:
            print(f"[VK] ❌ Не удалось загрузить файл {file_path}: {e}")
            return None

        if not photo:
            print(f"[VK] ❌ Пустой ответ при загрузке файла {file_path}")
            return None

        owner_id = photo.get("owner_id")
        photo_id = photo.get("id")

        print(f"[VK] upload ответ (wall): owner_id={owner_id}, id={photo_id}")

        if not owner_id or not photo_id:
            return None

        att_str = f"photo{owner_id}_{photo_id}"
        print(f"[VK] ✅ График загружен (wall), attachment={att_str}")
        return att_str

    # ---------- Загрузка фото из файла — для СООБЩЕНИЙ ----------

    def upload_photo_from_file_for_messages(self, file_path: str) -> str:
        """Загружает локальный файл для вложения в личное сообщение.
        Использует getMessagesUploadServer + saveMessagesPhoto."""
        if not self.upload_ok:
            return None

        try:
            with open(file_path, "rb") as f:
                content = f.read()
        except Exception as e:
            print(f"[VK] ❌ Не удалось прочитать файл {file_path}: {e}")
            return None

        filename = os.path.basename(file_path)
        ext = filename.rsplit(".", 1)[-1].lower()
        mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}.get(ext, "image/png")

        try:
            photo = self._upload_content_for_messages(content, filename, mime)
        except Exception as e:
            print(f"[VK] ❌ Не удалось загрузить файл (messages) {file_path}: {e}")
            return None

        if not photo:
            print(f"[VK] ❌ Пустой ответ при загрузке файла (messages) {file_path}")
            return None

        owner_id = photo.get("owner_id")
        photo_id = photo.get("id")

        print(f"[VK] upload ответ (messages): owner_id={owner_id}, id={photo_id}")

        if not owner_id or not photo_id:
            print(f"[VK] ❌ ВК не вернул owner_id или id: {photo}")
            return None

        att_str = f"photo{owner_id}_{photo_id}"
        print(f"[VK] ✅ График загружен (messages), attachment={att_str}")
        return att_str

    # ---------- Общая логика загрузки в ВК — стенa ----------

    def _upload_content(self, content: bytes, filename: str, mime: str) -> dict:
        try:
            upload_server = self.user_vk.photos.getWallUploadServer(group_id=VK_GROUP_ID)
            upload_url = upload_server["upload_url"]
        except Exception as e:
            print(f"[VK] ❌ Не удалось получить upload_url: {e}")
            raise

        print(f"[VK] Прогрев {self.INITIAL_DELAY} сек...")
        time.sleep(self.INITIAL_DELAY)

        last_error = None

        for attempt in range(1, self.MAX_UPLOAD_RETRIES + 1):
            print(f"[VK] Попытка загрузки {attempt}/{self.MAX_UPLOAD_RETRIES}...")

            if attempt > 1:
                try:
                    upload_server = self.user_vk.photos.getWallUploadServer(group_id=VK_GROUP_ID)
                    upload_url = upload_server["upload_url"]
                except Exception as e:
                    print(f"[VK] ⚠ Не удалось обновить upload_url: {e}")

            try:
                files = {"file1": (filename, content, mime)}
                upload_resp = req.post(upload_url, files=files, timeout=60)
                upload_resp.raise_for_status()
                upload_data = upload_resp.json()
            except Exception as e:
                print(f"[VK] ❌ Ошибка сети при загрузке: {e}")
                last_error = e
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                print(f"[VK] Пауза {delay} сек...")
                time.sleep(delay)
                continue

            if not upload_data.get("photo") and "files" not in upload_data:
                print(f"[VK] ⚠ ВК вернул пустое photo: {upload_data}")
                last_error = RuntimeError(f"ВК не принял файл: {upload_data}")
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                print(f"[VK] Пауза {delay} сек...")
                time.sleep(delay)
                continue

            try:
                if "files" in upload_data:
                    photo_data = list(upload_data["files"].values())[0]
                    photo_param = photo_data.get("id", photo_data.get("sha", ""))
                    save_resp = self.user_vk.photos.saveWallPhoto(
                        group_id=VK_GROUP_ID,
                        photo=photo_param,
                        server=upload_data["server"],
                        hash=upload_data["hash"],
                    )
                else:
                    save_resp = self.user_vk.photos.saveWallPhoto(
                        group_id=VK_GROUP_ID,
                        photo=upload_data["photo"],
                        server=upload_data["server"],
                        hash=upload_data["hash"],
                    )
            except Exception as e:
                print(f"[VK] ❌ Ошибка при saveWallPhoto: {e}")
                last_error = e
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                time.sleep(delay)
                continue

            if not save_resp or not save_resp[0].get("id"):
                print(f"[VK] ⚠ ВК не вернул ID фото: {save_resp}")
                last_error = RuntimeError(f"ВК не вернул ID фото: {save_resp}")
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                time.sleep(delay)
                continue

            print(f"[VK] ✅ Принято с попытки {attempt}")
            return save_resp[0]

        raise RuntimeError(
            f"Загрузка файла не удалась после {self.MAX_UPLOAD_RETRIES} попыток: {last_error}"
        )

    # ---------- Общая логика загрузки в ВК — сообщения ----------

    def _upload_content_for_messages(self, content: bytes, filename: str, mime: str) -> dict:
        """Загружает фото через getMessagesUploadServer — для вложений в сообщения."""
        try:
            upload_server = self.user_vk.photos.getMessagesUploadServer()
            upload_url = upload_server["upload_url"]
        except Exception as e:
            print(f"[VK] ❌ Не удалось получить upload_url (messages): {e}")
            raise

        print(f"[VK] Прогрев {self.INITIAL_DELAY} сек (messages)...")
        time.sleep(self.INITIAL_DELAY)

        last_error = None

        for attempt in range(1, self.MAX_UPLOAD_RETRIES + 1):
            print(f"[VK] Попытка загрузки (messages) {attempt}/{self.MAX_UPLOAD_RETRIES}...")

            if attempt > 1:
                try:
                    upload_server = self.user_vk.photos.getMessagesUploadServer()
                    upload_url = upload_server["upload_url"]
                except Exception as e:
                    print(f"[VK] ⚠ Не удалось обновить upload_url (messages): {e}")

            try:
                files = {"file1": (filename, content, mime)}
                upload_resp = req.post(upload_url, files=files, timeout=60)
                upload_resp.raise_for_status()
                upload_data = upload_resp.json()
            except Exception as e:
                print(f"[VK] ❌ Ошибка сети (messages): {e}")
                last_error = e
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                print(f"[VK] Пауза {delay} сек...")
                time.sleep(delay)
                continue

            if not upload_data.get("photo") and "files" not in upload_data:
                print(f"[VK] ⚠ Пустой photo (messages): {upload_data}")
                last_error = RuntimeError(f"ВК не принял файл: {upload_data}")
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                print(f"[VK] Пауза {delay} сек...")
                time.sleep(delay)
                continue

            try:
                if "files" in upload_data:
                    photo_data = list(upload_data["files"].values())[0]
                    photo_param = photo_data.get("id", photo_data.get("sha", ""))
                    save_resp = self.user_vk.photos.saveMessagesPhoto(
                        photo=photo_param,
                        server=upload_data["server"],
                        hash=upload_data["hash"],
                    )
                else:
                    save_resp = self.user_vk.photos.saveMessagesPhoto(
                        photo=upload_data["photo"],
                        server=upload_data["server"],
                        hash=upload_data["hash"],
                    )
            except Exception as e:
                print(f"[VK] ❌ Ошибка при saveMessagesPhoto: {e}")
                last_error = e
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                time.sleep(delay)
                continue

            if not save_resp or not save_resp[0].get("id"):
                print(f"[VK] ⚠ ВК не вернул ID фото (messages): {save_resp}")
                last_error = RuntimeError(f"ВК не вернул ID фото: {save_resp}")
                delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                time.sleep(delay)
                continue

            print(f"[VK] ✅ Принято с попытки {attempt} (messages)")
            return save_resp[0]

        raise RuntimeError(
            f"Загрузка файла (messages) не удалась после {self.MAX_UPLOAD_RETRIES} попыток: {last_error}"
        )