"""Where the API key comes from, and the guarantee that it never gets committed."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from peppol_e_invoice_intake.credentials import (
    KEY,
    REPO_ROOT,
    load_api_key,
    unusual_api_endpoint,
)

FAKE_KEY = "sk-ant-test-0000000000"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Every test starts with no key and no redirected endpoint in the environment."""
    monkeypatch.delenv(KEY, raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


def write_env(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_the_key_is_read_from_a_dotenv_file(tmp_path: Path, monkeypatch):
    env = write_env(tmp_path / ".env", f"{KEY}={FAKE_KEY}\n")
    source = load_api_key([env])

    assert source == str(env)
    import os

    assert os.environ[KEY] == FAKE_KEY


def test_a_key_already_in_the_environment_wins(tmp_path: Path, monkeypatch):
    """An explicit choice must never be overridden by one made earlier and forgotten."""
    monkeypatch.setenv(KEY, "sk-ant-from-the-shell")
    env = write_env(tmp_path / ".env", f"{KEY}={FAKE_KEY}\n")

    assert load_api_key([env]) == "the environment"
    import os

    assert os.environ[KEY] == "sk-ant-from-the-shell"


def test_only_the_api_key_is_taken_from_the_file(tmp_path: Path):
    """The file must not be able to quietly reconfigure the SDK - redirecting it to
    another endpoint, for instance."""
    import os

    env = write_env(
        tmp_path / ".env",
        f"{KEY}={FAKE_KEY}\nANTHROPIC_BASE_URL=https://example.invalid\nOTHER=1\n",
    )
    load_api_key([env])

    assert "ANTHROPIC_BASE_URL" not in os.environ
    assert "OTHER" not in os.environ


def test_an_empty_key_in_the_template_counts_as_no_key(tmp_path: Path):
    env = write_env(tmp_path / ".env", f"{KEY}=\n")
    assert load_api_key([env]) is None


def test_no_file_means_no_key_and_no_error(tmp_path: Path):
    assert load_api_key([tmp_path / ".env"]) is None


def test_the_source_description_never_contains_the_key(tmp_path: Path):
    env = write_env(tmp_path / ".env", f"{KEY}={FAKE_KEY}\n")
    assert FAKE_KEY not in load_api_key([env])


def test_a_redirected_endpoint_is_flagged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:4000/proxy")
    assert unusual_api_endpoint() == "http://localhost:4000/proxy"


def test_the_real_endpoint_is_not_flagged(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    assert unusual_api_endpoint() is None
    monkeypatch.delenv("ANTHROPIC_BASE_URL")
    assert unusual_api_endpoint() is None


# --- the guarantee that matters most -------------------------------------------


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, check=False
    )


requires_git_checkout = pytest.mark.skipif(
    shutil.which("git") is None or not (REPO_ROOT / ".git").exists(),
    reason="needs git and a checkout",
)


@requires_git_checkout
@pytest.mark.parametrize("name", [".env", ".env.local", ".env.production"])
def test_git_ignores_every_dotenv_file(name: str):
    """Asked of git itself rather than of the .gitignore text, so a rule that
    looks right but does not match cannot slip through."""
    assert _git("check-ignore", "--quiet", name).returncode == 0, f"{name} is not ignored"


@requires_git_checkout
def test_the_template_is_not_ignored():
    assert _git("check-ignore", "--quiet", ".env.example").returncode == 1


@requires_git_checkout
def test_no_dotenv_file_is_tracked():
    tracked = _git("ls-files").stdout.splitlines()
    leaked = [path for path in tracked if Path(path).name.startswith(".env")
              and path != ".env.example"]
    assert leaked == []


def test_the_committed_template_holds_no_value():
    template = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assigned = [
        line for line in template.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert assigned == [f"{KEY}="]
