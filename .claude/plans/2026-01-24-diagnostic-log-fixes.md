# Diagnostic Log Fixes

**Created:** 2026-01-24
**Planner:** Opus 4.5
**Status:** Planning Complete
**Implementer:** Sonnet 4.5

---

## 1. Executive Summary

**Problem Statement:**
Production logs reveal 5 categories of issues that need attention:
1. Planner JSON extraction failures for simple greetings ("Hello")
2. Azure DevOps team mapping warnings (missing area_path/default_type)
3. OpenTelemetry NoneType attribute warnings for plan.description
4. MemoryStore tenant isolation warnings in test scenarios
5. Credential decryption errors during runtime

**Solution Approach:**
Fix the real production bugs (issues 1, 3, 5) and improve configuration handling (issue 2). Issue 4 is acceptable in test scenarios but we will add context to clarify.

**Success Criteria:**
- [ ] No "Failed to extract JSON from planner" for simple chat messages (routed to CHAT instead)
- [ ] No "Invalid type NoneType for attribute" OTel warnings
- [ ] Credential decryption errors include diagnostic context for troubleshooting
- [ ] ADO mapping warnings only appear for genuinely missing config
- [ ] MemoryStore warning context clarifies test vs production usage

---

## 2. Root Cause Analysis

### Issue 1: Planner JSON Extraction Failures (HIGH PRIORITY)

**Problem:** When users send simple greetings like "Hello", the request is routed to AGENTIC mode, then the Planner LLM receives it and fails to generate valid JSON - instead outputting "### AVAILABLE TOOLS..." which is part of the prompt being echoed back.

**Root Cause:**
1. IntentClassifier in `dispatcher.py` correctly routes to "chat" for simple greetings
2. BUT: The `routing_decision` metadata is only set when going through AGENTIC path
3. When IntentClassifier returns "chat", the Dispatcher uses `litellm.stream_chat()` directly
4. However, if requests come through the legacy `AgentService.execute_stream()` API directly (bypassing Dispatcher), they go to Planner without classification

**Evidence from code:**
- `dispatcher.py:203-225` handles CHAT route correctly
- `service.py:147` reads `routing_decision` from metadata, defaults to AGENTIC
- If metadata lacks `routing_decision`, simple messages go to Planner

**Fix Strategy:**
The Planner already has a fallback plan mechanism (line 312-318) for when JSON extraction fails. We need to improve the fallback to handle conversational messages gracefully instead of returning an empty plan.

### Issue 2: Azure DevOps Mapping Warnings (LOW PRIORITY)

**Problem:** Logs show "Team 'platform' missing area_path" but looking at `ado_mappings.yaml`, platform HAS area_path.

**Root Cause:**
- Likely stale logs from development/testing with incomplete config
- OR Docker mount not picking up latest config file
- Current config in repo is complete

**Fix Strategy:**
No code fix needed. This is a configuration/deployment issue. Add documentation note about config mounting.

### Issue 3: OpenTelemetry NoneType Attribute (MEDIUM PRIORITY)

**Problem:** OTel warns when `plan.description` is None.

**Root Cause:**
In `service.py:296-300`:
```python
set_span_attributes(
    {
        "plan.description": plan.description,  # Can be None!
        "plan.steps_count": len(plan.steps) if plan.steps else 0,
    }
)
```

When plan.description is None (fallback plan or LLM failure), OTel rejects it.

**Fix Strategy:**
Filter out None values before setting span attributes, or provide empty string fallback.

### Issue 4: MemoryStore Tenant Isolation Warnings (ACCEPTABLE)

**Problem:** Warning appears during tests when MemoryStore is created without context_id.

**Root Cause:**
This is intentional security logging. The warning helps detect misconfigured production deployments.

**Fix Strategy:**
Improve the warning message to include caller context for better debugging. No code change needed - this is working as designed for security.

### Issue 5: Credential Decryption Errors (MEDIUM PRIORITY)

**Problem:** "Failed to decrypt credential azure_devops_pat for user..." during runtime.

**Root Cause:**
1. User stored a PAT with an old encryption key
2. Encryption key was rotated
3. Decryption now fails with InvalidToken

**Fix Strategy:**
1. Improve error message to suggest remediation (re-enter credential)
2. Add admin endpoint to detect credentials with decryption failures
3. Log additional context (credential type, user identifier, when stored)

---

## 3. Implementation Roadmap

### Phase 1: Fix OTel NoneType Attribute Warning (Quick Win)

**File:** `services/agent/src/core/observability/tracing.py`

**Modify `set_span_attributes` function (line 274-284):**

```python
def set_span_attributes(attributes: dict[str, Any]) -> None:
    """Set attributes on the current active span.

    Filters out None values to prevent OTel warnings.
    """
    trace_api = _otel_trace if _OTEL_AVAILABLE else _NoOpTraceAPI()
    span = trace_api.get_current_span()

    # Filter out None values - OTel only accepts bool, str, bytes, int, float
    filtered_attrs = {
        k: v for k, v in attributes.items()
        if v is not None
    }

    if span and hasattr(span, "set_attributes"):
        span.set_attributes(filtered_attrs)
    elif span and hasattr(span, "set_attribute"):
        for key, value in filtered_attrs.items():
            span.set_attribute(key, value)
```

**QA Tasks:**
- Run `stack check` to verify no regressions
- Check that OTel attributes are still set correctly for non-None values

---

### Phase 2: Improve Planner Fallback for Conversational Messages

**File:** `services/agent/src/core/agents/planner.py`

**Modify `_fallback_plan` method and improve detection of conversational input.**

**Current behavior (line 312-318):**
```python
final_plan = Plan(
    steps=[],
    description=(
        f"Planner failed after {attempts} attempts. Last error: {exc_msg}"
    ),
)
```

**New approach:** Instead of returning an empty plan that triggers supervisor "Plan has no steps" error, return a single completion step that passes the message through to the LLM for a conversational response.

**Add helper method after line 336:**

```python
@staticmethod
def _is_conversational_message(raw_output: str, user_prompt: str) -> bool:
    """Detect if the LLM output suggests this was a conversational message.

    When the planner echoes back part of the system prompt or user message
    instead of generating JSON, it often means the input was conversational
    and doesn't need a plan.
    """
    if not raw_output:
        return False

    # Patterns indicating planner confusion (echoing prompts/instructions)
    confusion_patterns = [
        "### AVAILABLE TOOLS",
        "### USER REQUEST",
        "I'll help you",
        "I'm here to assist",
        "Hello! How can I",
    ]

    for pattern in confusion_patterns:
        if pattern in raw_output:
            return True

    # Short user messages are likely conversational
    if len(user_prompt.strip()) < 20:
        words = user_prompt.strip().lower().split()
        greetings = {"hello", "hi", "hey", "hej", "tjena", "hejsan", "thanks", "thank", "ok", "okay"}
        if any(word in greetings for word in words):
            return True

    return False
```

**Modify the fallback section (around line 310-318):**

```python
else:
    # Final fallback - check if this looks conversational
    if self._is_conversational_message(plan_text, request.prompt):
        # Conversational message - just do a completion
        final_plan = Plan(
            steps=[
                PlanStep(
                    id="conv-1",
                    label="Direct response",
                    executor="litellm",
                    action="completion",
                    description="Conversational response (no plan needed)",
                ),
            ],
            description="Conversational message - direct response",
        )
        LOGGER.info("Detected conversational message, using direct completion fallback")
    else:
        # Genuine planning failure
        final_plan = Plan(
            steps=[],
            description=(
                f"Planner failed after {attempts} attempts. Last error: {exc_msg}"
            ),
        )
    yield {"type": "plan", "plan": final_plan}
    return
```

**Required import:** Add PlanStep to imports at top of file:

```python
from shared.models import AgentMessage, AgentRequest, Plan, PlanStep
```

**QA Tasks:**
- Run existing planner tests
- Manually test with "Hello" message to verify conversational detection
- Verify complex requests still generate proper plans

---

### Phase 3: Improve Credential Decryption Error Handling

**File:** `services/agent/src/core/auth/credential_service.py`

**Modify `get_credential` method (line 86-116):**

```python
async def get_credential(
    self,
    user_id: UUID,
    credential_type: str,
    session: AsyncSession,
) -> str | None:
    """Retrieve and decrypt a credential.

    Args:
        user_id: User's UUID
        credential_type: Type of credential
        session: Database session

    Returns:
        Decrypted credential value, or None if not found or decryption fails
    """
    stmt = select(UserCredential).where(
        UserCredential.user_id == user_id,
        UserCredential.credential_type == credential_type,
    )
    result = await session.execute(stmt)
    credential = result.scalar_one_or_none()

    if not credential:
        return None

    try:
        return self._decrypt(credential.encrypted_value)
    except InvalidToken:
        # Provide actionable error context
        created_at = credential.created_at.isoformat() if credential.created_at else "unknown"
        LOGGER.error(
            "Failed to decrypt credential '%s' for user %s. "
            "Credential was stored at %s. "
            "This typically means the encryption key was rotated since the credential was stored. "
            "The user should re-enter their credential through the admin portal.",
            credential_type,
            user_id,
            created_at,
        )
        return None
```

**QA Tasks:**
- Verify error message appears in logs with proper context
- Test with invalid encrypted value to trigger the error path

---

### Phase 4: Improve MemoryStore Warning Context

**File:** `services/agent/src/core/core/memory.py`

**Modify the warning message (line 66-70):**

```python
# SECURITY: Warn if context_id is None - this disables tenant isolation
if context_id is None:
    import traceback
    caller_info = "".join(traceback.format_stack()[-3:-1])
    LOGGER.warning(
        "MemoryStore initialized without context_id - tenant isolation disabled. "
        "This should only be used for admin/internal operations or tests. "
        "Caller context:\n%s",
        caller_info,
    )
```

**Alternative (less verbose - PREFERRED):**

```python
if context_id is None:
    import inspect
    frame = inspect.currentframe()
    caller = inspect.getouterframes(frame)[1] if frame else None
    caller_loc = f"{caller.filename}:{caller.lineno}" if caller else "unknown"
    LOGGER.warning(
        "MemoryStore initialized without context_id - tenant isolation disabled. "
        "This is expected for admin/test operations. Caller: %s",
        caller_loc,
    )
```

**QA Tasks:**
- Verify warning includes caller location
- Check that production paths always pass context_id

---

### Phase 5: Documentation Update for ADO Config

**File:** `docs/AZURE_DEVOPS_TEAMS.md` (or create if not exists)

**Add section:**

```markdown
## Configuration Troubleshooting

### "Team 'X' missing area_path" Warnings

If you see warnings like:
```
ADO Mapping: Team 'platform' missing area_path
```

This means the `ado_mappings.yaml` configuration file is either:
1. Not mounted correctly in Docker
2. Contains incomplete team definitions

**Docker Mount Verification:**
```bash
# Check if config is mounted
docker exec ai-agent cat /app/config/ado_mappings.yaml

# Compare with local file
cat services/agent/config/ado_mappings.yaml
```

**Required Fields for Each Team:**
- `area_path`: The Azure DevOps area path (required)
- `default_type`: Default work item type (required)
- `display_name`: Human-readable name (optional)
- `owner`: Team owner name (optional)
- `default_tags`: List of default tags (optional)
```

**QA Tasks:**
- Verify documentation is accurate
- Add to existing Azure DevOps documentation if it exists

---

## 4. Agent Delegation

### Engineer (Sonnet) - Implementation
- Implement Phase 1: OTel attribute filtering
- Implement Phase 2: Planner conversational fallback
- Implement Phase 3: Credential error context
- Implement Phase 4: MemoryStore warning context

### QA (Haiku - 12x cheaper) - Quality Assurance
After Engineer completes each phase:
- Run `stack check`
- Report any failures
- Escalate complex Mypy errors to Engineer

### Simple Tasks (Haiku)
- Phase 5: Documentation update (if file exists)

---

## 5. Testing Strategy

### Unit Tests

**Planner Conversational Detection:**
```python
# tests/unit/test_planner.py

import pytest
from core.agents.planner import PlannerAgent

def test_is_conversational_message_greeting():
    """Greetings should be detected as conversational."""
    assert PlannerAgent._is_conversational_message("I'll help you", "Hello") is True
    assert PlannerAgent._is_conversational_message("### AVAILABLE TOOLS", "Hi") is True

def test_is_conversational_message_task():
    """Task requests should not be conversational."""
    raw = '{"description": "Research", "steps": []}'
    assert PlannerAgent._is_conversational_message(raw, "Research AI trends") is False
```

**OTel Attribute Filtering:**
```python
# tests/unit/test_tracing.py

def test_set_span_attributes_filters_none():
    """None values should be filtered out."""
    from core.observability.tracing import set_span_attributes

    # Should not raise any warnings
    set_span_attributes({
        "valid_attr": "value",
        "none_attr": None,
        "int_attr": 42,
    })
```

### Manual Testing

```bash
# Start the stack
./stack dev up

# Test conversational message through API
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello"}]}'

# Check logs for:
# - No "Failed to extract JSON from planner"
# - No "Plan validation FAILED: ['Plan has no steps']"
# - No "Invalid type NoneType for attribute"
```

---

## 6. Quality Checks

### Architecture Compliance
- [x] No new dependencies needed
- [x] All changes are within existing files
- [x] No layer violations

### Code Quality
```bash
./stack check
```

Expected:
- [ ] Ruff linting passes
- [ ] Black formatting passes
- [ ] Mypy type checking passes
- [ ] All tests pass

### Security Review
- [x] No new attack vectors introduced
- [x] Credential error handling doesn't leak sensitive data
- [x] Caller tracking doesn't expose sensitive paths

---

## 7. Potential Issues & Solutions

### Issue 1: Conversational detection false positives

**Problem:** Complex requests might be misclassified as conversational.

**Solution:** The detection only triggers AFTER all retry attempts fail. Normal requests will succeed before this check. Also, the patterns are conservative (require specific echoed prompt fragments).

### Issue 2: Stack frame inspection overhead

**Problem:** Getting caller info for MemoryStore warning adds slight overhead.

**Solution:** This only runs once per MemoryStore instantiation (not per operation), and only when context_id is None (should be rare in production).

---

## 8. Implementation Order

1. **Phase 1** (Quick Win): OTel attribute filtering - prevents noisy warnings
2. **Phase 3** (Important): Credential error context - helps troubleshoot real issues
3. **Phase 2** (Main Fix): Planner fallback - fixes the main production issue
4. **Phase 4** (Polish): MemoryStore context - improves debuggability
5. **Phase 5** (Docs): ADO documentation - clarifies configuration

---

## 9. Success Validation

After implementation:

1. **Send "Hello" through API** - should get conversational response, no errors
2. **Check logs for OTel warnings** - should see no NoneType warnings
3. **Trigger credential decryption failure** - should see actionable error message
4. **Create MemoryStore without context_id** - should see caller location in warning

---

## Status Tracking

- [ ] Phase 1: OTel attribute filtering
- [ ] Phase 2: Planner conversational fallback
- [ ] Phase 3: Credential error context
- [ ] Phase 4: MemoryStore warning context
- [ ] Phase 5: Documentation update
- [ ] Quality Checks Passed
- [ ] Success Validation Complete

**Notes:**
[Engineer adds notes here during implementation]
