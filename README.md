# Headway News Bot

Telegram approval bot and news monitor for the EV charging channel `@headway74`.

The project searches EV charging news, creates Russian Telegram drafts, attaches relevant media when possible, sends drafts to the owner for review, and publishes only after an explicit approval command.

## Core Principles

- Human-gated publishing: draft first, channel publish only after `публикуй`.
- Channel voice is provider-independent and defined in `vps/channel_agent_rules.md`.
- The bot writes for EV drivers, charger buyers, site owners, operators, installers, and EV infrastructure readers.
- The bot must not publish template filler or misleading media.
- Original article media is preferred. If no original media exists, exact entity search is preferred. AI image generation is allowed only after an explicit owner command.

## Main Files

- `vps/monitor.py` - scheduled news monitor, source parsing, LLM drafting, China reporting.
- `vps/bot.py` - Telegram approval bot, publish/rewrite/media commands.
- `vps/channel_agent_rules.md` - mandatory editorial rules for every model/provider.
- `vps/database/history.py` - SQLite history, quality statistics, owner feedback memory.
- `vps/media/precise_image_search.py` - precise image search for draft media corrections.
- `vps/media/media_status.py` - media status messages and owner guidance.
- `vps/smoke_check.py` - fast deployment sanity check.
- `vps/tests/test_static_guards.py` - static guard tests for critical behavior.

## VPS Checks

Run from `/opt/headway-news-bot`:

```bash
./.venv/bin/python smoke_check.py
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
./.venv/bin/python -m pytest tests
systemctl status headway-news-bot.service --no-pager
systemctl list-timers --all | grep headway
```

## Надежность и управление ботом

### Уведомления об ошибках

Бот ловит необработанные ошибки, пишет их в лог и может отправлять владельцу короткое Telegram-уведомление без токенов и API-ключей.

В `.env`:

```bash
ADMIN_TELEGRAM_ID=117574226
ERROR_NOTIFICATIONS_ENABLED=1
```

Если `ADMIN_TELEGRAM_ID` не задан, бот использует `TELEGRAM_REVIEW_CHAT_ID`.

### Черновики и inline-кнопки

Под каждым черновиком появляется панель действий:

- `✅ Публиковать` - публикует только выбранный черновик.
- `🖼 Найти фото` - запускает точный поиск фото по сущностям черновика.
- `🎨 Сгенерировать` - запускает существующий сценарий генерации по явной команде владельца.
- `✏️ Переделать текст` - отправляет черновик на переписывание.
- `❌ Отклонить` - снимает черновик с публикации и записывает решение в историю.
- `ℹ️ Почему подходит?` - показывает короткое объяснение источника, темы, медиа и причины отбора.

Старые reply-команды остаются рабочими: `публикуй`, `найди фото ...`, `сгенерируй`, `переделай`, `отклонить`, `статистика`.

### Daily report

Команда `/daily_report` показывает сводку за последние 24 часа: источники, найденные новости, прошедшие фильтр материалы, созданные черновики, отклонения, ошибки источников, топ источников и темы дня.

Если данных еще нет, бот отвечает: `Статистика за сутки пока не накоплена.`

Для ежедневной автоматической отправки владельцу:

```bash
DAILY_REPORT_ENABLED=1
DAILY_REPORT_TIME=09:00
SCHEDULER_ENABLED=1
SCHEDULER_TIMEZONE=Asia/Yekaterinburg
```

APScheduler использует постоянные id задач и не дублирует их при перезапуске.

### Pytest

Root smoke-тесты лежат в `tests/` и не требуют реального Telegram token, платных API или публикации в канал.

```bash
python -m pytest tests
```

## Safety

Do not commit `.env`, API keys, Telegram tokens, SSH keys, runtime drafts, media storage, logs, or SQLite databases.

Use `.env.example` as the only committed environment template.
