# MENSIS

**A local-first personal assistant task workspace.** MENSIS is being built as a free, open-source, modular, self-hosted assistant that can do useful work while keeping the user in control.

> **Current stage: first runnable foundation (0.1.0).** This repository contains a small, usable read-only task MVP—not the full autonomous platform described in the product handoff. It has a persistent task queue, a web console, a bounded tool registry, and an optional local Ollama adapter. It does not yet edit files, administer services, install modules, or deploy generated code.

## What works today

- A dark, responsive web workspace for submitting tasks and inspecting plans, progress, results, and activity.
- Persistent task and event history in SQLite; queued and interrupted read-only work is recovered on startup.
- A background worker with bounded concurrency, cancellation checkpoints, retry-by-resume for blocked/failed tasks, and explicit verification labels.
- Three read-only tools: basic system status, storage for the configured workspace filesystem, and bounded file search inside the configured workspace.
- Tool-first routing for obvious deterministic requests, so those checks do not invoke a model.
- Optional server-side Ollama chat/tool calling. MENSIS does not download a model or contact a provider unless a model is explicitly configured.
- No telemetry, subscription, paid API, or cloud service is required.

## Quick start

Requires Python 3.11 or newer. No Node.js build step is needed.

**Windows:** run `setup.bat` from Command Prompt, or `.\setup.bat` from PowerShell. The script creates `.venv`, installs the application dependencies, uses `%LOCALAPPDATA%\MENSIS` for data by default, and starts the server on `127.0.0.1:8000`. Press Ctrl+C to stop it. Existing `MENSIS_*` environment settings are preserved.

**Linux/macOS:**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The initial tools work without an AI provider; try **Check system health**, **Check workspace disk usage**, or search the configured workspace.

By default, application state is stored under `~/.local/share/mensis/`, and the searchable workspace is the `workspace/` folder inside it. To use a different folder, stop MENSIS and set the environment variables before starting it:

```bash
export MENSIS_DATA_DIR="$HOME/.local/share/mensis"
export MENSIS_WORKSPACE="$HOME/Documents/mensis-workspace"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

MENSIS creates the configured workspace if it does not exist. File search stays within that directory, does not follow symlinks, skips hidden paths, common build folders and likely secret files, and never returns file-content snippets. Review the workspace path before using it.

## Optional local AI

The deterministic tools remain available without a model. For general natural-language planning, configure a locally managed [Ollama](https://ollama.com/) service and a model that supports tool calling. MENSIS never downloads a model automatically.

```bash
export MENSIS_OLLAMA_BASE_URL="http://127.0.0.1:11434"
export MENSIS_OLLAMA_MODEL="your-installed-tool-capable-model"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The model endpoint is called by the MENSIS server, not by the user's browser. An unconfigured request is not silently sent elsewhere. Model-generated summaries are marked **unverified**, and only the registered read-only tools are available to the model. The server-side endpoint is administrator-controlled; configure only an endpoint you trust.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `MENSIS_DATA_DIR` | `~/.local/share/mensis` | App data and default workspace location. |
| `MENSIS_DB_PATH` | `~/.local/share/mensis/mensis.sqlite3` | SQLite database file. |
| `MENSIS_WORKSPACE` | `$MENSIS_DATA_DIR/workspace` | The only directory searched by `files.search`. |
| `MENSIS_MAX_WORKERS` | `1` | Background workers, clamped to 1–4. |
| `MENSIS_OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Administrator-configured Ollama endpoint. |
| `MENSIS_OLLAMA_MODEL` | unset | Installed model name; unset means no provider calls. |
| `MENSIS_PROVIDER_TIMEOUT` | `45` seconds | Maximum wait for one model request, capped at 120 seconds. |
| `MENSIS_HOST` / `MENSIS_PORT` | `127.0.0.1` / `8000` | Used by `python -m app.main`. |

## Development and tests

```bash
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

For local development, keep the server bound to loopback. The current console has **no authentication** and is not designed for LAN or internet exposure. Do not bind it to a public interface or reverse proxy it externally. Remote access, account setup, approvals, module lifecycle, and sandboxed code development are future work.

See [`docs/architecture.md`](docs/architecture.md) for the component diagram, initial data model and API, UI outline, security boundaries, validation status, and open decisions.

## Project decisions still open

- Select and add a project-wide open-source licence before public release. No licence has been chosen in this repository yet; do not infer licensing terms from the source being visible.
- Confirm first-class operating systems and minimum hardware from testing.
- Validate Ollama/tool-calling behaviour against selected models and document their individual licences.
- Design authentication, approval policies, durable scheduling, backups, module management, and the isolated growth sandbox before expanding privileges.

MENSIS is inspired by dark, restrained science-fiction and gothic interfaces; it does not include third-party artwork or logos.
