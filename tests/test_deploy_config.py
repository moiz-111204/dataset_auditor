"""The Render Blueprint matches the app and never carries a secret value."""

import re
from pathlib import Path

from backend.app import app

ROOT = Path(__file__).resolve().parent.parent
BLUEPRINT = (ROOT / "render.yaml").read_text(encoding="utf-8")


def field(name: str) -> str:
    match = re.search(rf"^\s+{name}:\s*(.+?)\s*$", BLUEPRINT, re.MULTILINE)
    assert match, f"{name} missing from render.yaml"
    return match.group(1).strip('"')


def env_entry(key: str) -> str:
    """The lines of one envVars entry, from its `- key:` line up to the next entry."""
    match = re.search(rf"^\s+- key: {key}\n((?:\s+(?!- key:)[^\n]*\n)*)", BLUEPRINT, re.MULTILINE)
    assert match, f"{key} missing from render.yaml envVars"
    return match.group(1)


def test_blueprint_runs_the_app_on_the_free_plan():
    assert field("runtime") == "python"
    assert field("plan") == "free"
    assert field("buildCommand") == "pip install -r requirements.txt"
    assert field("startCommand") == "python backend/app.py"


def test_health_check_path_is_a_real_route():
    paths = {route.path for route in app.routes}
    assert field("healthCheckPath") in paths


def test_python_version_is_pinned_to_312():
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.12"


def test_spacy_model_is_installed_by_the_build():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert re.search(r"^en_core_web_sm @ https://github\.com/explosion/spacy-models/", requirements, re.MULTILINE)


def test_proxy_headers_are_trusted():
    entry = env_entry("FORWARDED_ALLOW_IPS")
    assert re.search(r'value: "\*"', entry)


def test_session_cap_is_lowered_for_the_free_instance():
    assert re.search(r'value: "10"', env_entry("MAX_SESSIONS"))


def test_secrets_are_prompted_in_the_dashboard_not_stored():
    for key in ("ANTHROPIC_API_KEY", "LLM_MODEL"):
        entry = env_entry(key)
        assert "sync: false" in entry, key
        assert "value:" not in entry, key
    assert "sk-ant-" not in BLUEPRINT
