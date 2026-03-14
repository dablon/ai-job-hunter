# Job Hunter (Minimax Edition)

Automated job hunting pipeline that collects listings from multiple sources, filters them with AI (Minimax, Claude, or OpenCode), and sends you daily notifications via email or Discord.

![Docker](https://img.shields.io/badge/Docker-Ready-blue)
![Python 3.11+](https://img.shields.io/badge/Python-3.11+-green)

## Features

- **Multi-source collection** — LinkedIn, Indeed, Glassdoor (via jobspy) + Gupy
- **AI-powered filtering** — Supports Minimax, Anthropic Claude, or OpenCode
- **Default: Minimax** — Most cost-effective AI provider
- **Dual notifications** — HTML email (Gmail SMTP) or Discord webhook
- **Checkpoint & resume** — Retry failed runs without re-collecting jobs
- **Docker-ready** — Fully containerized for easy deployment
- **CI/CD ready** — GitHub Actions workflow included

## Quick Start (Docker)

```bash
# 1. Clone and setup
git clone https://github.com/dablon/ai-job-hunter.git
cd ai-job-hunter

# 2. Copy configuration
cp config.example.json config/config.json
cp .env.example .env

# 3. Edit config.json and .env with your settings

# 4. Run with Docker
docker-compose run --rm job-hunter --provider minimax
```

## Configuration

### config.json

```json
{
  "profile": "Your professional profile",
  "keywords": ["python developer", "backend engineer"],
  "location": "Colombia",
  "remote_only": true
}
```

### .env

```bash
# Minimax API (get from https://platform.minimax.io/)
MINIMAX_API_KEY=your-key-here

# Email (Gmail App Password)
EMAIL_SENDER=you@gmail.com
EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
EMAIL_RECIPIENT=you@gmail.com
```

## AI Providers

| Provider   | Model           | Cost  | Setup |
|------------|-----------------|-------|-------|
| **minimax** (default) | MiniMax-M2.5 | $0.001/1K tokens | Get API key |
| anthropic  | claude-haiku-4-5 | ~$0.20/run | Get API key |
| opencode   | Any (ollama, etc.) | Free (local) | Install CLI |

## CLI Options

```bash
# Full pipeline with Minimax (default)
docker-compose run --rm job-hunter

# With Anthropic instead
docker-compose run --rm job-hunter --provider anthropic

# Send to Discord
docker-compose run --rm job-hunter --notify discord

# Resume from checkpoint (retry without re-collecting)
docker-compose run --rm job-hunter --resume

# Combine options
docker-compose run --rm job-hunter --resume --provider minimax --notify discord
```

## Docker Commands

```bash
# Build the image
docker-compose build

# Run once
docker-compose run --rm job-hunter

# Development shell (for debugging)
docker-compose --profile dev run --rm shell

# Schedule daily runs via cron on host
0 9 * * * cd /path/to/ai-job-hunter && docker-compose run --rm job-hunter
```

## Project Structure

```
ai-job-hunter/
├── src/job_hunter/
│   ├── main.py           # Pipeline orchestrator
│   ├── collector.py      # Job collection (jobspy, gupy)
│   ├── filter.py         # AI filtering (minimax, anthropic, opencode)
│   ├── mailer.py         # Email notifications
│   ├── notifier_discord.py  # Discord notifications
│   └── utils.py          # Shared utilities
├── config/               # Persistent configuration (mounted volume)
├── Dockerfile
├── docker-compose.yml
└── .env                 # Secrets (not committed)
```

## GitHub Actions (Optional)

The included workflow (`.github/workflows/job-hunter.yml`) can run daily:

1. Fork or copy to your private repo
2. Add secrets: `MINIMAX_API_KEY`, `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`
3. Enable workflow dispatch for manual runs

## Cost Estimation

- **Minimax**: ~$0.01-0.05 per run (500 jobs)
- **Anthropic**: ~$0.15-0.25 per run (500 jobs)
- **GCP/LinkedIn scraping**: Already included in jobspy

## License

GPL-3.0 — See LICENSE file.

