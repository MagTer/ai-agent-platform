---
phase: 01-infrastructure-hardening
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - docker-compose.yml
  - .env
  - .env.template
autonomous: true
requirements:
  - INFRA-01

must_haves:
  truths:
    - "Qdrant rejects requests without the correct API key"
    - "The agent service can still reach Qdrant after auth is enabled"
    - "A fresh deployment from .env.template produces an authenticated Qdrant setup"
  artifacts:
    - path: "docker-compose.yml"
      provides: "QDRANT__SERVICE__API_KEY env var in qdrant service block"
      contains: "QDRANT__SERVICE__API_KEY"
    - path: ".env"
      provides: "AGENT_QDRANT_API_KEY populated with generated key"
      contains: "AGENT_QDRANT_API_KEY="
    - path: ".env.template"
      provides: "AGENT_QDRANT_API_KEY placeholder with generation hint"
      contains: "openssl rand -hex 32"
  key_links:
    - from: ".env"
      to: "services/agent/src/core/runtime/config.py"
      via: "AGENT_QDRANT_API_KEY -> Settings.qdrant_api_key"
      pattern: "qdrant_api_key"
    - from: "docker-compose.yml"
      to: "qdrant container"
      via: "QDRANT__SERVICE__API_KEY env var enables auth"
      pattern: "QDRANT__SERVICE__API_KEY"
---

<objective>
Enable Qdrant API key authentication so the vector store cannot be read or written by
unauthenticated containers on the Docker network.

Purpose: Gate for Phase 3 (Retrieval Tool Core) — the rag_search tool must not be able to
bypass auth when calling Qdrant. Unauthenticated Qdrant is a security gap on any shared
Docker network.

Output: docker-compose.yml with QDRANT__SERVICE__API_KEY set, .env with AGENT_QDRANT_API_KEY
set, .env.template updated with generation hint, stack dev deploy health-checked.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/STATE.md
@.planning/phases/01-infrastructure-hardening/01-CONTEXT.md

<interfaces>
<!-- Extracted from source files. Use directly — no codebase exploration needed. -->

From services/agent/src/core/runtime/config.py (lines 67-71):
```python
qdrant_url: HttpUrl = Field(
    default=DEFAULT_QDRANT_URL,
    description="Base URL for the Qdrant vector database.",
)
qdrant_api_key: str | None = Field(default=None, description="Optional Qdrant API key.")
```
The field is already defined. It just needs a non-None value from the environment.

From docker-compose.yml (lines 140-166), current qdrant service block:
```yaml
  qdrant:
    image: qdrant/qdrant:v1.17.0
    expose:
      - "6333"
    volumes:
      - ./data/qdrant:/qdrant/storage
    healthcheck:
      test: ["CMD-SHELL", "timeout 5 bash -c '</dev/tcp/localhost/6333' || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
    deploy:
      resources:
        limits:
          memory: 512M
          cpus: '1.0'
```
No `environment:` block exists yet. One must be added.

From .env.template (lines 62-65):
```
# Qdrant vector database
AGENT_QDRANT_URL=http://qdrant:6333
AGENT_QDRANT_COLLECTION=agent-memories
AGENT_QDRANT_API_KEY=
```
The key is blank — needs a placeholder comment with generation command.
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Inject Qdrant API key into config files</name>
  <files>docker-compose.yml, .env, .env.template</files>

  <read_first>
    - docker-compose.yml — read the full qdrant service block (lines 140-166) before editing
    - .env — read current AGENT_QDRANT_API_KEY line to confirm it is blank
    - .env.template — read lines 62-70 to see current Qdrant section
  </read_first>

  <action>
    Step 1 — Generate the API key:
    ```bash
    openssl rand -hex 32
    ```
    Capture the output (e.g. `a3f7...`). This is KEY.

    Step 2 — Update docker-compose.yml: Add an `environment:` block to the `qdrant:` service
    immediately after the `expose:` block and before `volumes:`. The block must contain exactly:
    ```yaml
    environment:
      QDRANT__SERVICE__API_KEY: "${QDRANT__SERVICE__API_KEY}"
    ```
    Also add `QDRANT__SERVICE__API_KEY=${KEY}` to .env (same file that holds all other
    AGENT_* vars). Do NOT hardcode the key in docker-compose.yml — use variable substitution.

    Step 3 — Update .env: Set both variables:
    ```
    AGENT_QDRANT_API_KEY=<KEY>
    QDRANT__SERVICE__API_KEY=<KEY>
    ```
    The two env var names are different: AGENT_QDRANT_API_KEY is read by the Python Settings
    class; QDRANT__SERVICE__API_KEY is read by the Qdrant container.

    Step 4 — Update .env.template: Replace the blank `AGENT_QDRANT_API_KEY=` line with:
    ```
    AGENT_QDRANT_API_KEY=<generate with: openssl rand -hex 32>
    QDRANT__SERVICE__API_KEY=<same value as AGENT_QDRANT_API_KEY>
    ```

    Per D-07, D-08, D-10 from CONTEXT.md.
  </action>

  <verify>
    <automated>
      grep "QDRANT__SERVICE__API_KEY" /home/magnus/dev/ai-agent-platform/docker-compose.yml
      grep "AGENT_QDRANT_API_KEY=" /home/magnus/dev/ai-agent-platform/.env | grep -v "^#" | grep -v "=$"
      grep "openssl rand" /home/magnus/dev/ai-agent-platform/.env.template
    </automated>
  </verify>

  <acceptance_criteria>
    - `docker-compose.yml` contains `QDRANT__SERVICE__API_KEY` in the qdrant service environment block
    - `docker-compose.yml` uses `"${QDRANT__SERVICE__API_KEY}"` (variable reference, not hardcoded value)
    - `.env` has `AGENT_QDRANT_API_KEY=` set to a 64-char hex string (32 bytes = 64 hex chars)
    - `.env` has `QDRANT__SERVICE__API_KEY=` set to the same 64-char hex string
    - `.env.template` line for `AGENT_QDRANT_API_KEY` contains the text `openssl rand -hex 32`
    - `.env.template` line for `QDRANT__SERVICE__API_KEY` is present (not just AGENT_ variant)
  </acceptance_criteria>

  <done>All three files updated; no hardcoded secrets in docker-compose.yml</done>
</task>

<task type="auto">
  <name>Task 2: Verify RAGManager and MemoryStore pass api_key, then deploy and health-check</name>
  <files>
    services/agent/src/modules/rag/manager.py,
    services/agent/src/core/runtime/service.py
  </files>

  <read_first>
    - services/agent/src/modules/rag/manager.py — find QdrantClient instantiation, check if api_key kwarg is passed
    - services/agent/src/core/runtime/service.py — find MemoryStore / QdrantClient instantiation, check same
    - services/agent/src/core/runtime/config.py lines 67-72 — confirm qdrant_api_key field name
  </read_first>

  <action>
    Step 1 — Audit QdrantClient instantiations: Search for `QdrantClient(` in manager.py and
    service.py. For each instantiation, verify it includes `api_key=` passed from config/settings.
    The config field is `settings.qdrant_api_key` (type `str | None`).

    If `api_key=` is already present in the call — no change needed. If it is absent, add:
    ```python
    api_key=self._settings.qdrant_api_key,
    ```
    (or equivalent depending on how settings is accessed in that file).

    Only edit files where `api_key=` is genuinely absent from the QdrantClient constructor call.

    Step 2 — Deploy and health-check per D-09:
    ```bash
    cd /home/magnus/dev/ai-agent-platform && ./stack dev deploy
    ```
    Wait for the deploy to complete. Then verify Qdrant rejects unauthenticated requests:
    ```bash
    curl -s http://localhost:6333/collections
    ```
    Expected: HTTP 403 or `{"status":{"error":"Unauthorized"}}` (not a collections list).
    Note: port 6333 is only accessible if the commented-out `ports:` mapping is re-enabled
    for testing; otherwise test via the agent health endpoint instead:
    ```bash
    curl -s http://localhost:6333/healthz || curl -s $(grep AGENT_QDRANT_URL .env | cut -d= -f2)/healthz
    ```
    The agent service must still start healthy (stack dev deploy exits 0).
  </action>

  <verify>
    <automated>./stack dev deploy 2>&1 | tail -5</automated>
  </verify>

  <acceptance_criteria>
    - `./stack dev deploy` exits 0 (agent container starts and passes health check)
    - RAGManager QdrantClient call includes `api_key=` parameter (grep confirms)
    - MemoryStore or equivalent QdrantClient call in service.py includes `api_key=` parameter (grep confirms)
    - No Python import errors in agent container logs after deploy (`./stack dev logs | grep -i "error\|exception" | head -10` is clean)
  </acceptance_criteria>

  <done>
    Qdrant requires authentication; agent service connects successfully; stack dev deploy passes.
  </done>
</task>

</tasks>

<verification>
After both tasks complete:
1. `grep "QDRANT__SERVICE__API_KEY" docker-compose.yml` — confirms env var in qdrant block
2. `grep "AGENT_QDRANT_API_KEY=" .env | grep -v "=$"` — confirms key is non-empty
3. `./stack dev deploy` exit code is 0
4. Agent container logs show no QdrantClient authentication errors
</verification>

<success_criteria>
- Qdrant container starts with API key auth enabled (QDRANT__SERVICE__API_KEY set)
- Agent service passes qdrant_api_key to QdrantClient in both RAGManager and MemoryStore
- .env.template documents the required key with generation command
- stack dev deploy is green
</success_criteria>

<output>
After completion, create `.planning/phases/01-infrastructure-hardening/01-qdrant-auth-SUMMARY.md`
using the summary template at @$HOME/.claude/get-shit-done/templates/summary.md
</output>
