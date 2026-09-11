"""Server configuration: env vars first, then ~/.config/paperqueue/config.toml."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ServerConfig:
    url: str
    token: str


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "paperqueue" / "config.toml"


def ui_path() -> Path:
    """Where the chosen theme is remembered, beside the server config."""
    return config_path().parent / "ui.toml"


def load_theme(default: str = "destijl") -> str:
    path = ui_path()
    if not path.exists():
        return default
    try:
        with path.open("rb") as f:
            return str(tomllib.load(f).get("theme") or default)
    except (OSError, tomllib.TOMLDecodeError):
        return default


def save_theme(name: str) -> None:
    """Best effort: a theme that cannot be saved is not worth an error."""
    path = ui_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f'theme = "{name}"\n', encoding="utf-8")
    except OSError:
        pass


def load_server_config(path: Path | None = None) -> ServerConfig | None:
    """Return the server settings, or None when the TUI should run against the local file."""
    url = os.environ.get("PAPERQUEUE_URL", "").strip()
    token = os.environ.get("PAPERQUEUE_TOKEN", "").strip()
    if not url:
        path = path or config_path()
        if not path.exists():
            return None
        with path.open("rb") as f:
            data = tomllib.load(f)
        url = str(data.get("url", "")).strip()
        token = token or str(data.get("token", "")).strip()
    if not url:
        return None
    if not token:
        raise ValueError("server url is set but no token (PAPERQUEUE_TOKEN or `token` in config.toml)")
    return ServerConfig(url=url.rstrip("/"), token=token)
