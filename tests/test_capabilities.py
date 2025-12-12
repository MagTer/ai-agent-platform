import json
from pathlib import Path

import pytest
import yaml

CATALOG_PATH = Path(__file__).resolve().parent.parent / "capabilities" / "catalog.yaml"


@pytest.fixture(scope="module")
def catalog():
    with CATALOG_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise AssertionError("Catalog root must be a mapping")
    return data


def test_catalog_has_expected_root_keys(catalog):
    assert catalog.get("version"), "Catalog version saknas"
    assert catalog.get("kind") == "capability-catalog"
    actions = catalog.get("actions")
    assert isinstance(actions, list) and actions, "Minst en action krävs"


@pytest.mark.parametrize("key", ["id", "status", "summary", "entrypoint", "contract"])
def test_actions_include_required_fields(catalog, key):
    for action in catalog["actions"]:
        assert key in action, f"Fältet '{key}' saknas för action {action}"


def test_entrypoint_structure(catalog):
    for action in catalog["actions"]:
        entrypoint = action["entrypoint"]
        assert entrypoint.get("type") == "http"
        assert entrypoint.get("method")
        assert entrypoint.get("url")


def test_contract_structure(catalog):
    for action in catalog["actions"]:
        contract = action["contract"]
        request = contract.get("request")
        response = contract.get("response")
        assert request and response, "Både request och response måste definieras"
        assert request.get("content_type"), "Request saknar content_type"
        assert response.get("content_type"), "Response saknar content_type"


@pytest.mark.parametrize("section", ["openwebui"])
def test_optional_sections_are_mappings(catalog, section):
    for action in catalog["actions"]:
        if section in action:
            assert isinstance(action[section], dict)
            assert action[section].get("tool_name"), "tool_name krävs när openwebui-nyckeln används"


def test_verification_command_is_json_safe(catalog):
    for action in catalog["actions"]:
        verification = action.get("verification", {})
        smoke = verification.get("smoke_test")
        if not smoke:
            continue
        command = smoke.get("command")
        assert command, "Smoke test-kommandot saknas"
        if "-d" in command:
            try:
                payload = command.split("-d", 1)[1].strip().strip("'")
                json.loads(payload)
            except json.JSONDecodeError as exc:
                raise AssertionError(f"Ogiltig JSON i smoke test-kommandot: {exc}") from exc
