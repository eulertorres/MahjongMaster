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

OBSOLETE_PIXEL_PROBE_KEYS: set[str] = {
    "routine_match_end_yellow",
    "routine_one_more_match",
    "routine_confirm",
}


DEFAULT_CONFIG: dict[str, Any] = {
    "version": CONFIG_VERSION,
    "show_regions_overlay": True,
    "predict_fps": 30.0,
    "predict_conf": 0.50,
    "debug_enabled": False,
    "capture_mode_enabled": False,
    "capture_first_detection_threshold": 35,
    "capture_second_detection_threshold": 75,
    "routines_enabled": False,
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
        "button_ron_1": {"label": "Ron 1", "x": 610, "y": 700, "color": "#EF4444", "tolerance": 45, "enabled": False},
        "button_ron_2": {"label": "Ron 2", "x": 770, "y": 700, "color": "#EF4444", "tolerance": 45, "enabled": False},
        "button_tsumo_1": {"label": "Tsumo 1", "x": 610, "y": 700, "color": "#EF4444", "tolerance": 45, "enabled": False},
        "button_tsumo_2": {"label": "Tsumo 2", "x": 770, "y": 700, "color": "#EF4444", "tolerance": 45, "enabled": False},
        "opponent_ron_left": {"label": "RON adversario esq", "x": 350, "y": 360, "color": "#E11D48", "tolerance": 45, "enabled": False},
        "opponent_ron_top": {"label": "RON adversario frente", "x": 796, "y": 190, "color": "#E11D48", "tolerance": 45, "enabled": False},
        "opponent_ron_right": {"label": "RON adversario dir", "x": 1240, "y": 360, "color": "#E11D48", "tolerance": 45, "enabled": False},
        "opponent_tsumo_left": {"label": "TSUMO adversario esq", "x": 350, "y": 360, "color": "#E11D48", "tolerance": 45, "enabled": False},
        "opponent_tsumo_top": {"label": "TSUMO adversario frente", "x": 796, "y": 190, "color": "#E11D48", "tolerance": 45, "enabled": False},
        "opponent_tsumo_right": {"label": "TSUMO adversario dir", "x": 1240, "y": 360, "color": "#E11D48", "tolerance": 45, "enabled": False},
        "button_skip_1": {"label": "Skip 1", "x": 990, "y": 700, "color": "#94A3B8", "tolerance": 45, "enabled": False},
        "button_skip_2": {"label": "Skip 2", "x": 1120, "y": 700, "color": "#94A3B8", "tolerance": 45, "enabled": False},
        "chii_choose_header": {"label": "Header escolher Chii", "x": 795, "y": 523, "color": "#7F2430", "tolerance": 55, "enabled": True},
        "riichi_stick_changed": {
            "label": "Riichi cigarro mudou",
            "x": 60,
            "y": 126,
            "color": "#1B2536",
            "tolerance": 35,
            "enabled": False,
            "mode": "not_match",
        },
        "riichi_stick_player": {
            "label": "Riichi jogador mudou",
            "x": 68,
            "y": 126,
            "color": "#1B2536",
            "tolerance": 35,
            "enabled": False,
            "mode": "not_match",
        },
        "riichi_stick_left": {
            "label": "Riichi esquerda mudou",
            "x": 62,
            "y": 126,
            "color": "#1B2536",
            "tolerance": 35,
            "enabled": False,
            "mode": "not_match",
        },
        "riichi_stick_top": {
            "label": "Riichi frente mudou",
            "x": 756,
            "y": 44,
            "color": "#1B2536",
            "tolerance": 35,
            "enabled": False,
            "mode": "not_match",
        },
        "riichi_stick_right": {
            "label": "Riichi direita mudou",
            "x": 1530,
            "y": 126,
            "color": "#1B2536",
            "tolerance": 35,
            "enabled": False,
            "mode": "not_match",
        },
    },
    "routine_probes": {
        "end_confirm_yellow": {
            "label": "Confirm amarelo",
            "x": 1312,
            "y": 746,
            "color": "#F8C35A",
            "tolerance": 70,
            "enabled": True,
        },
        "one_more_match": {
            "label": "One More Match",
            "x": 1100,
            "y": 746,
            "color": "#2F61B4",
            "tolerance": 70,
            "enabled": True,
        },
        "center_confirm": {
            "label": "Confirm centro",
            "x": 586,
            "y": 596,
            "color": "#F8C35A",
            "tolerance": 70,
            "enabled": True,
        },
    },
    "routine_rules": {
        "end_confirm_yellow": {
            "label": "Confirm amarelo",
            "x": 1312,
            "y": 746,
            "color": "#F8C35A",
            "tolerance": 70,
            "delay_seconds": 2.0,
            "enabled": True,
        },
        "one_more_match": {
            "label": "One More Match",
            "x": 1100,
            "y": 746,
            "color": "#2F61B4",
            "tolerance": 70,
            "delay_seconds": 1.0,
            "enabled": True,
        },
        "center_confirm": {
            "label": "Confirm centro",
            "x": 586,
            "y": 596,
            "color": "#F8C35A",
            "tolerance": 70,
            "delay_seconds": 2.0,
            "enabled": True,
        },
    },
    "routine_sequences": {
        "match_end_restart": {
            "label": "Fim de partida: jogar de novo",
            "enabled": True,
            "cooldown_seconds": 0.8,
            "steps": [
                {
                    "enabled": True,
                    "label": "Confirm amarelo 3x",
                    "detect": "end_confirm_yellow",
                    "condition": "match",
                    "click_mode": "detect",
                    "delay_seconds": 0.4,
                    "repeat": 3,
                    "next": "advance",
                },
                {
                    "enabled": True,
                    "label": "One More Match",
                    "detect": "one_more_match",
                    "condition": "match",
                    "click_mode": "detect",
                    "delay_seconds": 0.8,
                    "repeat": 1,
                    "next": "advance",
                },
                {
                    "enabled": True,
                    "label": "Confirm centro",
                    "detect": "center_confirm",
                    "condition": "match",
                    "click_mode": "detect",
                    "delay_seconds": 5.0,
                    "repeat": 1,
                    "next": "reset",
                },
            ],
        },
    },
    "routines": {
        "match_end_restart": {
            "label": "Fim de partida: jogar de novo",
            "enabled": True,
            "cooldown_seconds": 0.8,
            "steps": [
                {
                    "trigger": "routine_match_end_yellow",
                    "click": "routine_match_end_yellow",
                    "until": "routine_one_more_match",
                    "label": "fechar tela amarela ate aparecer One More Match",
                },
                {
                    "trigger": "routine_one_more_match",
                    "click": "routine_one_more_match",
                    "until": "routine_confirm",
                    "label": "clicar One More Match",
                },
                {
                    "trigger": "routine_confirm",
                    "click": "routine_confirm",
                    "delay_seconds": 5.0,
                    "label": "confirmar nova partida",
                },
            ],
        },
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
        "score_player": {
            "label": "Pontuacao jogador",
            "x": 645,
            "y": 500,
            "w": 300,
            "h": 65,
            "color": "#22C55E",
            "enabled": True,
        },
        "score_left": {
            "label": "Pontuacao esquerda",
            "x": 555,
            "y": 300,
            "w": 95,
            "h": 210,
            "color": "#F59E0B",
            "enabled": True,
        },
        "score_top": {
            "label": "Pontuacao frente",
            "x": 645,
            "y": 255,
            "w": 300,
            "h": 65,
            "color": "#A855F7",
            "enabled": True,
        },
        "score_right": {
            "label": "Pontuacao direita",
            "x": 945,
            "y": 300,
            "w": 95,
            "h": 210,
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
    first_threshold = clamp_int(config.get("capture_first_detection_threshold", 35), 1, 200)
    second_threshold = clamp_int(config.get("capture_second_detection_threshold", 75), 1, 200)
    if first_threshold > second_threshold:
        first_threshold, second_threshold = second_threshold, first_threshold
    config["capture_first_detection_threshold"] = first_threshold
    config["capture_second_detection_threshold"] = second_threshold
    config["routines_enabled"] = bool(config.get("routines_enabled", False))
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
    legacy_probe_map = {
        "end_confirm_yellow": "routine_match_end_yellow",
        "one_more_match": "routine_one_more_match",
        "center_confirm": "routine_confirm",
    }
    legacy_routine_probe_sources = {
        new_key: deepcopy(probes[old_key])
        for new_key, old_key in legacy_probe_map.items()
        if old_key in probes
    }
    for key in OBSOLETE_PIXEL_PROBE_KEYS:
        probes.pop(key, None)
    for key, default_probe in DEFAULT_CONFIG["pixel_probes"].items():
        probe = probes.setdefault(key, deepcopy(default_probe))
        for field, value in default_probe.items():
            probe.setdefault(field, value)
        probe["x"] = clamp_int(probe["x"], 0, CAPTURE_WIDTH - 1)
        probe["y"] = clamp_int(probe["y"], 0, CAPTURE_HEIGHT - 1)
        probe["tolerance"] = clamp_int(probe["tolerance"], 0, 255)
        probe["enabled"] = bool(probe.get("enabled", True))
        if probe.get("mode") not in {"match", "not_match"}:
            probe["mode"] = "match"
    routine_probes = config.setdefault("routine_probes", {})
    for key, default_probe in DEFAULT_CONFIG["routine_probes"].items():
        source = legacy_routine_probe_sources.get(key, default_probe)
        probe = routine_probes.setdefault(key, deepcopy(source))
        for field, value in default_probe.items():
            probe.setdefault(field, value)
        probe["x"] = clamp_int(probe["x"], 0, CAPTURE_WIDTH - 1)
        probe["y"] = clamp_int(probe["y"], 0, CAPTURE_HEIGHT - 1)
        probe["tolerance"] = clamp_int(probe["tolerance"], 0, 255)
        probe["enabled"] = bool(probe.get("enabled", True))
    routine_rules = config.setdefault("routine_rules", {})
    for key, default_rule in DEFAULT_CONFIG["routine_rules"].items():
        probe_source = routine_probes.get(key, {})
        source = deepcopy(default_rule)
        for field in ("label", "x", "y", "color", "tolerance", "enabled"):
            if field in probe_source:
                source[field] = probe_source[field]
        rule = routine_rules.setdefault(key, source)
        for field, value in default_rule.items():
            rule.setdefault(field, value)
        rule["x"] = clamp_int(rule["x"], 0, CAPTURE_WIDTH - 1)
        rule["y"] = clamp_int(rule["y"], 0, CAPTURE_HEIGHT - 1)
        rule["tolerance"] = clamp_int(rule["tolerance"], 0, 255)
        rule["delay_seconds"] = clamp_float(rule.get("delay_seconds", 1.0), 0.0, 30.0)
        rule["enabled"] = bool(rule.get("enabled", True))
    routine_sequences = config.setdefault("routine_sequences", {})
    for key, default_sequence in DEFAULT_CONFIG["routine_sequences"].items():
        sequence = routine_sequences.setdefault(key, deepcopy(default_sequence))
        sequence.setdefault("label", default_sequence.get("label", key))
        sequence["enabled"] = bool(sequence.get("enabled", True))
        sequence["cooldown_seconds"] = clamp_float(sequence.get("cooldown_seconds", 0.8), 0.0, 30.0)
        if not isinstance(sequence.get("steps"), list):
            sequence["steps"] = deepcopy(default_sequence.get("steps", []))
        for step in sequence.get("steps", []):
            if not isinstance(step, dict):
                continue
            step["enabled"] = bool(step.get("enabled", True))
            step.setdefault("label", "Etapa")
            step.setdefault("detect", "")
            if step.get("condition") not in {"match", "not_match"}:
                step["condition"] = "match"
            if step.get("click_mode") not in {"detect", "probe", "point"}:
                step["click_mode"] = "detect"
            step.setdefault("click_probe", "")
            step["x"] = clamp_int(step.get("x", 0), 0, CAPTURE_WIDTH - 1)
            step["y"] = clamp_int(step.get("y", 0), 0, CAPTURE_HEIGHT - 1)
            step["delay_seconds"] = clamp_float(step.get("delay_seconds", 0.0), 0.0, 30.0)
            step["repeat"] = clamp_int(step.get("repeat", 1), 1, 50)
            if step.get("next") not in {"advance", "stay", "reset"}:
                step["next"] = "advance"
    routines = config.setdefault("routines", {})
    for key, default_routine in DEFAULT_CONFIG["routines"].items():
        routine = routines.setdefault(key, deepcopy(default_routine))
        routine.setdefault("label", default_routine.get("label", key))
        routine["enabled"] = bool(routine.get("enabled", True))
        routine["cooldown_seconds"] = clamp_float(routine.get("cooldown_seconds", 0.8), 0.0, 30.0)
        if not isinstance(routine.get("steps"), list):
            routine["steps"] = deepcopy(default_routine.get("steps", []))
        default_steps = default_routine.get("steps", [])
        for index, step in enumerate(routine.get("steps", [])):
            if not isinstance(step, dict):
                continue
            if index < len(default_steps):
                for field, value in default_steps[index].items():
                    step.setdefault(field, value)
            step["delay_seconds"] = clamp_float(step.get("delay_seconds", 0.0), 0.0, 30.0)


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
