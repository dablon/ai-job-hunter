# Job Hunter (Minimax Edition)

Automated job hunting pipeline that collects listings from multiple sources, filters them with AI (Minimax, Claude, or OpenCode), and sends you daily notifications via email, Discord, Telegram, SMS, or WhatsApp.

![Docker](https://img.shields.io/badge/Docker-Ready-blue)
![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green)
![Version](https://img.shields.io/badge/version-1.1.0-blue)
![License](https://img.shields.io/badge/License-GPL--3.0-green)

## Table of Contents

- [Features](#features)
- [Quick Start](#quick-start-docker)
- [Sample Output](#sample-output)
- [Configuration](#configuration)
  - [config.json](#configjson)
  - [Environment Variables](#environment-variables)
  - [Notification Channels](#notification-channels)
- [AI Providers](#ai-providers)
- [CLI Options](#cli-options)
- [Docker Commands](#docker-commands)
- [Project Structure](#project-structure)
- [Architecture](#architecture)
- [GitHub Actions](#github-actions-optional)
- [Cost Estimation](#cost-estimation)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [License](#license)

---

## Features

- **Multi-source collection** — LinkedIn, Indeed, Glassdoor (via jobspy), Gupy (Brazil), RemoteOK, WeWorkRemotely
- **AI-powered filtering** — Supports Minimax (default), Anthropic Claude, or OpenCode
- **Smart profile analysis** — AI refines your keywords based on your profile
- **Multiple notification channels** — Email (HTML + plain text), Discord, Telegram, SMS (Twilio), WhatsApp (Twilio)
- **Checkpoint & resume** — Retry failed runs without re-collecting jobs
- **Deduplication** — Tracks sent jobs to avoid duplicate notifications
- **Docker-ready** — Fully containerized for easy deployment
- **CI/CD ready** — GitHub Actions workflow included
- **Hard constraints** — Enforces remote-only, location filters automatically

---

## Quick Start (Docker)

```bash
# 1. Clone and setup
git clone https://github.com/dablon/ai-job-hunter.git
cd ai-job-hunter

# 2. Copy configuration
cp config.example.json config/config.json
cp .env.example .env

# 3. Edit config.json and .env with your settings
#    See Configuration section below for details

# 4. Run with Docker
docker-compose run --rm job-hunter --provider minimax
```

---

## Sample Output

### Terminal Output

```
    ██████╗ ███████╗ ██████╗ ██╗   ██╗██╗     ███████╗███████╗
    ██╔══██╗██╔════╝██╔════╝ ██║   ██║██║     ██╔════╝██╔════╝
    ██████╔╝█████╗   ██║  ███╗██║   ██║██║     █████╗  ███████╗
    ██╔══██╗██╔══╝   ██║   ██║██║   ██║██║     ██╔══╝  ╚════██║
    ██║  ██║███████╗ ╚██████╔╝╚██████╔╝███████╗███████╗███████║
    ╚═╝  ╚═╝╚══════╝  ╚═════╝  ╚═════╝ ╚══════╝╚══════╝╚══════╝
    ╔═══════════════════════════════════════════════════════════╗
    ║         🤖  AI-POWERED JOB HUNTING PIPELINE v2.0            ║
    ╚═══════════════════════════════════════════════════════════╝

┌──────────────────────────────────────────────────────────┐
│ Provider: MINIMAX                               │
│ Notify:   email                                 │
└──────────────────────────────────────────────────────────┘

04:48:15 [INFO] job_hunter.main: Loaded minimax_api_key: sk-cp-gyQ8...
04:50:42 [INFO] job_hunter.filter: AI Profile Analysis response: {...}
04:50:42 [INFO] job_hunter.filter: Profile refined. New keywords: ['Principal Software Architect', ...]

╭────────────────────────────────────────────────────────╮
│ 🔍               GATHERING JOB LISTINGS               ◐ │
├────────────────────────────────────────────────────────┤
│ Searching 17 keywords                                    │
│ Sources: LinkedIn, Indeed, Glassdoor, Gupy, RemoteOK     │
╰────────────────────────────────────────────────────────╯
  ├─ jobspy
  │       ✓ linkedin        →  83 jobs
  │       ✓ indeed          →  31 jobs
  │       ✓ gupy            →   0 jobs
  │       ✓ remoteok        →   4 jobs
  │
  └─ Total: 130 jobs collected

╭────────────────────────────────────────────────────────╮
│ 🧠                AI-POWERED FILTERING                ◐ │
├────────────────────────────────────────────────────────┤
│ Processing 130 jobs                                      │
│ AI Provider: MINIMAX                            │
╰────────────────────────────────────────────────────────╯
04:55:59 [INFO] job_hunter.filter: [minimax] Batch 1/6 (25 jobs)
...
04:59:30 [INFO] job_hunter.filter: AI filter (minimax): 130 in -> 24 approved

╭────────────────────────────────────────────────────────╮
│ 🧠                AI-POWERED FILTERING                ● │
├────────────────────────────────────────────────────────┤
│ Approved 24 jobs                            │
│ Pass rate: 18.5%                                │
╰────────────────────────────────────────────────────────╯

╔════════════════════════════════════════════════════════╗
║                    🎉 HUNT COMPLETE!                    ║
╠════════════════════════════════════════════════════════╣
║
║    Jobs Found:     24             ║
║    Notified Via:    email         ║
║    Total Sent:      24                ║
╚════════════════════════════════════════════════════════╝

   🎊 Amazing haul! You're on fire! 🔥
```

### Email Report (Text)

```
Job Hunter — 14/03/2026
24 job(s) found
==================================================

1. Staff Software Architect, LearnWith.AI (Remote) - $200,000/year USD @ Crossover
   Location: Remote
   Link: https://www.linkedin.com/jobs/view/4382477030
   Reason: Staff Software Architect role at Crossover explicitly offers remote work
   ($200k/year USD). This matches the user's Principal Architect level...

2. .NET Principal Architect @ Sparq
   Location: Remote
   Link: https://www.linkedin.com/jobs/view/4375424980
   Reason: .NET Principal Architect at Sparq - Matches user's Principal level,
   requires Azure migration and modernization expertise (monolith to microservices)...

...

Automatically generated by Job Hunter (Minimax Edition).
```

---

## Configuration

### config.json

```json
{
  "profile": "Principal Software Architect with 20+ years designing and delivering high-scale financial and enterprise platforms...",
  "keywords": [
    "Principal Software Architect",
    "Staff Architect",
    "Distinguished Engineer",
    "FinTech Architect",
    "Platform Architect"
  ],
  "location": "Colombia",
  "remote_only": true,
  "minimax_model": "MiniMax-M2.5"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `profile` | string | Yes | Your professional background, skills, and target roles |
| `keywords` | array | Yes | Search keywords (max 20) |
| `location` | string | Yes | Location for job search |
| `remote_only` | boolean | Yes | Only show remote positions |
| `minimax_model` | string | No | Minimax model (default: MiniMax-M2.5) |

### Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `MINIMAX_API_KEY` | If using minimax | Get from https://platform.minimax.io/ |
| `ANTHROPIC_API_KEY` | If using anthropic | Get from https://console.anthropic.com/ |
| `EMAIL_SENDER` | For email | Gmail address (e.g., you@gmail.com) |
| `EMAIL_APP_PASSWORD` | For email | Gmail App Password (16 chars) |
| `EMAIL_RECIPIENT` | For email | Where to send notifications |
| `DISCORD_WEBHOOK_URL` | For Discord | Discord webhook URL |
| `TELEGRAM_BOT_TOKEN` | For Telegram | Bot Token from @BotFather |
| `TELEGRAM_CHAT_ID` | For Telegram | Your chat ID |
| `TWILIO_ACCOUNT_SID` | For SMS/WhatsApp | From Twilio Console |
| `TWILIO_AUTH_TOKEN` | For SMS/WhatsApp | From Twilio Console |
| `TWILIO_FROM_NUMBER` | For SMS | Twilio phone number |
| `TWILIO_TO_NUMBER` | For SMS | Your phone number |
| `TWILIO_WHATSAPP_FROM` | For WhatsApp | WhatsApp sender (format: whatsapp:+1234567890) |
| `TWILIO_WHATSAPP_TO` | For WhatsApp | Your WhatsApp number |

### Notification Channels

Use the `--notify` flag to specify which channels to use (comma-separated):

```bash
# Single channel
docker-compose run --rm job-hunter --notify email

# Multiple channels
docker-compose run --rm job-hunter --notify email,discord,telegram

# All available
docker-compose run --rm job-hunter --notify email,discord,telegram,sms,whatsapp
```

Available channels: `email`, `discord`, `telegram`, `sms`, `whatsapp`

---

## AI Providers

| Provider | Model | Cost | Speed | Setup |
|----------|-------|------|-------|-------|
| **minimax** (default) | MiniMax-M2.5 | ~$0.01/run | Fast | Get API key from https://platform.minimax.io/ |
| anthropic | claude-haiku-4-5 | ~$0.20/run | Medium | Get API key from https://console.anthropic.com/ |
| opencode | Any (ollama, etc.) | Free (local) | Varies | Install CLI: `npm install -g opencode-ai` |

### Choosing a Provider

- **Minimax** (default): Most cost-effective, fast, great results
- **Anthropic**: Higher quality, more expensive, good for complex profiles
- **OpenCode**: Free if you run local models (Ollama, etc.)

---

## CLI Options

```bash
# Full pipeline with Minimax (default)
docker-compose run --rm job-hunter

# With Anthropic instead
docker-compose run --rm job-hunter --provider anthropic

# With OpenCode
docker-compose run --rm job-hunter --provider opencode

# Send to specific notification channels
docker-compose run --rm job-hunter --notify discord
docker-compose run --rm job-hunter --notify email,telegram

# Resume from checkpoint (retry without re-collecting jobs)
docker-compose run --rm job-hunter --resume

# Combine options
docker-compose run --rm job-hunter --resume --provider minimax --notify discord
```

---

## Docker Commands

```bash
# Build the image
docker-compose build

# Run once
docker-compose run --rm job-hunter

# Run with specific provider
docker-compose run --rm job-hunter --provider anthropic

# Development shell (for debugging)
docker-compose --profile dev run --rm shell

# View logs
docker-compose logs -f job-hunter

# Schedule daily runs via cron on host
0 9 * * * cd /path/to/ai-job-hunter && docker-compose run --rm job-hunter

# Stop and remove containers
docker-compose down
```

---

## Project Structure

```
ai-job-hunter/
├── src/job_hunter/
│   ├── main.py              # Pipeline orchestrator
│   ├── collector.py        # Job collection (jobspy, gupy, remoteok)
│   ├── filter.py           # AI filtering (minimax, anthropic, opencode)
│   ├── mailer.py           # Email notifications (HTML + plain)
│   ├── notifier_discord.py # Discord webhook notifications
│   ├── notifier_telegram.py# Telegram bot notifications
│   ├── notifier_twilio.py  # SMS/WhatsApp notifications
│   └── utils.py            # Shared utilities, retry logic
├── tests/                  # Test suite
│   ├── test_filter.py
│   ├── test_main.py
│   └── ...
├── config/                 # Persistent configuration
│   ├── config.json        # Your search configuration
│   ├── reports/           # Generated job reports (HTML + TXT)
│   ├── pending_jobs.json  # Checkpoint for --resume
│   └── sent_urls.json     # Deduplication tracking
├── .github/workflows/     # GitHub Actions
│   └── job-hunter.yml
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env                   # Secrets (not committed)
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     job_hunter main.py                      │
└─────────────────────────────┬───────────────────────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        ▼                     ▼                     ▼
┌───────────────┐    ┌───────────────┐    ┌───────────────┐
│   Step 0      │    │   Step 1      │    │   Step 2       │
│ Profile       │    │   Collect     │    │   AI Filter    │
│ Analysis      │    │   Jobs        │    │   Jobs         │
│ (Minimax)     │    │               │    │                │
└───────────────┘    └───────────────┘    └───────────────┘
                              │                     │
                              ▼                     ▼
                     ┌───────────────┐    ┌───────────────┐
                     │ Sources:      │    │ Providers:    │
                     │ - LinkedIn    │    │ - Minimax     │
                     │ - Indeed      │    │ - Anthropic   │
                     │ - Glassdoor   │    │ - OpenCode    │
                     │ - Gupy        │    └───────────────┘
                     │ - RemoteOK    │
                     └───────────────┘
                                          │
                                          ▼
                                 ┌───────────────┐
                                 │   Step 3      │
                                 │   Notify      │
                                 └───────────────┘
                                        │
        ┌───────────────┬───────────────┼───────────────┐
        ▼               ▼               ▼               ▼
   ┌─────────┐    ┌────────────┐  ┌──────────┐   ┌──────────┐
   │  Email  │    │  Discord   │  │Telegram  │   │ Twilio   │
   │ (SMTP)  │    │  (Webhook) │  │  (Bot)   │   │ SMS/WA   │
   └─────────┘    └────────────┘  └──────────┘   └──────────┘
```

### Data Flow

1. **Load Config** — Merge config.json with environment variables
2. **Profile Analysis** — AI refines keywords based on your profile
3. **Collect Jobs** — Scrape multiple job boards in parallel
4. **AI Filter** — Batch process jobs through AI to score/recommend
5. **Deduplicate** — Remove already-sent jobs
6. **Notify** — Send to enabled channels (email, Discord, etc.)
7. **Checkpoint** — Save state for resume capability

---

## GitHub Actions (Optional)

The included workflow (`.github/workflows/job-hunter.yml`) can run daily or on schedule:

1. Fork or copy to your private repo
2. Add these secrets in GitHub Settings > Secrets:
   - `MINIMAX_API_KEY`
   - `EMAIL_SENDER`
   - `EMAIL_APP_PASSWORD`
   - `EMAIL_RECIPIENT`
3. Enable workflow dispatch for manual runs
4. Adjust the schedule in `on.schedule.cron` if needed

```yaml
# .github/workflows/job-hunter.yml
on:
  schedule:
    - cron: '0 9 * * *'  # Daily at 9 AM
  workflow_dispatch:     # Manual trigger
```

---

## Cost Estimation

| Provider | Jobs Processed | Cost/Run | Notes |
|----------|----------------|----------|-------|
| **Minimax** | 500 jobs | $0.01-0.05 | ~6 API calls (25 jobs/batch) |
| **Anthropic** | 500 jobs | $0.15-0.25 | ~6 API calls |
| **OpenCode** | 500 jobs | $0.00 | Requires local Ollama |

### Minimax Pricing (as of 2024)

- Input: $0.01/1M tokens
- Output: $0.10/1M tokens
- A typical run with 500 jobs costs ~$0.02-0.05

---

## Troubleshooting

### "SMTP server unreachable"

```
04:50:25 [WARNING] job_hunter.mailer: SMTP server unreachable (smtp.sendgrid.net:587): timed out
```

**Solution**: Check your SMTP settings. For Gmail, ensure you're using an [App Password](https://support.google.com/accounts/answer/185833), not your regular password.

### "No jobs found from any source"

**Possible causes**:
1. Keywords too specific/niche
2. Location not supported by job boards
3. Rate limiting from job sites

**Solutions**:
- Broaden your keywords
- Try different location (e.g., "Remote" instead of "Colombia")
- Run with `--resume` to use cached jobs

### "All batches failed" during AI filtering

**Possible causes**:
1. API key invalid or expired
2. Network issues
3. Rate limiting

**Solutions**:
- Verify your API key
- Try a different provider (`--provider anthropic`)
- Check logs for specific error messages

### "Missing minimax_api_key"

```
logger.error: Missing minimax_api_key — set it in .env or MINIMAX_API_KEY env var
```

**Solution**: Add `MINIMAX_API_KEY=your-key-here` to your `.env` file.

### Docker container exits immediately

**Check**:
```bash
docker-compose logs job-hunter
```

---

## Development

### Local Setup (without Docker)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or: venv\Scripts\activate  # Windows

# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run locally
python -m job_hunter.main --provider minimax
```

### Running Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/test_filter.py

# Run with coverage
pytest --cov=job_hunter
```

### Adding New Job Sources

1. Add collector function in `collector.py`
2. Implement `_xxx_job_to_canonical()` helper
3. Add to `collect_all()` function
4. Update README documentation

---

## License

GPL-3.0 — See LICENSE file.

---

## Credits

- [jobspy](https://github.com/BunsenDev/jobspy) — Job scraping library
- [Minimax](https://www.minimax.io/) — AI provider (default)
- [Anthropic](https://www.anthropic.com/) — Claude AI provider
- [OpenCode](https://opencode.ai/) — Local AI CLI