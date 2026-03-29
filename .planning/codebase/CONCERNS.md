---
focus: concerns
generated: 2026-03-28
---

# Codebase Concerns

**Analysis Date:** 2026-03-28

---

## Tech Debt

**Dispatcher async refactor deferred:**
- Issue: `orchestrator/dispatcher.py` line 380 contains `TODO: Refactor AgentService to be fully async generator`. The current code wraps a blocking call and simulates streaming.
- Files: `services/agent/src/orchestrator/dispatcher.py`
- Impact: Limits true backpressure; the streaming simulation adds latency and may drop events under load.
- Fix approach: Refactor `AgentService.execute_stream` to yield directly into the dispatcher without bridging.

**Legacy StepExecutorAgent still in service.py:**
- Issue: `AgentService` maintains two execution code paths: the SkillExecutor path and the legacy `StepExecutorAgent` path (comments at lines 437, 1620-1642 of `service.py`). Both are kept alive to support older plans and tests.
- Files: `services/agent/src/core/runtime/service.py`, `services/agent/src/core/agents/executor.py`
- Impact: Increases cognitive overhead and surface area for bugs. Any behaviour change must be mirrored or carefully gated.
- Fix approach: Fully migrate all execution to the SkillExecutor path and delete the `StepExecutorAgent` fallback once all callers are updated.

**Deprecated `consult_expert` plan steps still handled:**
- Issue: `supervisor_plan.py` actively migrates deprecated `consult_expert` steps to skills-native format at runtime on every plan evaluation.
- Files: `services/agent/src/core/agents/supervisor_plan.py` (lines 59, 258-319)
- Impact: Runtime migration cost on each plan; old plans never fail clearly.
- Fix approach: Enforce the new format in the planner prompt and remove migration shim once no legacy plans remain in production conversations.

**Deprecated config field still exposed:**
- Issue: `Settings.price_tracker_from_email` is marked `DEPRECATED: Use email_from_address instead` but still present in `config.py` line 226.
- Files: `services/agent/src/core/runtime/config.py`
- Impact: Confusing configuration surface; two env vars could silently conflict.
- Fix approach: Remove field after confirming no deployments still set `AGENT_PRICE_TRACKER_FROM_EMAIL`.

**Source code volume-mounted into production container:**
- Issue: `docker-compose.yml` mounts `./services/agent/src:/app/src` into the running agent container. This means any host-side file change is immediately live without a rebuild.
- Files: `docker-compose.yml` line 53
- Impact: Reduces image reproducibility and blurs the boundary between dev and prod. A compromised dev machine directly exposes production code.
- Fix approach: Remove the src volume mount from `docker-compose.yml` (keep only in `docker-compose.dev.yml`). Require a rebuild for production changes.

**Large admin files approaching or exceeding split threshold:**
- Issue: `admin_contexts.py` is 2452 lines and `admin_price_tracker.py` is 1689 lines. The CLAUDE.md threshold for HTML separation is 40KB / 500 HTML lines, but the Python modules themselves are oversized.
- Files: `services/agent/src/interfaces/http/admin_contexts.py`, `services/agent/src/interfaces/http/admin_price_tracker.py`
- Impact: Hard to navigate, high merge conflict risk, high test fixture complexity.
- Fix approach: Extract sub-routers for distinct tab/feature groups (e.g., skill quality tab endpoints, workspace tab endpoints) into separate files.

**Qdrant API key optional with blank default:**
- Issue: `AGENT_QDRANT_API_KEY` is empty in `.env.template`. Qdrant runs unauthenticated by default in both dev and production compose files.
- Files: `.env.template` line 65, `docker-compose.yml`
- Impact: Any container on the same Docker network can read or write the vector store without authentication.
- Fix approach: Generate a random API key and set it in both `.env.template` and Qdrant's startup config.

---

## Known Bugs

**HITL draft extraction relies on fragile regex parsing:**
- Symptoms: If the `requirements_drafter` skill outputs text that does not exactly match `DRAFT READY` or `Type:` markers, `_extract_draft_from_messages` returns `None` and the HITL approval flow yields a user-visible `"Could not extract draft data"` error.
- Files: `services/agent/src/core/runtime/hitl.py` (lines 305-365)
- Trigger: Any LLM variation in the output format of `requirements_drafter.md`, or whitespace/formatting changes.
- Workaround: None; the user must restart the conversation.

**HITL approval keywords are Swedish/English mixed and hardcoded:**
- Symptoms: The resume logic explicitly checks for `"ja"`, `"nej"`, `"approve"`, `"yes"`, `"no"`, `"cancel"` (lines 120-132 in `hitl.py`). Any other phrasing silently falls through to the normal resume path.
- Files: `services/agent/src/core/runtime/hitl.py`
- Trigger: User types "confirmed", "looks good", "do it", etc.
- Workaround: User must use exact keywords.

---

## Security Considerations

**Agent trusts X-OpenWebUI-* headers without any signing or HMAC:**
- Risk: The agent service identifies the user from `X-OpenWebUI-User-Email`, `X-OpenWebUI-User-Role`, and similar headers forwarded by Open WebUI. If any container on the same Docker network can reach port 8000, it can impersonate any user by injecting these headers.
- Files: `services/agent/src/core/auth/header_auth.py`, `services/agent/src/interfaces/http/admin_auth.py`
- Current mitigation: Agent port 8000 is `expose`-only (not `ports`), relying on Docker network isolation.
- Recommendations: Add a shared secret or HMAC signature between Open WebUI and the agent to prove header authenticity. Document the trust boundary explicitly in the compose file.

**OAuth token plaintext fallback in production decrypt path:**
- Risk: `decrypt_token()` in `oauth_models.py` (lines 104-111) silently returns ciphertext-as-plaintext when `InvalidToken` is raised. This fallback was intended for tokens stored before encryption was introduced (PR #162). It is still active and will accept any token that fails Fernet decryption — including corrupted or attacker-supplied data if the DB is compromised.
- Files: `services/agent/src/core/db/oauth_models.py`
- Current mitigation: DB access requires credentials; Docker network isolation.
- Recommendations: Log a high-severity alert when plaintext fallback is triggered; set a deadline to retire the fallback once all pre-encryption tokens have been refreshed.

**No encryption key rotation mechanism for CredentialService:**
- Risk: `CredentialService` uses a single Fernet key from `AGENT_CREDENTIAL_ENCRYPTION_KEY`. If the key is compromised or needs to be rotated, all stored credentials become unreadable until re-entered by users.
- Files: `services/agent/src/core/auth/credential_service.py`
- Current mitigation: Error is logged and `None` is returned; users are prompted to re-enter credentials.
- Recommendations: Implement multi-key Fernet (`MultiFernet`) to allow rotation without service interruption. Document a rotation runbook.

**`_SanitizingSpanProcessor` mutates OTel span internals via `_attributes`:**
- Risk: The sanitizer accesses the private `span._attributes` dict to strip secrets before export. This is not part of the public OTel SDK API and may break on OTel SDK upgrades.
- Files: `services/agent/src/core/observability/tracing.py` (lines 183, 197)
- Current mitigation: Sensitive keyword list covers common cases.
- Recommendations: Use the supported `SpanLimits` and custom processors at export time, or submit span events only after sanitization.

**Rate limiting uses in-process memory (slowapi) — resets on restart:**
- Risk: `slowapi` tracks request counts in process memory. A restart or container redeploy resets all counters, defeating brute-force limits on `/auth/oauth` (5/minute) during rolling deploys.
- Files: `services/agent/src/core/middleware/rate_limit.py`
- Current mitigation: Single-process deployment; limits are applied.
- Recommendations: Back the limiter with Redis for persistence across restarts if the platform scales to multiple replicas.

---

## Performance Considerations

**HITL skill messages serialized into `conversation_metadata` JSON column:**
- Problem: The entire skill message history (potentially many LLM turns) is stored as JSON in `conversation_metadata` when a HITL pause occurs. For long skill conversations, this column can grow large.
- Files: `services/agent/src/core/runtime/hitl.py`, `services/agent/src/core/db/models.py` (Conversation model)
- Cause: `pending_hitl` dict contains the full `skill_messages` list including raw LLM messages.
- Improvement path: Store only a reference (e.g., a slice of Message IDs) and reconstruct from the messages table on resume.

**Span log file read is synchronous and scans the full JSONL file:**
- Problem: The Diagnostic API reads `data/spans.jsonl` synchronously (via `asyncio.to_thread`) by scanning all records. For high-traffic deployments the file can grow to many MB between rotations.
- Files: `services/agent/src/core/observability/debug_logger.py` (lines 145-200), `services/agent/src/core/diagnostics/service.py`
- Cause: No index; full linear scan per diagnostic query.
- Improvement path: Consider appending to a rolling indexed store (e.g., SQLite FTS) or limiting scan to the last N MB of the file.

**DB query for `SystemConfig` on every request when quality eval is enabled:**
- Problem: `is_quality_eval_enabled()` in `debug_logger.py` caches the result for 30 seconds, but under high load the 30-second TTL means frequent DB hits from multiple concurrent requests.
- Files: `services/agent/src/core/observability/debug_logger.py` (lines 41-73)
- Cause: Module-level global cache is not request-scoped.
- Improvement path: Extend TTL or push the toggle into `Settings` (loaded at startup) with admin-triggered reload.

**Prompt history capped at 50 messages but no per-message size limit:**
- Problem: `MAX_PROMPT_HISTORY_MESSAGES = 50` in `service.py` prevents unbounded message count, but individual messages can be arbitrarily large (e.g., tool results from web fetches or code analysis).
- Files: `services/agent/src/core/runtime/service.py` (line 57)
- Cause: No token or byte budget on individual messages in history.
- Improvement path: Apply token counting before passing history to the LLM; truncate or summarise old messages when budget is exceeded.

---

## Scalability Constraints

**In-process scheduler — not horizontally scalable:**
- Constraint: `SchedulerAdapter` runs as an `asyncio.Task` inside the agent process with a 60-second polling loop and `MAX_CONCURRENT_JOBS = 3`. Multiple replicas would trigger duplicate job executions.
- Files: `services/agent/src/interfaces/scheduler/adapter.py`
- Current capacity: Single-replica deployments only.
- Scaling path: Add a `locked_by` / `lock_expires_at` column to `scheduled_jobs` for distributed locking, or migrate to a dedicated job queue (e.g., Celery, Dramatiq, or pg-cron).

**In-memory OTel metrics snapshot lost on restart:**
- Constraint: `_metric_snapshot` in `metrics.py` is a module-level dict that resets to zero on every container restart. Cumulative metrics (error counts, total requests) are not persisted.
- Files: `services/agent/src/core/observability/metrics.py` (lines 71-87)
- Scaling path: Export metrics to an external OTLP collector (already supported via `OTEL_EXPORTER_OTLP_ENDPOINT`); use the external store for dashboard queries instead of the in-memory snapshot.

**Qdrant on single container with bind-mounted local storage:**
- Constraint: Qdrant data lives in `./data/qdrant` on the host. No replication, no backup automation.
- Files: `docker-compose.yml` (lines 140-165)
- Scaling path: Move to Qdrant Cloud or configure snapshots/backups. Not suitable for high-availability without additional ops work.

**DB connection pool fixed at 10+20 connections with no env override:**
- Constraint: `pool_size=10, max_overflow=20` is hardcoded in `engine.py`. In a high-concurrency scenario (scheduler + streaming requests + admin API), PostgreSQL can be exhausted with no knob to tune without a code change.
- Files: `services/agent/src/core/db/engine.py`
- Scaling path: Expose `AGENT_DB_POOL_SIZE` and `AGENT_DB_MAX_OVERFLOW` as environment variables.

---

## Fragile Areas

**Skill self-correction loop (REPLAN up to 3 times, RETRY once per step):**
- Files: `services/agent/src/core/runtime/service.py` (line 1110: `max_replans = 3`), `services/agent/src/core/skills/executor.py` (lines 249, 346)
- Why fragile: A flaky LLM response can burn 3 replans and up to `max_turns * 4` LLM calls before aborting. Failure modes are log-only; the user sees a final error without context.
- Safe modification: Always test the supervisor prompt changes with `test_step_executor.py` and `test_executor_agent.py`. Add integration scenario tests for ABORT paths.
- Test coverage: Covered in `src/core/tests/test_executor_agent.py` but not for all HITL+REPLAN combinations.

**SkillQualityAnalyser can autonomously modify skill markdown files:**
- Files: `services/agent/src/core/runtime/skill_quality.py`
- Why fragile: When `skill_quality_evaluation_enabled=true` and `debug_enabled=true`, the analyser uses an LLM to rewrite skill `.md` files in `skills/`. A bad LLM output could corrupt or break skill definitions. The container has the `skills/` directory mounted read-write (`docker-compose.yml` line 51 shows `ro` but this needs verification).
- Safe modification: Keep `skill_quality_evaluation_enabled` disabled unless actively monitoring. Always review skill changes via git diff before deploying. Verify the skills volume mount is truly read-only in prod.
- Test coverage: Integration tests exist in `src/modules/price_tracker/tests/` but not for the autonomous file-write path.

**HITL coordinator hardcoded to `requirements_drafter`/`requirements_writer` skills:**
- Files: `services/agent/src/core/runtime/hitl.py` (lines 134-172)
- Why fragile: HITL handoff logic is conditionally branched on exact skill names. Adding a new skill that needs HITL confirmation requires modifying `hitl.py` directly — there is no plugin or registry mechanism.
- Safe modification: Treat HITL as a generic protocol; any new skill needing user confirmation must add an explicit branch.

**`_SanitizingSpanProcessor` accesses `span._attributes` (private API):**
- Files: `services/agent/src/core/observability/tracing.py` (lines 177-198)
- Why fragile: Mutating `_attributes` is unsupported and will silently fail or raise `AttributeError` on future OTel SDK versions.
- Safe modification: Pin OTel SDK version tightly; add a test that confirms sanitization still works after any SDK upgrade.

---

## Dependency Risks

**LiteLLM as the sole LLM gateway:**
- Risk: All LLM calls route through the LiteLLM proxy container. A LiteLLM bug, breaking API change, or memory OOM (currently capped at 2G) causes complete loss of agent functionality.
- Impact: Entire platform is unavailable until LiteLLM recovers or is restarted.
- Migration plan: `LiteLLMClient` wraps only `httpx` calls to a standard OpenAI-compatible endpoint. Swapping the gateway is feasible but requires testing model-specific response parsing (especially `reasoning_content` handling for `gpt-oss-120b:exacto`).

**OpenRouter as the upstream LLM provider:**
- Risk: Primary model `openai/gpt-oss-120b:exacto` routes via ZDR through third-party providers (Groq, DeepInfra, Novita). Provider availability, rate limits, and model availability are not under platform control.
- Impact: Model-specific failures (e.g., tool-calling regressions, reasoning_content format changes) appear as platform bugs.
- Migration plan: `AGENT_LITELLM_MODEL` env var allows switching models. Add a fallback model config to LiteLLM for automatic failover.

**`litellm` image not pinned to digest:**
- Risk: `docker-compose.yml` uses `ghcr.io/berriai/litellm:v1.63.8` (a mutable tag). Image content can change on re-pull even with the same tag.
- Files: `docker-compose.yml` (line 6), note the TODO comment on line 3-5 acknowledging this.
- Impact: Non-reproducible deployments; supply chain risk.
- Migration plan: Pin to SHA digest as documented in the TODO comment.

**`qdrant/qdrant` image not pinned to digest:**
- Risk: `docker-compose.yml` uses `qdrant/qdrant:v1.17.0` (mutable tag), with the same TODO comment as LiteLLM.
- Files: `docker-compose.yml` (lines 140-145).
- Migration plan: Same as LiteLLM — pin to SHA digest.

**`open-webui` image at `0.8.3` (mutable tag):**
- Risk: Open WebUI is a rapidly evolving project; breaking changes to the admin API or auth headers could silently affect the platform.
- Files: `docker-compose.yml` (line 197).
- Migration plan: Pin to SHA digest; test admin and auth flows after any version bump.

**`slowapi` rate limiter backed by in-process memory:**
- Risk: `slowapi` (based on `limits`) has no Redis backend configured. If the package is abandoned or its API changes, all rate limiting silently degrades.
- Files: `services/agent/src/core/middleware/rate_limit.py`
- Migration plan: The Limiter constructor already accepts a `storage_uri` parameter; pointing it at Redis is a one-line change.

---

## Test Coverage Gaps

**Legacy `tests/` directories excluded from CI testpaths:**
- What is not tested: `tests/core/`, `tests/integration/`, `tests/interfaces/`, `tests/unit/` contain tests for diagnostics, MCP client, runtime, tools, and multi-user flows but are excluded from `pyproject.toml` testpaths to avoid CI failures from DB-dependent tests.
- Files: `services/agent/tests/core/`, `services/agent/tests/integration/`, `services/agent/tests/unit/`
- Risk: These tests (including `test_multi_user_flow.py`, `test_db_persistence.py`, `test_streaming_api.py`) can drift silently from the current codebase.
- Priority: High — multi-user isolation and streaming are critical paths.

**HITL resume and approval flows lack automated tests:**
- What is not tested: The full HITL lifecycle (`requirements_drafter` pause -> user approves -> `requirements_writer` executes) has no end-to-end scenario test.
- Files: `services/agent/src/core/runtime/hitl.py`
- Risk: Regex extraction changes or skill instruction changes can silently break the approval flow.
- Priority: High — this is user-facing and breaks silently.

**SkillQualityAnalyser file-write path has no test:**
- What is not tested: The autonomous skill-file modification code in `skill_quality.py` (invoked when both `debug_enabled` and `skill_quality_evaluation_enabled` are true) has no test covering the actual file write.
- Files: `services/agent/src/core/runtime/skill_quality.py`
- Risk: LLM output format change could produce malformed skill YAML without detection.
- Priority: Medium — feature is disabled by default.

**MCP OAuth flow and connection pool not tested in CI:**
- What is not tested: `tests/core/mcp/` is excluded from CI testpaths. MCP client connection, reconnect, and OAuth token exchange code has unit tests that do not run in CI.
- Files: `services/agent/tests/core/mcp/`
- Risk: MCP connection regressions not caught before deployment.
- Priority: Medium.

**Architecture baseline violations accepted rather than fixed:**
- What is not tested: `stack check --architecture` uses `.architecture-baseline.json` to suppress known import violations. The baseline file contains `[]` (empty), meaning all current violations are already encoded. Future code can introduce new cross-layer imports without failing the check if the baseline is not maintained.
- Files: `services/agent/.architecture-baseline.json`
- Risk: Architecture erosion goes undetected.
- Priority: Medium — run `stack check --architecture` periodically and review new violations.

---

## Operational Concerns

**`.env` management is entirely manual:**
- Problem: There is no secret management integration (Vault, AWS Secrets Manager, etc.). The single `.env` file on the host contains all production secrets. Rotation requires manual edits and service restarts.
- Files: `.env.template`, `docker-compose.yml` (`env_file: .env`)
- Risk: Secret sprawl; no audit trail for secret access; accidental git commit of `.env`.
- Improvement path: For production hardening, mount secrets via Docker Swarm secrets, Kubernetes Secrets, or a sidecar vault agent.

**`data/spans.jsonl` accumulates on host disk with configurable but potentially slow rotation:**
- Problem: Span logs are written to `./services/agent/data/spans.jsonl` (bind-mounted via `docker-compose.yml` line 57). The rotation is size-based but controlled by `AGENT_TRACE_SPAN_MAX_SIZE_MB` and `AGENT_TRACE_SPAN_MAX_FILES`. Default values are not visible in `.env.template`.
- Files: `services/agent/src/core/observability/tracing.py`, `docker-compose.yml`
- Risk: On high-traffic deployments, disk can fill before rotation triggers.
- Improvement path: Add `AGENT_TRACE_SPAN_MAX_SIZE_MB` and `AGENT_TRACE_SPAN_MAX_FILES` to `.env.template` with explicit defaults.

**`git clone` workspaces stored in `/tmp/agent-workspaces` — not persisted across restarts:**
- Problem: The `git_clone` tool defaults to `AGENT_WORKSPACE_BASE=/tmp/agent-workspaces`. On container restart, all cloned repositories are lost. The `workspaces` DB table records `status` but the data is gone.
- Files: `services/agent/src/core/tools/git_clone.py`, `.env.template` (line 84)
- Risk: Users lose in-progress workspaces on any restart; DB and filesystem state diverge.
- Improvement path: Mount a persistent volume at `/app/workspaces` and set `AGENT_WORKSPACE_BASE` to point there. Update `docker-compose.yml` to include the volume.

**Traefik TLS cert and dynamic config stored as bind-mounted host files:**
- Problem: `docker-compose.prod.yml` mounts `./config/traefik-dynamic.yml` and the Let's Encrypt ACME storage from the host. A misconfigured host path silently breaks TLS on deployment.
- Files: `docker-compose.prod.yml`
- Risk: Silent TLS failure; no health check validates cert validity.
- Improvement path: Add a smoke-test step to `stack deploy` that verifies the TLS certificate is valid and not near expiry.

---

*Concerns audit: 2026-03-28*
