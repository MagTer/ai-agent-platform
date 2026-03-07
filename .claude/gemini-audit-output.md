YOLO mode is enabled. All tool calls will be automatically approved.
Loaded cached credentials.
YOLO mode is enabled. All tool calls will be automatically approved.
Bash command parsing error detected for command: cat > audit_report.md << 'EOF' Syntax Errors: [ `Error node: "<< 'EOF'" at 0:22` ]
# Comprehensive Codebase Audit Report: AI Agent Platform

## Severity Counts Summary

| Domain | CRITICAL | HIGH | MEDIUM | LOW | POSITIVE |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **A: Documentation vs Code** | 0 | 1 | 2 | 1 | 1 |
| **B: Component Functionality** | 0 | 0 | 2 | 0 | 3 |
| **C: Dead Code** | 0 | 0 | 1 | 2 | 1 |
| **D: Logging & Observability** | 0 | 1 | 1 | 0 | 2 |
| **E: Testing Gaps** | 0 | 2 | 1 | 0 | 1 |
| **F: Stack Script** | 0 | 1 | 2 | 0 | 0 |
| **G: CI/CD Pipeline** | 0 | 1 | 2 | 1 | 2 |
| **H: Agent Config & Architecture** | 0 | 0 | 1 | 1 | 2 |
| **I: Refactoring Opportunities** | 2 | 2 | 0 | 0 | 0 |
| **J: Configuration Review** | 1 | 1 | 1 | 0 | 1 |
| **K: Admin Portal HTML/JS** | 1 | 1 | 2 | 1 | 0 |
| **L: Docker & Infrastructure** | 1 | 0 | 1 | 1 | 2 |
| **M: Database & Migrations** | 0 | 1 | 2 | 0 | 1 |

---

## Domain A: Documentation vs Code

- **[HIGH] Stale Documented File Paths:** `CLAUDE.md` references `shared/chunk_filter.py`, but it appears the actual functionality lives in `shared/content_classifier.py`.
- **[MEDIUM] Doc vs Practice (File Sizes):** `CLAUDE.md` explicitly mandates that HTML files exceeding 500 lines or 40KB must be separated. However, `admin_context_detail.html` (2143 lines), `price_tracker_dashboard.html` (1742 lines), and `admin_diagnostics.html` (1449 lines) severely violate this rule.
- **[MEDIUM] CI Workflow Mismatch:** `CLAUDE.md` instructs agents to run `./stack check` (which auto-fixes via Ruff and Black), but `.github/workflows/ci.yml` treats them as separate parallel jobs with `--check`. The documentation's "CI mode" (`--no-fix`) doesn't map perfectly to how the GitHub Actions run.
- **[LOW] Protocol Implementation Mismatch:** `docs.txt` notes `core/protocols/embedder.py` for `IEmbedder`, but `services/agent/src/core/protocols/` directories are sometimes referred to incorrectly compared to actual injection boundaries.
- **[POSITIVE] Excellent Multi-Tenant Docs:** The `docs/ARCHITECTURE.md` perfectly matches the PostgreSQL `models.py` schema regarding context, user, and credential isolation.

## Domain B: Component Functionality

- **[MEDIUM] MCP Auth Rotation Gap:** `McpServer` in `models.py` handles `set_auth_token` and `set_oauth_client_secret` statically via Fernet encryption, but lacks mechanisms for automatic key rotation or detecting invalid/expired tokens before tool execution.
- **[MEDIUM] Missing Subprocess Timeouts:** The `stack_cli_wrapper.py` executes `subprocess.run(cmd, ...)` without a timeout parameter, potentially hanging indefinitely if the underlying poetry command freezes.
- **[POSITIVE] Excellent Orchestrator Maturity:** The `SkillRegistry` and `SkillExecutor` cleanly isolate tools to specific Markdown-defined skills via `config/tools.yaml`.
- **[POSITIVE] Secure State Management:** `AgentService` enforces hierarchical RACS execution, correctly creating `context_id` and passing it to scoped tools.
- **[POSITIVE] Robust LiteLLM Gateway:** Configuration is fully decoupled into an OpenRouter gateway (`litellm/config.yaml`), preventing backend keys from leaking into the core application.

## Domain C: Dead Code

- **[MEDIUM] Unused Dependencies/Env Vars:** Several variables in `.env.template` are entirely unused by Python code (though some are consumed by Docker): `AGENT_ADMIN_API_KEY`, `AGENT_ADMIN_JWT_SECRET`, `ACME_EMAIL`.
- **[LOW] Misaligned Tools Config:** `tools.yaml` registers `claude_code` and `github_pr`, but these are barely integrated with standard workflows unless explicitly explicitly called out by `.claude/agents/*.md`.
- **[LOW] Empty PyCache Commits:** `.mypy_cache`, `.pytest_cache`, and `.ruff_cache` exist locally but should be strictly ignored in `.gitignore` to prevent cache bloat in the repository.
- **[POSITIVE] Clean Capabilities:** `skills/` Markdown files correctly and strictly reference valid registered tools from `tools.yaml`.

## Domain D: Logging & Observability

- **[HIGH] Missing Trace Context on Exceptions:** While `core/observability/error_codes.py` creates robust AI-readable diagnostics, unhandled exceptions in `admin_*.py` endpoints may not always attach the `trace_id` automatically to the resulting `HTTPException`.
- **[MEDIUM] Diagnostic API Security Risk:** The diagnostic API allows querying full traces (`/platformadmin/api/traces/{trace_id}`). If sensitive data (like prompts containing PII) isn't redacted in `spans.jsonl`, this exposes a data leakage vector.
- **[POSITIVE] Exceptional Error Formatting:** `format_error_for_ai()` is a brilliant mechanism for enabling the `StepSupervisorAgent` to parse, understand, and self-correct during tool failures.
- **[POSITIVE] Built-in OTel Spans:** Tracking debug events as OpenTelemetry span events in `data/spans.jsonl` rather than scattered log files drastically improves debuggability.

## Domain E: Testing Gaps

- **[HIGH] Test Coverage Reporting Disabled:** `.github/workflows/ci.yml` relies heavily on `pytest -x --testmon`, which explicitly disables test coverage reporting. There is no automated way to ensure new MRs meet coverage thresholds.
- **[HIGH] Untested Admin Views:** `admin_contexts.py` and `admin_price_tracker.py` handle sensitive state mutations (CredentialService, DB flushes) but lack robust integration test coverage mapping in `test_agent_scenarios.py`.
- **[MEDIUM] testmon SQLite Concurrency:** Running `PRAGMA wal_checkpoint(TRUNCATE)` in GitHub Actions cache can cause issues if parallel matrix jobs attempt to seed the cache simultaneously.
- **[POSITIVE] Semantic Testing Framework:** Level 3 testing (Golden queries, `semantic_eval` tool) is a highly robust way to catch prompt drifts and LLM logic regressions.

## Domain F: Stack Script

- **[HIGH] God-Object CLI:** `services/agent/src/stack/cli.py` is nearly 2000 lines long. It violates the Single Responsibility Principle and is incredibly difficult to navigate.
- **[MEDIUM] Unsafe Subprocesses:** `stack_cli_wrapper.py` executes without capturing `stderr` properly for end-user feedback if Poetry fails to initialize.
- **[MEDIUM] Missing Wait-For-Healthy:** Deployment commands in the stack script do not appear to robustly poll the `/healthz` endpoint before reporting "success" to the terminal.

## Domain G: CI/CD Pipeline

- **[HIGH] Unoptimized Docker Builds:** The `trivy` job runs `docker build` sequentially without leveraging BuildKit cache mounts (`--build-arg BUILDKIT_INLINE_CACHE=1` or `type=gha`). This wastes significant CI time.
- **[MEDIUM] Duplicate Ruff/Black Operations:** The stack script does Ruff and Black sequentially, and the CI duplicates this without utilizing `pre-commit` effectively in the Actions workflow.
- **[LOW] Permissive Trivy Ignores:** Trivy is set to `ignore-unfixed: true`, which is pragmatic but risks accumulating technical debt for HIGH/CRITICAL vulnerabilities that have no patch yet.
- **[POSITIVE] CodeQL SAST Integration:** Weekly and PR-based CodeQL scans successfully add a robust layer of static analysis.
- **[POSITIVE] Dependency Auditing:** `pip-audit` runs independently, enforcing strict supply chain security.

## Domain H: Agent Config & Architecture Guards

- **[MEDIUM] Static Validation Limitations:** The architecture validator (`validate_architecture` in `checks.py`) enforces rules strictly but might miss dynamic imports or dependency injection violations inside complex factories.
- **[LOW] Hardcoded Agent IDs:** Claude Code agent prompts rely heavily on matching exact models (`sonnet`, `haiku`, `opus`). If Anthropic deprecates these tags, the `.claude/agents/*.md` files will break silently.
- **[POSITIVE] Claude Context Management:** The `.claude/agents/` setup with strict instructions on *never* running destructive Git commands is an excellent operational guardrail.
- **[POSITIVE] Architecture Baseline Enforcement:** Checking against `.architecture-baseline.json` prevents accidental layer violations.

## Domain I: Refactoring Opportunities

- **[CRITICAL] Massive Admin Endpoints:** `admin_contexts.py` (2452 lines) and `admin_price_tracker.py` (1689 lines) are dangerously bloated. They mix routing, DB queries, presentation logic, and business logic. They must be split into `routers/`, `services/`, and `schemas/`.
- **[CRITICAL] Monolithic Templates:** `admin_context_detail.html` (2143 lines) and `price_tracker_dashboard.html` (1742 lines) contain extreme amounts of inline JS and CSS. These should be componentized (e.g., using Jinja includes/macros) and static assets should be moved to external files.
- **[HIGH] CLI Refactor:** `stack/cli.py` (1995 lines) must be broken down into modular command files (e.g., `commands/db.py`, `commands/test.py`, `commands/infra.py`).
- **[HIGH] Complex Conditional Nesting:** Tool execution and routing logic inside `StepExecutorAgent` has deep nesting for context injection, which could be refactored into a middleware/interceptor pattern.

## Domain J: Configuration Review

- **[CRITICAL] Hidden Environment Requirements:** `AZURE_DEVOPS_ORG_URL`, `CACHE_DIR`, `CACHE_TTL`, `CONTEXT_DATA_DIR`, `OBSIDIAN_AUTH_TOKEN`, `OTEL_EXPORTER_OTLP_ENDPOINT` are actively used in `os.getenv()` calls in the codebase but are completely absent from `.env.template`. New developers will face runtime crashes without knowing why.
- **[HIGH] Dangerous Defaults:** `default_cwd: Mapped[str] = mapped_column(String, default="/tmp")` in `Context` model. If multiple contexts default to `/tmp` without strict isolation, they could overwrite each other's workspaces.
- **[MEDIUM] Overloaded Configuration Models:** `SystemConfig` JSONB table is good, but mixed usage between `.env` for infrastructure and `SystemConfig` for feature flags causes split-brain configuration management.
- **[POSITIVE] Secure Credential Service:** Storing user credentials as Fernet-encrypted bytes via `UserCredential` and tying them directly to `context_id` is highly secure.

## Domain K: Admin Portal HTML/JS

- **[CRITICAL] Potential XSS Vectors:** Massive inline HTML templates (`admin_context_detail.html`) likely interpolate Jinja variables directly into inline JavaScript without utilizing safe JSON serialization (e.g., `{{ variable | tojson }}`). This exposes the portal to Cross-Site Scripting.
- **[HIGH] Lack of CSRF Protection:** State-mutating endpoints in `admin_*.py` do not appear to implement strict CSRF tokens. Since the portal is multi-tenant and authenticated via Entra ID, this is a severe vulnerability.
- **[MEDIUM] Error Swallowing:** Large single-page dashboards usually rely on Fetch API calls. If these fail natively, they might fail silently in the browser console without user feedback.
- **[LOW] Accessibility & Styling Consistency:** Mixing 2000+ lines of HTML/CSS makes enforcing accessible contrast ratios, ARIA labels, and responsive design nearly impossible.

## Domain L: Docker & Infrastructure

- **[CRITICAL] Exposed Docker Socket:** `docker-compose.prod.yml` mounts `- /var/run/docker.sock:/var/run/docker.sock:ro` directly into the Traefik container. If Traefik is compromised, an attacker can trivially achieve container escape and root access to the host. Use a secure proxy like `Tecnativa/docker-socket-proxy`.
- **[MEDIUM] Layer Bloat in Dockerfile:** The `agent` Dockerfile uses an `INCLUDE_NODEJS` and `INCLUDE_VAULT` ARG that conditionally installs massive dependencies (`nodejs`, `npm`, `@google/gemini-cli`). This balloons the image size and dramatically increases the attack surface for a Python application.
- **[LOW] Traefik Dev/Prod Parity:** Dev environment strips security headers explicitly defined in prod, reducing the confidence that dev testing accurately reflects production security posture.
- **[POSITIVE] Rootless Containers:** The agent Dockerfile explicitly creates `appgroup` and `appuser` (UID 1000) and executes uvicorn as a non-root user.
- **[POSITIVE] Strict Traefik Middleware:** Stripping `X-OpenWebUI-*` headers securely prevents privilege escalation via header spoofing.

## Domain M: Database & Migrations

- **[HIGH] SQLAlchemy Metadata Collision Risk:** The `Session` model defines `session_metadata: Mapped[dict] = mapped_column("metadata", JSONB)`. Naming a column exactly `"metadata"` overrides SQLAlchemy's internal `Base.metadata` registry, which can cause obscure migration and relation errors.
- **[MEDIUM] Missing Indices:** `ScheduledJob` defines `status` (`active`, `paused`, `error`), but lacks an index on it, meaning background workers polling for `active` jobs will trigger full table scans.
- **[MEDIUM] UTC Naive Datetimes:** `_utc_now()` deliberately strips `tzinfo` to return naive datetimes for PostgreSQL. While common, this can lead to timezone-aware mismatch bugs when interacting with Python's modern `datetime.now(UTC)` unless handled uniformly across all Pydantic schemas.
- **[POSITIVE] Excellent Cascade Definitions:** `cascade="all, delete-orphan"` is utilized correctly across all models (`UserContext`, `Workspace`, `Message`), ensuring that deleting a multi-tenant `Context` cleanly wipes all associated data without leaving orphans.
