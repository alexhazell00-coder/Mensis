# MENSIS 0.1 — architecture and implementation notes

This document records the first implementation decisions made from the product handoff. It distinguishes the confirmed product requirements from the scope choices made to keep the initial release small and testable.

## 1. Scope and decisions

### Kept as product constraints

- Local-first operation; no mandatory provider, paid API, telemetry, or cloud dependency.
- Persistent, inspectable task state rather than chat-only answers.
- A replaceable provider boundary and a discoverable tool registry.
- Tool-first handling for deterministic requests.
- Independent enforcement of a least-privilege tool allow-list.
- Explicit evidence and verification labels; model-authored summaries are not treated as proof.
- A small single-process deployment with SQLite rather than premature services or agents.

### Deliberate 0.1 scope

- Python 3.11+, FastAPI, Pydantic, Uvicorn, SQLite, and a static HTML/CSS/JavaScript client. There is no frontend build system or separate service.
- One local Ollama adapter, optional and disabled unless an installed model is named in configuration. This is an initial integration, not a final provider decision.
- Three read-only tools: host/runtime status, workspace-filesystem storage, and bounded workspace search.
- One persistent task queue with 1 worker by default (configurable from 1 to 4). No broker, scheduler, approval engine, plugin installer, or autonomous code-generation environment.
- No user accounts or remote-access mode. Bind to loopback for a normal installation.

The implementation intentionally does not provide file writes, shell execution, service restarts, module installation, or self-deployment. These capabilities need a fuller policy, approval, isolation, recovery, and audit design before they are introduced.

## 2. Component diagram

```text
Browser (same-origin static UI)
  |  GET /api/health, /api/tools, /api/tasks
  |  POST /api/tasks, /cancel, /resume
  v
FastAPI application (single process; loopback by default)
  +-- API / request validation
  +-- TaskEngine -------------------- OllamaProvider (optional, server-side)
  |     |  persistent queue           |  bounded HTTP request
  |     |  bounded worker threads      |  tool-call responses only
  |     v                             v
  |   TaskStore <--- SQLite       ToolRegistry <--- Pydantic input schemas
  |                         \\        |
  |                          \\---- Supervisor (fixed permission allow-list)
  |                                    |
  |                              Read-only tools
  |                              +-- system status
  |                              +-- workspace storage
  |                              +-- workspace file search
  v
web/ static assets
```

The provider does not receive direct filesystem, process, or database handles. A model-suggested function call is treated as untrusted input: the registry checks that the tool exists, the supervisor checks its permission, and Pydantic validates its arguments before the handler runs.

## 3. Request and execution flow

1. `POST /api/tasks` validates and stores the user's request as `queued`, and appends a `received` event in the same SQLite transaction.
2. A worker atomically claims the task (`queued → planning`) so duplicate queue entries cannot execute it twice.
3. A small deterministic router handles obvious status, storage, and file-search requests directly. These operations do not use a model.
4. Other requests use the configured local Ollama adapter if one is available. With no model, the task is marked `blocked` with the available capabilities; it is never silently forwarded to another provider.
5. Model tool calls are checked against the registry and fixed permissions. A task is limited to four successful tool calls per model-assisted run. Provider requests are non-streaming and time-limited.
6. Tool outputs and status transitions are appended to the task event history. Search results include paths and match types, not file excerpts.
7. The engine transitions through `verifying` before it finishes. A deterministic tool result is labelled `verified` as evidence from the invoked read-only tool. A model-authored summary is labelled `unverified`, even when tool evidence is also attached.
8. Queued work survives restart. Interrupted in-progress work is re-queued at startup because the only initial handlers are bounded read operations. A pending cancellation is preserved as `cancelled` instead.

### Lifecycle

```text
RECEIVED → QUEUED → PLANNING → RUNNING → VERIFYING → COMPLETED
                         |          |          |
                         +----------+----------+→ BLOCKED / FAILED / CANCELLED

BLOCKED or FAILED -- explicit user resume --> QUEUED (attempt increments)
```

The database is authoritative for status; the in-memory queue is only a dispatch mechanism. A cancellation request can stop work at checkpoints. It cannot forcibly abort an in-flight Python handler or an Ollama HTTP call; the provider timeout bounds that wait.

## 4. Initial data model

SQLite is selected to keep the first installation self-contained. Connections are short-lived, foreign keys are enabled, the journal uses WAL mode, and an index supports recent/status task queries.

### `tasks`

| Field | Purpose |
|---|---|
| `id` | UUID task identifier and primary key. |
| `request` | Original, user-submitted request. |
| `status` | Lifecycle state: queued, planning, running, verifying, completed, blocked, failed, cancelled. |
| `plan_json` | Current concise plan, as JSON. |
| `result_json` | Structured answer, verification label, and evidence, when available. |
| `verification` | pending, verified, unverified, or not_applicable. |
| `error` | Bounded failure/block reason; no provider credentials are stored here. |
| `cancel_requested` | Cooperative cancellation flag. |
| `attempt` | Starts at 1; increments on explicit resume. |
| timestamps | Created, updated, started, and finished UTC ISO timestamps. |

### `task_events`

Append-only task history with an integer event ID, task foreign key, event type, readable message, JSON data, and UTC timestamp. Events include received, plan-created, status-changed, tool-started/succeeded/denied, verification, cancellation, and recovery. Sensitive filesystem content is not captured because the search tool never returns snippets.

### Not yet modelled

Conversation history, long-term memory, approvals, module versions, schedules, backups, growth proposals, users, and audit policies are deliberately outside this MVP schema. They should be added only alongside explicit retention, access-control, and migration requirements.

## 5. HTTP API contract

All endpoints are same-origin and live under `/api`. Request and tool inputs are validated; unknown tool fields are rejected.

| Method | Path | Contract |
|---|---|---|
| `GET` | `/api/health` | Core/provider status, workspace label, worker count, available-tool count. Does not probe or wake a model. |
| `GET` | `/api/tools` | Tool IDs, descriptions, versions, permissions, timeouts, and JSON input schemas. |
| `POST` | `/api/tasks` | Body `{ "request": "..." }`, 1–2000 non-blank characters; returns the persistent task with HTTP 202. |
| `GET` | `/api/tasks?limit=50&status=...` | Recent tasks, bounded to 1–100 and optionally filtered by status. |
| `GET` | `/api/tasks/{id}` | Task, plan, result, and ordered event history. |
| `POST` | `/api/tasks/{id}/cancel` | Cooperative cancellation request; reports whether it was accepted and current task state. |
| `POST` | `/api/tasks/{id}/resume` | Re-queues only blocked/failed tasks; other statuses return HTTP 409. |

FastAPI's generated schema is available at `/docs` on the local service. There is no CORS policy for cross-origin clients.

## 6. Tool contracts

Each initial tool declares a stable ID, display name, description, version, permission, Pydantic input schema, expected timeout, and handler. The current tool set is:

| ID | Permission | Input | Output |
|---|---|---|---|
| `system.status` | `system.read` | Empty object. | OS/runtime versions, CPU count, memory totals where available, and configured workspace storage. |
| `system.disk_usage` | `system.read` | Empty object. | Total/used/free capacity for the filesystem containing the configured workspace. |
| `files.search` | `workspace.read` | Query (1–200 characters), max results (1–50). | Relative paths, match type, scan count, truncation indicator; no file contents. |

Search is constrained to the resolved configured root; it does not follow symlinks, skips hidden directories, common generated folders, and likely secret files, and caps the scan at 5,000 files and 12 MiB of file reads. It skips large files. These are pragmatic safeguards, not a general-purpose sandbox.

The supervisor's initial allow-list is fixed to `system.read` and `workspace.read`. There are no write-capable tools to approve. Tool `timeout_seconds` is contract metadata; handlers are bounded internally and model requests have a hard network timeout. If long-running or untrusted tools are added, enforceable process-level timeouts and isolation will be required.

## 7. Interface outline

```text
┌─────────────┬──────────────────────────────────────────────────────────┐
│ MENSIS      │ MENSIS / Overview            SELF-HOSTED       09:42     │
│ Overview    ├──────────────────────────────────────────────────────────┤
│ Task queue  │ Your work, accounted for.      Local session            │
│ Tool registry│ Delegate a task. Follow the evidence.                    │
│             ├─────────────────────────────────┬────────────────────────┤
│ CORE ONLINE │ New task + read-only shortcuts  │ Task inspector         │
│ No model    │ In progress / Verified / Alerts │ plan, status, events,  │
│             │ Recent persistent task history │ result, evidence       │
│ LOCAL DATA  │                                 │ Read-only boundary     │
└─────────────┴─────────────────────────────────┴────────────────────────┘
```

The client is static, uses same-origin relative API URLs, escapes user/model/tool strings before rendering, and polls for status updates every four seconds while visible. It remains usable without an AI provider. The queue and tool registry have dedicated views; task details expose the event log and structured evidence.

## 8. Security and privacy boundaries

### Enforced in 0.1

- Normal run command binds to `127.0.0.1`; the management UI has no authentication and must not be exposed to a network.
- No cross-origin browser API, user-configurable provider endpoint, or remote AI fallback.
- Only three registered read-only tools; the supervisor rejects any permission outside its fixed allow-list.
- Workspace search resolves paths under one configured root, ignores symlinks/hidden paths and likely secret names, caps work, and does not return text content.
- Model-generated tool names and arguments are untrusted; unknown tools and invalid schemas are denied.
- File/page/tool text is not authorization. The system prompt reinforces this, while the allow-list enforces it independently.
- API task text is bounded; errors and tool outputs are retained locally in SQLite.

### Known boundary gaps

- There is no authentication, CSRF defense, multi-user isolation, encrypted database, secret manager, independent supervisor process, OS sandbox, module signature scheme, or remote-access support.
- Tool reads run in the MENSIS process. An administrator-configured Ollama URL is trusted configuration and can direct server-side requests; do not set it to an untrusted endpoint.
- The task/event database may contain user requests, searched paths, model answers, and host statistics. No retention/deletion/backup UI exists yet.
- The read-only guarantee is limited to the initial tool registry. It does not make the application production-ready for internet exposure.

These limitations are why the initial release does not accept arbitrary shell commands, write operations, generated modules, or remote access. Adding any such capability requires a new threat review and tests before release.

## 9. Tests and validation targets

Automated tests cover direct routing (no model call for deterministic work), workspace confinement/secret skipping/symlink handling, tool-schema rejection and supervisor denial, task lifecycle persistence/recovery, blocked behavior without a model, and HTTP task/health/tool flows.

The current acceptance slice is intentionally small:

- Fresh installation starts with documented commands and no provider or paid key.
- A deterministic supported task persists, reaches a terminal status, and includes structured evidence.
- An unsupported request is clearly blocked if no model is configured.
- A failed/blocked request can be explicitly resumed; attempts remain visible.
- The user can inspect tool permissions, task events, and verification state in the browser.
- Browser requests use same-origin paths; simple tools do not require model inference.

No CPU/RAM/startup or model-speed claims are made yet. Baselines still need measurement on representative low-resource Linux and higher-end systems. Ollama tool-call compatibility must be tested against each selected model before recommending it.

## 10. Unresolved decisions

| Decision | Current state / next action |
|---|---|
| Open-source licence | Not selected. Maintainer must choose and add it before public release. |
| Supported OS | Python-only starting point; Linux is a likely first-class target but not yet formally supported. Test Linux/macOS/Windows before claims. |
| Minimum hardware | Not established; benchmark app overhead separately from model inference. |
| Initial AI model | Ollama is a replaceable candidate path; select and test a tool-capable model and review its licence. No model is auto-downloaded. |
| Network/authentication | Loopback only in this release. Decide account and remote-access model before any network exposure. |
| Data retention/privacy | Define task history retention, memory controls, deletion, backup, restore, and migrations. |
| Approvals and permissions | Read-only baseline only. Design scoped approvals before adding consequential actions. |
| Modules and self-improvement | Define compatibility/signing/isolation and release/rollback contracts before implementing a sandbox or module manager. |
| Performance targets | Measure startup, idle CPU/RAM, API latency, tool dispatch, and model latency before publishing numerical goals. |
