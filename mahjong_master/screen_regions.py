from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


CONFIG_VERSION = 1
CAPTURE_WIDTH = 1592
CAPTURE_HEIGHT = 933

OBSOLETE_REGION_KEYS = {
    "button_chii",
    "button_pon",
    "button_kan",
    "button_riichi",
    "button_win",
    "call_arrow_left",
    "call_arrow_top",
    "call_arrow_right",
    "call_arrow_bottom",
    "wind_letter_player",
    "wind_letter_left",
    "wind_letter_top",
    "wind_letter_right",
}


DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "show_regions_overlay": True,
    "predict_fps": 30.0,
    "predict_conf": 0.50,
    "debug_enabled": False,
    "capture_mode_enabled": False,
    "auto_mouse_delay_min": 0.4,
    "auto_mouse_delay_max": 1.2,
    "auto_click_delay_min": 3.0,
    "auto_click_delay_max": 5.0,
    "auto_call_settle_seconds": 1.6,
    "pixel_probes": {
        "turn_left": {"label": "Vez esquerda", "x": 494, "y": 395, "color": "#FBBF24", "tolerance": 45, "enabled": True},
        "turn_top": {"label": "Vez frente", "x": 796, "y": 270, "color": "#FBBF24", "tolerance": 45, "enabled": True},
        "turn_right": {"label": "Vez direita", "x": 1016, "y": 395, "color": "#FBBF24", "tolerance": 45, "enabled": True},
        "turn_player": {"label": "Vez jogador", "x": 796, "y": 532, "color": "#FBBF24", "tolerance": 45, "enabled": True},
        "east_left": {"label": "Leste esquerda", "x": 520, "y": 360, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "east_top": {"label": "Leste frente", "x": 796, "y": 255, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "east_right": {"label": "Leste direita", "x": 1035, "y": 360, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "east_player": {"label": "Leste jogador", "x": 796, "y": 485, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "button_chii_1": {"label": "Chii 1", "x": 610, "y": 700, "color": "#22C55E", "tolerance": 45, "enabled": True},
        "button_chii_2": {"label": "Chii 2", "x": 770, "y": 700, "color": "#22C55E", "tolerance": 45, "enabled": True},
        "button_pon_1": {"label": "Pon 1", "x": 610, "y": 700, "color": "#06B6D4", "tolerance": 45, "enabled": True},
        "button_pon_2": {"label": "Pon 2", "x": 770, "y": 700, "color": "#06B6D4", "tolerance": 45, "enabled": True},
        "button_kan_1": {"label": "Kan 1", "x": 610, "y": 700, "color": "#F97316", "tolerance": 45, "enabled": True},
        "button_kan_2": {"label": "Kan 2", "x": 770, "y": 700, "color": "#F97316", "tolerance": 45, "enabled": True},
        "button_riichi_1": {"label": "Riichi 1", "x": 610, "y": 700, "color": "#F97316", "tolerance": 45, "enabled": True},
        "button_riichi_2": {"label": "Riichi 2", "x": 770, "y": 700, "color": "#F97316", "tolerance": 45, "enabled": True},
        "button_ron_1": {"label": "Ron 1", "x": 610, "y": 700, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "button_ron_2": {"label": "Ron 2", "x": 770, "y": 700, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "button_tsumo_1": {"label": "Tsumo 1", "x": 610, "y": 700, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "button_tsumo_2": {"label": "Tsumo 2", "x": 770, "y": 700, "color": "#E11D48", "tolerance": 45, "enabled": True},
        "button_skip_1": {"label": "Skip 1", "x": 990, "y": 700, "color": "#94A3B8", "tolerance": 45, "enabled": False},
        "button_skip_2": {"label": "Skip 2", "x": 1120, "y": 700, "color": "#94A3B8", "tolerance": 45, "enabled": False},
        "chii_choose_header": {"label": "Header escolher Chii", "x": 795, "y": 523, "color": "#7F2430", "tolerance": 55, "enabled": True},
    },
    "regions": {
        "player_hand": {
            "label": "Mao jogador",
            "x": 70,
            "y": 675,
            "w": 1245,
            "h": 230,
            "color": "#22C55E",
            "enabled": True,
        },
        "player_closed_line": {
            "label": "Linha mao fechada",
            "x": 70,
            "y": 785,
            "w": 1245,
            "h": 8,
            "color": "#FBBF24",
            "enabled": True,
        },
        "left_opponent_tiles": {
            "label": "Mao/chamadas esquerda",
            "x": 0,
            "y": 70,
            "w": 300,
            "h": 620,
            "color": "#F59E0B",
            "enabled": True,
        },
        "top_opponent_tiles": {
            "label": "Mao/chamadas frente",
            "x": 360,
            "y": 0,
            "w": 860,
            "h": 150,
            "color": "#A855F7",
            "enabled": True,
        },
        "right_opponent_tiles": {
            "label": "Mao/chamadas direita",
            "x": 1285,
            "y": 70,
            "w": 307,
            "h": 620,
            "color": "#38BDF8",
            "enabled": True,
        },
        "dora_indicators": {
            "label": "Doras",
            "x": 0,
            "y": 0,
            "w": 260,
            "h": 150,
            "color": "#F43F5E",
            "enabled": True,
        },
        "chii_options": {
            "label": "Opcoes Chii",
            "x": 505,
            "y": 535,
            "w": 680,
            "h": 180,
            "color": "#F97316",
            "enabled": True,
        },
        "discard_player": {
            "label": "Descartes jogador",
            "x": 535,
            "y": 455,
            "w": 520,
            "h": 185,
            "color": "#EF4444",
            "enabled": True,
        },
        "discard_left": {
            "label": "Descartes esquerda",
            "x": 350,
            "y": 250,
            "w": 300,
            "h": 330,
            "color": "#F59E0B",
            "enabled": True,
        },
        "discard_top": {
            "label": "Descartes frente",
            "x": 520,
            "y": 155,
            "w": 550,
            "h": 210,
            "color": "#A855F7",
            "enabled": True,
        },
        "discard_right": {
            "label": "Descartes direita",
            "x": 940,
            "y": 250,
            "w": 300,
            "h": 330,
            "color": "#38BDF8",
            "enabled": True,
        },
    },
}


def load_config(path: Path) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                deep_update(config, loaded)
        except (OSError, json.JSONDecodeError):
            pass
    normalize_config(config)
    return config


def save_config(path: Path, config: dict[str, Any]) -> None:
    normalize_config(config)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")


def reset_config(path: Path) -> dict[str, Any]:
    config = deepcopy(DEFAULT_CONFIG)
    save_config(path, config)
    return config


def deep_update(target: dict[str, Any], source: dict[str, Any]) -> None:
    for key, value in source.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            deep_update(target[key], value)
        else:
            target[key] = value


def normalize_config(config: dict[str, Any]) -> None:
    config["version"] = CONFIG_VERSION
    config.setdefault("show_regions_overlay", True)
    config["predict_fps"] = clamp_float(config.get("predict_fps", 30.0), 0.2, 60.0)
    config["predict_conf"] = clamp_float(config.get("predict_conf", 0.50), 0.01, 0.99)
    config["debug_enabled"] = bool(config.get("debug_enabled", False))
    config["capture_mode_enabled"] = bool(config.get("capture_mode_enabled", False))
    mouse_min = clamp_float(config.get("auto_mouse_delay_min", 0.4), 0.0, 30.0)
    mouse_max = clamp_float(config.get("auto_mouse_delay_max", 1.2), 0.0, 30.0)
    if mouse_min > mouse_max:
        mouse_min, mouse_max = mouse_max, mouse_min
    click_min = clamp_float(config.get("auto_click_delay_min", 3.0), 0.1, 30.0)
    click_max = clamp_float(config.get("auto_click_delay_max", 5.0), 0.1, 30.0)
    if click_min > click_max:
        click_min, click_max = click_max, click_min
    config["auto_mouse_delay_min"] = mouse_min
    config["auto_mouse_delay_max"] = mouse_max
    config["auto_click_delay_min"] = click_min
    config["auto_click_delay_max"] = click_max
    config["auto_call_settle_seconds"] = clamp_float(config.get("auto_call_settle_seconds", 1.6), 0.0, 10.0)
    regions = config.setdefault("regions", {})
    for key in OBSOLETE_REGION_KEYS:
        regions.pop(key, None)
    for key, default_region in DEFAULT_CONFIG["regions"].items():
        region = regions.setdefault(key, deepcopy(default_region))
        for field, value in default_region.items():
            region.setdefault(field, value)
        region["x"] = clamp_int(region["x"], 0, CAPTURE_WIDTH)
        region["y"] = clamp_int(region["y"], 0, CAPTURE_HEIGHT)
        region["w"] = clamp_int(region["w"], 1, CAPTURE_WIDTH - region["x"])
        region["h"] = clamp_int(region["h"], 1, CAPTURE_HEIGHT - region["y"])
    probes = config.setdefault("pixel_probes", {})
    for key, default_probe in DEFAULT_CONFIG["pixel_probes"].items():
        probe = probes.setdefault(key, deepcopy(default_probe))
        for field, value in default_probe.items():
            probe.setdefault(field, value)
        probe["x"] = clamp_int(probe["x"], 0, CAPTURE_WIDTH - 1)
        probe["y"] = clamp_int(probe["y"], 0, CAPTURE_HEIGHT - 1)
        probe["tolerance"] = clamp_int(probe["tolerance"], 0, 255)
        probe["enabled"] = bool(probe.get("enabled", True))


def clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))


def clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = minimum
    return max(minimum, min(maximum, number))
