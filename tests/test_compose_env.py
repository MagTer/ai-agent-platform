import os
from pathlib import Path

import yaml


COMPOSE_PATH = Path(__file__).resolve().parents[1] / "compose" / "docker-compose.yml"


def _env_list_to_dict(env_list):
    out = {}
    for item in env_list or []:
        if isinstance(item, str) and "=" in item:
            k, v = item.split("=", 1)
            out[k.strip()] = v
    return out


def test_webfetch_receives_enable_qdrant():
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    wf = data.get("services", {}).get("webfetch", {})
    env = _env_list_to_dict(wf.get("environment", []))
    assert "ENABLE_QDRANT" in env, "ENABLE_QDRANT must be passed to webfetch"


def test_ragproxy_receives_rag_flags():
    with COMPOSE_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    rp = data.get("services", {}).get("ragproxy", {})
    env = _env_list_to_dict(rp.get("environment", []))
    assert env.get("ENABLE_RAG") is not None
    assert env.get("RAG_MAX_SOURCES") is not None
    assert env.get("RAG_MAX_CHARS") is not None

