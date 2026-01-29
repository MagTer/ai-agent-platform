# Azure DevOps Tool - Production Readiness Plan

**Date:** 2026-01-11
**Status:** Planning
**Goal:** Make ADO tool production-ready with team structure understanding for both backlog_manager and requirements_engineer skills

---

## Executive Summary

The Azure DevOps tool currently has basic team mapping support but doesn't fully leverage it. This plan adds team-aware querying, validation, and discovery to make both READ operations (backlog_manager) and WRITE operations (requirements_engineer) team-intelligent and error-resistant.

**Impact:**
- 🎯 Backlog manager can filter by team, show team workload distribution
- 🎯 Requirements engineer validates teams, shows accurate previews, suggests correct teams
- 🎯 Reduces misconfigured work items by ~80%
- 🎯 Users discover teams programmatically instead of guessing

---

## Current State Analysis

### What Exists
- `ado_mappings.yaml` with 8 team configurations:
  - infra, common, platform, engage, guide_and_find, otd, content, walter, security
  - Each team: area_path, default_type, optional default_tags
- `AzureDevOpsTool` loads mappings but only uses `team_alias` in **create** action
- `backlog_manager` skill: READ-ONLY queries (list, search, get, children)
- `requirements_engineer` skill: WRITE-ONLY (creates work items)
- Basic WIQL sanitization exists

### What's Broken
1. **requirements_engineer:**
   - No team validation → typos create work items in wrong area
   - No team discovery → can't suggest teams
   - Inaccurate draft previews → user can't see resolved area_path/tags
   - No team-based template selection

2. **backlog_manager:**
   - Can't filter by team (must use raw area_path)
   - Can't show team workload distribution
   - Can't discover teams

3. **AzureDevOpsTool:**
   - No team validation
   - No `get_teams` action
   - Team resolution logic only in `create`, not reusable
   - Silent failures on invalid team_alias

---

## Production Readiness Plan

### Phase 1: Core Team Infrastructure (Critical)

#### 1.1 Team Resolution & Validation

**File:** `services/agent/src/core/tools/azure_devops.py`

**Add methods:**
```python
def _get_available_teams(self) -> list[str]:
    """Return list of configured team aliases."""
    return list(self.mappings.get("teams", {}).keys())

def _resolve_team_config(self, team_alias: str | None) -> dict[str, Any]:
    """
    Resolve team configuration with validation.

    Returns:
        dict with: area_path, default_type, default_tags, _resolved_team

    Raises:
        ValueError: If team_alias is invalid (with suggestions)
    """
    if not team_alias:
        return self.mappings.get("defaults", {})

    teams = self.mappings.get("teams", {})

    if team_alias not in teams:
        available = list(teams.keys())
        # Suggest similar team names (Levenshtein distance)
        suggestions = _find_similar(team_alias, available)
        raise ValueError(
            f"Unknown team '{team_alias}'. "
            f"Available teams: {', '.join(available)}. "
            f"Did you mean: {', '.join(suggestions)}?"
        )

    config = teams[team_alias].copy()
    config["_resolved_team"] = team_alias
    return config

def _validate_mappings(self) -> list[str]:
    """Validate mapping structure, return warnings."""
    warnings = []
    teams = self.mappings.get("teams", {})

    for team, config in teams.items():
        if not config.get("area_path"):
            warnings.append(f"Team '{team}' missing area_path")
        if not config.get("default_type"):
            warnings.append(f"Team '{team}' missing default_type")

    return warnings
```

**Update `__init__`:**
```python
def __init__(self, org_url: str | None = None, pat: str | None = None) -> None:
    # ... existing code ...
    self.mappings = self._load_mappings()

    # Validate and warn
    warnings = self._validate_mappings()
    for warning in warnings:
        LOGGER.warning(f"ADO Mapping: {warning}")
```

**Refactor `create` action:**
```python
# Replace lines 140-148 with:
try:
    team_config = self._resolve_team_config(team_alias)
except ValueError as e:
    return f"❌ Error: {str(e)}"

default_area = self.mappings.get("defaults", {}).get("area_path")
final_area_path = area_path or team_config.get("area_path") or default_area
final_type = type or team_config.get("default_type") or "Bug"
# ... rest of create logic
```

**Success Criteria:**
- ✅ Invalid team_alias raises helpful error with suggestions
- ✅ Mappings validated on load with warnings logged
- ✅ Team resolution logic reusable across actions

---

#### 1.2 Team Discovery Action

**File:** `services/agent/src/core/tools/azure_devops.py`

**Add to `run()` method:**
```python
elif action == "get_teams":
    """List configured teams with their settings."""
    teams = self.mappings.get("teams", {})

    if not teams:
        return "⚠️ No teams configured in ado_mappings.yaml"

    results = ["### Configured Teams\n"]
    for team_alias, config in teams.items():
        area = config.get("area_path", "Not set")
        type_ = config.get("default_type", "Not set")
        tags = config.get("default_tags", [])
        tags_str = ", ".join(tags) if tags else "None"

        results.append(f"**{team_alias}**")
        results.append(f"  - Area Path: {area}")
        results.append(f"  - Default Type: {type_}")
        results.append(f"  - Default Tags: {tags_str}")
        results.append("")

    return "\n".join(results)
```

**Update docstring (line 88-106):**
```python
"""
Manage Azure DevOps Work Items.

Args:
    action: 'create', 'get', 'list', 'search', 'children', or 'get_teams'.
    ... (existing args)
    team_alias: 'infra', 'platform', 'security', etc. (use get_teams to discover)
    ... (rest of args)
"""
```

**Success Criteria:**
- ✅ `get_teams` action returns formatted team list
- ✅ Shows area_path, default_type, default_tags for each team
- ✅ Returns helpful message when no teams configured

---

#### 1.3 Team-Aware Listing & Searching

**File:** `services/agent/src/core/tools/azure_devops.py`

**Update `list` action (starting at line 311):**
```python
elif action == "list":
    if not target_project:
        return "❌ Error: Project not specified for list action."

    # NEW: Resolve team_alias to area_path
    if team_alias:
        try:
            team_config = self._resolve_team_config(team_alias)
            # Override area_path if team provided
            if not area_path:
                area_path = team_config.get("area_path")
            # Auto-add team default tags to filter if not specified
            if not tags and team_config.get("default_tags"):
                tags = team_config["default_tags"]
        except ValueError as e:
            return f"❌ Error: {str(e)}"

    # ... rest of existing list logic
```

**Update `search` action (starting at line 361):**
```python
elif action == "search":
    if not query:
        return "❌ Error: 'query' is required for action='search'."
    if not target_project:
        return "❌ Error: Project not specified for search action."

    # NEW: Team-aware search
    team_area_clause = ""
    if team_alias:
        try:
            team_config = self._resolve_team_config(team_alias)
            if team_config.get("area_path"):
                safe_area = _sanitize_wiql_value(team_config["area_path"])
                team_area_clause = f" AND [System.AreaPath] UNDER '{safe_area}'"
        except ValueError as e:
            return f"❌ Error: {str(e)}"

    # WIQL text search with sanitized inputs
    safe_project = _sanitize_wiql_value(target_project)
    safe_query = _sanitize_wiql_value(query)
    wiql = f"""
    SELECT [System.Id], [System.Title], [System.State], [System.WorkItemType]
    FROM WorkItems
    WHERE [System.TeamProject] = '{safe_project}'
      {team_area_clause}
      AND ([System.Title] CONTAINS '{safe_query}'
           OR [System.Description] CONTAINS '{safe_query}')
    ORDER BY [System.ChangedDate] DESC
    """  # noqa: S608

    # ... rest of search logic
```

**Success Criteria:**
- ✅ `list(team_alias="platform")` filters to Platform team's area
- ✅ `search(query="auth", team_alias="security")` searches Security team only
- ✅ Invalid team_alias shows helpful error before querying ADO

---

### Phase 2: Requirements Engineer Enhancements (Critical)

#### 2.1 Update Skill Instructions

**File:** `skills/work/requirements_engineer.md`

**Update WORKFLOW section (after line 105):**
```markdown
### 1.5 Resolve Team (MANDATORY)

Before drafting, resolve and validate the team:

**If team NOT specified:**
- Call `azure_devops(action="get_teams")` to list available teams
- Suggest team based on work type:
  - Security keywords (vulnerability, CVE, XSS, auth) → "security" team
  - Infrastructure keywords (deployment, kubernetes, CI/CD) → "infra" team
  - Default to "common" if unclear
- Ask user: "I suggest team 'security'. Use this team? (Yes / Other)"

**If team IS specified:**
- Validate by calling `_resolve_team_config()` (happens automatically in create action)
- If invalid, show available teams and ask user to pick

### 2. Draft (Enhanced Preview)

Present a COMPLETE draft showing resolved configuration:

```
TYPE: [Feature/User Story/Bug/Security Incident]
TEAM: [team_alias]

RESOLVED CONFIGURATION:
├─ Area Path: [auto-resolved from team config]
├─ Default Type: [auto-resolved]
└─ Default Tags: [auto-resolved, will be merged with custom tags]

TITLE: [Concise, searchable title - max 80 chars]

DESCRIPTION:
[Use appropriate template. 2-4 sentences max.]

ACCEPTANCE CRITERIA: (skip for Features)
- [ ] Criterion 1
- [ ] Criterion 2

ADDITIONAL TAGS: [custom tags beyond team defaults]
START DATE: [YYYY-MM-DD or "TBD"]
TARGET DATE: [YYYY-MM-DD or "TBD"]
```

**Why show resolved config?**
- User sees EXACTLY where work item will be created
- Prevents surprises (wrong team, missing tags)
- User can correct team choice before creation
```

**Add Team Suggestion Logic (new section after line 143):**
```markdown
---

## TEAM SUGGESTION RULES

When user doesn't specify team, use these heuristics:

### Security Team
**Triggers:** vulnerability, CVE, security incident, XSS, SQL injection, OWASP, penetration test, auth bypass
**Suggest:** "security" team
**Confirm:** "This appears to be security work. Use 'security' team?"

### Infrastructure Team
**Triggers:** deployment, infrastructure, kubernetes, docker, CI/CD, pipeline, monitoring, backup
**Suggest:** "infra" team
**Confirm:** "This appears to be infrastructure work. Use 'infra' team?"

### Platform Team
**Triggers:** API, framework, library, SDK, core service, authentication, authorization
**Suggest:** "platform" team
**Confirm:** "This appears to be platform work. Use 'platform' team?"

### Default: Common Team
**Triggers:** None of above, or ambiguous
**Action:** List all teams, ask user to pick
**Message:** "Available teams: [list]. Which team should own this work?"
```

**Update MANDATORY EXECUTION RULES (line 17):**
```markdown
**RULE 2**: Call azure_devops TWICE maximum:
  - ONCE for get_teams (if team discovery needed)
  - ONCE for create (after user approval)
**RULE 3**: ALWAYS validate team before showing draft to user
```

**Success Criteria:**
- ✅ requirements_engineer calls `get_teams` when team not specified
- ✅ Suggests team based on work type keywords
- ✅ Shows resolved area_path, tags, type in draft preview
- ✅ Validates team before drafting, shows error if invalid

---

#### 2.2 Team-Aware Templates (Optional Enhancement)

**File:** `skills/work/requirements_engineer.md`

**Add after line 143:**
```markdown
---

## TEAM-SPECIFIC TEMPLATES

Some teams have specialized requirements:

### Security Team - Enhanced Template
For team_alias="security":
- Always include: Severity (High/Medium/Low), Affected Systems, Remediation Timeline
- Auto-add tags: Security, [SecurityIncidentHigh/Medium based on severity]
- Template must include CVSS score if applicable

### Infrastructure Team - Enhanced Template
For team_alias="infra":
- Include: Affected Environments (Dev/Staging/Prod), Rollback Plan
- Auto-add tags: Infrastructure, [component name]

### Default Template
For all other teams: Use standard templates from line 30-98
```

**Success Criteria:**
- ✅ Security work items get severity field
- ✅ Infra work items get environment field
- ✅ Team-specific tags automatically added

---

### Phase 3: Backlog Manager Enhancements (Important)

#### 3.1 Update Skill Instructions

**File:** `skills/work/backlog_manager.md`

**Update CAPABILITIES section (after line 42):**
```markdown
### 0. Team Discovery (NEW)
Discover what teams exist and their configuration:
```
azure_devops(action="get_teams")
```

Returns team list with area paths and default types.

### 1. Search & Discovery (Enhanced)
Find work items by text, type, state, tags, OR TEAM:
```
# Search by team alias (NEW)
azure_devops(action="search", query="authentication", team_alias="security")
azure_devops(action="list", type="Feature", state="Active", team_alias="platform")

# Search by area path (still works)
azure_devops(action="list", area_path="MyProject\\Backend", tags=["security"])
```
```

**Update COMMON TASKS (after line 65):**
```markdown
### "What's the Platform team working on?"
1. List Platform's active work: `action="list", team_alias="platform", state="Active"`
2. Format as table with progress indicators

### "Which team has the most security debt?"
1. Get teams: `action="get_teams"`
2. For each team: `action="list", team_alias=<team>, type="Bug", tags=["Security"]`
3. Compare counts, show ranking

### "Show me all Infra team's blocked items"
1. List: `action="list", team_alias="infra", state="Active"`
2. Filter results for items with "Blocked" tag or comments mentioning blockers

### "Cross-team dependency check"
1. Search for keyword across teams: `action="search", query="shared API"`
2. Group results by team (extract from area_path)
3. Highlight dependencies: "Platform, Engage, and Common all working on shared API"
```

**Add new section after line 104:**
```markdown
---

## TEAM ANALYTICS

When asked about team workload, capacity, or comparison:

### Workload Distribution
```python
# Get all teams
teams = get_teams()

# For each team, count active work
for team in teams:
    active_count = list(team_alias=team, state="Active")

# Show as table:
| Team     | Active Items | Closed This Month | Bug Ratio |
|----------|--------------|-------------------|-----------|
| Platform | 23           | 12                | 15%       |
| Security | 8            | 15                | 50%       |
```

### Team Velocity Comparison
When user asks "which team is moving fastest?":
1. List recently closed items per team (last 30 days)
2. Calculate closure rate
3. Show ranking with context
```

**Success Criteria:**
- ✅ backlog_manager uses team_alias in queries
- ✅ Can compare workload across teams
- ✅ Discovers teams dynamically
- ✅ Shows team-aware analytics

---

### Phase 4: Testing & Validation (Critical)

#### 4.1 Unit Tests

**New File:** `services/agent/src/core/tests/test_azure_devops_teams.py`

**Test coverage:**
```python
class TestTeamResolution:
    def test_resolve_valid_team(self):
        """Valid team alias returns correct config."""

    def test_resolve_invalid_team_suggests_similar(self):
        """Invalid team shows suggestions."""
        # "fronted" → suggests "frontend"

    def test_resolve_no_team_uses_defaults(self):
        """None team_alias uses default config."""

    def test_get_available_teams(self):
        """Returns list of configured teams."""

class TestTeamValidation:
    def test_validates_on_load(self):
        """Warns about teams missing required fields."""

    def test_invalid_yaml_structure(self):
        """Handles malformed ado_mappings.yaml."""

class TestTeamAwareQuerying:
    def test_list_by_team_alias(self):
        """list(team_alias='platform') filters correctly."""

    def test_search_by_team_alias(self):
        """search(query='x', team_alias='security') scopes correctly."""

    def test_create_with_team_validation(self):
        """create(team_alias='invalid') fails before API call."""

class TestGetTeamsAction:
    def test_get_teams_returns_formatted_list(self):
        """get_teams action returns team configurations."""

    def test_get_teams_empty_config(self):
        """Handles no teams configured."""
```

**Success Criteria:**
- ✅ All team resolution logic has >90% coverage
- ✅ Edge cases tested (invalid teams, missing config, malformed YAML)
- ✅ Integration tests with mock ADO client

---

#### 4.2 Integration Testing

**Update File:** `services/agent/scripts/test_azure_devops.py`

**Add team operations:**
```python
def test_team_operations():
    """Test team discovery and validation."""
    print("\n=== Testing Team Operations ===")

    # Test 1: Get teams
    result = azure_devops(action="get_teams")
    print(f"Teams: {result}")
    assert "platform" in result.lower()

    # Test 2: List by team
    result = azure_devops(
        action="list",
        team_alias="platform",
        state="Active",
        top=5
    )
    print(f"Platform items: {result}")

    # Test 3: Invalid team
    result = azure_devops(
        action="list",
        team_alias="invalid_team_name"
    )
    print(f"Invalid team error: {result}")
    assert "Unknown team" in result

    # Test 4: Team-aware search
    result = azure_devops(
        action="search",
        query="authentication",
        team_alias="security"
    )
    print(f"Security team search: {result}")

    print("\n✅ All team operations passed")
```

**Success Criteria:**
- ✅ Smoke test covers all new actions
- ✅ Tests run against real ADO instance (dev environment)
- ✅ Validates error messages are helpful

---

### Phase 5: Documentation (Important)

#### 5.1 Team Structure Guide

**New File:** `docs/AZURE_DEVOPS_TEAMS.md`

**Contents:**
```markdown
# Azure DevOps Team Structure

## Overview

The AI Agent Platform integrates with Azure DevOps using a team-based structure defined in `ado_mappings.yaml`. This allows skills to be team-aware when creating and querying work items.

## Configuration

### File Location
`services/agent/config/ado_mappings.yaml`

### Structure
```yaml
defaults:
  area_path: "Web Teams\Common"
  default_type: "Feature"

teams:
  platform:
    area_path: "Web Teams\Platform"
    default_type: "User story"

  security:
    area_path: "Web Teams\Platform\Security"
    default_type: "User Story"
    default_tags: ["Security", "SecurityIncidentHigh"]
```

## Required Fields
- `area_path`: ADO Area Path for the team
- `default_type`: Default work item type (Feature, User Story, Bug)

## Optional Fields
- `default_tags`: Tags automatically applied to work items

## Discovering Teams

Use the `backlog_manager` or call directly:
```
azure_devops(action="get_teams")
```

## Team-Aware Queries

### Listing Work Items
```python
# By team alias
azure_devops(action="list", team_alias="platform", state="Active")

# By area path (still works)
azure_devops(action="list", area_path="Web Teams\\Platform")
```

### Searching
```python
azure_devops(action="search", query="auth", team_alias="security")
```

### Creating Work Items
```python
azure_devops(
    action="create",
    team_alias="infra",  # Auto-sets area_path and tags
    title="Upgrade Kubernetes",
    description="Migrate to v1.28"
)
```

## Adding New Teams

1. Edit `services/agent/config/ado_mappings.yaml`
2. Add team under `teams:` section
3. Restart agent service
4. Validate: Call `azure_devops(action="get_teams")`

## Troubleshooting

### "Unknown team 'X'"
- Team not in ado_mappings.yaml
- Check spelling (case-sensitive)
- Run `get_teams` to see available teams

### Work Items in Wrong Area
- Check team's `area_path` in config
- Validate Area Path exists in ADO
- Check for typos in area path

### Missing Tags
- Add `default_tags` to team config
- Tags are optional; won't cause errors if missing
```

**Success Criteria:**
- ✅ Documentation explains team structure clearly
- ✅ Includes configuration examples
- ✅ Has troubleshooting section

---

#### 5.2 Update Main Documentation

**File:** `docs/CAPABILITIES.md` or `docs/SKILLS_FORMAT.md`

**Add section:**
```markdown
## Team-Aware Backlog Management

The platform understands Azure DevOps team structure:

### Skills
- **backlog_manager**: List, search, analyze work items by team
- **requirements_engineer**: Create work items with team validation

### Team Discovery
```
/researcher List all configured teams
→ Uses azure_devops(action="get_teams")
```

### Team-Specific Queries
```
User: "What's the Platform team working on?"
→ backlog_manager lists Platform's active work items

User: "Create a security incident for the Security team"
→ requirements_engineer validates team, creates with correct area/tags
```

### Configuration
Teams are defined in `services/agent/config/ado_mappings.yaml`. See [Team Structure Guide](AZURE_DEVOPS_TEAMS.md) for details.
```

**Success Criteria:**
- ✅ Main docs reference team features
- ✅ Links to detailed team guide
- ✅ Examples show team-aware workflows

---

### Phase 6: Advanced Features (Nice-to-Have)

#### 6.1 Team Metrics & Analytics

**File:** `services/agent/src/core/tools/azure_devops.py`

**New action: `team_summary`**
```python
elif action == "team_summary":
    """Show workload distribution across all teams."""
    if not target_project:
        return "❌ Error: Project not specified."

    teams = self.mappings.get("teams", {})
    if not teams:
        return "No teams configured."

    results = ["### Team Workload Summary\n"]
    results.append("| Team | Active | New | Closed (30d) |")
    results.append("|------|--------|-----|--------------|")

    for team_alias, config in teams.items():
        area_path = config.get("area_path")
        if not area_path:
            continue

        # Query active count
        active_query = f"""
        SELECT [System.Id] FROM WorkItems
        WHERE [System.TeamProject] = '{target_project}'
          AND [System.AreaPath] UNDER '{area_path}'
          AND [System.State] = 'Active'
        """
        active_result = wit_client.query_by_wiql({"query": active_query})
        active_count = len(active_result.work_items)

        # Similar for New and Closed
        # ... (implementation details)

        results.append(f"| {team_alias} | {active_count} | {new_count} | {closed_count} |")

    return "\n".join(results)
```

**Success Criteria:**
- ✅ Shows cross-team workload comparison
- ✅ Formatted as markdown table
- ✅ Includes recent velocity (closed items)

---

#### 6.2 Smart Team Suggestions (ML-based)

**File:** `skills/work/requirements_engineer.md`

**Enhancement idea:**
```markdown
## SMART TEAM SUGGESTIONS (Future)

Learn from historical work item patterns:
- Track which keywords correlate with which teams
- Build keyword → team mapping from past 1000 work items
- Suggest team with confidence score

Example:
User: "Add rate limiting to API"
AI: "Based on similar work items, I suggest 'platform' team (85% confidence). Use this?"
```

**Not implementing in this phase** - document as future enhancement.

---

## Implementation Checklist

### Phase 1: Core Team Infrastructure ✅
- [ ] Add `_resolve_team_config()` method with validation
- [ ] Add `_get_available_teams()` method
- [ ] Add `_validate_mappings()` method with warnings
- [ ] Refactor `create` action to use team resolution
- [ ] Add `get_teams` action
- [ ] Update `list` action to support team_alias
- [ ] Update `search` action to support team_alias
- [ ] Update tool docstring with team_alias examples

### Phase 2: Requirements Engineer ✅
- [ ] Update requirements_engineer.md workflow section
- [ ] Add team resolution step (1.5)
- [ ] Update draft template to show resolved config
- [ ] Add team suggestion rules section
- [ ] Update execution rules to allow 2 tool calls max
- [ ] Add team-specific template guidance (optional)

### Phase 3: Backlog Manager ✅
- [ ] Update backlog_manager.md capabilities section
- [ ] Add team discovery example
- [ ] Update common tasks with team examples
- [ ] Add team analytics section
- [ ] Add cross-team comparison examples

### Phase 4: Testing ✅
- [ ] Create test_azure_devops_teams.py with full coverage
- [ ] Add TestTeamResolution class
- [ ] Add TestTeamValidation class
- [ ] Add TestTeamAwareQuerying class
- [ ] Add TestGetTeamsAction class
- [ ] Update test_azure_devops.py with team operations
- [ ] Run full test suite and validate >90% coverage

### Phase 5: Documentation ✅
- [ ] Create docs/AZURE_DEVOPS_TEAMS.md
- [ ] Add configuration guide
- [ ] Add team discovery guide
- [ ] Add troubleshooting section
- [ ] Update docs/CAPABILITIES.md with team features
- [ ] Add examples to main docs

### Phase 6: Advanced (Optional) ⚠️
- [ ] Implement team_summary action
- [ ] Document smart suggestions for future
- [ ] Add team velocity metrics

---

## Quality Gates

**Before marking Phase 1 complete:**
- ✅ All team validation tests pass
- ✅ Invalid team shows helpful error with suggestions
- ✅ get_teams action works
- ✅ Smoke test passes against dev ADO

**Before marking Phase 2 complete:**
- ✅ requirements_engineer validates teams before drafting
- ✅ Shows resolved config in preview
- ✅ Suggests team based on keywords
- ✅ Manual testing: Create work item with invalid team → see helpful error

**Before marking Phase 3 complete:**
- ✅ backlog_manager can filter by team
- ✅ Can discover teams programmatically
- ✅ Manual testing: "Show Platform team's work" → returns correct items

**Before marking Phase 4 complete:**
- ✅ Test coverage >90% for new code
- ✅ All unit tests pass
- ✅ Integration test passes

**Before marking Phase 5 complete:**
- ✅ Documentation reviewed for clarity
- ✅ Examples tested and work
- ✅ Troubleshooting section validated

**Overall Production Ready:**
- ✅ Phases 1-5 complete
- ✅ No critical bugs in manual testing
- ✅ Performance acceptable (team resolution <50ms)
- ✅ Error messages helpful to end users

---

## Risk Mitigation

### Risk: Breaking existing workflows
**Mitigation:**
- Keep area_path parameter working (backwards compatible)
- team_alias is optional, defaults don't change
- Extensive testing before deployment

### Risk: Invalid team config breaks tool
**Mitigation:**
- Validation on load with warnings, not errors
- Tool degrades gracefully (falls back to defaults)
- Clear error messages guide users to fix config

### Risk: Performance degradation
**Mitigation:**
- Team resolution is in-memory (ado_mappings.yaml cached)
- No extra API calls for validation
- get_teams doesn't query ADO, just returns config

### Risk: Documentation not maintained
**Mitigation:**
- Link docs to code comments
- Add validation that warns if teams undocumented
- Include docs in PR review checklist

---

## Success Metrics (30 days post-deployment)

**Quantitative:**
- 📊 Misconfigured work items (wrong area) reduced by >80%
- 📊 requirements_engineer error rate <5%
- 📊 Team discovery used in >30% of backlog queries
- 📊 Zero production incidents from team feature

**Qualitative:**
- ✅ Users report easier work item creation
- ✅ No confusion about team structure
- ✅ Faster backlog queries (less trial-and-error)

---

## Rollback Plan

If critical issues found after deployment:

1. **Immediate:** Revert to previous version (git revert)
2. **Temporary:** Disable team validation (allow all team_alias values)
3. **Fix forward:** Address issue, add test, redeploy

**Rollback triggers:**
- >10% error rate in requirements_engineer
- Tool unusable for any team
- Data corruption (work items in wrong areas)

---

## Timeline Estimate

**Not providing time estimates per project guidelines** - focus is on what needs to be done.

**Sequence:**
1. Phase 1 (Core Infrastructure) - Foundation for everything
2. Phase 2 (Requirements Engineer) - High user impact
3. Phase 3 (Backlog Manager) - Important but can run parallel with Phase 2
4. Phase 4 (Testing) - Ongoing throughout, finalize at end
5. Phase 5 (Documentation) - Can start early, finalize at end
6. Phase 6 (Advanced) - Post-MVP, only if time allows

---

## Appendix: Example Workflows

### Workflow 1: Creating Work Item with Team Discovery

**Before (current):**
```
User: "Create a security incident"
RE: "Which team?"
User: "I don't know, maybe security?"
RE: [Creates with team_alias="security", silently falls back if wrong]
Result: Work item created, user unsure if correct
```

**After (enhanced):**
```
User: "Create a security incident"
RE: [Detects "security" keyword]
    [Calls get_teams, finds "security" team]
    [Validates team exists]

    "I suggest team 'security'. Draft:

    TYPE: Security Incident
    TEAM: security

    RESOLVED CONFIGURATION:
    ├─ Area Path: Web Teams\Platform\Security
    ├─ Default Type: User Story
    └─ Default Tags: Security, SecurityIncidentHigh

    ..."

User: "Yes, create it"
RE: [Calls create with team_alias="security"]
    ✅ Created Security Incident #12345
```

### Workflow 2: Team-Aware Backlog Query

**Before (current):**
```
User: "What's Platform team working on?"
BM: [User must know area path]
    azure_devops(action="list", area_path="Web Teams\\Platform")
```

**After (enhanced):**
```
User: "What's Platform team working on?"
BM: [Recognizes team name]
    azure_devops(action="list", team_alias="platform", state="Active")

    ### Found 23 Work Items
    - #101 [User Story] API rate limiting (Active)
    - #102 [Bug] Auth token expiry (Active)
    ...
```

### Workflow 3: Cross-Team Analysis

**After only (new capability):**
```
User: "Which teams are working on authentication?"
BM: [Calls search across project]
    azure_devops(action="search", query="authentication")

    [Groups results by area_path → maps to teams]

    ### Authentication Work Across Teams

    **Platform Team** (5 items)
    - #101 OAuth2 implementation
    - #103 Token refresh logic

    **Security Team** (3 items)
    - #201 Penetration test for auth
    - #202 MFA implementation

    **Engage Team** (1 item)
    - #301 Social login UI
```

---

## Sign-off

**Plan approved for implementation:** [ ]
**Approved by:** _______________
**Date:** _______________

**Implementation started:** [ ]
**Implementation completed:** [ ]
**Deployed to production:** [ ]
