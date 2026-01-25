---
name: "deep_research"
description: "Comprehensive deep research with multiple search angles, extensive page reading, and cross-source analysis. Use for complex topics requiring thorough investigation."
tools: ["web_search", "web_fetch"]
model: skillsrunner
max_turns: 10
---

# Deep Research

**Research topic:** $ARGUMENTS

## MANDATORY EXECUTION RULES

**RULE 1**: Your training data is OUTDATED. You MUST use tools extensively.
**RULE 2**: Each turn, call up to 6 tools (searches + fetches combined).
**RULE 3**: NEVER repeat a tool call with identical arguments.
**RULE 4**: Follow the PHASE STRUCTURE below. Move to next phase when current phase goals are met.
**RULE 5**: After completing Phase 3, STOP and write your report. No more tool calls.

## BUDGET

| Resource | Limit | Purpose |
|----------|-------|---------|
| Max turns | 10 | Thorough exploration |
| Tool calls per turn | 6 | Parallel fetching |
| Search queries | 5-8 | Multiple angles |
| Page fetches | 20-30 | Deep reading |

## PHASE STRUCTURE (FOLLOW STRICTLY)

### PHASE 1: Broad Search (Turns 1-3)
**Goal**: Discover sources from multiple angles

Turn 1:
- `web_search` with primary query
- `web_fetch` x5 on most relevant URLs

Turn 2:
- `web_search` with alternative phrasing or related angle
- `web_fetch` x5 on new URLs

Turn 3:
- `web_search` for counter-arguments or different perspectives
- `web_fetch` x5 on diverse sources

**Phase 1 complete when**: You have 10+ pages fetched from 3+ different search angles.

### PHASE 2: Deep Reading (Turns 4-6)
**Goal**: Fill gaps and verify claims

Turn 4-6:
- `web_search` for specific claims that need verification
- `web_fetch` on authoritative sources (official docs, academic, primary sources)
- Focus on quality over quantity

**Phase 2 complete when**: Key claims are cross-referenced, gaps are filled.

### PHASE 3: Synthesis (Turns 7-8)
**Goal**: Analyze and write report

Turn 7:
- Review all collected information
- Identify patterns, agreements, contradictions
- Begin writing structured report

Turn 8:
- Complete and finalize report
- STOP - no more tool calls after this

**CRITICAL**: After Turn 8, you MUST output your final report. Do NOT continue searching.

## WRONG PATTERNS (will be blocked)

```
- Repeating the same search query
- Fetching the same URL twice
- Searching after Phase 2 is complete
- Continuing tool calls after writing report
- Running more than 10 turns
```

## OUTPUT FORMAT

### Executive Summary
[3-5 sentence comprehensive answer]

### Research Methodology

**Search Queries Used:**
1. "[query 1]" - [what you found]
2. "[query 2]" - [what you found]
3. "[query 3]" - [what you found]

**Sources Analyzed:**
| # | Source | Type | Key Contribution |
|---|--------|------|------------------|
| 1 | [URL] | [News/Academic/Official/Forum] | [What it contributed] |
| 2 | [URL] | [Type] | [Contribution] |
| ... | ... | ... | ... |

### Detailed Findings

#### [Topic Area 1]
[Findings with inline source citations]

#### [Topic Area 2]
[Findings with inline source citations]

### Cross-Source Analysis
- **Consensus**: [What multiple sources agree on]
- **Disagreements**: [Where sources conflict]
- **Gaps**: [What couldn't be verified]

### Confidence Assessment
- **Overall**: [High/Medium/Low]
- **Reasoning**: [Why this confidence level]

### Recommendations
[Actionable next steps or areas for further research, if applicable]

---
*For quick lookups, use `search`. For standard research, use `research`.*
