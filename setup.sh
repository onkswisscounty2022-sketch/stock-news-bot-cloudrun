#!/bin/bash
# ─── Stock News Bot — One-time Setup Script ───────────────────────────────────
echo ">>> Updating system packages..."
sudo apt-get update -y && sudo apt-get upgrade -y

echo ">>> Installing Python3, pip, and git..."
sudo apt-get install -y python3 python3-pip git

echo ">>> Creating project directory..."
mkdir -p ~/stock_news_bot
cd ~/stock_news_bot

echo ">>> Installing Python dependencies..."
pip3 install feedparser requests pytz

echo ">>> Setup complete! Now upload news_bot.py and .env to ~/stock_news_bot/"
