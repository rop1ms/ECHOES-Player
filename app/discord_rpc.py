# discord_rpc.py
"""
Discord Rich Presence — показывает в профиле пользователя в Discord,
что он сейчас слушает в Neon Player (название трека, исполнитель,
прогресс воспроизведения), примерно как встроенная интеграция Spotify.

Требует пакет `pypresence` (pip install pypresence) и запущенный
десктопный клиент Discord на этом же компьютере — RPC работает только
через локальный IPC-сокет Discord, слушать "как будто это я" удалённо
нельзя, только с той же машины, где открыт Discord.

Работает мягко, если что-то не так:
  * pypresence не установлен → presence тихо отключается, плеер работает как обычно
  * Discord не запущен / не отвечает → фоновый поток раз в RETRY_SEC пытается
    переподключиться, ничего не блокируя и не падая
  * ошибки при отправке presence проглатываются с логом в консоль

Нужен свой Client ID приложения Discord (бесплатно, за 2 минуты):
  1. https://discord.com/developers/applications → New Application
  2. Название — как хотите, оно будет отображаться под статусом ("via <имя>")
  3. Скопируйте "Application ID" — это и есть CLIENT_ID
  4. (опционально) Rich Presence → Art Assets — загрузите картинку, например
     "vinyl", и впишите её ключ в DEFAULT_LARGE_IMAGE ниже — тогда вместо
     пустой иконки всегда будет крутящаяся пластинка/лого плеера.

Обложка трека: Discord Rich Presence НЕ может показывать произвольный
локальный файл как картинку — только "assets", заранее загруженные в
Developer Portal, либо публичный https-URL картинки. Поэтому:
  * по умолчанию используется статичная large_image (лого/пластинка из
    Art Assets вашего приложения) — надёжно работает офлайн
  * если у трека есть публичный URL обложки (например, вы храните его
    в теге/библиотеке при скачивании с сервиса) — можно передать его
    напрямую, Discord подхватит и покажет именно её (см. cover_url в
    update_track)
"""

import os
import threading
import time

try:
    from pypresence import Presence
    from pypresence.exceptions import PyPresenceException
    PYPRESENCE_AVAILABLE = True
except ImportError:
    Presence = None
    PyPresenceException = Exception
    PYPRESENCE_AVAILABLE = False

# ── Настройка ──────────────────────────────────────────────────────────
# Впишите сюда свой Client ID из Discord Developer Portal (см. докстринг выше).
CLIENT_ID = os.environ.get("ECHOES_DISCORD_CLIENT_ID", "")

# Ключ ассета (загруженной картинки) в вашем приложении, показывается
# как большая иконка, если у трека нет собственного публичного cover_url.
DEFAULT_LARGE_IMAGE = "vinyl"
DEFAULT_LARGE_TEXT = "Neon Player"

RETRY_SEC = 15          # пауза между попытками переподключения к Discord
UPDATE_MIN_INTERVAL = 15  # Discord RPC не любит частые update() — троттлим


class DiscordPresence:
    """
    Использование в main.py:

        self.discord_rpc = DiscordPresence(enabled=self.settings.get("discord_rpc", True))
        self.discord_rpc.start()
        ...
        # при смене трека / паузе / плей:
        self.discord_rpc.update_track(title, artist, duration, position, is_playing)
        ...
        # при остановке/выключении трека:
        self.discord_rpc.clear()
        ...
        # при закрытии окна:
        self.discord_rpc.stop()
    """

    def __init__(self, client_id: str = CLIENT_ID, enabled: bool = True):
        self.client_id = client_id
        self._enabled = bool(enabled) and PYPRESENCE_AVAILABLE and bool(client_id) and client_id.strip("0") != ""
        self._rpc = None
        self._connected = False
        self._lock = threading.Lock()
        self._stop_flag = False
        self._thread = None
        self._last_update_ts = 0.0
        self._pending_state = None  # последний presence, который нужно отправить

        if enabled and PYPRESENCE_AVAILABLE and (not client_id or client_id.strip("0") == ""):
            print("[discord_rpc] CLIENT_ID не задан — впишите свой Application ID из "
                  "Discord Developer Portal в discord_rpc.py, чтобы включить Rich Presence.")
        if enabled and not PYPRESENCE_AVAILABLE:
            print("[discord_rpc] Пакет 'pypresence' не установлен — "
                  "выполните: pip install pypresence")

    # ------------------------------------------------------------------ #
    #  Публичный API                                                      #
    # ------------------------------------------------------------------ #

    def is_available(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool):
        value = bool(value)
        if value == self._enabled:
            return
        self._enabled = value and PYPRESENCE_AVAILABLE and bool(self.client_id)
        if self._enabled:
            self.start()
        else:
            self.clear()
            self.stop()

    def start(self):
        if not self._enabled or self._thread is not None:
            return
        self._stop_flag = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_flag = True
        with self._lock:
            self._disconnect()
        self._thread = None

    def update_track(self, title: str, artist: str, duration: float,
                      position: float, is_playing: bool, cover_url: str = ""):
        """Ставит трек в очередь на публикацию в Discord (троттлится фоновым потоком)."""
        if not self._enabled:
            return
        now = time.time()
        start_ts = int(now - max(0.0, position))
        end_ts = int(start_ts + duration) if duration and duration > 0 else None

        payload = {
            "details": (title or "Неизвестный трек")[:128],
            "state": f"— {artist}"[:128] if artist else "Neon Player",
            "large_image": cover_url or DEFAULT_LARGE_IMAGE,
            "large_text": (f"{artist} — {title}" if artist else (title or DEFAULT_LARGE_TEXT))[:128],
            "small_image": "play" if is_playing else "pause",
            "small_text": "Слушает" if is_playing else "Пауза",
        }
        if is_playing:
            payload["start"] = start_ts
            if end_ts:
                payload["end"] = end_ts
        # Без start/end при паузе — иначе Discord показывает "тикающий" таймер,
        # хотя на самом деле трек стоит.

        with self._lock:
            self._pending_state = ("update", payload)

    def clear(self):
        """Убирает activity из профиля (когда ничего не играет)."""
        if not self._enabled:
            return
        with self._lock:
            self._pending_state = ("clear", None)

    # ------------------------------------------------------------------ #
    #  Внутреннее: один поток на всё подключение + троттлинг отправки     #
    # ------------------------------------------------------------------ #

    def _connect(self) -> bool:
        try:
            self._rpc = Presence(self.client_id)
            self._rpc.connect()
            self._connected = True
            print("[discord_rpc] Подключено к Discord.")
            return True
        except Exception as e:
            self._connected = False
            self._rpc = None
            return False

    def _disconnect(self):
        if self._rpc is not None:
            try:
                self._rpc.clear()
            except Exception:
                pass
            try:
                self._rpc.close()
            except Exception:
                pass
        self._rpc = None
        self._connected = False

    def _run_loop(self):
        while not self._stop_flag:
            if not self._connected:
                if not self._connect():
                    time.sleep(RETRY_SEC)
                    continue

            with self._lock:
                pending = self._pending_state
                self._pending_state = None

            now = time.time()
            if pending is not None and (now - self._last_update_ts) >= 0:
                kind, payload = pending
                try:
                    if kind == "clear":
                        self._rpc.clear()
                    else:
                        self._rpc.update(**payload)
                    self._last_update_ts = now
                except (PyPresenceException, Exception) as e:
                    print("[discord_rpc] Ошибка отправки presence:", e)
                    self._connected = False
                    self._rpc = None

            time.sleep(0.5)

        self._disconnect()
