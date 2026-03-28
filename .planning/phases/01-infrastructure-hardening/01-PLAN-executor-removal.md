---
phase: 01-infrastructure-hardening
plan: 03
type: execute
wave: 3
depends_on:
  - 01-PLAN-consult-expert-removal
files_modified:
  - services/agent/src/core/runtime/service.py
  - services/agent/src/core/agents/executor.py
  - services/agent/src/core/agents/__init__.py
  - services/agent/src/core/tests/test_executor_agent.py
  - services/agent/src/core/tests/test_step_executor.py
  - services/agent/src/core/tests/test_service.py
autonomous: true
requirements:
  - INFRA-02

must_haves:
  truths:
    - "service.py contains no StepExecutorAgent import or instantiation"
    - "The legacy else: branch in _execute_step_stream is gone — only the skill executor path exists"
    - "stack check passes with no new mypy or test errors"
  artifacts:
    - path: "services/agent/src/core/runtime/service.py"
      provides: "Single SkillExecutor execution path — no legacy fallback"
      contains: "skill_executor"
    - path: "services/agent/src/core/agents/executor.py"
      provides: "Deleted or empty file — StepExecutorAgent class gone"
  key_links:
    - from: "services/agent/src/core/runtime/service.py"
      to: "_execute_step_stream method"
      via: "if is_skill_step block only — no else branch"
      pattern: "is_skill_step"
---

<objective>
Remove the legacy StepExecutorAgent fallback code path from service.py and delete the
StepExecutorAgent class. After this change there is exactly one execution path: SkillExecutor.

Purpose: Single testable execution path is required before retrieval instrumentation begins.
Two live paths make it impossible to write reliable integration tests for the retrieval tool
(per D-01, D-02, D-03 from CONTEXT.md).

Output: service.py with the legacy else: branch removed; executor.py deleted; affected test
files updated to remove or replace StepExecutorAgent references; stack check green.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/PROJECT.md
@.planning/ROADMAP.md
@.planning/phases/01-infrastructure-hardening/01-CONTEXT.md

<interfaces>
<!-- Extracted from source. Use directly — no codebase exploration needed. -->

From services/agent/src/core/runtime/service.py — the fork in _execute_step_stream:

```python
# Lines 349-453 (condensed):
is_skill_step = plan_step.executor == "skill" or plan_step.action == "skill"

if is_skill_step and skill_executor:
    async for event in skill_executor.execute_stream(...):
        # ... yield events ...
        await asyncio.sleep(0)
else:
    # Use legacy StepExecutorAgent     <-- ENTIRE else block to delete
    async for event in executor.run_stream(
        plan_step,
        request=request,
        conversation_id=request.conversation_id or str(uuid.uuid4()),
        prompt_history=prompt_history,
    ):
        if event["type"] == "content":
            yield {"type": "content", "content": event["content"]}
        elif event["type"] == "thinking":
            meta = (event.get("metadata") or {}).copy()
            meta["id"] = plan_step.id
            yield {"type": "thinking", "content": event["content"], "metadata": meta}
        elif event["type"] == "result":
            step_result = event["result"]
        await asyncio.sleep(0)
```

After removal: the `else:` block is gone. The `if is_skill_step and skill_executor:` guard
should become `if skill_executor:` (or keep as-is and raise if not skill_executor — see action).

Imports to remove from service.py:
```python
from core.agents import (
    PlannerAgent,
    PlanSupervisorAgent,
    StepExecutorAgent,   # <-- remove
    StepSupervisorAgent,
)
```

Instantiation to remove (line 268):
```python
executor = StepExecutorAgent(self._memory, self._litellm, self._tool_registry)
```

Method signatures to update — remove `executor: StepExecutorAgent` parameter from:
- `_execute_step_stream(self, plan_step, skill_executor, executor, request, ...)` (line 330)
- `_execute_step_with_supervision(self, plan_step, skill_executor, executor, ...)` (line 528)
- `_run_agent_loop(self, planner, plan_supervisor, executor, ...)` (line 1050)

Return type annotation of _initialize_agents() (line 239-244) includes StepExecutorAgent — update.

Files with StepExecutorAgent references (non-pycache):
- services/agent/src/core/agents/executor.py — the class itself; DELETE this file
- services/agent/src/core/agents/__init__.py — exports StepExecutorAgent; remove the export
- services/agent/src/core/tests/test_executor_agent.py — tests legacy executor; DELETE
- services/agent/src/core/tests/test_step_executor.py — tests step executor; READ first to
  determine if behavior tested is now covered by test_skill_executor.py before deleting
- services/agent/src/core/tests/test_service.py — imports StepExecutorAgent for mocking;
  update mocks to remove StepExecutorAgent, keep tests that test SkillExecutor path
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Remove StepExecutorAgent from service.py</name>
  <files>services/agent/src/core/runtime/service.py</files>

  <read_first>
    - services/agent/src/core/runtime/service.py — read the full file before editing.
      Locate: import block (lines 25-29), _initialize_agents return type (lines 239-244),
      StepExecutorAgent instantiation (line 268), _execute_step_stream method signature and
      body (lines 329-453), _execute_step_with_supervision signature (line 528),
      _run_agent_loop signature (line 1050).
  </read_first>

  <action>
    Step 1 — Remove import:
    Delete `StepExecutorAgent,` from the `from core.agents import (...)` block.

    Step 2 — Update _initialize_agents return type annotation:
    Remove `StepExecutorAgent,` from the return type tuple. Update the return statement and
    all callers of _initialize_agents that unpack the tuple (they pass executor to the
    methods that are being updated — find and fix each one).

    Step 3 — Remove instantiation:
    Delete `executor = StepExecutorAgent(self._memory, self._litellm, self._tool_registry)`
    (line 268). Remove executor from the return tuple of _initialize_agents.

    Step 4 — Remove the legacy else: branch from _execute_step_stream:
    Delete lines 436-453 (the `else:` block and its contents). The method now only contains
    the `if is_skill_step and skill_executor:` path. Replace the guard condition to:
    ```python
    if skill_executor:
    ```
    And add an else branch that raises clearly:
    ```python
    else:
        raise RuntimeError(
            f"No SkillExecutor available for step '{plan_step.label}'. "
            "All plan steps must use executor='skill'."
        )
    ```
    This makes the single-path assumption explicit and loud (per D-01, D-04 spirit).

    Step 5 — Remove executor parameter from method signatures:
    - _execute_step_stream: remove `executor: StepExecutorAgent` parameter
    - _execute_step_with_supervision: remove `executor: StepExecutorAgent` parameter
    - _run_agent_loop: remove `executor: StepExecutorAgent` parameter
    Update all call sites in the file that pass `executor` as an argument to these methods.

    Per D-01, D-03 from CONTEXT.md.
  </action>

  <verify>
    <automated>
      grep "StepExecutorAgent" /home/magnus/dev/ai-agent-platform/services/agent/src/core/runtime/service.py
    </automated>
  </verify>

  <acceptance_criteria>
    - `grep "StepExecutorAgent" services/agent/src/core/runtime/service.py` returns nothing
    - `grep "Use legacy" services/agent/src/core/runtime/service.py` returns nothing
    - `grep "RuntimeError" services/agent/src/core/runtime/service.py` returns the new guard raise
    - File is syntactically valid Python (mypy or python -m py_compile confirms)
  </acceptance_criteria>

  <done>service.py contains no StepExecutorAgent references; legacy else: branch gone; explicit RuntimeError guard added</done>
</task>

<task type="auto">
  <name>Task 2: Delete executor.py and update affected test files; stack check must pass</name>
  <files>
    services/agent/src/core/agents/executor.py,
    services/agent/src/core/agents/__init__.py,
    services/agent/src/core/tests/test_executor_agent.py,
    services/agent/src/core/tests/test_step_executor.py,
    services/agent/src/core/tests/test_service.py
  </files>

  <read_first>
    - services/agent/src/core/agents/__init__.py — see what is exported; remove StepExecutorAgent line
    - services/agent/src/core/tests/test_executor_agent.py — read in full; this tests the legacy
      executor directly; DELETE the file
    - services/agent/src/core/tests/test_step_executor.py — read in full; determine if behavior
      tested is now covered by test_skill_executor.py; if fully covered, DELETE; if unique
      behavior not covered elsewhere, migrate the unique tests to test_skill_executor.py then delete
    - services/agent/src/core/tests/test_service.py — read the StepExecutorAgent mock setup;
      remove those mocks and any test that only exercised the legacy path; keep tests that
      exercise the SkillExecutor path (update them to not pass executor)
  </read_first>

  <action>
    Step 1 — Delete executor.py:
    ```bash
    rm /home/magnus/dev/ai-agent-platform/services/agent/src/core/agents/executor.py
    ```

    Step 2 — Update __init__.py:
    Remove `StepExecutorAgent` from the import and `__all__` list. If executor.py was the
    only thing exported, remove the entire import line for it.

    Step 3 — Delete test_executor_agent.py:
    ```bash
    rm /home/magnus/dev/ai-agent-platform/services/agent/src/core/tests/test_executor_agent.py
    ```

    Step 4 — Evaluate and handle test_step_executor.py:
    Read the file. Compare with test_skill_executor.py to identify overlap.
    - Tests that duplicate what test_skill_executor.py already covers: delete those test methods
    - Tests for behavior that is genuinely absent from test_skill_executor.py: migrate them
      (add to test_skill_executor.py with updated imports — no StepExecutorAgent, use SkillExecutor)
    - When the file has no remaining unique tests, delete it:
      ```bash
      rm /home/magnus/dev/ai-agent-platform/services/agent/src/core/tests/test_step_executor.py
      ```

    Step 5 — Update test_service.py:
    - Find all `StepExecutorAgent` mock setups (e.g. `MagicMock(spec=StepExecutorAgent)`)
    - Remove the `from core.agents import StepExecutorAgent` import line
    - Remove mock construction for executor
    - Update calls to _initialize_agents, _execute_step_stream, _execute_step_with_supervision,
      _run_agent_loop that passed `executor=` — remove that argument from all call sites
    - Tests that specifically asserted on legacy executor path behavior: delete those tests
    - Tests that test SkillExecutor path behavior: keep, update signatures

    Step 6 — Run stack check:
    ```bash
    cd /home/magnus/dev/ai-agent-platform && ./stack check --no-fix
    ```
    Fix any mypy or ruff errors that result from the removal (e.g. unused imports, type
    annotation mismatches). Do NOT add `# type: ignore` suppression — fix the root cause.

    Per D-02, D-03 from CONTEXT.md.
  </action>

  <verify>
    <automated>
      cd /home/magnus/dev/ai-agent-platform && ./stack check --no-fix 2>&1 | tail -30
    </automated>
  </verify>

  <acceptance_criteria>
    - `ls services/agent/src/core/agents/executor.py` returns "No such file" (deleted)
    - `grep -r "StepExecutorAgent" services/agent/src/ --include="*.py" | grep -v __pycache__` returns nothing
    - `grep -r "from core.agents import.*StepExecutorAgent" services/agent/src/ --include="*.py"` returns nothing
    - `./stack check --no-fix` exits 0 — ruff, black, mypy, and pytest all pass
    - No `# type: ignore` lines added as part of this change
  </acceptance_criteria>

  <done>
    executor.py deleted; all test files updated or deleted; stack check green;
    zero StepExecutorAgent references remain in the codebase.
  </done>
</task>

</tasks>

<verification>
1. `grep -r "StepExecutorAgent" services/agent/src/ --include="*.py" | grep -v __pycache__` — empty
2. `ls services/agent/src/core/agents/executor.py` — file not found
3. `./stack check --no-fix` — exits 0
4. `grep "Use legacy" services/agent/src/core/runtime/service.py` — empty
</verification>

<success_criteria>
- service.py has single SkillExecutor execution path with explicit RuntimeError guard
- StepExecutorAgent class deleted (executor.py gone)
- All test files updated — no StepExecutorAgent references anywhere in src/
- stack check green (ruff + black + mypy + pytest)
</success_criteria>

<output>
After completion, create `.planning/phases/01-infrastructure-hardening/01-executor-removal-SUMMARY.md`
using the summary template at @$HOME/.claude/get-shit-done/templates/summary.md
</output>
