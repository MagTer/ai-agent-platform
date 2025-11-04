from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker-compose.yml"


def _env_map(env_block):
    out = {}
    if isinstance(env_block, dict):
        for key, value in env_block.items():
            if value is None:
                continue
            out[str(key).strip()] = str(value)
        return out

    for item in env_block or []:
        if isinstance(item, str) and "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v
    return out


def test_webfetch_receives_enable_qdrant():
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    wf = data.get("services", {}).get("webfetch", {})
    env = _env_map(wf.get("environment", []))
    assert "ENABLE_QDRANT" in env, "ENABLE_QDRANT must be passed to webfetch"


def test_ragproxy_receives_rag_flags():
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rp = data.get("services", {}).get("ragproxy", {})
    env = _env_map(rp.get("environment", []))
    assert env.get("ENABLE_RAG") is not None
    assert env.get("RAG_MAX_SOURCES") is not None
    assert env.get("RAG_MAX_CHARS") is not None


def test_openwebui_targets_agent():
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    ow = data.get("services", {}).get("openwebui", {})
    env = _env_map(ow.get("environment", []))
    assert env.get("OPENAI_API_BASE_URL", "").startswith(
        "http://agent:"
    ), "Open WebUI must proxy requests through the agent"
    assert env.get("OPENAI_API_KEY") is not None
