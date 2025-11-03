from __future__ import annotations

from datetime import datetime

from stack import health


class DummyContainer:
    def __init__(self, name: str, status: str, health_status: str | None = None):
        self.name = name
        health_info = {"Status": health_status} if health_status else {}
        self.attrs = {
            "State": {
                "Status": status,
                "Health": health_info,
                "StartedAt": "2024-05-17T10:30:00Z",
            }
        }


class DummyDockerClient:
    def __init__(self, containers):
        self._containers = containers

    @property
    def containers(self):  # type: ignore[override]
        return self

    def list(self, all: bool, filters):  # type: ignore[override]
        return self._containers


def test_fetch_container_states(monkeypatch):
    containers = [
        DummyContainer("agent", "running", "healthy"),
        DummyContainer("qdrant", "exited"),
    ]
    monkeypatch.setattr(health.docker, "from_env", lambda: DummyDockerClient(containers))

    rows = health.fetch_container_states()

    assert rows[0]["name"] == "agent"
    assert rows[0]["health"] == "healthy"
    assert rows[1]["status"] == "exited"


def test_render_status_table(monkeypatch):
    class DummyConsole:
        def __init__(self):
            self.rendered = None

        def print(self, table):  # type: ignore[override]
            self.rendered = table

    dummy_console = DummyConsole()
    monkeypatch.setattr(health, "Console", lambda: dummy_console)

    def fake_states():
        return [
            {
                "name": "agent",
                "status": "running",
                "health": "healthy",
                "started": datetime.utcnow().isoformat(),
            }
        ]

    monkeypatch.setattr(health, "fetch_container_states", fake_states)

    health.render_status_table()

    assert dummy_console.rendered is not None
    assert dummy_console.rendered.row_count == 1
