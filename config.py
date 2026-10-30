"""
Configuration loader for BL2 Save Editor.
Reads paths from config.json, falling back to auto-detection.
"""
from __future__ import annotations

import os
import json
import platform
import re
from typing import Any

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Marker file shipped by https://github.com/gibbed/Borderlands2Dumps
GIBBED_MARKER = "Asset Library Manager.json"

_VDF_PATH_RE = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"')


def _steam_library_roots() -> list[str]:
    """Steam library folders from libraryfolders.vdf, plus the default install."""
    home = os.path.expanduser("~")
    vdf_candidates: list[str] = []
    system = platform.system()
    if system == "Windows":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
            base = os.environ.get(env, "")
            if base:
                steam = os.path.join(base, "Steam")
                vdf_candidates.append(os.path.join(steam, "steamapps", "libraryfolders.vdf"))
                vdf_candidates.append(os.path.join(steam, "config", "libraryfolders.vdf"))
    elif system == "Linux":
        for steam in (
            os.path.join(home, ".steam", "steam"),
            os.path.join(home, ".local", "share", "Steam"),
        ):
            vdf_candidates.append(os.path.join(steam, "steamapps", "libraryfolders.vdf"))
            vdf_candidates.append(os.path.join(steam, "config", "libraryfolders.vdf"))
    elif system == "Darwin":
        steam = os.path.join(home, "Library", "Application Support", "Steam")
        vdf_candidates.append(os.path.join(steam, "config", "libraryfolders.vdf"))
        vdf_candidates.append(os.path.join(steam, "steamapps", "libraryfolders.vdf"))

    roots: list[str] = []
    for vdf in vdf_candidates:
        if not os.path.isfile(vdf):
            continue
        try:
            text = open(vdf, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        for match in _VDF_PATH_RE.finditer(text):
            path = match.group(1).replace("\\\\", "\\")
            if path and path not in roots and os.path.isdir(path):
                roots.append(path)
    return roots


def _accept_game_dir(path: str) -> str:
    """Return the install root that contains WillowGame, or ''."""
    if not path:
        return ""
    if os.path.isdir(os.path.join(path, "WillowGame")):
        return path
    # Aspyr Mac/Linux ports keep the Windows tree inside the app bundle.
    for nested in (
        os.path.join(path, "Borderlands2.app", "Contents", "GameData"),
        os.path.join(path, "Contents", "GameData"),
    ):
        if os.path.isdir(os.path.join(nested, "WillowGame")):
            return nested
    return ""


def _detect_steam_dir() -> str:
    """Try to find the Steam BL2 installation directory."""
    candidates: list[str] = []
    for root in _steam_library_roots():
        candidates.append(os.path.join(root, "steamapps", "common", "Borderlands 2"))

    if platform.system() == "Windows":
        candidates.extend([
            os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Steam", "steamapps", "common", "Borderlands 2"),
            os.path.join(os.environ.get("PROGRAMFILES", ""), "Steam", "steamapps", "common", "Borderlands 2"),
        ])
    elif platform.system() == "Linux":
        home = os.path.expanduser("~")
        candidates.extend([
            os.path.join(home, ".steam", "steam", "steamapps", "common", "Borderlands 2"),
            os.path.join(home, ".local", "share", "Steam", "steamapps", "common", "Borderlands 2"),
        ])
    elif platform.system() == "Darwin":
        home = os.path.expanduser("~")
        candidates.append(
            os.path.join(home, "Library", "Application Support", "Steam", "steamapps", "common", "Borderlands 2")
        )

    seen: set[str] = set()
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        accepted = _accept_game_dir(candidate)
        if accepted:
            return accepted
    return ""


def _save_subdir_with_saves(docs: str) -> str:
    """Pick the Steam-ID folder under SaveData that actually holds .sav files."""
    if not os.path.isdir(docs):
        return ""
    best = ""
    best_count = 0
    fallback = ""
    try:
        entries = sorted(os.listdir(docs))
    except OSError:
        return ""
    for entry in entries:
        subdir = os.path.join(docs, entry)
        if not os.path.isdir(subdir):
            continue
        if not fallback:
            fallback = subdir
        try:
            count = sum(1 for name in os.listdir(subdir) if name.lower().endswith(".sav"))
        except OSError:
            continue
        if count > best_count:
            best = subdir
            best_count = count
    return best if best_count > 0 else fallback


def _detect_save_dir() -> str:
    """Try to find the BL2 save directory."""
    home = os.path.expanduser("~")
    candidates: list[str] = []
    if platform.system() == "Windows":
        candidates.append(os.path.join(
            os.environ.get("USERPROFILE", ""), "Documents", "My Games",
            "Borderlands 2", "WillowGame", "SaveData",
        ))
    elif platform.system() == "Linux":
        candidates.append(os.path.join(
            home, ".local", "share", "aspyr-media", "borderlands 2", "willowgame", "savedata",
        ))
    elif platform.system() == "Darwin":
        candidates.append(os.path.join(
            home, "Library", "Application Support", "Borderlands 2", "WillowGame", "SaveData",
        ))
        # Mac App Store build keeps saves inside the app container.
        candidates.append(os.path.join(
            home, "Library", "Containers", "com.aspyr.borderlands2.appstore",
            "Data", "Library", "Application Support", "Borderlands 2", "WillowGame", "SaveData",
        ))
    for docs in candidates:
        found = _save_subdir_with_saves(docs)
        if found:
            return found
    return ""


def _is_gibbed_dir(path: str) -> bool:
    """True when path holds the Gibbed JSON dump the asset database loads."""
    return bool(path) and os.path.isfile(os.path.join(path, GIBBED_MARKER))


def _detect_gibbed_dir() -> str:
    """Try to find the Gibbed data directory (cloned by the wizard, or nearby)."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(project_root, "gibbed_data"),
        os.path.join(project_root, "..", "gibbed_data"),
    ]
    if platform.system() == "Windows":
        candidates.append(os.path.join(os.environ.get("USERPROFILE", ""), "gibbed_data"))
    elif platform.system() == "Darwin":
        candidates.append(os.path.join(os.path.expanduser("~"), "gibbed_data"))
    for c in candidates:
        if _is_gibbed_dir(c):
            return c
    return ""


def _detect_borderlands2_tool_dir() -> str:
    """Try to find the apocalyptech borderlands2-tool library."""
    project_root = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(project_root, "borderlands2-tool"),
        os.path.join(project_root, "..", "borderlands2-tool"),
    ]
    for c in candidates:
        if os.path.isdir(c) and os.path.isfile(os.path.join(c, "borderlands", "savefile.py")):
            return c
    return ""


def load_config() -> dict[str, Any]:
    """Load config from config.json, auto-detecting missing values."""
    config = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            config = json.load(f)

    defaults = {
        "save_dir": _detect_save_dir(),
        "gibbed_dir": _detect_gibbed_dir(),
        "borderlands2_tool_dir": _detect_borderlands2_tool_dir(),
        "game_dir": _detect_steam_dir(),
        "backup_generations": 5,
    }

    for key, default in defaults.items():
        if key not in config or not config[key]:
            config[key] = default

    # Derive exe path from game_dir when that binary is actually present.
    # The grade-cap patcher targets the Windows exe; the Mac port's Mach-O
    # lives elsewhere and is not a valid target.
    if "exe_path" not in config and config.get("game_dir"):
        if platform.system() == "Windows":
            exe = os.path.join(config["game_dir"], "Binaries", "Win32", "Borderlands2.exe")
        elif platform.system() == "Linux":
            exe = os.path.join(config["game_dir"], "Binaries", "Linux", "Borderlands2")
        else:
            exe = ""
        if exe and os.path.isfile(exe):
            config["exe_path"] = exe

    return config



# Singleton
_config = None

def get_config() -> dict[str, Any]:
    global _config
    if _config is None:
        _config = load_config()
    return _config
