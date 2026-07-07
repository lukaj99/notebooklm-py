# NotebookLM API Backend

FastAPI REST API server for Google NotebookLM automation using `notebooklm-py`.

## Features

- **Notebook management**: List, create, rename, delete notebooks
- **Source management**: Add URLs, text, and YouTube videos to notebooks
- **Chat API**: Query notebooks with natural language
- **Artifacts**: Generate and download podcasts, videos, infographics, and more

## Quick Start

```bash
# Install dependencies
uv venv .venv
source .venv/bin/activate
uv pip install -e ../ -e .

# Login (from main notebooklm-py CLI)
notebooklm login

# Start the API server
notebooklm-api

# Or with custom settings
NOTEBOOKLM_API_HOST=0.0.0.0 NOTEBOOKLM_API_PORT=8000 notebooklm-api
```

## API Documentation

Once the server is running, visit:
- **Swagger UI**: http://127.0.0.1:8000/docs
- **OpenAPI JSON**: http://127.0.0.1:8000/openapi.json

## Configuration

Set environment variables with `NOTEBOOKLM_API_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `NOTEBOOKLM_API_HOST` | `127.0.0.1` | Bind address |
| `NOTEBOOKLM_API_PORT` | `8000` | HTTP port |
| `NOTEBOOKLM_API_STORAGE_PATH` | `None` | Custom auth storage path |
| `NOTEBOOKLM_API_LOG_LEVEL` | `info` | Logging level |

## Example Usage

```bash
# Health check
curl http://127.0.0.1:8000/api/v1/health

# List notebooks
curl http://127.0.0.1:8000/api/v1/notebooks

# Create a notebook
curl -X POST http://127.0.0.1:8000/api/v1/notebooks \
  -H "Content-Type: application/json" \
  -d '{"name": "My Notebook"}'

# Add a source
curl -X POST http://127.0.0.1:8000/api/v1/notebooks/{id}/sources \
  -H "Content-Type: application/json" \
  -d '{"type": "url", "content": "https://example.com/article"}'

# Ask a question
curl -X POST http://127.0.0.1:8000/api/v1/notebooks/{id}/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize the sources"}'
```

## Development

```bash
# Install with dev dependencies
uv pip install -e ".[dev]"

# Run tests
pytest

# Format code
ruff format src/ tests/

# Lint
ruff check src/ tests/
```

## Architecture

```
src/notebooklm_api/
├── main.py         # FastAPI app factory and CLI
├── config.py       # Settings management
├── deps.py         # Dependency injection
├── models/         # Pydantic models
│   ├── notebooks.py
│   ├── sources.py
│   ├── chat.py
│   └── artifacts.py
└── routes/         # API endpoints
    ├── notebooks.py
    ├── sources.py
    ├── chat.py
    └── artifacts.py
```

## Error Handling

The API returns appropriate HTTP status codes:

- `200` - Success
- `401` - Authentication error
- `404` - Notebook or source not found
- `422` - Validation error
- `429` - Rate limit exceeded
- `502` - NotebookLM API error

## License

MIT
