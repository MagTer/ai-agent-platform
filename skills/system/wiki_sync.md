---
name: wiki_sync
description: Syncs the TIBP corporate wiki from Azure DevOps into the Qdrant search index. Use for scheduled imports.
model: skillsrunner
max_turns: 2
tools:
  - wiki_sync
---
# Wiki Sync

Sync the TIBP wiki from Azure DevOps.

## Instructions

Call the wiki_sync tool immediately:

```json
{"name": "wiki_sync", "arguments": {"action": "sync"}}
```

Report the result to the user exactly as returned by the tool.
Do not ask questions. Do not add commentary.
