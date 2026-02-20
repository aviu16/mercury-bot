# Mercury Bot — AI Financial Assistant for Discord

Discord bot that integrates with the Mercury banking API to provide real-time financial monitoring, transaction tracking, and AI-powered financial insights via Claude.

## Features

- **Real-Time Balance Tracking** — monitors Mercury bank accounts with auto-refreshing dashboard
- **Transaction Monitoring** — automated alerts for new transactions, categorization, and anomaly detection
- **AI Financial Insights** — Claude-powered natural language Q&A about your finances
- **Scheduled Reports** — daily/weekly financial summaries posted to Discord channels
- **Multi-Account Support** — handles multiple Mercury accounts simultaneously
- **SQLite Storage** — local transaction history for trend analysis
- **Production Runner** — health checks, auto-restart, and keep-alive for 24/7 operation

## Tech Stack

- **Python** — async runtime
- **discord.py** — Discord bot framework
- **Mercury API** — banking data access
- **Anthropic API** — Claude for AI-powered financial analysis
- **SQLite** — transaction history database
- **asyncio** — concurrent API polling and event handling

## Architecture

```
mercury-bot/
├── main.py                    # Core bot — commands, events, Mercury polling
├── main_enhanced.py           # Enhanced version with all features
├── financial_agent.py         # Claude-powered financial Q&A agent
├── enhanced_mercury_api.py    # Mercury API client with retry logic
├── transaction_monitor.py     # Real-time transaction alerting
├── enhanced_features.py       # Dashboard, reports, analytics
├── add_transaction_monitoring.py  # Transaction categorization
├── keep_alive.py              # Health check server
├── production_runner.py       # Auto-restart wrapper
├── requirements.txt           # Dependencies
└── SETUP_GUIDE.md             # Deployment guide
```

## Setup

1. Clone the repo
2. `pip install -r requirements.txt`
3. Create `.env`:
   ```
   MERCURY_API_KEY=...
   DISCORD_TOKEN=...
   ANTHROPIC_API_KEY=...
   ```
4. `python main.py`

## License

MIT
