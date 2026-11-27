"""
gui/ui_settings.py

Persistent user UI display preferences (theme, text scale, density, window geometry, column widths).
Stored locally in the user's OS application data directory outside event folders and git repo.
Primary storage is ui.ini under %LOCALAPPDATA%/SessionScribe/ui.ini.
STRICT CONSTRAINT: Never store event paths, event names, or any session data here.
"""

from __future__ import annotations

import configparser
import json
import os
from typing import Any, Dict, Optional


def get_settings_dir() -> str:
    appdata = os.environ.get("LOCALAPPDATA")
    if not appdata:
        appdata = os.path.expanduser("~")
    settings_dir = os.path.join(appdata, "SessionScribe")
    try:
        os.makedirs(settings_dir, exist_ok=True)
    except OSError:
        pass
    return settings_dir


def get_ui_ini_path() -> str:
    """Return the absolute path to ui.ini."""
    return os.path.join(get_settings_dir(), "ui.ini")


def get_ui_settings_path() -> str:
    """Return the absolute path to ui_settings.json for backwards compatibility."""
    return os.path.join(get_settings_dir(), "ui_settings.json")


def default_ui_settings() -> Dict[str, Any]:
    return {
        "theme": "system",
        "text_scale": 1.0,
        "table_density": "comfortable",  # "compact" (24px) or "comfortable" (30px)
        "window_width": 0,
        "window_height": 0,
        "window_x": -1,
        "window_y": -1,
        "column_widths": {},
        "vault_setup_skipped": False,
    }


def load_ui_settings(custom_path: Optional[str] = None) -> Dict[str, Any]:
    """Load UI settings from disk (ui.ini first, then json). Returns defaults on missing or corrupted file."""
    data = default_ui_settings()
    ini_path = custom_path if (custom_path and custom_path.endswith(".ini")) else get_ui_ini_path()
    json_path = custom_path if (custom_path and custom_path.endswith(".json")) else get_ui_settings_path()

    loaded_from_ini = False
    if os.path.isfile(ini_path):
        try:
            cp = configparser.ConfigParser()
            cp.read(ini_path, encoding="utf-8")
            if "Display" in cp:
                sec = cp["Display"]
                if "theme" in sec:
                    data["theme"] = sec.get("theme", "system")
                if "text_scale" in sec:
                    data["text_scale"] = float(sec.get("text_scale", "1.0"))
                if "table_density" in sec:
                    data["table_density"] = sec.get("table_density", "comfortable")
                if "window_width" in sec:
                    data["window_width"] = int(sec.get("window_width", "0"))
                if "window_height" in sec:
                    data["window_height"] = int(sec.get("window_height", "0"))
                if "window_x" in sec:
                    data["window_x"] = int(sec.get("window_x", "-1"))
                if "window_y" in sec:
                    data["window_y"] = int(sec.get("window_y", "-1"))
                if "vault_setup_skipped" in sec:
                    data["vault_setup_skipped"] = sec.getboolean("vault_setup_skipped", fallback=False)
            if "Preferences" in cp:
                psec = cp["Preferences"]
                if "vault_setup_skipped" in psec:
                    data["vault_setup_skipped"] = psec.getboolean("vault_setup_skipped", fallback=False)
            if "ColumnWidths" in cp:
                for k, v in cp["ColumnWidths"].items():
                    try:
                        data["column_widths"][k] = json.loads(v)
                    except Exception:
                        pass
            loaded_from_ini = True
        except Exception:
            pass

    if not loaded_from_ini and os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                j_data = json.load(f)
            for k, v in j_data.items():
                if k in data:
                    data[k] = v
        except Exception:
            pass

    # Ensure bounds
    data["text_scale"] = max(0.85, min(1.50, float(data.get("text_scale", 1.0))))
    if data.get("table_density") not in ("compact", "comfortable"):
        data["table_density"] = "comfortable"
    if data.get("theme") not in ("system", "light", "dark"):
        data["theme"] = "system"
    if not isinstance(data.get("column_widths"), dict):
        data["column_widths"] = {}
    return data


def save_ui_settings(settings: Dict[str, Any], custom_path: Optional[str] = None) -> None:
    """Persist UI settings atomically to ui.ini and ui_settings.json. Strips any disallowed keys."""
    clean_data = {
        "theme": settings.get("theme", "system") if settings.get("theme") in ("system", "light", "dark") else "system",
        "text_scale": max(0.85, min(1.50, float(settings.get("text_scale", 1.0)))),
        "table_density": "compact" if settings.get("table_density") == "compact" else "comfortable",
        "window_width": int(settings.get("window_width", 0)),
        "window_height": int(settings.get("window_height", 0)),
        "window_x": int(settings.get("window_x", -1)),
        "window_y": int(settings.get("window_y", -1)),
        "column_widths": {str(k): v for k, v in settings.get("column_widths", {}).items() if isinstance(v, dict)},
        "vault_setup_skipped": bool(settings.get("vault_setup_skipped", False)),
    }

    ini_path = custom_path if (custom_path and custom_path.endswith(".ini")) else get_ui_ini_path()
    json_path = custom_path if (custom_path and custom_path.endswith(".json")) else get_ui_settings_path()

    # Save to ui.ini
    try:
        os.makedirs(os.path.dirname(ini_path), exist_ok=True)
        cp = configparser.ConfigParser()
        cp["Display"] = {
            "theme": clean_data["theme"],
            "text_scale": str(clean_data["text_scale"]),
            "table_density": clean_data["table_density"],
            "window_width": str(clean_data["window_width"]),
            "window_height": str(clean_data["window_height"]),
            "window_x": str(clean_data["window_x"]),
            "window_y": str(clean_data["window_y"]),
        }
        cp["Preferences"] = {
            "vault_setup_skipped": str(clean_data["vault_setup_skipped"]).lower(),
        }
        if clean_data["column_widths"]:
            cp["ColumnWidths"] = {k: json.dumps(v) for k, v in clean_data["column_widths"].items()}

        tmp_ini = ini_path + ".tmp"
        with open(tmp_ini, "w", encoding="utf-8") as f:
            cp.write(f)
        os.replace(tmp_ini, ini_path)
    except OSError:
        pass

    # Save to json for backwards compatibility
    try:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        tmp_json = json_path + ".tmp"
        with open(tmp_json, "w", encoding="utf-8") as f:
            json.dump(clean_data, f, indent=2)
        os.replace(tmp_json, json_path)
    except OSError:
        pass


def is_vault_setup_skipped(custom_path: Optional[str] = None) -> bool:
    """True if user chose 'Skip for now' on the vault setup prompt."""
    data = load_ui_settings(custom_path=custom_path)
    return bool(data.get("vault_setup_skipped", False))


def set_vault_setup_skipped(skipped: bool, custom_path: Optional[str] = None) -> None:
    """Remember whether user skipped vault setup."""
    data = load_ui_settings(custom_path=custom_path)
    data["vault_setup_skipped"] = bool(skipped)
    save_ui_settings(data, custom_path=custom_path)

