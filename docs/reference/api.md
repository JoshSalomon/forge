# API Endpoints

Forge exposes a FastAPI server that receives webhooks and serves metrics.

## Base URL

```
http://localhost:8000
```

## Endpoints

### Health Check

```http
GET /api/v1/health
```

Returns HTTP 200 when the API server is running. Does not check worker or Redis connectivity.

**Response:**

```json
{"status": "ok"}
```

---

### Jira Webhook

```http
POST /api/v1/webhooks/jira
```

Receives Jira webhook events. Validates the signature and enqueues the event for async processing by the worker.

**Required headers:**

| Header | Description |
|--------|-------------|
| `X-Hub-Signature` | HMAC-SHA256 of the request body, using `JIRA_WEBHOOK_SECRET` |

**Supported events:**

- `jira:issue_created` — triggers new workflow if `forge:managed` label is present
- `jira:issue_updated` — handles label changes (approvals, retry)
- `jira:issue_commented` — handles Q&A and revision requests

Returns HTTP 200 immediately. Processing is asynchronous.

---

### GitHub Webhook

```http
POST /api/v1/webhooks/github
```

Receives GitHub webhook events. Validates the signature and enqueues for async processing.

**Required headers:**

| Header | Description |
|--------|-------------|
| `X-Hub-Signature-256` | HMAC-SHA256 of the request body, using `GITHUB_WEBHOOK_SECRET` |

**Supported events:**

- `pull_request` — PR opened, closed, synchronized
- `pull_request_review` — human review submitted
- `check_run` — CI check completed
- `issue_comment` — PR comment (for `/forge skip-gate` commands)

Returns HTTP 200 immediately. Processing is asynchronous.

---

### Prometheus Metrics

```http
GET /metrics
```

Exposes Prometheus-format metrics for the API server.

**Key metrics:**

| Metric | Type | Description |
|--------|------|-------------|
| `forge_workflows_started_total` | Counter | Workflows started, labeled by type |
| `forge_workflows_completed_total` | Counter | Workflows completed |
| `forge_ci_fix_attempts_total` | Counter | CI fix attempts |
| `forge_agent_duration_seconds` | Histogram | Agent execution time |

Worker metrics are available separately at `http://localhost:8001/metrics`.

## Webhook Configuration

### Jira

Configure under **Project Settings → Webhooks**:

- **URL:** `https://your-server.com/api/v1/webhooks/jira`
- **Events:** Issue created, Issue updated, Comment created
- **Secret:** Set `JIRA_WEBHOOK_SECRET` in `.env`

### GitHub

Configure under **Repository Settings → Webhooks**:

- **URL:** `https://your-server.com/api/v1/webhooks/github`
- **Content type:** `application/json`
- **Events:** Pull requests, Pull request reviews, Check runs, Issue comments
- **Secret:** Set `GITHUB_WEBHOOK_SECRET` in `.env`
