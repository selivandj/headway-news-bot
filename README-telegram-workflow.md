# Telegram workflow for Headway

## What is connected

- Bot: `@codexheadway_bot`
- Channel: `@headway74`
- Publishing script: `telegram-publish.ps1`

## Security

Do not store the real Telegram bot token in this folder. Set it only as an environment variable.

Because the first token was pasted into chat, rotate it in BotFather before using this in production:

1. Open `@BotFather`.
2. Run `/revoke`.
3. Select `@codexheadway_bot`.
4. Save the new token somewhere private.

## Publish a text post

```powershell
$env:TELEGRAM_BOT_TOKEN = "new_token_from_botfather"
.\telegram-publish.ps1 -TextFile .\tg-post-example.html
```

## Publish a post with an image

```powershell
$env:TELEGRAM_BOT_TOKEN = "new_token_from_botfather"
.\telegram-publish.ps1 -TextFile .\tg-post-example.html -PhotoPath "C:\path\to\image.jpg"
```

## Recommended operating mode

Use approval-first publishing:

1. Collect fresh news from the last 24 hours.
2. Score each item by freshness, engagement, business relevance, and fit for Headway.
3. Create a Telegram-ready post.
4. Choose media:
   - source preview link for safest use,
   - original article image only when usage rights are acceptable,
   - generated image when the post needs a unique visual.
5. Publish only after review.
