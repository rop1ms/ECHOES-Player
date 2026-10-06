<p align="center">
  <img src="icon-6.png" width="96" alt="Логотип ECHOES">
</p>

<h1 align="center">ECHOES</h1>

<p align="center">
https://buymeacoffee.com/rop1ms
  
  Музыкальный плеер для Windows — а ещё конструктор тем, просмотр клипов, маленькая студия
  и ритм-игра в духе osu! по вашей же музыке.
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: GPL v3" src="https://img.shields.io/badge/license-GPL--3.0-blue"></a>
  <img alt="Windows 10/11" src="https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-0078d4">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776ab">
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/README-English-lightgrey"></a>
</p>

<p align="center">
  <img src="docs/screenshots/theme-echoes-music.jpg" alt="ECHOES — тема Echoes Music, «Моя волна»">
</p>

## Возможности

**Плеер**
- Своя библиотека с обложками, тегами, плейлистами, очередью, перемешиванием/повтором и статистикой прослушиваний.
- Воспроизведение без пауз между треками через свой аудиовывод, 10-полосный эквалайзер, усиление до 10 000 %.
- «Моя волна» — бесконечные рекомендации из вашей коллекции по настроению, занятию и языку.
- Скачивание с YouTube, YouTube Music и SoundCloud (и ссылки Spotify через spotDL).
- Синхронный текст песен: поиск, редактор `.lrc` и автоматическая расстановка времени.
- Глобальные горячие клавиши, пропорции окна (16:9, 4:3, 21:9…), статус в Discord.
- Плейлист можно отправить другу маленьким файлом — недостающие треки у него скачаются сами.
- Интерфейс на **русском** и **английском**.

**Темы** — Echoes Music, Vinyl glass, Fluid, Fluid Dark, Winamp и свои.

| | |
|---|---|
| ![Vinyl glass](docs/screenshots/theme-vinyl-glass.jpg) | ![Winamp](docs/screenshots/theme-winamp.jpg) |
| ![Fluid](docs/screenshots/theme-fluid.jpg) | ![Текст песни](docs/screenshots/lyrics.jpg) |

**Конструктор тем** — тема собирается мышью: фон (картинка, GIF, видео, живые обои), слои, готовые
виджеты, частицы и эффекты экрана, своя картинка вместо пластинки, удаление фона без интернета и
**EchoScript** — небольшой язык скриптов для анимированных слоёв и виджетов.

![Конструктор тем](docs/screenshots/constructor.jpg)

**Клипы** — официальный клип играющей песни, синхронно с музыкой, с микшером (музыка / звук клипа)
и психоделическими эффектами. Клип можно поставить и фоном.

| | |
|---|---|
| ![Режим клипа](docs/screenshots/clip-mode.jpg) | ![Микшер](docs/screenshots/clip-mixer.jpg) |

**Echoes Studio** — плейлист, стойка каналов, пианоролл, микшер с эффектами (эквалайзер, компрессор,
реверб, дилей, автотюн, вокодер…), инструменты (синтезатор, супер-пила, клавишные, FM, 808, сэмплер),
запись с микрофона и **мэшап-бот**: вокал одного трека на бите другого.

![Echoes Studio](docs/screenshots/studio.jpg)

**esu!** — ритм-игра в духе osu! прямо в плеере:
- карты строятся по **любому треку** библиотеки моделью, которая слушает удары, части песни и припевы;
- импорт настоящих карт (`.osz`) и скинов osu! (`.osk`) — в том числе combo burst, элементы спиннера и полосы здоровья, дым курсора;
- выбор песни, моды, повторы, рекорды, попытки, редактор карт, свои хитсаунды;
- raw input и перенос чувствительности из Roblox, CS2, Valorant, Fortnite и других игр;
- классический вид, близкий к osu!, и минималистичный стиль Y2K.

| | |
|---|---|
| ![Меню esu!](docs/screenshots/esu-menu.jpg) | ![Выбор песни](docs/screenshots/esu-select.jpg) |
| ![Игра](docs/screenshots/esu-gameplay.jpg) | ![Моды](docs/screenshots/esu-mods.jpg) |

> esu! — фанатский проект, не связанный с osu! и ppy Pty Ltd. Файлов osu! в репозитории нет —
> см. [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Установка

Нужно: Windows 10 или 11 (64-бит), интернет на время установки, около 1 ГБ места.
Права администратора **не нужны**.

1. Скачайте репозиторий архивом (**Code → Download ZIP**) или архив релиза и распакуйте **целиком**.
2. Запустите **`install.bat`**. Если появится синее окно SmartScreen — «Подробнее» → «Выполнить в любом случае».
3. Нажмите Enter и подождите 5–15 минут. Установщик сам скачает и поставит свой Python, все библиотеки,
   VLC и ffmpeg и создаст ярлыки на рабочем столе и в меню «Пуск».

**Обновление:** распакуйте новую версию и запустите **`update.bat`** — библиотека, плейлисты и настройки сохранятся.
**Удаление:** Пуск → ECHOES → «Удалить ECHOES» или Параметры → Приложения.

| Что | Где |
|---|---|
| Программа | `%LOCALAPPDATA%\Programs\ECHOES` |
| Ваши данные (библиотека, плейлисты, настройки, обложки, карты) | `%USERPROFILE%\.neon_player` |
| Журнал установки | `%LOCALAPPDATA%\Programs\ECHOES\setup.log` |

## Запуск из исходников

```bat
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app\main.py
```

Ещё нужны **VLC 3.x (64-бит)** (или `ECHOES_VLC_DIR` с путём к портативной папке VLC) и **ffmpeg** в `PATH`.

### Необязательные ключи

| Переменная | Что включает |
|---|---|
| `ECHOES_DISCORD_CLIENT_ID` | статус в Discord — *Application ID* вашего приложения в [Discord Developer Portal](https://discord.com/developers/applications) |
| `ECHOES_SPOTIFY_CLIENT_ID`, `ECHOES_SPOTIFY_CLIENT_SECRET` | свои ключи Spotify API для spotDL (без них spotDL берёт свои) |

## Устройство проекта

```
app/          программа (Python, PyQt6); точка входа — main.py
setup/        install.ps1, update.ps1, uninstall.ps1 (их запускают install.bat / update.bat)
docs/         скриншоты
```

## Лицензия

ECHOES — свободная программа под **GNU General Public License v3.0**, см. [LICENSE](LICENSE).
Сторонние компоненты и их лицензии — [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
Скачивайте только ту музыку и видео, на которые у вас есть право.
