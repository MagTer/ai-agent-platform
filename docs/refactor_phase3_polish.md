# Phase 3: The Professional Polish - Implementation Plan

## Goal
Elevate the agent's behavior from "naive chatbot" to "senior developer assistant" by implementing surgical file editing, a strict ReAct loop, and safety guardrails, while adhering to strict Python standards.

## User Review Required
> [!IMPORTANT]
> **Safety Mechanism**: Tools marked with `requires_confirmation` (like `git_push`) will pause execution. The `AgentService` will return a `CONFIRMATION_REQUIRED` status/message to the API caller. The caller must re-submit the request (potentially with a "confirm" flag or decision) to proceed. This changes the API contract slightly for dangerous tools.

> [!WARNING]
> **Deprecating `write_file`**: The standard `write_file` (overwriting entire files) is being deprecated in favor of `edit_file_search_replace`. Agents must now "read -> locate -> replace".

## Proposed Changes

### 1. Smart File Editing (Surgical Precision)
**File**: `services/agent/src/core/tools/filesystem.py`

#### [NEW] `EditFileTool`
Implement `EditFileTool(path, target, replacement)`.
- **Logic**:
    1. Read file content.
    2. Search for `target` (exact match, checking for multiple occurrences).
    3. If 0 matches: Return Error "Target not found".
    4. If >1 matches: Return Error "Target ambiguous (found N matches)".
    5. If 1 match: Replace with `replacement` and write back.
    6. Return success message with line numbers modified.

#### Deprecation
- Ensure `tool_registry` does not load `write_file` if it exists. (Confirmed it is not currently in `filesystem.py`, ensure it is not added).

### 2. Safety Guardrails
**File**: `services/agent/src/core/tools/base.py`
- Add `requires_confirmation: bool = False` to `Tool` base class.

**File**: `services/agent/src/core/agents/executor.py`
- Modify `StepExecutorAgent._run_tool`:
    - Check `tool.requires_confirmation`.
    - If `True`, allow execution ONLY if `args` contains explicit `confirmation_token` or similar override (for now, raise `ToolConfirmationRequired` exception if not confirmed, or return a specific status).
    - *Plan*: Raise `ToolConfirmationRequired(tool_name, args)`.

### 3. Strict ReAct Loop (The Engine)
**File**: `services/agent/src/core/core/service.py` (`AgentService.handle_request`)

- **Refactor Loop**:
    - Change the iteration over `plan.steps` to be robust.
    - Catch `ToolConfirmationRequired` from `executor.run`.
        - If caught, STOP the loop.
        - Persist a `Message` with `role="system"`: "Requesting confirmation for action...".
        - Return `AgentResponse` indicating confirmation needed.
    - **System Observation**:
        - Ensure `Executor` returns observations.
        - `AgentService` already appends `role="tool"` (or `system` masking as tool) messages to history.
        - **Refine**: Ensure the format is strictly `Tool [name] output:\n[content]`.

**File**: `services/agent/src/core/agents/executor.py`
- Ensure `StepResult` clearly carries the observation.

### 4. Coding Standards & Compliance
- **Strict Typing**: Use `mypy` compliant signatures (`list[str]`, `str | None`).
- **Docstrings**: Google-style docstrings for all new methods.
- **Imports**: Clean imports.

## Verification Plan

### Automated Tests
**Run**: `pytest services/agent/src/core/tests/test_filesystem.py`
- **New Tests**:
    - `test_edit_file_success`: Single match replacement.
    - `test_edit_file_not_found`: Error.
    - `test_edit_file_ambiguous`: Error on duplicates.
    - `test_edit_file_traversal`: Security check.

**Run**: `python services/agent/src/manual_test_confirmation.py` (New Script)
- Mock `Executor` with a `SafeTool` and `DangerousTool`.
- Verify `SafeTool` runs.
- Verify `DangerousTool` raises confirmation requirement.

### Manual Verification
1. **Edit File**:
    - Use the agent to "Change 'logging.info' to 'logging.error' in `services/agent/src/logging_demo.py`".
    - Verify it invokes `edit_file` and succeeds.
2. **Safety**:
    - Ask agent to "Run `rm -rf /`" (Simulated dangerous command).
    - Verify it asks for confirmation (or returns confirmation status).
