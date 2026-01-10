# Opus → Sonnet Handoff Workflow

**Purpose:** Optimize token usage and costs by separating planning (Opus) from implementation (Sonnet).

## Cost Optimization Strategy

### The Problem
- Single long session accumulates context tokens
- Context > 200K tokens = higher API costs
- Complex features require extensive exploration

### The Solution
1. **Opus Planning Session:** Explores codebase, creates comprehensive plan (20-50K tokens)
2. **Sonnet Implementation Session:** NEW session reads plan, implements (starts at 0 tokens)
3. **Result:** Stay under 200K threshold, lower costs

## Complete Workflow

### Phase 1: Planning (Opus)

**Start Opus session:**
```bash
claude --model opus
```

**Trigger planning:**
```
> /opus-planner
> Plan feature: [describe feature]
```

**What Opus does:**
1. **Reads project primer** (Turn 1)
   - Reads `.claude/PRIMER.md` for essential project context
   - Gets architecture, standards, patterns foundation
   - No need to re-learn basics every time

2. **Explores codebase** (Turns 2-4)
   - Finds similar implementations for THIS feature
   - Studies task-specific patterns
   - Understands specific constraints
   - Identifies integration points

3. **Makes decisions** (Turns 5-6)
   - Chooses architectural approach
   - Defines integration points
   - Selects patterns to follow (references PRIMER.md)
   - Identifies dependencies

4. **Writes comprehensive plan** (Turns 7-8)
   - Creates `.claude/plans/YYYY-MM-DD-feature-name.md`
   - Fills all sections with task-specific context
   - Includes task-specific code examples
   - References PRIMER.md for general patterns (avoids duplication)
   - Documents decisions and rationale

5. **Asks: Auto-spawn or Manual?** (Turn 9)
   - Offers two options:
     - **[1] Auto-spawn:** Opus spawns Sonnet agent automatically (convenient)
     - **[2] Manual:** You start new Sonnet session (max token savings)
   - User chooses

**Output:**
```
Plan created: .claude/plans/2026-01-09-redis-caching.md

How would you like to proceed?

[1] Auto-spawn Sonnet agent now (automatic, same session)
[2] Manual implementation (you start new Sonnet session for max token savings)

Choose 1 or 2:
```

**If you choose [1] - Auto-Spawn:**
- Opus spawns Sonnet agent via Task tool
- Sonnet gets fresh context (separate bubble)
- Implementation happens automatically
- You stay in same session

**If you choose [2] - Manual:**
```
To implement in NEW session (max token savings):

  exit
  claude --model sonnet
  > /sonnet-implementer .claude/plans/2026-01-09-redis-caching.md
```

### Phase 2: Implementation (Sonnet)

**Start NEW Sonnet session (critical for cost optimization):**
```bash
claude --model sonnet
```

**Trigger implementation:**
```
> /sonnet-implementer
> Implement plan from .claude/plans/2026-01-09-redis-caching.md
```

**What Sonnet does:**
1. **Loads context** (Turn 1)
   - Reads `.claude/PRIMER.md` FIRST (project essentials)
   - Reads plan file (task-specific context)
   - Understands both general patterns and specific requirements
   - Confirms success criteria

2. **Implements sequentially** (Turns 2-N)
   - Follows phases in order
   - Creates/modifies files as specified
   - Uses patterns from plan examples
   - Runs quality checks after each phase
   - Updates todo list in real-time

3. **Validates quality** (Turn N+1)
   - Runs `python scripts/code_check.py`
   - Fixes any issues
   - Re-runs until passes

4. **Updates documentation** (Turn N+2)
   - Updates docs as specified in plan
   - Verifies cross-references

5. **Tests** (Turn N+3)
   - Runs automated tests
   - Performs manual testing if specified
   - Validates success criteria

6. **Reports completion** (Turn N+4)
   - Summarizes what was done
   - Lists files created/modified
   - Confirms success criteria met
   - Suggests next steps

**Output:**
```
✅ Implementation Complete: Redis Caching

Phases Completed:
- Core Infrastructure
- Module Implementation
- Integration
- Tests
- Quality checks passed
- Documentation updated

Files Created:
- services/agent/src/core/protocols/cache.py
- services/agent/src/modules/cache/redis_provider.py
- services/agent/tests/unit/test_redis_cache.py

Files Modified:
- services/agent/src/core/providers.py
- services/agent/src/modules/rag/manager.py
- services/agent/src/interfaces/app.py

Success Criteria: ✅ All met

Ready for review and PR creation.
```

## What to Do at Each Planning Session

### 1. Describe the Feature Clearly

**Good:**
```
Add Redis caching layer to RAG module:
- Cache query results with 5min TTL
- Use Redis as optional dependency
- Follow existing protocol pattern
- Maintain backward compatibility
```

**Bad:**
```
Add caching
```

### 2. Let Opus Explore

Opus will:
- Read PRIMER.md first (gets project foundation automatically)
- Explore task-specific implementations
- Find similar features
- Identify integration points
- Make informed decisions

**Note:** Because PRIMER.md exists, Opus spends less time re-learning basics and more time on your specific feature.

### 3. Review the Plan Before Implementation

After Opus creates the plan:
1. Read `.claude/plans/YYYY-MM-DD-feature-name.md`
2. Verify it makes sense
3. Check that architectural decisions are sound
4. Confirm scope is correct
5. Approve or request changes

### 4. Choose Implementation Method

**Option 1: Auto-Spawn (Convenient)**
- Choose [1] when Opus asks
- Sonnet spawns automatically in separate context
- Stay in same session, but Sonnet has fresh context bubble
- Good balance of convenience and cost

**Option 2: Manual (Max Savings)**
- Choose [2] when Opus asks
- Exit Opus session completely
- Start fresh Sonnet session
- Maximum token savings (completely separate sessions)

```bash
# For Option 2 (Manual):
exit
claude --model sonnet
> /sonnet-implementer .claude/plans/2026-01-09-feature.md
```

### 5. Let Sonnet Execute

Sonnet should:
- Follow the plan exactly
- Not deviate or add features
- Run quality checks frequently
- Update you on progress

### 6. Validate Results

After implementation:
- Review code changes
- Run manual smoke tests
- Verify success criteria
- Create PR if satisfied

## File Structure

```
.claude/
├── PRIMER.md                              # Project essentials (architecture, standards, patterns)
├── OPUS_SONNET_WORKFLOW.md                # This file (workflow guide)
├── plans/
│   ├── README.md                          # Workflow documentation
│   ├── PLAN_TEMPLATE.md                   # Template for plans
│   ├── 2026-01-09-redis-caching.md        # Example plan
│   └── 2026-01-10-oauth2-auth.md          # Example plan
└── skills/
    ├── opus-planner/
    │   └── SKILL.md                       # Planning skill (creates plans)
    ├── sonnet-implementer/
    │   └── SKILL.md                       # Implementation skill (executes plans)
    └── primer-sync/
        └── SKILL.md                       # Keeps PRIMER.md updated
```

## Plan File Naming Convention

```
YYYY-MM-DD-feature-slug.md
```

**Examples:**
- `2026-01-09-redis-caching.md`
- `2026-01-09-refactor-rag-module.md`
- `2026-01-10-oauth2-authentication.md`
- `2026-01-10-fix-memory-leak.md`

**Date = when plan was created**

## When to Use This Workflow

### ✅ Use Opus → Sonnet handoff for:

- **Complex features** (3+ files, multiple layers)
- **Architectural changes** (new modules, refactoring)
- **Security-sensitive work** (authentication, authorization)
- **Multi-phase implementations** (database + API + tests)
- **Uncertain approaches** (need to explore options)
- **Large scope** (would accumulate 50K+ tokens)

### ❌ Don't use for:

- **Simple bug fixes** (1-2 line changes)
- **Trivial updates** (typos, comments)
- **Documentation only** (no code changes)
- **Quick experiments** (exploratory work)
- **Clear, small tasks** (already know how to implement)

## Cost Comparison

### Without Workflow (Single Session)

```
Session: Opus
- Exploration: 20K tokens
- Planning: 10K tokens
- Implementation: 80K tokens
- Testing: 30K tokens
- Documentation: 10K tokens
= 150K tokens total (approaching 200K threshold)

Cost: $$$ (higher rate approaching/exceeding 200K)
```

### With Workflow (Separated Sessions)

```
Session 1: Opus Planning
- Exploration: 20K tokens
- Planning: 10K tokens
- Write plan: 5K tokens
= 35K tokens

Session 2: Sonnet Implementation (NEW context)
- Read plan: 5K tokens
- Implementation: 40K tokens
- Testing: 20K tokens
- Documentation: 10K tokens
= 75K tokens

Total: 110K tokens
Cost: $$ (lower rate, under 200K)
```

**Savings: ~25% cost reduction + using cheaper Sonnet model**

## Skills Reference

### `/opus-planner`
- **Model:** Opus 4.5
- **Purpose:** Create comprehensive implementation plans
- **Reads:** `.claude/PRIMER.md` (project context)
- **Output:** Plan file in `.claude/plans/`
- **Triggers:** Complex features, architectural changes
- **Special:** Asks user: auto-spawn or manual?

### `/sonnet-implementer`
- **Model:** Sonnet 4.5
- **Purpose:** Execute implementation plans
- **Reads:** `.claude/PRIMER.md` + plan file
- **Input:** Path to plan file
- **Output:** Implemented feature with quality checks
- **Can be:** Auto-spawned by Opus or manually run

### `/primer-sync`
- **Model:** Sonnet 4.5
- **Purpose:** Keep PRIMER.md updated
- **When:** Architecture/standards/patterns change
- **Output:** Updated `.claude/PRIMER.md`
- **Frequency:** Every 2-3 months or after major changes

### Other Skills (Run by Sonnet)
- `/quality-check` - Code quality validation
- `/architecture-guard` - Architecture compliance
- `/security-review` - Security validation
- `/documentation-sync` - Doc updates

## Troubleshooting

### "Sonnet asks too many clarifying questions"

**Problem:** Plan lacks necessary context

**Solution:**
- Opus needs to explore more thoroughly
- Plan should include more code examples
- Show existing patterns more clearly

### "Sonnet deviates from plan"

**Problem:** Sonnet is being too creative

**Solution:**
- Remind Sonnet to follow plan exactly
- Plan should be more prescriptive
- Use "DO NOT" sections in plan

### "Quality checks fail repeatedly"

**Problem:** Plan doesn't match project standards

**Solution:**
- Opus should study existing code patterns more
- Include quality requirements in plan
- Reference code_check.py in plan

### "Plan is too high-level"

**Problem:** Not enough concrete steps

**Solution:**
- Use PLAN_TEMPLATE.md more thoroughly
- Include exact file paths and code snippets
- Break down into smaller phases

## Best Practices

### For Opus (Planning):
1. **Explore thoroughly** - Don't rush
2. **Copy real examples** - From existing codebase
3. **Be specific** - Exact paths, concrete steps
4. **Explain decisions** - Why this approach?
5. **Include context** - Sonnet starts fresh

### For Sonnet (Implementation):
1. **Read plan completely** - Before coding
2. **Follow exactly** - No improvisation
3. **Quality check frequently** - After each phase
4. **Update progress** - Keep todo list current
5. **Ask if unclear** - Don't guess

### For You (User):
1. **Review plans** - Before implementation
2. **Start fresh sessions** - For cost optimization
3. **Validate results** - After implementation
4. **Iterate** - Improve plans over time
5. **Document learnings** - What works well

## Success Metrics

A successful workflow results in:
- ✅ Implementation matches plan
- ✅ Quality checks pass first time
- ✅ Architecture compliance maintained
- ✅ Documentation updated correctly
- ✅ Sonnet asks < 2 clarifying questions
- ✅ Token usage stays under 200K total

## Next Steps

1. **Try it:** Pick a complex feature to implement
2. **Run Opus planning:** `/opus-planner`
3. **Review plan:** Check for completeness
4. **Start fresh Sonnet:** New session
5. **Run implementation:** `/sonnet-implementer`
6. **Validate:** Quality checks and testing
7. **Iterate:** Improve based on learnings

---

**Remember:** The goal is not just to separate planning from implementation, but to create plans so comprehensive that Sonnet can execute autonomously in a fresh context, optimizing both quality and cost.
