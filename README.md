# Stock News Bot

Automated Indian stock market + crypto news digest bot running on Google Cloud VM.

## Features
- 3x daily market digest (8 AM, 2 PM, 6 PM IST)
- Live Nifty/Sensex data
- AI summaries in English + Marathi (Gemini)
- Concall intelligence with manipulation detection
- Smart alerts for breaking market events
- Sunday weekly wrap
- Delivers to Discord + Gmail

## Channels
- `#stocks-news` — Daily market digest
- `#earning-alerts` — Earnings results
- `#concall-intel` — Concall analysis
- `#smart-alerts` — Breaking news
- `#weekly-wrap` — Sunday digest

## Files
- `news_bot.py` — Main digest bot
- `alert_bot.py` — Smart alerts + earnings
- `concall_bot.py` — Concall analysis
- `weekly_bot.py` — Sunday weekly digest
- `config.py` — Webhook configuration

## Setup
1. Copy `.env.example` to `.env` and fill credentials
2. `pip3 install feedparser requests pytz --break-system-packages`
3. Set up cron jobs (see cron schedule below)

## Cron Schedule (UTC)
```
30 2  * * * python3 news_bot.py      # 8 AM IST
30 8  * * * python3 news_bot.py      # 2 PM IST
30 12 * * * python3 news_bot.py      # 6 PM IST
*/30 * * * * python3 alert_bot.py    # Every 30 min
0 13 * * *  python3 concall_bot.py   # 6:30 PM IST
30 15 * * * python3 concall_bot.py   # 9 PM IST
0 3  * * 0  python3 weekly_bot.py    # Sunday 8:30 AM IST
```
