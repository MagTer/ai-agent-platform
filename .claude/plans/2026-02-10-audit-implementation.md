# Audit Implementation Plan -- 2026-02-10

**Source:** `.claude/plans/2026-02-10-comprehensive-audit.md`
**Executor:** Engineer agents (Sonnet) with parallel batches
**Approach:** 4 sequential batches, each with 4-5 parallel agents grouped by file independence

---

## Batch 1: Critical Quick Wins (5 parallel agents)

All agents in this batch touch completely independent files. Run simultaneously.

### Agent 1A: Database Indices & Composite Index
**Files:** `alembic/versions/` (new migration), `services/agent/src/core/db/models.py`
**Audit refs:** #1 (FK indices), #2 (composite index), #16-P (FK indices)

**Tasks:**
1. Create new Alembic migration adding:
   - Index on `conversations.context_id` (if missing after verification)
   - Index on `sessions.conversation_id` (if missing after verification)
   - Index on `messages.session_id` (if missing after verification)
   - **Composite index** on `Conversation(platform, platform_id)` -- eliminates full table scan on every Telegram request
   - Composite index on `sessions(session_id, created_at)` for retention query optimization
2. Verify indices are also declared in SQLAlchemy models (`models.py`) with `index=True` or `Index()` constructs
3. Add downgrade that drops the new indices

**Verification:** `./stack check` passes, migration applies cleanly

---

### Agent 1B: OAuth Security Fixes
**Files:** `services/agent/src/interfaces/http/admin_auth_oauth.py`
**Audit refs:** #4 (timing attack), #11 (Cookie Secure flag)

**Tasks:**
1. Replace `stored_state != state` with `not secrets.compare_digest(stored_state, state)` at line ~168
2. Add `import secrets` if not present
3. Fix Cookie Secure flag: change `secure=request.url.scheme == "https"` to check `X-Forwarded-Proto` header:
   ```python
   is_secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
   ```
   Apply to both the JWT cookie (~line 392) and OAuth state cookie (~line 95-96)

**Verification:** `./stack check` passes

---

### Agent 1C: Docker Hardening
**Files:** `docker-compose.yml`, `docker-compose.prod.yml`
**Audit refs:** #5 (resource limits), #6 (log rotation)

**Tasks:**
1. Add resource limits to all services in both compose files:
   ```yaml
   deploy:
     resources:
       limits:
         memory: 512M    # agent service
         cpus: '1.0'
       reservations:
         memory: 256M
   ```
   Suggested limits:
   - agent: 1G memory, 2 CPU
   - postgres: 512M memory, 1 CPU
   - qdrant: 512M memory, 1 CPU
   - litellm: 512M memory, 1 CPU
   - traefik: 256M memory, 0.5 CPU
2. Add log rotation to all services:
   ```yaml
   logging:
     driver: "json-file"
     options:
       max-size: "10m"
       max-file: "5"
   ```
3. Fix agent healthcheck: increase timeout from 5s to 10s, add `start_period: 60s` if missing

**Verification:** `docker compose -f docker-compose.yml config` and `docker compose -f docker-compose.prod.yml config` validate

---

### Agent 1D: Admin Portal Double-Submit Prevention
**Files:** `services/agent/src/interfaces/http/admin_credentials.py`, `services/agent/src/interfaces/http/templates/admin_mcp.html`
**Audit refs:** #20 (double-click duplicates), #18 (MCP setTimeout race)

**Tasks:**
1. In admin_credentials.py: Add `disabled` attribute to submit buttons during form submission in the inline JavaScript. Pattern:
   ```javascript
   btn.disabled = true;
   btn.textContent = 'Saving...';
   // ... fetch call ...
   .finally(() => { btn.disabled = false; btn.textContent = 'Save'; });
   ```
2. In admin_mcp.html: Fix the `setTimeout(300ms)` race condition in `editServer` -- replace with proper event-driven approach (wait for DOM update, not arbitrary timeout)
3. Apply the same disabled-state pattern to MCP form submissions

**Verification:** `./stack check` passes

---

### Agent 1E: Stack CLI -- Architecture Checks & Import Safety
**Files:** `services/agent/src/stack/cli.py`, `services/agent/src/stack/checks.py`
**Audit refs:** #3 (enable arch checks), #13 (confirmation prompts), #14 (CI parity)

**Tasks:**
1. In cli.py: Remove or change `skip_architecture=True` to `skip_architecture=False` at ~line 952
2. In cli.py: Add confirmation prompts to `openwebui import` (~line 1520) and `n8n import` (~line 1410):
   ```python
   if not Confirm.ask("This will import data. Continue?"):
       return
   ```
3. In checks.py: Add `-x` flag to local pytest invocation to match CI behavior (~line 357-378). Add a `--ci` flag that also enables `-n auto`

**Verification:** `./stack check` passes

---

## Batch 2: Security & Performance (5 parallel agents)

Start after Batch 1 completes. All agents touch independent files.

### Agent 2A: git_clone URL Sanitization
**Files:** `services/agent/src/core/tools/git_clone.py`
**Audit refs:** #9 (credential-embedded URLs)

**Tasks:**
1. Add URL validation that rejects:
   - URLs with embedded credentials (`https://user:pass@host/repo`)
   - Non-HTTPS/SSH protocols (verify existing check at line 92-93 is comprehensive)
   - URLs with suspicious characters (newlines, null bytes)
2. Use `urllib.parse.urlparse()` to validate and check for `username`/`password` components
3. Add unit test for the validation in `services/agent/src/core/tests/`

**Verification:** `./stack check` passes, new test passes

---

### Agent 2B: WebFetcher Async Cache
**Files:** `services/agent/src/modules/fetcher/__init__.py`
**Audit refs:** #7 (sync file I/O), #6-F (cache no size limit)

**Tasks:**
1. Convert cache read/write from synchronous `open()` to `aiofiles`:
   - Replace `with open(cache_path, 'r')` with `async with aiofiles.open(cache_path, 'r')`
   - Replace `with open(cache_path, 'w')` with `async with aiofiles.open(cache_path, 'w')`
2. Add cache size limit: max 1000 entries or 100MB disk usage
3. Add LRU eviction: when limit exceeded, delete oldest cache files
4. Check if `aiofiles` is in dependencies, add if not

**Verification:** `./stack check` passes

---

### Agent 2C: MCP Lightweight Ping
**Files:** `services/agent/src/core/mcp/client.py`
**Audit refs:** #10 (MCP ping uses list_tools)

**Tasks:**
1. Replace `list_tools()` call in ping/health check (~line 431-433) with a lightweight operation:
   - Option A: Use MCP `ping` method if available in the protocol
   - Option B: Use a simple `list_tools()` with a very short timeout and cache the result
   - Option C: Just check if the transport connection is alive
2. Keep the existing 5-second timeout
3. Ensure the health check still returns meaningful status

**Verification:** `./stack check` passes

---

### Agent 2D: N+1 Query Fix in Admin Contexts
**Files:** `services/agent/src/interfaces/http/admin_contexts.py`
**Audit refs:** #8 (N+1 query, 3 extra queries per context)

**Tasks:**
1. Replace the N+1 pattern at lines 197-214 with a single query using:
   - `func.count()` with `outerjoin` and `group_by` to get all counts in one query
   - Or use `selectinload` / subquery loading for related counts
2. Pattern:
   ```python
   stmt = (
       select(
           Context,
           func.count(distinct(Conversation.id)).label('conv_count'),
           func.count(distinct(OAuthToken.id)).label('oauth_count'),
           ...
       )
       .outerjoin(Conversation, Conversation.context_id == Context.id)
       .outerjoin(OAuthToken, OAuthToken.context_id == Context.id)
       .group_by(Context.id)
   )
   ```
3. This should reduce 1+3N queries to 1 query

**Verification:** `./stack check` passes

---

### Agent 2E: Subprocess Timeouts in Stack CLI
**Files:** `services/agent/src/stack/cli.py`, `services/agent/src/stack/tooling.py`
**Audit refs:** #12 (missing timeouts on subprocess calls)

**Tasks:**
1. Audit all `subprocess.run()` and `subprocess.Popen()` calls in cli.py and tooling.py
2. Add `timeout=` parameter to all calls that lack it:
   - Docker build commands: `timeout=600` (10 min)
   - Docker compose up/down: `timeout=120` (2 min)
   - Health checks: `timeout=30`
   - Git commands: `timeout=60`
   - Image inspection: `timeout=30`
3. Wrap timeout exceptions with user-friendly error messages:
   ```python
   except subprocess.TimeoutExpired:
       console.print("[red]Command timed out after Xs[/red]")
   ```

**Verification:** `./stack check` passes

---

## Batch 3: Testing & Observability (4 parallel agents)

Start after Batch 2 completes.

### Agent 3A: SkillExecutor Context Validation Tests
**Files:** `services/agent/src/core/tests/test_skill_executor_context.py` (new)
**Audit refs:** #H (testing gaps -- context ownership untested)

**Tasks:**
1. Write tests for `_validate_context_ownership()` in executor.py:
   - Test: valid context_id with matching user -> allowed
   - Test: valid context_id with non-matching user -> denied
   - Test: missing context_id -> behavior
   - Test: caching behavior (_validated_contexts)
   - Test: database query failure handling
2. Write tests for tool scoping enforcement:
   - Test: skill can only access tools listed in its frontmatter
   - Test: attempt to use unlisted tool -> error
3. Use `pytest.mark.asyncio` and `AsyncMock` patterns

**Verification:** `./stack check` passes, new tests pass

---

### Agent 3B: UnifiedOrchestrator Plan Parsing Tests
**Files:** `services/agent/src/core/tests/test_unified_orchestrator.py` (new or extend existing)
**Audit refs:** #H (plan parsing 0 dedicated tests)

**Tasks:**
1. Write tests for plan parsing in unified_orchestrator.py:
   - Test: valid JSON plan -> parsed correctly
   - Test: malformed JSON -> graceful error
   - Test: missing required fields -> error with details
   - Test: extra/unknown fields -> ignored gracefully
   - Test: empty plan -> appropriate error
   - Test: plan with invalid step types -> error
2. Write tests for fallback plan generation on error:
   - Test: error triggers fallback plan
   - Test: fallback plan is valid and executable

**Verification:** `./stack check` passes, new tests pass

---

### Agent 3C: CI Coverage Reporting
**Files:** `.github/workflows/ci.yml`
**Audit refs:** #J (no coverage in CI)

**Tasks:**
1. Add `pytest-cov` to CI pytest invocation:
   ```yaml
   run: pytest -x -n auto --dist=worksteal --cov=services/agent/src --cov-report=xml
   ```
2. Add coverage report upload step (GitHub Actions artifact):
   ```yaml
   - uses: actions/upload-artifact@v4
     with:
       name: coverage-report
       path: coverage.xml
     if: always()
   ```
3. Verify `pytest-cov` is in dev dependencies

**Verification:** CI YAML validates

---

### Agent 3D: Fix Swallowed Exceptions
**Files:** Various (survey first, then fix highest-impact ones)
**Audit refs:** #G (30+ exception handlers without logging)

**Tasks:**
1. Search for bare `except:` and `except Exception:` blocks that don't log
2. Prioritize fixes in critical paths:
   - `core/core/service.py` (AgentService)
   - `core/skills/executor.py` (SkillExecutor)
   - `orchestrator/dispatcher.py` (Dispatcher)
   - `interfaces/http/app.py` (request handlers)
3. Add `logger.exception("...")` or `logger.error("...", exc_info=True)` to each
4. Do NOT change control flow -- only add logging

**Verification:** `./stack check` passes

---

## Batch 4: Config, Dead Code & Documentation (4 parallel agents)

Start after Batch 3 completes.

### Agent 4A: Clean Up .env.template
**Files:** `.env.template`
**Audit refs:** #M (12 unused vars, 8 missing vars)

**Tasks:**
1. Remove unused env vars: AGENT_SQLITE_STATE_PATH, HOMEY_API_TOKEN, ENABLE_QDRANT, and other confirmed-unused vars
2. Add missing env vars with comments: POSTGRES_URL, GITHUB_TOKEN, AGENT_WORKSPACE_BASE, and others used in code
3. Add section headers and documentation for each variable
4. Verify no code references the removed variables (grep first)

**Verification:** `./stack check` passes

---

### Agent 4B: Remove Dead Code
**Files:** `services/agent/src/core/tools/search_code.py`, `services/agent/src/core/tools/github.py`, `services/agent/src/core/tools/qa.py`
**Audit refs:** #E (4 unregistered tool classes)

**Tasks:**
1. Verify these tools are NOT referenced in `config/tools.yaml`
2. Verify no skill references them in frontmatter `tools:` lists
3. Verify no code imports them
4. Delete the files: search_code.py, github.py, qa.py
5. Remove any imports of these in `__init__.py` files

**Verification:** `./stack check` passes

---

### Agent 4C: Documentation Fixes
**Files:** `CLAUDE.md`, `docs/ARCHITECTURE.md`
**Audit refs:** #B (documentation accuracy 93%)

**Tasks:**
1. CLAUDE.md: Fix `admin_dashboard.py` reference -> `admin_portal.py` (line ~686, 115)
2. CLAUDE.md: Fix protocol names: `MemoryProtocol` -> `MemoryStore`, `LLMProtocol` -> `LiteLLMClient`, `ToolProtocol` -> `Tool` (line ~287-289)
3. ARCHITECTURE.md: Fix `core/core/app.py` path -> `interfaces/http/app.py` (line ~309)
4. CLAUDE.md: Update NAV_ITEMS example to reflect actual 13 items (or note it's a subset)
5. ARCHITECTURE.md: Fix "3-layer" -> "4-layer" consistency (line ~29)
6. CLAUDE.md: Remove or fix `get_memory_store`/`get_tool_registry` examples (line ~296-305)

**Verification:** No broken references remain

---

### Agent 4D: Admin Portal Fetch Error Handling
**Files:** All `admin_*.py` files, `templates/admin_mcp.html`
**Audit refs:** #18 (80%+ fetch calls lack error handling)

**Tasks:**
1. Create a standard fetch wrapper pattern with error handling:
   ```javascript
   async function safeFetch(url, options) {
       try {
           const resp = await fetch(url, options);
           if (!resp.ok) {
               showToast('Error: ' + resp.statusText, 'error');
               return null;
           }
           return resp;
       } catch (e) {
           showToast('Network error', 'error');
           return null;
       }
   }
   ```
2. If a shared `fetchWithCsrf` or similar wrapper already exists, add error handling there
3. Apply to all fetch calls in admin portal modules

**Verification:** `./stack check` passes

---

## Execution Summary

| Batch | Agents | Parallel? | Estimated Duration | Key Risk |
|-------|--------|-----------|-------------------|----------|
| 1 | 5 (1A-1E) | Yes | 15-30 min | DB migration must be tested |
| 2 | 5 (2A-2E) | Yes | 30-60 min | WebFetcher cache needs aiofiles dep |
| 3 | 4 (3A-3D) | Yes | 30-60 min | New test files need proper fixtures |
| 4 | 4 (4A-4D) | Yes | 20-40 min | Doc changes need accuracy verification |

**Total: 18 parallel agents across 4 sequential batches**

### Pre-flight Checks
Before starting any batch:
1. `git stash` or commit any uncommitted changes
2. Create a new branch: `git checkout -b fix/comprehensive-audit-phase-N`
3. After each batch: `./stack check` to verify nothing is broken

### Post-batch Protocol
After each batch completes:
1. Run `./stack check` (full quality gate)
2. Fix any failures before proceeding to next batch
3. Commit batch changes with descriptive message
4. Optionally create a PR per batch for easier review
