# Getting Started

Get Forge running locally in about 10 minutes.

## Prerequisites

- **Python 3.11+** with [uv](https://github.com/astral-sh/uv)
- **Podman** — for running task containers (`brew install podman` / `dnf install podman`)
- **Docker Compose** — for Redis (`brew install docker-compose` / included with Docker Desktop)
- **Jira Cloud** account with API access
- **GitHub** Personal Access Token (scopes: `repo`, `read:org`)
- **Claude API key** (Anthropic direct) or Google Cloud project with Vertex AI enabled

## 1. Install

```bash
git clone https://github.com/forge-sdlc/forge.git
cd forge
uv sync
```

## 2. Configure

```bash
cp .env.example .env
```

Edit `.env` with your credentials at minimum:

```bash
# Jira
JIRA_BASE_URL=https://your-org.atlassian.net
JIRA_USER_EMAIL=you@example.com
JIRA_API_TOKEN=your-jira-api-token

# GitHub
GITHUB_TOKEN=github_pat_your_token

# LLM — choose one
ANTHROPIC_API_KEY=sk-ant-your-key       # Anthropic direct
# ANTHROPIC_VERTEX_PROJECT_ID=my-proj  # OR Vertex AI
# ANTHROPIC_VERTEX_REGION=us-east5

LLM_MODEL=claude-opus-4-5@20251101
```

## 3. Build the Container Image

```bash
podman build -t forge-dev:latest -f containers/Containerfile containers/
```

## 4. Start Services

```bash
# Terminal 1 — Redis
docker compose up redis -d

# Terminal 2 — API server
uv run uvicorn forge.main:app --reload --port 8000 --host 0.0.0.0

# Terminal 3 — Worker (must run on the host — it spawns Podman containers)
uv run forge worker
```

## 5. Configure Webhooks

Point Jira and GitHub webhooks at your server.

=== "Jira"

    **URL:** `https://your-server.com/api/v1/webhooks/jira`

    **Events:** Issue created, Issue updated, Comment created

=== "GitHub"

    **URL:** `https://your-server.com/api/v1/webhooks/github`

    **Events:** Pull requests, Pull request reviews, Check runs, Issue comments

For local development, use [ngrok](https://ngrok.com/) or a similar tunnel to expose your local server.

## 6. Start Your First Workflow

1. Create a Jira issue (type: Feature) and add the label `forge:managed`
2. Forge will automatically generate a PRD and post it as a comment
3. Review the PRD and change the label to `forge:prd-approved` to continue

That's it. Forge will carry the ticket through the full pipeline with approval gates at each stage.

!!! tip "Local development shortcut"
    Set `FORGE_REQUIRE_PROJECT_CONFIG=false` in `.env` and configure `GITHUB_KNOWN_REPOS` / `GITHUB_DEFAULT_REPO` to skip the Jira project property setup. See the [Developer Guide](developer-guide.md) for details.

## Next Steps

- Read the [Feature Workflow](guide/feature-workflow.md) to understand each stage
- See the [Developer Guide](developer-guide.md) for payload-based testing and debugging
- Check out [Reference: Configuration](reference/config.md) for all environment variables
