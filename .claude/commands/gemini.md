Use the gemini-analyst subagent to answer a codebase question using Gemini 3.1 Pro's 1M-token context window. Gemini reads the full codebase in one coherent pass -- faster and cheaper than Opus/Sonnet file-reading for discovery tasks.

Spawn subagent_type="gemini-analyst" with this prompt:

---

Run from /home/magnus/dev/ai-agent-platform

The user's question is: $ARGUMENTS

Read the files most likely relevant to this question from the codebase, then answer it. Use your judgment on which directories to read -- typical starting points:
- services/agent/src/core/ for orchestration, tools, agents, runtime
- services/agent/src/interfaces/ for HTTP, Telegram, scheduler
- skills/ for skill definitions
- services/agent/config/tools.yaml for tool registration

Invoke as:
```bash
gemini -m gemini-3.1-pro-preview --yolo -p "PROMPT"
```

Where PROMPT includes the file-reading instructions above followed by the user's question.

Return Gemini's full answer verbatim.
