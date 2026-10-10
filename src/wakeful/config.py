"""Tiny JSON configuration.

    {"keep_awake": {"enabled": false, "mode": "native", "interval_seconds": 60}}

``mode`` is ``native`` (OS power API only) or ``mouse`` (also simulate
activity every ``interval_seconds``). Stored in the per-user config directory.
Loading is tolerant: a missing or corrupt file yields defaults and a log
message, never an exception.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

APP_DIR_NAME = "wakeful"
CONFIG_FILE_NAME = "config.json"
DEFAULT_INTERVAL_SECONDS = 60
MODES = ("native", "mouse")


@dataclass(slots=True)
class KeepAwakeConfig:
    enabled: bool = False
    mode: str = "native"
    interval_seconds: float = DEFAULT_INTERVAL_SECONDS


def default_config_path() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / APP_DIR_NAME / CONFIG_FILE_NAME


def load_config(path: Path) -> KeepAwakeConfig:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.debug("no config at %s; using defaults", path)
        return KeepAwakeConfig()
    except (OSError, ValueError) as exc:
        log.warning("ignoring unreadable config %s: %s", path, exc)
        return KeepAwakeConfig()
    section = raw.get("keep_awake", {}) if isinstance(raw, dict) else {}
    if not isinstance(section, dict):
        section = {}
    cfg = KeepAwakeConfig()
    cfg.enabled = bool(section.get("enabled", cfg.enabled))
    mode = section.get("mode", cfg.mode)
    if mode in MODES:
        cfg.mode = mode
    else:
        log.warning("ignoring unknown keep_awake.mode %r in %s", mode, path)
    interval = section.get("interval_seconds", cfg.interval_seconds)
    if isinstance(interval, (int, float)) and not isinstance(interval, bool) and interval >= 1:
        cfg.interval_seconds = float(interval)
    else:
        log.warning("ignoring invalid keep_awake.interval_seconds %r in %s", interval, path)
    return cfg


def save_config(path: Path, cfg: KeepAwakeConfig) -> None:
    """Write atomically. Raises OSError; callers decide whether that matters."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = asdict(cfg)
    if float(data["interval_seconds"]).is_integer():
        data["interval_seconds"] = int(data["interval_seconds"])
    tmp.write_text(json.dumps({"keep_awake": data}, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    log.debug("config saved to %s", path)
