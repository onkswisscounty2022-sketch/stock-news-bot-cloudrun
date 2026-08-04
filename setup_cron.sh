#!/bin/bash
# Complete cron setup for all bots
crontab -l > /tmp/current_cron 2>/dev/null

cat >> /tmp/current_cron << 'CRON'
# ─── Stock News Bot ──────────────────────────────────────────────────────────
30 2  * * * /bin/bash -c 'source /home/onkswisscounty2022/.env && cd /home/onkswisscounty2022/stock_news_bot && /usr/bin/python3 news_bot.py >> /home/onkswisscounty2022/stock_news_bot/log.txt 2>&1'
30 8  * * * /bin/bash -c 'source /home/onkswisscounty2022/.env && cd /home/onkswisscounty2022/stock_news_bot && /usr/bin/python3 news_bot.py >> /home/onkswisscounty2022/stock_news_bot/log.txt 2>&1'
30 12 * * * /bin/bash -c 'source /home/onkswisscounty2022/.env && cd /home/onkswisscounty2022/stock_news_bot && /usr/bin/python3 news_bot.py >> /home/onkswisscounty2022/stock_news_bot/log.txt 2>&1'
# ─── Alert Bot (every 30 min) ────────────────────────────────────────────────
*/30 * * * * /bin/bash -c 'source /home/onkswisscounty2022/.env && cd /home/onkswisscounty2022/stock_news_bot && /usr/bin/python3 alert_bot.py >> /home/onkswisscounty2022/stock_news_bot/alert_log.txt 2>&1'
# ─── Concall Bot (6:30 PM and 9 PM IST) ─────────────────────────────────────
0  13 * * * /bin/bash -c 'source /home/onkswisscounty2022/.env && cd /home/onkswisscounty2022/stock_news_bot && /usr/bin/python3 concall_bot.py >> /home/onkswisscounty2022/stock_news_bot/concall_log.txt 2>&1'
30 15 * * * /bin/bash -c 'source /home/onkswisscounty2022/.env && cd /home/onkswisscounty2022/stock_news_bot && /usr/bin/python3 concall_bot.py >> /home/onkswisscounty2022/stock_news_bot/concall_log.txt 2>&1'
# ─── Weekly Bot (Sunday 8:30 AM IST = 3:00 UTC) ──────────────────────────────
0  3  * * 0 /bin/bash -c 'source /home/onkswisscounty2022/.env && cd /home/onkswisscounty2022/stock_news_bot && /usr/bin/python3 weekly_bot.py >> /home/onkswisscounty2022/stock_news_bot/weekly_log.txt 2>&1'
CRON

crontab /tmp/current_cron
echo "Cron jobs installed:"
crontab -l
