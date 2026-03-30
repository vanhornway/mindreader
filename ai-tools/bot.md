# bot.py — Agent Zero Telegram Bridge

**Location:** `Downloads/bot.py`

## What It Does

A Telegram bot that acts as a conversational bridge to a self-hosted [Agent Zero](https://github.com/frdel/agent-zero) AI instance. Users interact via Telegram; messages are forwarded to the Agent Zero API and responses are sent back.

**Features:**
- Per-user conversation context (each user has their own context ID)
- Project support — activate a named Agent Zero project per conversation
- File and photo attachment support (base64-encoded and forwarded)
- Long response handling — auto-splits messages that exceed Telegram's 4000-char limit
- Typing indicator — shows "typing..." while waiting for Agent Zero response
- User authorization whitelist via `config.json`

## Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message and command list |
| `/newchat` | Clear current context and start fresh |
| `/project <name>` | Activate a named Agent Zero project |
| `/projects` | List available projects from `/usr/projects/` |
| `/status` | Show your current context ID and active project |
| `/help` | Same as `/start` |

## Setup

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather) and get the token
2. Create `config.json` next to `bot.py`:

```json
{
  "telegram_bot_token": "YOUR_BOT_TOKEN",
  "agent_zero_url": "http://localhost:80",
  "api_key": "YOUR_AGENT_ZERO_API_KEY",
  "allowed_user_ids": [123456789],
  "lifetime_hours": 168
}
```

3. Install dependencies and run:

```bash
pip install python-telegram-bot requests
python bot.py
```

## State Persistence

Conversation state (context IDs, active projects) is stored in `state.json` alongside the script. Safe to delete to reset all user contexts.

## Dependencies

```
python-telegram-bot
requests
```

## Notes

- Agent Zero must be running and accessible at the URL in `config.json`
- `allowed_user_ids: []` (empty list) allows all users — populate it to restrict access
- Default conversation lifetime: 168 hours (7 days)
