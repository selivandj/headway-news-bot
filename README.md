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
- `vps/keyboards.py` - inline keyboards with draft-bound callback data.
- `vps/media/image_dedup.py` - ImageHash-based media duplicate detection.
- `vps/backup.py` - SQLite gzip backups.
- `vps/security/rate_limit.py` - simple in-memory command rate limiter.
- `vps/datasette-metadata.json` - local-only Datasette metadata.
- `vps/smoke_check.py` - fast deployment sanity check.
- `vps/tests/test_static_guards.py` - static guard tests for critical behavior.

## VPS Checks

Run from `/opt/headway-news-bot`:

```bash
./.venv/bin/python vps/smoke_check.py
./.venv/bin/python -m unittest discover -s tests -p "test_*.py"
./.venv/bin/python -m pytest tests
systemctl status headway-news-bot.service --no-pager
systemctl list-timers --all | grep headway
```

Main runtime configuration should live in the project root `.env`:
`/opt/headway-news-bot/.env`.

For compatibility, the bot also checks `/opt/headway-news-bot/vps/.env`.
Load order is root `.env` first, then `vps/.env` for missing values. The bot does not fail if either file is absent.

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

## Этап 2: управление черновиками и защита медиа

### Inline-кнопки черновиков

Клавиатуры вынесены в `vps/keyboards.py`. Каждая кнопка содержит короткий `draft_id`, поэтому действие привязано к конкретному черновику:

- `publish:{draft_id}` - сначала показывает подтверждение публикации.
- `confirm_publish:{draft_id}` - публикует после второго клика владельца.
- `find_photo:{draft_id}` - запускает точный поиск фото.
- `generate_image:{draft_id}` - запускает генерацию только по явной команде владельца.
- `rewrite:{draft_id}` - отправляет черновик на переписывание.
- `reject:{draft_id}` - показывает причины отклонения.
- `reject_reason:{draft_id}:{reason}` - сохраняет причину и снимает черновик.
- `why:{draft_id}` - объясняет релевантность новости.

Старые текстовые команды не удалены.

### ImageHash и защита от дублей

`vps/media/image_dedup.py` считает perceptual hash изображения и хранит его в `vps/database/image_hashes.db`.

Настройки:

```bash
IMAGE_HASH_ENABLED=1
IMAGE_HASH_THRESHOLD=10
```

Точный поиск фото через `vps/media/precise_image_search.py` проверяет найденное изображение через ImageHash. Если фото похоже на уже использованное, бот не подставляет его автоматически и пишет это в лог.

### Просмотр статистики через Datasette

Это не веб-админка, а локальный просмотр SQLite. Порт наружу не открывать.

```bash
pip install datasette
datasette vps/database/history.db \
  --host=127.0.0.1 \
  --port=8001 \
  --metadata=vps/datasette-metadata.json
```

Доступ с компьютера:

```bash
ssh -L 8001:localhost:8001 root@VPS_IP
```

Потом открыть `http://127.0.0.1:8001`. Пример systemd unit: `deploy/headway-datasette.service.example`.

### Автобэкап базы

`vps/backup.py` делает gzip-бэкап всех SQLite-баз из `vps/database/*.db` и хранит последние архивы.

Минимально в бэкап попадают:

- `history.db`
- `image_hashes.db`, если база уже создана ImageHash-дедупликацией

Настройки:

```bash
BACKUP_ENABLED=1
BACKUP_KEEP_DAYS=14
BACKUP_DIR=backups
```

Ручной запуск в Telegram:

```text
/backup_now
```

Команда доступна только владельцу. Если базы еще нет, бот не падает и отвечает, что бэкап не создан.

### Owner-only команды

Критичные действия проверяют владельца:

- публикация;
- отклонение;
- генерация изображения;
- поиск изображения;
- бэкап.

Настройки:

```bash
OWNER_CHAT_ID=117574226
ADMIN_TELEGRAM_ID=117574226
```

Если команду вызывает не владелец, бот отвечает: `Команда доступна только владельцу.`

### Rate limit

Простой in-memory limiter без Redis:

```bash
RATE_LIMIT_ENABLED=1
RATE_LIMIT_REQUESTS=30
RATE_LIMIT_WINDOW_SECONDS=60
RATE_LIMIT_OWNER_BYPASS=1
```

По умолчанию владелец не ограничивается rate limit.

При превышении лимита бот отвечает: `Слишком много команд. Попробуйте позже.`

### Проверка после деплоя

```bash
python -m compileall vps
python -m pytest tests -v
./.venv/bin/python vps/smoke_check.py
systemctl restart headway-news-bot.service
systemctl status headway-news-bot.service --no-pager
```

## 7-day editorial training mode

This temporary mode is used when the channel is being tuned again with owner feedback.
It does not publish automatically. It only sends drafts to the owner for review.

Goal for the training week:

- collect enough candidates every day;
- keep at least 2 strong publishable drafts per day in the review flow;
- save owner comments, rejects, media notes, and publish decisions into `history.db`;
- let future prompts use recent rejection feedback so provider swaps do not change the channel voice.

Files:

- `vps/headway-news-monitor-training7d.timer`
- `vps/headway-news-monitor-training7d.service`
- `vps/run_training_monitor.sh`

Settings:

```bash
TRAINING_DURATION_HOURS=168
TRAINING_LOOKBACK_HOURS=24
TRAINING_TARGET_DRAFTS_PER_DAY=2
```

Enable on VPS:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now headway-news-monitor-training7d.timer
sudo systemctl start headway-news-monitor-training7d.service
```

Stop manually:

```bash
sudo systemctl disable --now headway-news-monitor-training7d.timer
```

The timer disables itself after the configured duration.

## Safety

Do not commit `.env`, API keys, Telegram tokens, SSH keys, runtime drafts, media storage, logs, or SQLite databases.

Use `.env.example` as the only committed environment template.
