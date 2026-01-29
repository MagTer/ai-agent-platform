# Refactor Phase 2: RACS Parity (Context Awareness)

## 1. Goal
Achieve feature parity with "Remote Agentic Coding System" (RACS) by implementing strict Context and Session management. The Agent must be creating and managing its own working environments ("Bootstrapping").

## 2. Architecture Changes

### A. New Component: `ContextManager`
**File:** `src/core/context_manager.py` (NEW)

**Responsibility:**
- Physical management of contexts (directories, git clones).
- Database persistence of `Context` records.
- "Context-Aware" logic (e.g., verifying if a context is active/valid).

**Interface:**
```python
class ContextManager:
    def __init__(self, session: AsyncSession, settings: Settings): ...
    
    async def create_context(self, name: str, type: str, args: dict) -> Context:
        """
        1. Check content collision (DB).
        2. Perform physical setup:
           - 'git': git clone args['url'] to contexts_dir/name
           - 'local': verify path exists (MVP: symlink or just reference?)
           - 'virtual': mkdir contexts_dir/name
        3. Create DB record.
        """
        
    async def get_context(self, name: str) -> Context | None: ...
    
    async def get_details(self, context_id: uuid.UUID) -> dict:
        """Return config, default_cwd, variables."""
```

### B. Refactor: `AgentService`
**File:** `src/core/core/service.py`

**Change 1: System Command Layer (The "Interceptor")**
Before normal processing (Step 1), valid if input starts with `/`.
- `/init <name> <type> <args>` -> Calls `ContextManager.create_context`, then switches current conversation to it.
- `/switch <name>` -> Updates `Conversation.context_id` to point to new context. Updates `current_cwd` to context root.
- `/status` -> Returns markdown table of Current Context, CWD, Active Session.

**Change 2: Strict Hierarchy in `handle_request`**
Flow:
1. **Load Conversation**:
   - IF exists: Use it.
   - IF NOT exists: Create new `Conversation` attached to 'default' context (create 'default' if missing).
2. **Resolve Context**:
   - Load `Context` from `conversation.context_id`.
   - **CRITICAL**: If physical path missing (e.g. restart), warn or re-bootstrap? (MVP: warn).
3. **Load Session**:
   - IF exists & active: Use it.
   - IF missing or `/reset`: Create new `Session`.
4. **Injection**:
   - Inject `Context.config` (e.g. "project_type": "python") into Planner Prompt.
   - Inject `Context.default_cwd` as `current_cwd` for Tool execution if not set to specific value.

### C. System Commands Implementation
**Location:** `src/core/system_commands.py` (NEW)
**Pattern:** Simple dispatcher or class methods.

```python
async def handle_system_command(cmd: str, args: list[str], service: AgentService, session: AsyncSession) -> str:
    if cmd == "/init":
        # parsing args...
        return await service.context_manager.create_context(...)
    # ...
```

## 3. Implementation Steps

### Step 1: `ContextManager` + Tests
1. Create `src/core/context_manager.py`.
2. Implement `create_context` (Git/Virtual support).
3. Test with `tests/test_context_manager.py` (Mock filesystem/git).

### Step 2: System Command Parser
1. Create `src/core/system_commands.py`.
2. Implement parsing logic for `/init`, `/switch`.

### Step 3: `AgentService` Integration
1. Inject `ContextManager` into `AgentService`.
2. Add interception logic in `handle_request`.
3. Update specific logic to persist `context_id` changes.

### Step 4: Verification
1. Verify `/init my-project virtual` creates folder and DB record.
2. Verify `/switch my-project` changes context.
3. Verify bootstrapping: `/init gemini git https://github.com/google-deepmind/gemini` (Mocked URL).

## 4. Risks & Mitigations
- **Breaking Changes:** `handle_request` logic change might break existing `test_service.py` or `test_app.py`.
  - *Mitigation:* We will update tests to mock the new Context flow. Use "default" context in tests.
- **Git Cloning:** Real cloning is slow/network-dependent.
  - *Mitigation:* `ContextManager` should use `run_command` (async) but for MVP we might need to block or background it?
  - *Decision:* Await it. Bootstrapping is rare and important. Show "Bootstrapping..." message to user (User can't see it in HTTP request/response model easily unless streaming).
  - *Constraint:* HTTP Request timeout.
  - *Better Approach:* `ContextManager` creates the record and starts the clone. The /init command returns "Initialization started. Check /status".
  - *MVP:* Just await it (assume small repos or fast network for now, or Mock for tests).
