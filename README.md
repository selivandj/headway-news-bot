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
systemctl status headway-news-bot.service --no-pager
systemctl list-timers --all | grep headway
```

## Safety

Do not commit `.env`, API keys, Telegram tokens, SSH keys, runtime drafts, media storage, logs, or SQLite databases.

Use `.env.example` as the only committed environment template.
