#!/usr/bin/env python3
"""
Configuration file management for keyboard RGB control
Supports loading/saving presets from JSON file
"""

import json
import os
from typing import Dict, List, Tuple
from pathlib import Path

DEFAULT_CONFIG_DIR = Path.home() / ".config" / "kbdrgb"
DEFAULT_PRESETS_FILE = DEFAULT_CONFIG_DIR / "presets.json"

class ConfigManager:
    """Manages user configuration and presets"""

    def __init__(self, config_file: Path = None):
        self.config_file = config_file or DEFAULT_PRESETS_FILE
        self.config_file.parent.mkdir(parents=True, exist_ok=True)

    def load_presets(self) -> Dict[str, Tuple[int, int, int, int]]:
        """
        Load user presets from config file
        Returns dict of {name: (r, g, b, i)}
        """
        if not self.config_file.exists():
            return {}

        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)
                presets = {}
                for name, values in data.get("presets", {}).items():
                    if isinstance(values, (list, tuple)) and len(values) == 4:
                        presets[name] = tuple(values)
                return presets
        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load presets from {self.config_file}: {e}")
            return {}

    def save_presets(self, presets: Dict[str, Tuple[int, int, int, int]]) -> bool:
        """
        Save user presets to config file
        Returns True on success, False on failure
        """
        try:
            data = {
                "version": "1.0",
                "presets": {name: list(values) for name, values in presets.items()}
            }
            with open(self.config_file, 'w') as f:
                json.dump(data, f, indent=2)
            return True
        except IOError as e:
            print(f"Error: Failed to save presets to {self.config_file}: {e}")
            return False

    def add_preset(self, name: str, r: int, g: int, b: int, i: int) -> bool:
        """Add or update a preset"""
        presets = self.load_presets()
        presets[name] = (r, g, b, i)
        return self.save_presets(presets)

    def delete_preset(self, name: str) -> bool:
        """Delete a preset by name"""
        presets = self.load_presets()
        if name in presets:
            del presets[name]
            return self.save_presets(presets)
        return False

    def get_preset(self, name: str) -> Tuple[int, int, int, int] | None:
        """Get a specific preset by name"""
        presets = self.load_presets()
        return presets.get(name)
