import os
import sys
import time
import threading

_vk = None
_peer_id = None
_buffer = []
_lock = threading.Lock()
_running = False

LOG_DIR = "logs"
LOG_FILE = os.path.join(LOG_DIR, "bot.log")
os.makedirs(LOG_DIR, exist_ok=True)

_SKIP_PREFIXES = (
    "DevTools listening on",
    "ChromeDriver was started",
    "[WDM]",
    "Starting ChromeDriver",
    "Only local connections are allowed",
)

LOG_SEPARATOR_TOP = "-----------!ЛОГИ!-------------"
LOG_SEPARATOR_BOTTOM = "--------------------------------"


class _TeeStream:
    def __init__(self, original):
        self._original = original

    def write(self, text):
        if not text:
            return 0
        try:
            self._original.write(text)
            self._original.flush()
        except Exception:
            pass

        line = text.rstrip("\n")
        if not line.strip():
            return len(text)
        if line.startswith(_SKIP_PREFIXES):
            return len(text)

        # Пишем в файл
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

        with _lock:
            _buffer.append(line)
            if len(_buffer) > 200:
                del _buffer[:100]
        return len(text)

    def flush(self):
        try:
            self._original.flush()
        except Exception:
            pass

    def isatty(self):
        return False


def install(vk_api, default_peer_id: int):
    global _vk, _peer_id, _running
    _vk = vk_api
    _peer_id = default_peer_id

    if not _running:
        sys.stdout = _TeeStream(sys.__stdout__)
        sys.stderr = _TeeStream(sys.__stderr__)
        _running = True
        threading.Thread(target=_sender_loop, daemon=True).start()
        _raw("[NOTIFIER] Перехват консоли включён")


def set_peer(peer_id: int):
    global _peer_id
    _peer_id = peer_id


def flush_now():
    _flush_buffer()


def read_last_lines(n: int = 30) -> str:
    """Возвращает последние N строк логов из файла."""
    try:
        if not os.path.exists(LOG_FILE):
            return "(лог пуст)"
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        last = lines[-n:] if len(lines) > n else lines
        return "".join(last).strip()
    except Exception as e:
        return f"(ошибка чтения лога: {e})"


def _raw(text: str):
    try:
        sys.__stdout__.write(text + "\n")
        sys.__stdout__.flush()
    except Exception:
        pass


def _sender_loop():
    while True:
        time.sleep(2)
        _flush_buffer()


def _flush_buffer():
    global _buffer
    with _lock:
        if not _buffer:
            return
        lines = _buffer[:]
        _buffer = []

    target = _peer_id
    if not target or not _vk:
        return

    body = "\n".join(lines)
    text = f"{LOG_SEPARATOR_TOP}\n{body}\n{LOG_SEPARATOR_BOTTOM}"

    if len(text) > 4000:
        text = text[:3990] + "\n...(обрезано)"

    try:
        _vk.messages.send(
            peer_id=target,
            message=text,
            random_id=int(time.time() * 1000) % (2 ** 31),
            disable_mentions=1,
        )
    except Exception as e:
        _raw(f"[NOTIFIER] send error: {e}")