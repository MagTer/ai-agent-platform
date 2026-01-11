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

### Required Fields

- `area_path`: ADO Area Path for the team
- `default_type`: Default work item type (Feature, User Story, Bug)

### Optional Fields

- `default_tags`: Tags automatically applied to work items

## Discovering Teams

Use the `backlog_manager` skill or call the tool directly:

```python
azure_devops(action="get_teams")
```

**Output:**
```
### Configured Teams

**platform**
  - Area Path: Web Teams\Platform
  - Default Type: User story
  - Default Tags: None

**security**
  - Area Path: Web Teams\Platform\Security
  - Default Type: User Story
  - Default Tags: Security, SecurityIncidentHigh
```

## Team-Aware Queries

### Listing Work Items

```python
# By team alias (RECOMMENDED)
azure_devops(action="list", team_alias="platform", state="Active")

# By area path (still works for backwards compatibility)
azure_devops(action="list", area_path="Web Teams\\Platform")
```

**Benefits of team_alias:**
- Shorter, more readable queries
- Automatic area path resolution
- Built-in validation with helpful error messages
- Auto-applies team's default tags to filters

### Searching

```python
# Search within a specific team's area
azure_devops(action="search", query="authentication", team_alias="security")

# Search entire project
azure_devops(action="search", query="authentication")
```

### Creating Work Items

```python
azure_devops(
    action="create",
    team_alias="infra",  # Auto-sets area_path and tags
    title="Upgrade Kubernetes cluster",
    description="Migrate to v1.28",
    confirm_write=True
)
```

**What happens:**
1. Tool validates `team_alias` exists
2. Resolves team's `area_path` and `default_tags`
3. Merges with any explicitly provided values
4. Creates work item with resolved configuration

**If invalid team:**
```
Error: Unknown team 'infr'. Available teams: infra, platform, security, common.
Did you mean: infra?
```

## Skills Integration

### Requirements Engineer

The `requirements_engineer` skill uses team structure to:

1. **Suggest teams** based on work item content:
   - Security keywords → "security" team
   - Infrastructure keywords → "infra" team
   - Platform/API keywords → "platform" team

2. **Show resolved configuration** before creating:
   ```
   TYPE: User Story
   TEAM: security

   RESOLVED CONFIGURATION:
   ├─ Area Path: Web Teams\Platform\Security
   ├─ Default Type: User Story
   └─ Default Tags: Security, SecurityIncidentHigh

   TITLE: Fix XSS vulnerability in search
   ```

3. **Validate team** before user approval (prevents misconfiguration)

### Backlog Manager

The `backlog_manager` skill uses team structure to:

1. **Filter by team**:
   ```
   User: "What's Platform team working on?"
   → Lists Platform's active items
   ```

2. **Compare team workload**:
   ```
   User: "Which team has the most security debt?"
   → Counts security bugs per team, shows ranking
   ```

3. **Discover teams programmatically** instead of guessing area paths

## Adding New Teams

1. Edit `services/agent/config/ado_mappings.yaml`
2. Add team under `teams:` section:
   ```yaml
   teams:
     new_team:
       area_path: "Web Teams\NewTeam"
       default_type: "User story"
       default_tags: ["NewTeamTag"]  # Optional
   ```
3. Restart agent service (if running)
4. Validate: Call `azure_devops(action="get_teams")`

**Validation on Load:**
The tool validates mappings when initialized and logs warnings for:
- Teams missing `area_path`
- Teams missing `default_type`

## Troubleshooting

### "Unknown team 'X'"

**Cause:** Team not configured in `ado_mappings.yaml`

**Solution:**
1. Check spelling (team names are case-sensitive)
2. Run `get_teams` to see available teams
3. Add team to config if needed

**Example:**
```
Error: Unknown team 'platfrom'. Available teams: platform, security, infra.
Did you mean: platform?
```

### Work Items Created in Wrong Area

**Cause:** Team's `area_path` in config doesn't match ADO

**Solution:**
1. Verify Area Path exists in Azure DevOps
2. Check for typos in `ado_mappings.yaml`
3. Ensure backslashes are properly escaped: `"Web Teams\\Platform"`

### Missing Tags on Work Items

**Cause:** Team doesn't have `default_tags` configured

**Solution:**
1. Add `default_tags` to team config (optional field)
2. Or specify tags explicitly when creating:
   ```python
   azure_devops(
       action="create",
       team_alias="platform",
       tags=["CustomTag"],
       ...
   )
   ```

### Team Validation Warnings on Startup

**Example:**
```
WARNING: ADO Mapping: Team 'platform' missing area_path
```

**Solution:**
1. Check logs for specific missing fields
2. Update `ado_mappings.yaml` with required fields
3. Restart agent

**Note:** Warnings don't prevent tool from working, but features may be limited

## Examples

### Example 1: Creating Security Incident

**User Request:**
"Create a security incident for XSS vulnerability"

**requirements_engineer workflow:**
1. Detects "security" keyword
2. Calls `get_teams` to validate "security" team exists
3. Shows draft with resolved config:
   ```
   TEAM: security
   RESOLVED CONFIGURATION:
   ├─ Area Path: Web Teams\Platform\Security
   ├─ Default Type: User Story
   └─ Default Tags: Security, SecurityIncidentHigh
   ```
4. On approval, creates with merged configuration

### Example 2: Team Workload Comparison

**User Request:**
"Compare workload across all teams"

**backlog_manager workflow:**
1. Calls `get_teams` to discover all teams
2. For each team, calls `list(team_alias=<team>, state="Active")`
3. Formats as table:
   ```
   | Team     | Active Items | Bug Ratio |
   |----------|--------------|-----------|
   | Platform | 23           | 15%       |
   | Security | 8            | 50%       |
   | Infra    | 12           | 25%       |
   ```

### Example 3: Cross-Team Dependency Analysis

**User Request:**
"Which teams are working on the authentication system?"

**backlog_manager workflow:**
1. Searches across project: `search(query="authentication")`
2. Groups results by team (from area_path)
3. Shows summary:
   ```
   ### Authentication Work Across Teams

   Platform Team (5 items):
   - #101 OAuth2 implementation
   - #103 Token refresh logic

   Security Team (3 items):
   - #201 Penetration test for auth
   - #202 MFA implementation

   Engage Team (1 item):
   - #301 Social login UI
   ```

## Best Practices

### DO:
- Use `team_alias` instead of `area_path` for queries (more readable)
- Call `get_teams` when unsure which team to use
- Let skills suggest teams based on content
- Keep team configs in sync with Azure DevOps structure

### DON'T:
- Hardcode area paths in skills (use team_alias)
- Skip team validation (prevents misconfigured items)
- Use relative area paths (always use full paths)
- Forget to restart agent after config changes

## Architecture Notes

### Team Resolution Flow

```
User specifies team_alias
    ↓
Tool calls _resolve_team_config()
    ↓
Validates team exists (raises ValueError if not)
    ↓
Returns config: {area_path, default_type, default_tags, _resolved_team}
    ↓
Tool merges with explicit parameters
    ↓
Executes Azure DevOps API call
```

### Performance

- Team resolution is in-memory (no API calls)
- Config loaded once on tool initialization
- Validation happens before expensive API operations
- `get_teams` returns cached config (instant)

### Security

- Team names are validated against allowlist (config file)
- WIQL queries sanitize all inputs (single quote escaping)
- No arbitrary team names accepted
- Area paths from config are trusted (no user input)

## Related Documentation

- [Architecture](ARCHITECTURE.md) - Overall system design
- [Style Guide](STYLE.md) - Documentation conventions
- [Skills Format](skills/work/README.md) - Creating new skills

## Support

For issues with team configuration:
1. Check logs for validation warnings
2. Verify ADO structure matches config
3. Test with integration script: `python services/agent/scripts/test_azure_devops.py`
4. Review this guide's Troubleshooting section
