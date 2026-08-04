#!/bin/bash
# Run this once to update your .env with all new webhooks
cat >> ~/.env << 'EOF'
export WEBHOOK_EARNINGS="https://discord.com/api/webhooks/1522202265140854885/OtUqK2uCGSzFLkkPkyyj_QgJBil7m17GIzE1EC25atDdQgq15MMPlSFgO9MsnbhwL_Y1"
export WEBHOOK_CONCALL="https://discord.com/api/webhooks/1522204980793049178/N7ed_Rak3dAZfFM9YhDNN0ipK3eY8gaIMRT7Xe05Dn4Zl5MU2UeyaOrG3lel9Y49FMG1"
export WEBHOOK_SMART_ALERTS="https://discord.com/api/webhooks/1522205328807170058/wIrn6dviuLKUZy87AACjlo1vrUHcLfJhH9XCgIEbgUmyr6s1aZm0ksF2ZOwMc7ZlesiW"
export WEBHOOK_WEEKLY_WRAP="https://discord.com/api/webhooks/1522205478497681603/lEWY_jQthAB3bFVce3BLymdKr8SE7YBx9yCvYXPCc5fQHrC4k06kH8iz7xOF68KmiRq2"
EOF
source ~/.env
echo "Environment updated successfully"
