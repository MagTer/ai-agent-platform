---
name: opus-planner
description: Create comprehensive implementation plan for Sonnet execution. Use when starting complex feature work that would benefit from detailed planning before implementation. Designed for Opus model.
allowed-tools: Read, Grep, Glob, Write
model: claude-opus-4-5-20251101
---

# Opus Implementation Planner

## Purpose

Create detailed implementation plans that allow Sonnet to execute in a fresh context (token optimization).

**Cost Strategy:**
- Opus explores codebase deeply (20-50K tokens)
- Creates comprehensive plan file with ALL context Sonnet needs
- Sonnet starts NEW session, reads plan (context starts at 0)
- Result: Avoid hitting 200K+ token threshold

## When This Skill Activates

Use this skill when:
- Starting complex feature implementation (3+ files, multiple layers)
- Major refactoring across modules
- Architectural changes affecting multiple components
- Security-sensitive features requiring careful planning
- User explicitly requests planning phase

**Do NOT use for:**
- Simple bug fixes (1-2 lines)
- Trivial changes
- Documentation-only updates

## Planning Workflow

### Phase 1: Explore Codebase (Turns 1-3)

**Goal:** Understand current architecture, patterns, and constraints

1. **Read project primer (MANDATORY FIRST):**
   - `.claude/PRIMER.md` - Essential project context (architecture, standards, patterns)
   - This gives you the foundation - don't skip it!

2. **Read architecture documentation:**
   - `docs/ARCHITECTURE.md` - Detailed layer structure
   - `docs/architecture/02_agent.md` - Module patterns
   - `.clinerules` - Additional project standards

3. **Explore relevant code:**
   ```bash
   # Find similar implementations
   glob: "services/agent/src/**/*[similar_feature]*.py"

   # Study patterns (PRIMER.md has examples, but see real usage)
   read: services/agent/src/modules/rag/manager.py  # Example of module pattern
   read: services/agent/src/core/providers.py       # DI pattern (see PRIMER.md first)
   read: services/agent/src/interfaces/app.py       # Startup injection
   ```

4. **Understand task-specific context:**
   - How does this feature fit into existing architecture?
   - What similar features exist? (PRIMER.md doesn't know about all features)
   - What are the specific integration points?
   - What are the task-specific constraints?

**Note:** PRIMER.md covers general patterns. Your job is to find task-specific examples and decisions.

### Phase 2: Make Architectural Decisions (Turns 4-5)

**Goal:** Decide on approach before writing plan

**Key Decisions:**
1. **Where does this feature live?**
   - New module in `modules/`?
   - Extension of existing module?
   - Core infrastructure change?

2. **What protocols are needed?**
   - Define interfaces in `core/protocols/`
   - Plan provider pattern usage

3. **What are the integration points?**
   - API endpoints?
   - CLI commands?
   - Background workers?

4. **What are the dependencies?**
   - Existing modules used via protocols
   - New external dependencies needed

### Phase 3: Write Comprehensive Plan (Turns 6-8)

**Goal:** Create plan file with ALL task-specific context Sonnet needs

1. **Create plan file:**
   ```python
   # Filename format: YYYY-MM-DD-feature-slug.md
   filename = f".claude/plans/{date.today()}-{feature_slug}.md"
   ```

2. **Use template structure:**
   - Copy from `.claude/plans/PLAN_TEMPLATE.md`
   - Fill in ALL sections thoroughly
   - Include concrete code examples from codebase
   - Show exact file paths and line numbers

3. **Critical sections to complete:**
   - **Codebase Context:** Task-specific patterns and existing implementations
     - Don't repeat PRIMER.md basics (DI pattern, typing rules)
     - Focus on: "Here's how feature X currently works" (Sonnet reads PRIMER.md separately)
   - **Implementation Roadmap:** Step-by-step with code snippets
   - **Configuration Changes:** Exact env vars and config updates
   - **Quality Checks:** How to verify correctness

4. **Code Examples - Task-Specific Only:**
   - Copy REAL examples showing how THIS feature should work
   - Reference existing similar features: "Follow pattern from modules/rag/manager.py"
   - Show integration points: "Inject at startup like embedder (see PRIMER.md for pattern)"
   - Don't repeat generic patterns from PRIMER.md

**Remember:** Sonnet will read PRIMER.md first (general project context), then your plan (task-specific context). Avoid duplication.

### Phase 4: Finalize and Handoff (Turn 9)

**Goal:** Ensure plan is complete and actionable

1. **Self-review checklist:**
   - [ ] All sections filled in (no TODOs or placeholders)
   - [ ] Code examples are task-specific (not duplicating PRIMER.md)
   - [ ] File paths are specific and accurate
   - [ ] Dependencies clearly listed
   - [ ] Success criteria measurable
   - [ ] Quality checks defined
   - [ ] Architecture decisions explained
   - [ ] References PRIMER.md where appropriate ("Follow DI pattern, see PRIMER.md")

2. **Ask user: Manual or Auto-Spawn?**
   ```
   Plan created: .claude/plans/YYYY-MM-DD-feature-name.md

   How would you like to proceed?

   [1] Auto-spawn Sonnet agent now (automatic, same session)
   [2] Manual implementation (you start new Sonnet session for max token savings)

   Choose 1 or 2:
   ```

3. **If user chooses [1] - Auto-Spawn:**
   ```python
   Task(
       subagent_type="general-purpose",
       model="sonnet",
       prompt=f"Implement plan from .claude/plans/YYYY-MM-DD-feature-name.md",
       description="Implement [feature name]"
   )
   ```

4. **If user chooses [2] - Manual:**
   ```
   To implement in NEW session (max token savings):

   exit
   claude --model sonnet
   > /sonnet-implementer .claude/plans/YYYY-MM-DD-feature-name.md
   ```

## What Makes a Good Plan

### ✅ Good Plan Characteristics

1. **Self-Contained:**
   - Sonnet doesn't need to read other files to understand
   - All patterns shown with examples
   - All context included

2. **Concrete:**
   - Specific file paths, not "somewhere in modules/"
   - Actual code snippets, not "implement a function"
   - Exact commands to run

3. **Ordered:**
   - Clear phase sequence (1 → 2 → 3)
   - Dependencies between steps explained
   - Quality checks at each phase

4. **Comprehensive:**
   - Covers happy path AND error cases
   - Includes testing strategy
   - Documents configuration changes
   - Updates documentation

### ❌ Bad Plan Characteristics

1. **Too High-Level:**
   - "Add caching layer" (no specifics)
   - "Implement feature X" (no steps)
   - "Follow existing patterns" (which ones?)

2. **Missing Context:**
   - No code examples from codebase
   - Doesn't explain WHY decisions were made
   - Missing integration points

3. **Incomplete:**
   - Skips testing
   - Forgets documentation
   - Misses configuration changes
   - No quality validation

## Example Planning Session

**User Request:** "Add Redis caching for RAG queries"

**Turn 1-3: Explore**
```python
# Read existing RAG implementation
read("services/agent/src/modules/rag/manager.py")
read("services/agent/src/modules/rag/config.py")

# Check if Redis already used
grep("redis", pattern="redis", path="services/agent/src/")

# Study caching patterns
read("services/agent/src/core/config.py")  # Config pattern
```

**Turn 4-5: Decide**
- Decision: Add Redis as optional dependency, use Protocol pattern
- New protocol: `ICacheProvider` in core/protocols/
- Implementation: `RedisCacheProvider` in modules/cache/
- Integration: Inject into RAGManager via provider

**Turn 6-8: Write Plan**
- Create `.claude/plans/2026-01-09-redis-caching-rag.md`
- Fill template with:
  - Exact Redis client usage pattern
  - Code snippets from RAGManager to modify
  - Cache key structure and TTL strategy
  - Configuration additions
  - Testing approach (mock Redis in tests)

**Turn 9: Finalize**
- Review plan completeness
- Inform user of plan location
- Suggest starting fresh Sonnet session

## Critical Guidelines

### DO:
- Explore thoroughly before planning
- Copy real code examples from codebase
- Explain WHY decisions were made
- Include fallback strategies for issues
- Make plan actionable (Sonnet can follow blindly)
- Use exact file paths and line numbers

### DO NOT:
- Rush exploration phase
- Use placeholder text or TODOs
- Assume Sonnet knows project patterns
- Skip error handling or edge cases
- Leave out testing or documentation steps
- Make plan too abstract or high-level

## Integration with Existing Skills

After creating plan, suggest Sonnet run:
1. Implementation (following plan)
2. `/quality-check` - Code quality validation
3. `/architecture-guard` - Architecture compliance
4. `/security-review` - Security validation (if needed)
5. `/documentation-sync` - Doc updates

## Success Metrics

A successful plan enables Sonnet to:
- Implement feature without asking clarifying questions
- Follow architectural patterns correctly
- Write tests that match project style
- Pass quality checks on first try
- Update documentation appropriately

**If Sonnet needs to ask many questions during implementation, the plan was insufficient.**

---

**After running this skill:**
- Plan file created at `.claude/plans/YYYY-MM-DD-feature-name.md`
- User informed of next steps (start fresh Sonnet session)
- Implementation ready to begin in new context
