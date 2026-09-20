"""Find the API key for the commands that call the model.

The key lives in a project-local `.env` that git ignores. It is read into this
process's environment only when a paid command runs, so:

- your shell never holds it, and closing the terminal loses nothing;
- other tools - including future Claude Code sessions, where an
  `ANTHROPIC_API_KEY` in the environment silently switches billing to per-token -
  never see it;
- the key never has to be typed into a command line, where it would land in
  shell history.

Only `ANTHROPIC_API_KEY` is read from the file. Anything else in it is ignored,
so the file cannot quietly reconfigure the SDK - redirect it to another endpoint,
for instance.
"""

from __future__ import annotations

import os
from pathlib import Path

KEY = "ANTHROPIC_API_KEY"
ENV_FILE = ".env"
DEFAULT_API_HOST = "api.anthropic.com"

#: Repository root. The package is used as an editable install, so this is the
#: checkout the .env sits in.
REPO_ROOT = Path(__file__).resolve().parents[2]


def candidate_files() -> list[Path]:
    """The working directory first, then the repository root."""
    paths = [Path.cwd() / ENV_FILE, REPO_ROOT / ENV_FILE]
    unique: list[Path] = []
    for path in paths:
        if path.resolve() not in {existing.resolve() for existing in unique}:
            unique.append(path)
    return unique


def load_api_key(files: list[Path] | None = None) -> str | None:
    """Make the key available to the SDK and say where it came from.

    An `ANTHROPIC_API_KEY` already set in the environment wins over the file, so
    an explicit choice is never overridden by one made earlier and forgotten.
    Returns a description of the source - never the key itself - or None when no
    key was found, in which case the SDK falls back to its other credential
    sources, such as an `ant auth login` profile.
    """
    if os.environ.get(KEY, "").strip():
        return "the environment"

    from dotenv import dotenv_values

    for path in files if files is not None else candidate_files():
        if not path.is_file():
            continue
        value = (dotenv_values(path).get(KEY) or "").strip()
        if value:
            os.environ[KEY] = value
            return _display(path)
    return None


def _display(path: Path) -> str:
    """The path relative to where the command was run, when it is below it.

    The notice is printed on every paid run, and an absolute path puts the
    user's directory layout into every screenshot and log of one.
    """
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(Path.cwd().resolve()))
    except ValueError:
        pass
    try:
        return f"{resolved.relative_to(REPO_ROOT.resolve())} in the project folder"
    except ValueError:
        return str(path)


def unusual_api_endpoint() -> str | None:
    """The API endpoint when something has redirected it away from Anthropic's.

    Tools can set `ANTHROPIC_BASE_URL` for their own use, and a terminal opened
    from one of them may inherit it. Paid commands say so before sending anything,
    because requests carrying your key would otherwise go somewhere you did not
    choose without any visible sign.
    """
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "").strip()
    if base_url and DEFAULT_API_HOST not in base_url:
        return base_url
    return None
