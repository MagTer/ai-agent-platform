# AI Agent Platform (local, containerized)

## Arkitektur
- **LLM gateway**: LiteLLM → Ollama (GPU), ev. OpenRouter fallback
- **Client**: Open WebUI (Reasoning/Research/Actions)
- **Search**: SearxNG + `webfetch` (FastAPI) → sammanfattning via LiteLLM
- **Vector DB**: Qdrant
- **Scripts**: `scripts/Stack-Up.ps1`, `Stack-Down.ps1`, `Repo-Save.ps1`

## Quick start
```powershell
# klona & kopiera env
cp .env.template .env
# (fyll i nycklar om du använder OpenRouter)
.\scripts\Stack-Up.ps1
