from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from typing import Callable, Iterable


SUITS = ("man", "pin", "sou")
WINDS = ("wind_east", "wind_south", "wind_west", "wind_north")
WIND_VALUE_TO_KEY = {
    "east": "wind_east",
    "south": "wind_south",
    "west": "wind_west",
    "north": "wind_north",
}
DRAGONS = ("dragon_white", "dragon_green", "dragon_red")
HONORS = (*WINDS, *DRAGONS)
TERMINAL_NUMBERS = {1, 9}
ALL_BASE_KEYS = (
    *(f"{suit}_{value}" for suit in SUITS for value in range(1, 10)),
    *HONORS,
)
BASE_KEY_INDEX = {key: index for index, key in enumerate(ALL_BASE_KEYS)}
BOT_LOGIC_VERSION = "2026.05.07.strategy-v7.coherent-objectives"
BOT_LOGIC_NOTES = (
    "flush_candidates_respect_open_meld_suit",
    "attack_objective_prioritizes_feasibility_before_han",
    "autoplay_logs_logic_version",
    "defense_danger_context_cached_per_frame",
    "immediate_defense_short_circuit_before_attack_plans",
    "defense_explanations_skip_ukeire_and_furiten",
    "defense_danger_scores_cached_by_tile_key",
    "speculative_yaku_confidence_capped_by_real_hand_shape",
    "riichi_added_as_closed_hand_attack_plan_with_dora_bonus",
    "autoplay_ron_tsumo_clicks_have_absolute_priority",
    "attack_objective_can_be_multi_yaku_flexible_hand",
    "outside_hand_yakus_penalized_when_shape_is_not_real",
    "hand_shape_block_evaluator_rewards_ryanmen_and_penalizes_bad_taatsu",
    "chitoitsu_blocks_open_calls",
    "open_calls_require_relevant_open_yaku",
    "tenpai_lock_preserves_waits_and_avoids_furiten",
    "ittsu_requires_complete_segment_commitment",
    "honor_pair_objectives_are_protected",
)


class MeldKind(str, Enum):
    CHI = "CHI"
    PON = "PON"
    KAN = "KAN"
    UNKNOWN = "CALL"


@dataclass(frozen=True, order=True)
class Tile:
    suit: str
    value: int | str
    red: bool = False

    @property
    def key(self) -> str:
        if self.suit in SUITS:
            suffix = "_red" if self.red else ""
            return f"{self.suit}_{self.value}{suffix}"
        return f"{self.suit}_{self.value}"

    @property
    def base_key(self) -> str:
        if self.suit in SUITS:
            return f"{self.suit}_{self.value}"
        return self.key

    @property
    def compact(self) -> str:
        if self.suit in SUITS:
            suffix = "r" if self.red else ""
            return f"{self.value}-{self.suit}{suffix}"
        if self.suit == "wind":
            return f"{self.value}-wind"
        return f"{self.value}-dragon"

    @property
    def is_suited(self) -> bool:
        return self.suit in SUITS

    @property
    def is_honor(self) -> bool:
        return self.suit in ("wind", "dragon")

    @property
    def is_terminal(self) -> bool:
        return self.is_suited and int(self.value) in TERMINAL_NUMBERS

    @property
    def is_simple(self) -> bool:
        return self.is_suited and 2 <= int(self.value) <= 8


@dataclass(frozen=True)
class Meld:
    kind: MeldKind
    tiles: tuple[Tile, ...]

    @property
    def compact(self) -> str:
        tiles = ",".join(tile.compact for tile in self.tiles)
        if self.kind == MeldKind.KAN and self.tiles:
            return f"KAN {self.tiles[0].compact} 4x"
        return f"{self.kind.value} {tiles}"

    @property
    def is_sequence(self) -> bool:
        return self.kind == MeldKind.CHI

    @property
    def is_triplet_like(self) -> bool:
        return self.kind in (MeldKind.PON, MeldKind.KAN)


@dataclass(frozen=True)
class Yaku:
    key: str
    name: str
    closed_only: bool = False
    situational: bool = False
    enabled: bool = True
    matcher: Callable[["HandState"], bool] | None = None


@dataclass(frozen=True)
class YakuMatch:
    yaku: Yaku
    reason: str
    confidence: int = 100


@dataclass(frozen=True)
class HandProgress:
    shanten: int
    percent: int
    label: str


@dataclass(frozen=True)
class HandShapeProfile:
    score: int
    blocks: int
    complete_blocks: int
    ryanmen: int
    ryankan: int
    kanchan: int
    penchan: int
    pairs: int
    excess_pairs: int
    isolated_middle: int
    isolated_terminal: int
    isolated_honor: int
    summary: str


@dataclass(frozen=True)
class ShapeCounts:
    complete: int = 0
    pairs: int = 0
    ryanmen: int = 0
    ryankan: int = 0
    kanchan: int = 0
    penchan: int = 0
    isolated_middle: int = 0
    isolated_terminal: int = 0
    isolated_honor: int = 0

    def __add__(self, other: "ShapeCounts") -> "ShapeCounts":
        return ShapeCounts(
            complete=self.complete + other.complete,
            pairs=self.pairs + other.pairs,
            ryanmen=self.ryanmen + other.ryanmen,
            ryankan=self.ryankan + other.ryankan,
            kanchan=self.kanchan + other.kanchan,
            penchan=self.penchan + other.penchan,
            isolated_middle=self.isolated_middle + other.isolated_middle,
            isolated_terminal=self.isolated_terminal + other.isolated_terminal,
            isolated_honor=self.isolated_honor + other.isolated_honor,
        )


@dataclass(frozen=True)
class HandValueEstimate:
    han: int
    fu: int
    yakus: tuple[tuple[str, int], ...]
    bonus_han: int
    bonus_items: tuple[tuple[str, int], ...]
    ron_points: int
    tsumo_points: str
    limit_name: str
    dealer: bool
    note: str = ""


@dataclass(frozen=True)
class TileNeed:
    tile: Tile
    score: int
    reason: str


@dataclass(frozen=True)
class DiscardExplanation:
    tile: Tile
    rank: int
    color: str
    pressure: int
    shanten_after: int
    ukeire_after: int
    visible_count: int
    remaining_count: int
    reasons: tuple[str, ...]
    safeguards: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class AttackObjective:
    yaku_key: str
    name: str
    probability: int
    han: int
    score: int
    reason: str
    wanted: tuple[str, ...] = tuple()


@dataclass(frozen=True)
class DangerContext:
    visible_counts: Counter[str]
    opponent_discards: dict[str, set[str]]
    riichi_threats: tuple[str, ...]
    riichi_all_safe: dict[str, set[str]]
    riichi_after_safe: dict[str, set[str]]
    dora_keys: frozenset[str]


@dataclass(frozen=True)
class ChiiOption:
    discarded_tile: Tile
    needed_tiles: tuple[Tile, Tile]
    sequence: tuple[Tile, Tile, Tile]

    @property
    def compact(self) -> str:
        needed = "+".join(tile.compact for tile in self.needed_tiles)
        sequence = "-".join(str(tile.value) for tile in sort_tiles(self.sequence))
        return f"{self.discarded_tile.compact} com {needed} => {sequence}-{self.discarded_tile.suit}"


@dataclass(frozen=True)
class PonOption:
    discarded_tile: Tile
    source_player: str
    matching_tiles: tuple[Tile, Tile]

    @property
    def compact(self) -> str:
        pair = "+".join(tile.compact for tile in self.matching_tiles)
        return f"{self.discarded_tile.compact} de {self.source_player} com {pair}"


@dataclass(frozen=True)
class KanOption:
    tile: Tile
    source_player: str
    tiles: tuple[Tile, ...]

    @property
    def compact(self) -> str:
        return f"{self.tile.compact} 4x ({self.source_player})"


@dataclass(frozen=True)
class CallDecision:
    action: str
    recommended: bool
    confidence: int
    option: str
    reason: str

    @property
    def compact(self) -> str:
        verdict = "SIM" if self.recommended else "NAO"
        return f"{self.action} {verdict} {self.confidence}%: {self.option} ({self.reason})"


@dataclass(frozen=True)
class HandDecomposition:
    melds: tuple[Meld, ...]
    pair: tuple[Tile, Tile]


@dataclass
class HandState:
    hand_tiles: list[Tile]
    open_melds: list[Meld]
    likely_yaku: list[YakuMatch]
    blocked_yaku: list[YakuMatch] = field(default_factory=list)
    missing_count: int = 0
    discard_candidates: list[Tile] = field(default_factory=list)
    discard_reason: str = ""
    discard_explanations: list[DiscardExplanation] = field(default_factory=list)
    strategy_mode: str = "attack"
    strategy_reason: str = ""
    attack_objective: AttackObjective | None = None
    attack_candidates: list[AttackObjective] = field(default_factory=list)
    furiten_waits: list[TileNeed] = field(default_factory=list)
    current_furiten_waits: list[TileNeed] = field(default_factory=list)
    discarded_tiles: list[Tile] = field(default_factory=list)
    discarded_by_player: dict[str, list[Tile]] = field(default_factory=dict)
    opponent_open_tiles: dict[str, list[Tile]] = field(default_factory=dict)
    riichi_players: tuple[str, ...] = tuple()
    riichi_discard_marks: dict[str, int] = field(default_factory=dict)
    opponent_win_signals: dict[str, dict[str, bool]] = field(default_factory=dict)
    helpful_missing_tiles: list[TileNeed] = field(default_factory=list)
    chii_button_visible: bool = False
    chii_discard: Tile | None = None
    chii_options: list[ChiiOption] = field(default_factory=list)
    pon_button_visible: bool = False
    pon_source_player: str | None = None
    pon_discard: Tile | None = None
    pon_options: list[PonOption] = field(default_factory=list)
    kan_button_visible: bool = False
    kan_options: list[KanOption] = field(default_factory=list)
    riichi_button_visible: bool = False
    win_button_visible: bool = False
    call_decisions: list[CallDecision] = field(default_factory=list)
    dora_indicators: list[Tile] = field(default_factory=list)
    player_winds: dict[str, str] = field(default_factory=dict)

    @property
    def all_known_tiles(self) -> list[Tile]:
        tiles = list(self.hand_tiles)
        for meld in self.open_melds:
            tiles.extend(meld.tiles)
        return tiles

    @property
    def all_visible_tiles(self) -> list[Tile]:
        tiles = [*self.all_known_tiles, *self.discarded_tiles, *self.dora_indicators]
        for opponent_tiles in self.opponent_open_tiles.values():
            tiles.extend(opponent_tiles)
        return tiles

    @property
    def is_closed(self) -> bool:
        return not self.open_melds

    @property
    def kan_count(self) -> int:
        return sum(1 for meld in self.open_melds if meld.kind == MeldKind.KAN)

    @property
    def expected_visible_min(self) -> int:
        return 13 + self.kan_count

    @property
    def dora_tiles(self) -> list[Tile]:
        return [
            dora
            for indicator in self.dora_indicators
            if (dora := dora_from_indicator(indicator)) is not None
        ]

    def refresh_missing_count(self) -> None:
        self.missing_count = max(0, self.expected_visible_min - len(self.all_known_tiles))

    def summary(self) -> str:
        hand_items = [tile.compact for tile in self.hand_tiles]
        hand_items.extend("???" for _ in range(self.missing_count))
        meld_items = [meld.compact for meld in self.open_melds]
        hand = self.format_grouped(hand_items, group_size=8)
        melds = self.format_grouped(meld_items, group_size=2)
        yaku = self.format_grouped((f"{match.yaku.name} {match.confidence}%" for match in self.likely_yaku), group_size=3)
        strategy_target = "-"
        if self.attack_objective is not None:
            strategy_target = (
                f"{self.attack_objective.name} {self.attack_objective.probability}% "
                f"{self.attack_objective.han} han"
            )
        blocked = self.format_grouped((f"{match.yaku.name} 0% ({match.reason})" for match in self.blocked_yaku[:8]), group_size=2)
        needs = self.format_grouped(
            (f"{need.tile.compact} {need.score}% ({need.reason})" for need in self.helpful_missing_tiles[:8]),
            group_size=3,
        )
        furiten = self.format_grouped(
            (f"{need.tile.compact} {need.score}% ({need.reason})" for need in self.furiten_waits[:6]),
            group_size=3,
        )
        current_furiten = self.format_grouped(
            (f"{need.tile.compact} {need.score}% ({need.reason})" for need in self.current_furiten_waits[:6]),
            group_size=3,
        )
        best_discard = self.format_grouped((tile.compact for tile in self.discard_candidates[:4]), group_size=4)
        discard_reason = self.discard_reason or "menor contribuicao estimada para fechar a mao"

        dora = self.format_pipe(tile.compact for tile in self.dora_tiles[:5])
        indicators = self.format_pipe(tile.compact for tile in self.dora_indicators[:5])
        winds = " | ".join(
            f"{label}:{self.player_winds.get(player, '?')}"
            for player, label in (("principal", "P"), ("esquerda", "E"), ("cima", "C"), ("direita", "D"))
        )
        parts = [f"[INFO] {len(self.all_known_tiles)}/{self.expected_visible_min} pecas | Ventos {winds} | Dora {dora} | Indic {indicators}"]
        if self.missing_count:
            parts.append(f"[INFO] Incompleta: faltam {self.missing_count} peca(s); decisao pode oscilar.")

        parts.extend(["[MAO] Fechada:", f"  {hand}"])
        if meld_items:
            parts.extend(["[MAO] Abertas:", f"  {melds}"])

        parts.extend(["[YAKU] Provaveis/ativos:", f"  {yaku}"])
        parts.extend(["[ESTRATEGIA]", f"  Modo: {self.strategy_mode} | Alvo: {strategy_target}", f"  Motivo: {self.strategy_reason or '-'}"])
        if self.blocked_yaku:
            parts.extend(["[YAKU] Bloqueados:", f"  {blocked}"])
        parts.extend(["[DESCARTE] Melhor descarte agora:", f"  {best_discard}", f"  Motivo: {discard_reason}"])
        if current_furiten:
            parts.extend(["[FURITEN ATUAL] Esperas bloqueadas:", f"  {current_furiten}"])
        if furiten:
            parts.extend(["[FURITEN] Esperas arriscadas:", f"  {furiten}"])
        parts.extend(["[FALTAM] Pecas que mais ajudam:", f"  {needs}"])

        call_summary = self.format_grouped((decision.compact for decision in self.call_decisions), group_size=1)
        if call_summary != "-" or self.chii_button_visible or self.pon_button_visible or self.kan_button_visible:
            buttons = []
            if self.chii_button_visible:
                buttons.append("Chii")
            if self.pon_button_visible:
                buttons.append("Pon")
            if self.kan_button_visible:
                buttons.append("Kan")
            if self.riichi_button_visible:
                buttons.append("Riichi")
            if self.win_button_visible:
                buttons.append("Ron/Tsumo")
            parts.extend(["[CALL]", f"  Botoes: {', '.join(buttons) if buttons else '-'}", f"  {call_summary}"])

        if self.discarded_by_player or self.discarded_tiles:
            parts.append("[DESCARTES VISTOS]")
            for player in ("principal", "esquerda", "cima", "direita"):
                tiles = self.discarded_by_player.get(player, [])
                parts.append(f"  {player.capitalize()}: {self.format_grouped((tile.compact for tile in tiles[:18]), group_size=10)}")

        if self.opponent_open_tiles:
            parts.append("[OPONENTES ABERTAS]")
            for player in ("esquerda", "cima", "direita"):
                tiles = self.opponent_open_tiles.get(player, [])
                parts.append(f"  {player.capitalize()}: {self.format_grouped((tile.compact for tile in tiles[:18]), group_size=10)}")

        return "\n".join(parts)

    @staticmethod
    def format_pipe(items: Iterable[str]) -> str:
        values = [item for item in items if item]
        return " | ".join(values) if values else "-"

    @staticmethod
    def format_grouped(items: Iterable[str], group_size: int = 8) -> str:
        values = [item for item in items if item]
        if not values:
            return "-"
        lines = []
        for index in range(0, len(values), group_size):
            lines.append(" | ".join(values[index : index + group_size]))
        return "\n  ".join(lines)


@dataclass(frozen=True)
class YakuPlan:
    yaku_key: str
    confidence: int
    useful_keys: frozenset[str]
    reason: str = "estimate"
    wanted_keys: frozenset[str] = frozenset()


def tile_from_name(name: str) -> Tile | None:
    parts = name.split("_")
    if len(parts) < 2:
        return None

    if parts[0] in SUITS:
        red = len(parts) >= 3 and parts[2] == "red"
        try:
            value = int(parts[1])
        except ValueError:
            return None
        return Tile(parts[0], value, red)

    if parts[0] == "wind" and len(parts) == 2:
        return Tile("wind", parts[1])
    if parts[0] == "dragon" and len(parts) == 2:
        return Tile("dragon", parts[1])
    return None


def canonical_counter(tiles: Iterable[Tile]) -> Counter[str]:
    return Counter(tile.base_key for tile in tiles)


def representative_tile(base_key: str) -> Tile:
    tile = tile_from_name(base_key)
    if tile is None:
        raise ValueError(f"Invalid tile key: {base_key}")
    return tile


def dora_from_indicator(indicator: Tile) -> Tile | None:
    if indicator.is_suited:
        value = int(indicator.value)
        next_value = 1 if value == 9 else value + 1
        return Tile(indicator.suit, next_value)

    if indicator.suit == "wind":
        wind_order = ("east", "south", "west", "north")
        if indicator.value not in wind_order:
            return None
        return Tile("wind", wind_order[(wind_order.index(str(indicator.value)) + 1) % 4])

    if indicator.suit == "dragon":
        dragon_order = ("white", "green", "red")
        if indicator.value not in dragon_order:
            return None
        return Tile("dragon", dragon_order[(dragon_order.index(str(indicator.value)) + 1) % 3])

    return None


def dora_base_keys(state: HandState) -> set[str]:
    return {tile.base_key for tile in state.dora_tiles}


def classify_meld(tiles: list[Tile]) -> Meld:
    sorted_tiles = sort_tiles(tiles)
    base_counts = canonical_counter(sorted_tiles)
    if len(sorted_tiles) >= 4 and len(base_counts) == 1:
        return Meld(MeldKind.KAN, tuple(sorted_tiles[:4]))
    if len(sorted_tiles) >= 3 and len(base_counts) == 1:
        return Meld(MeldKind.PON, tuple(sorted_tiles[:3]))
    if len(sorted_tiles) >= 3:
        suited = [tile for tile in sorted_tiles if tile.is_suited]
        if len(suited) >= 3:
            first_three = sort_tiles(suited)[:3]
            suits = {tile.suit for tile in first_three}
            values = sorted(int(tile.value) for tile in first_three)
            if len(suits) == 1 and values[1] == values[0] + 1 and values[2] == values[1] + 1:
                return Meld(MeldKind.CHI, tuple(first_three))
    return Meld(MeldKind.UNKNOWN, tuple(sorted_tiles))


def sort_tiles(tiles: Iterable[Tile]) -> list[Tile]:
    suit_order = {"man": 0, "pin": 1, "sou": 2, "wind": 3, "dragon": 4}
    honor_order = {
        "east": 1,
        "south": 2,
        "west": 3,
        "north": 4,
        "white": 5,
        "green": 6,
        "red": 7,
    }

    def key(tile: Tile) -> tuple[int, int, int]:
        value = int(tile.value) if tile.is_suited else honor_order.get(str(tile.value), 99)
        return suit_order.get(tile.suit, 99), value, 1 if tile.red else 0

    return sorted(tiles, key=key)


def has_sequence(tiles: Iterable[Tile]) -> bool:
    by_suit: dict[str, set[int]] = {suit: set() for suit in SUITS}
    for tile in tiles:
        if tile.is_suited:
            by_suit[tile.suit].add(int(tile.value))
    return any({start, start + 1, start + 2}.issubset(values) for values in by_suit.values() for start in range(1, 8))


def is_terminal_or_honor(tile: Tile) -> bool:
    return tile.is_terminal or tile.is_honor


def meld_contains_terminal_or_honor(meld: Meld) -> bool:
    return any(is_terminal_or_honor(tile) for tile in meld.tiles)


def meld_contains_terminal(meld: Meld) -> bool:
    return any(tile.is_terminal for tile in meld.tiles)


def pair_contains_terminal_or_honor(pair: tuple[Tile, Tile]) -> bool:
    return any(is_terminal_or_honor(tile) for tile in pair)


def pair_contains_terminal(pair: tuple[Tile, Tile]) -> bool:
    return any(tile.is_terminal for tile in pair)


def decompose_counter(counts: Counter[str], melds_needed: int) -> list[tuple[Meld, ...]]:
    if melds_needed == 0:
        return [tuple()] if not any(counts.values()) else []

    first_key = next((key for key, count in sorted(counts.items()) if count > 0), None)
    if first_key is None:
        return []

    decompositions: list[tuple[Meld, ...]] = []
    first_tile = representative_tile(first_key)

    if counts[first_key] >= 3:
        counts[first_key] -= 3
        meld = Meld(MeldKind.PON, (first_tile, first_tile, first_tile))
        for rest in decompose_counter(counts, melds_needed - 1):
            decompositions.append((meld, *rest))
        counts[first_key] += 3

    if first_tile.is_suited and int(first_tile.value) <= 7:
        key2 = f"{first_tile.suit}_{int(first_tile.value) + 1}"
        key3 = f"{first_tile.suit}_{int(first_tile.value) + 2}"
        if counts[key2] > 0 and counts[key3] > 0:
            counts[first_key] -= 1
            counts[key2] -= 1
            counts[key3] -= 1
            meld = Meld(
                MeldKind.CHI,
                (
                    first_tile,
                    representative_tile(key2),
                    representative_tile(key3),
                ),
            )
            for rest in decompose_counter(counts, melds_needed - 1):
                decompositions.append((meld, *rest))
            counts[first_key] += 1
            counts[key2] += 1
            counts[key3] += 1

    return decompositions


def closed_decompositions(tiles: list[Tile], melds_needed: int) -> list[HandDecomposition]:
    if len(tiles) != melds_needed * 3 + 2:
        return []

    counts = canonical_counter(tiles)
    decompositions: list[HandDecomposition] = []
    for pair_key, count in list(counts.items()):
        if count < 2:
            continue
        pair_tile = representative_tile(pair_key)
        counts[pair_key] -= 2
        for melds in decompose_counter(counts, melds_needed):
            decompositions.append(HandDecomposition(melds=melds, pair=(pair_tile, pair_tile)))
        counts[pair_key] += 2
    return decompositions


def standard_decompositions(state: HandState) -> list[HandDecomposition]:
    open_melds = tuple(meld for meld in state.open_melds if meld.kind in (MeldKind.CHI, MeldKind.PON, MeldKind.KAN))
    melds_needed = 4 - len(open_melds)
    if melds_needed < 0:
        return []

    decompositions = []
    for decomposition in closed_decompositions(state.hand_tiles, melds_needed):
        decompositions.append(
            HandDecomposition(
                melds=(*open_melds, *decomposition.melds),
                pair=decomposition.pair,
            )
        )
    return decompositions


def has_triplet_of(state: HandState, base_keys: set[str]) -> bool:
    counts = canonical_counter(state.all_known_tiles)
    return any(counts[key] >= 3 for key in base_keys)


def seat_wind_key(state: HandState) -> str | None:
    wind = str(state.player_winds.get("principal", "")).lower()
    return WIND_VALUE_TO_KEY.get(wind)


def has_seat_wind_triplet(state: HandState) -> bool:
    key = seat_wind_key(state)
    return bool(key and has_triplet_of(state, {key}))


def all_tiles_simple(state: HandState) -> bool:
    tiles = state.all_known_tiles
    return bool(tiles) and all(tile.is_simple for tile in tiles)


def has_red_five(state: HandState) -> bool:
    return any(tile.red for tile in state.all_known_tiles)


def has_all_simples_shape(state: HandState) -> bool:
    return all_tiles_simple(state) and hand_has_complete_shape(state)


def has_pinfu_like_shape(state: HandState) -> bool:
    if not state.is_closed:
        return False
    for decomposition in standard_decompositions(state):
        if all(meld.is_sequence for meld in decomposition.melds) and not decomposition.pair[0].is_honor:
            return True
    return False


def has_iipeikou_like_shape(state: HandState) -> bool:
    if not state.is_closed:
        return False
    for decomposition in standard_decompositions(state):
        sequences = [sequence for sequence in sequence_keys_from_decomposition(decomposition)]
        if any(count >= 2 for count in Counter(sequences).values()):
            return True
    return False


def has_twice_pure_double_sequence(state: HandState) -> bool:
    if not state.is_closed:
        return False
    for decomposition in standard_decompositions(state):
        sequences = [sequence for sequence in sequence_keys_from_decomposition(decomposition)]
        duplicate_sequences = sum(1 for count in Counter(sequences).values() if count >= 2)
        if duplicate_sequences >= 2:
            return True
    return False


def has_toitoi_like_shape(state: HandState) -> bool:
    return any(all(meld.is_triplet_like for meld in decomposition.melds) for decomposition in standard_decompositions(state))


def has_half_outside_hand(state: HandState) -> bool:
    for decomposition in standard_decompositions(state):
        groups_ok = all(meld_contains_terminal_or_honor(meld) for meld in decomposition.melds)
        pair_ok = pair_contains_terminal_or_honor(decomposition.pair)
        has_sequence_group = any(meld.is_sequence for meld in decomposition.melds)
        has_honor = any(tile.is_honor for tile in state.all_known_tiles)
        if groups_ok and pair_ok and has_sequence_group and has_honor:
            return True
    return False


def has_fully_outside_hand(state: HandState) -> bool:
    for decomposition in standard_decompositions(state):
        groups_ok = all(meld_contains_terminal(meld) for meld in decomposition.melds)
        pair_ok = pair_contains_terminal(decomposition.pair)
        has_sequence_group = any(meld.is_sequence for meld in decomposition.melds)
        no_honors = not any(tile.is_honor for tile in state.all_known_tiles)
        if groups_ok and pair_ok and has_sequence_group and no_honors:
            return True
    return False


def has_seven_pairs(state: HandState) -> bool:
    if not state.is_closed:
        return False
    counts = canonical_counter(state.hand_tiles)
    return len(state.hand_tiles) == 14 and len(counts) == 7 and all(count == 2 for count in counts.values())


def has_all_terminals_and_honors(state: HandState) -> bool:
    tiles = state.all_known_tiles
    return bool(tiles) and all(is_terminal_or_honor(tile) for tile in tiles) and hand_has_complete_shape(state)


def has_three_quads(state: HandState) -> bool:
    open_quads = sum(1 for meld in state.open_melds if meld.kind == MeldKind.KAN)
    concealed_quads = sum(1 for count in canonical_counter(state.hand_tiles).values() if count >= 4)
    return open_quads + concealed_quads >= 3


def has_three_same_number_triplets(state: HandState) -> bool:
    triplet_numbers_by_suit: dict[int, set[str]] = {number: set() for number in range(1, 10)}
    for key, count in canonical_counter(state.all_known_tiles).items():
        tile = representative_tile(key)
        if tile.is_suited and count >= 3:
            triplet_numbers_by_suit[int(tile.value)].add(tile.suit)
    return any(len(suits) == 3 for suits in triplet_numbers_by_suit.values())


def sequence_keys_from_decomposition(decomposition: HandDecomposition) -> list[tuple[str, int]]:
    sequences = []
    for meld in decomposition.melds:
        if not meld.is_sequence:
            continue
        suited = sort_tiles(meld.tiles)
        if suited and suited[0].is_suited:
            sequences.append((suited[0].suit, int(suited[0].value)))
    return sequences


def has_mixed_triple_sequence(state: HandState) -> bool:
    for decomposition in standard_decompositions(state):
        sequences = sequence_keys_from_decomposition(decomposition)
        for start in range(1, 8):
            if all((suit, start) in sequences for suit in SUITS):
                return True
    return False


def has_pure_straight(state: HandState) -> bool:
    for decomposition in standard_decompositions(state):
        sequences = sequence_keys_from_decomposition(decomposition)
        for suit in SUITS:
            if {(suit, 1), (suit, 4), (suit, 7)}.issubset(sequences):
                return True
    return False


def has_three_concealed_triplets(state: HandState) -> bool:
    if state.open_melds:
        return False
    counts = canonical_counter(state.hand_tiles)
    return sum(1 for count in counts.values() if count >= 3) >= 3


def has_little_three_dragons(state: HandState) -> bool:
    counts = canonical_counter(state.all_known_tiles)
    dragon_triplets = sum(1 for key in DRAGONS if counts[key] >= 3)
    dragon_pairs = sum(1 for key in DRAGONS if counts[key] == 2)
    return dragon_triplets == 2 and dragon_pairs >= 1


def yaku_by_key(key: str) -> Yaku:
    for yaku in YAKU_REGISTRY:
        if yaku.key == key:
            return yaku
    raise KeyError(key)


def estimate_shape_yaku(state: HandState) -> tuple[list[YakuMatch], list[Tile]]:
    plans = top_yaku_plans(state, limit=3)
    if not plans:
        return [], []
    matches = [YakuMatch(yaku_by_key(plan.yaku_key), plan.reason, plan.confidence) for plan in plans]
    discards = plan_discards_for_plans(state, plans)
    return matches, discards


def plan_discards_for_plans(state: HandState, plans: list[YakuPlan]) -> list[Tile]:
    if not plans:
        return []
    useful_keys = set().union(*(set(plan.useful_keys) for plan in plans))
    state.helpful_missing_tiles = helpful_missing_tiles_from_plans(plans, state)
    dora_keys = dora_base_keys(state)
    counts = canonical_counter(state.hand_tiles)
    return [
        tile
        for tile in state.hand_tiles
        if tile.base_key not in useful_keys and not tile.red and tile.base_key not in dora_keys
        and not speculative_plan_should_keep_shape(tile, counts)
    ]


def speculative_plan_should_keep_shape(tile: Tile, counts: Counter[str]) -> bool:
    if counts[tile.base_key] >= 2:
        return True
    if tile.is_suited and suited_tile_has_connection(tile, counts):
        return True
    return False


def top_yaku_plans(state: HandState, limit: int = 3) -> list[YakuPlan]:
    tiles = state.all_known_tiles
    if not tiles:
        return []

    candidates: list[YakuPlan] = []
    for match in suppress_weaker_matches(confirmed_yaku_matches(state)):
        useful = confirmed_useful_keys(state, match.yaku.key)
        candidates.append(YakuPlan(match.yaku.key, match.confidence, useful, match.reason, missing_keys_for_yaku(match.yaku.key, state)))

    candidates.append(compatibility_candidate("tanyao", tiles, lambda tile: tile.is_simple))
    candidates.append(
        compatibility_candidate(
            "honroutou",
            tiles,
            lambda tile: is_terminal_or_honor(tile),
        )
    )
    candidates.append(
        compatibility_candidate(
            "chanta",
            tiles,
            lambda tile: tile.is_honor or tile.is_terminal or (tile.is_suited and int(tile.value) in {2, 3, 7, 8}),
        )
    )
    candidates.append(
        compatibility_candidate(
            "junchan",
            tiles,
            lambda tile: tile.is_terminal or (tile.is_suited and int(tile.value) in {2, 3, 7, 8}),
        )
    )

    candidates.extend(flush_candidates(state))
    candidates.extend(dragon_candidates(state))
    candidates.extend(wind_candidates(state))
    candidates.extend(seven_pairs_candidate(state))
    candidates.extend(pure_straight_candidates(state))
    candidates.extend(riichi_candidates(state))
    candidates = [apply_shape_feasibility_to_plan(state, candidate) for candidate in candidates]

    valid = [
        candidate
        for candidate in candidates
        if candidate.confidence >= 45
        and candidate.useful_keys
        and not yaku_block_reason(candidate.yaku_key, state)
    ]
    if not valid:
        return []

    best_by_key: dict[str, YakuPlan] = {}
    for candidate in valid:
        confidence = min(candidate.confidence, 70) if state.missing_count and candidate.reason != "shape" else candidate.confidence
        candidate = YakuPlan(candidate.yaku_key, confidence, candidate.useful_keys, candidate.reason, candidate.wanted_keys)
        previous = best_by_key.get(candidate.yaku_key)
        if previous is None or candidate.confidence > previous.confidence:
            best_by_key[candidate.yaku_key] = candidate

    return select_display_plans(sorted(best_by_key.values(), key=lambda item: item.confidence, reverse=True), limit)


def apply_shape_feasibility_to_plan(state: HandState, plan: YakuPlan) -> YakuPlan:
    if plan.reason == "shape":
        return plan
    confidence = min(plan.confidence, speculative_plan_shape_cap(state, plan))
    if state.missing_count:
        confidence = min(confidence, 70)
    return YakuPlan(plan.yaku_key, confidence, plan.useful_keys, plan.reason, plan.wanted_keys)


def speculative_plan_shape_cap(state: HandState, plan: YakuPlan) -> int:
    shanten = plan_shape_shanten(state, plan)
    if shanten < 0:
        return 100
    if shanten == 0:
        return 92
    if shanten == 1:
        return 82
    if shanten == 2:
        return 70
    if shanten == 3:
        return 58
    if shanten == 4:
        return 50
    return 45


def plan_shape_shanten(state: HandState, plan: YakuPlan) -> int:
    if plan.yaku_key == "chitoitsu":
        return seven_pairs_shanten_number(state.hand_tiles) if state.is_closed else 8
    if plan.yaku_key == "riichi":
        return best_shanten_number(state.hand_tiles, state.open_melds)
    return standard_shanten_number(state.hand_tiles, state.open_melds)


def select_display_plans(plans: list[YakuPlan], base_limit: int = 3) -> list[YakuPlan]:
    strong = [plan for plan in plans if plan.confidence >= 50]
    if strong:
        return strong
    return plans[:base_limit]


def confirmed_yaku_matches(state: HandState) -> list[YakuMatch]:
    matches: list[YakuMatch] = []
    for yaku in YAKU_REGISTRY:
        if not yaku.enabled or yaku.matcher is None or yaku.situational:
            continue
        if yaku_block_reason(yaku.key, state):
            continue
        if yaku.matcher(state):
            matches.append(YakuMatch(yaku, "shape"))
    return matches


def confirmed_useful_keys(state: HandState, yaku_key: str) -> frozenset[str]:
    tiles = state.all_known_tiles
    if yaku_key == "tanyao":
        return frozenset(tile.base_key for tile in tiles if tile.is_simple)
    if yaku_key in {"honroutou", "chanta"}:
        return frozenset(tile.base_key for tile in tiles if is_terminal_or_honor(tile))
    if yaku_key == "junchan":
        return frozenset(tile.base_key for tile in tiles if tile.is_terminal)
    if yaku_key in {"yakuhai_dragon", "haku", "hatsu", "chun"}:
        return frozenset(tile.base_key for tile in tiles if tile.suit == "dragon")
    if yaku_key == "seat_wind":
        key = seat_wind_key(state)
        return frozenset({key}) if key and any(tile.base_key == key for tile in tiles) else frozenset()
    if yaku_key == "prevalent_wind":
        return frozenset(tile.base_key for tile in tiles if tile.base_key == "wind_east")
    return frozenset(tile.base_key for tile in tiles if tile.base_key)


def blocked_yaku_matches(state: HandState) -> list[YakuMatch]:
    matches = []
    for yaku in YAKU_REGISTRY:
        reason = yaku_block_reason(yaku.key, state)
        if reason:
            matches.append(YakuMatch(yaku, reason, 0))
    return sorted(matches, key=lambda match: (match.reason == "mao aberta", match.yaku.name))


def yaku_block_reason(yaku_key: str, state: HandState) -> str | None:
    yaku = yaku_by_key(yaku_key)
    open_melds = state.open_melds
    if not open_melds:
        return None

    if yaku.closed_only or yaku_key in CLOSED_ONLY_YAKU_KEYS:
        return "mao aberta"

    open_tiles = [tile for meld in open_melds for tile in meld.tiles]
    if yaku_key == "tanyao" and any(not tile.is_simple for tile in open_tiles):
        return "chamada aberta contem terminal/honra"
    if yaku_key == "honroutou" and any(not is_terminal_or_honor(tile) for tile in open_tiles):
        return "chamada aberta contem simples"
    if yaku_key == "chinroutou" and any(not tile.is_terminal for tile in open_tiles):
        return "chamada aberta contem nao-terminal"
    if yaku_key == "chanta" and any(not meld_contains_terminal_or_honor(meld) for meld in open_melds):
        return "chamada aberta sem terminal/honra"
    if yaku_key == "junchan" and (
        any(any(tile.is_honor for tile in meld.tiles) for meld in open_melds)
        or any(not meld_contains_terminal(meld) for meld in open_melds)
    ):
        return "chamada aberta nao serve para Fully Outside Hand"
    if yaku_key == "toitoi" and any(meld.is_sequence for meld in open_melds):
        return "chamada aberta e sequencia"
    if yaku_key in {"honitsu", "chinitsu", "chuuren_poutou", "junsei_chuuren_poutou"}:
        reason = flush_block_reason(yaku_key, open_tiles)
        if reason:
            return reason
    if yaku_key == "tsuuiisou" and any(not tile.is_honor for tile in open_tiles):
        return "chamada aberta contem numero"
    if yaku_key == "ryuuiisou" and any(not is_green_tile(tile) for tile in open_tiles):
        return "chamada aberta contem peca nao-verde"
    if yaku_key == "daisuushii" and any(not all(tile.suit == "wind" for tile in meld.tiles) for meld in open_melds):
        return "chamada aberta nao e vento"
    if yaku_key == "shousuushii" and count_open_melds_not_matching(open_melds, lambda tile: tile.suit == "wind") > 1:
        return "muitas chamadas abertas sem vento"
    if yaku_key == "daisangen" and count_open_melds_not_matching(open_melds, lambda tile: tile.suit == "dragon") > 1:
        return "muitas chamadas abertas sem dragao"
    if yaku_key == "suukantsu" and any(meld.kind == MeldKind.CHI for meld in open_melds):
        return "chamada aberta e sequencia"

    return None


def flush_block_reason(yaku_key: str, tiles: list[Tile]) -> str | None:
    suited_suits = {tile.suit for tile in tiles if tile.is_suited}
    has_honor = any(tile.is_honor for tile in tiles)
    if yaku_key in {"chinitsu", "chuuren_poutou", "junsei_chuuren_poutou"} and has_honor:
        return "chamada aberta contem honra"
    if len(suited_suits) > 1:
        return "chamadas abertas misturam naipes"
    return None


def count_open_melds_not_matching(open_melds: list[Meld], predicate: Callable[[Tile], bool]) -> int:
    return sum(1 for meld in open_melds if not all(predicate(tile) for tile in meld.tiles))


def is_green_tile(tile: Tile) -> bool:
    return tile.base_key in {"sou_2", "sou_3", "sou_4", "sou_6", "sou_8", "dragon_green"}


CLOSED_ONLY_YAKU_KEYS = {
    "kokushi_musou",
    "kokushi_musou_13",
    "suuankou",
    "suuankou_tanki",
}


def compatibility_candidate(key: str, tiles: list[Tile], predicate: Callable[[Tile], bool]) -> YakuPlan:
    contributing = [tile for tile in tiles if predicate(tile)]
    confidence = round(100 * len(contributing) / max(1, len(tiles)))
    confidence = min(confidence, structural_requirement_cap(key, tiles))
    useful = frozenset(tile.base_key for tile in contributing)
    wanted = generic_wanted_keys(key, tiles)
    return YakuPlan(key, confidence, useful, wanted_keys=wanted)


def structural_requirement_cap(yaku_key: str, tiles: list[Tile]) -> int:
    if yaku_key == "junchan":
        cap = count_based_requirement_cap(sum(1 for tile in tiles if tile.is_terminal))
        if any(tile.is_honor for tile in tiles):
            cap = min(cap, 50)
        return cap
    if yaku_key == "chanta":
        cap = count_based_requirement_cap(sum(1 for tile in tiles if tile.is_terminal or tile.is_honor))
        if sum(1 for tile in tiles if tile.is_terminal) < 2:
            cap = min(cap, 55)
        return cap
    return 100


def count_based_requirement_cap(count: int) -> int:
    if count <= 0:
        return 30
    if count == 1:
        return 45
    if count == 2:
        return 60
    if count == 3:
        return 75
    return 88


def flush_candidates(state: HandState) -> list[YakuPlan]:
    tiles = state.all_known_tiles
    allowed_suits = flush_allowed_suits_from_open_melds(state.open_melds)
    if not allowed_suits:
        return []

    candidates = []
    for suit in allowed_suits:
        half_contrib = [tile for tile in tiles if tile.is_honor or (tile.is_suited and tile.suit == suit)]
        half_confidence = round(100 * len(half_contrib) / max(1, len(tiles)))
        candidates.append(
            YakuPlan(
                "honitsu",
                half_confidence,
                frozenset(tile.base_key for tile in half_contrib),
                wanted_keys=flush_wanted_keys(suit, allow_honors=True),
            )
        )

        full_contrib = [tile for tile in tiles if tile.is_suited and tile.suit == suit]
        full_confidence = round(100 * len(full_contrib) / max(1, len(tiles)))
        candidates.append(
            YakuPlan(
                "chinitsu",
                full_confidence,
                frozenset(tile.base_key for tile in full_contrib),
                wanted_keys=flush_wanted_keys(suit, allow_honors=False),
            )
        )
    return candidates


def flush_allowed_suits_from_open_melds(open_melds: list[Meld]) -> tuple[str, ...]:
    suited_suits = {
        tile.suit
        for meld in open_melds
        for tile in meld.tiles
        if tile.is_suited
    }
    if len(suited_suits) > 1:
        return tuple()
    if len(suited_suits) == 1:
        return (next(iter(suited_suits)),)
    return SUITS


def visible_remaining_for_key(state: HandState, key: str) -> int:
    return max(0, 4 - canonical_counter(state.all_visible_tiles)[key])


def can_complete_copies(state: HandState, key: str, target_count: int) -> bool:
    known_count = canonical_counter(state.all_known_tiles)[key]
    needed = max(0, target_count - known_count)
    return needed <= visible_remaining_for_key(state, key)


def dragon_candidates(state: HandState) -> list[YakuPlan]:
    key_map = {
        "dragon_white": "haku",
        "dragon_green": "hatsu",
        "dragon_red": "chun",
    }
    tiles = state.all_known_tiles
    counts = canonical_counter(tiles)
    candidates = []
    for dragon_key, yaku_key in key_map.items():
        count = counts[dragon_key]
        if count == 0:
            continue
        needed = max(0, 3 - count)
        if needed > visible_remaining_for_key(state, dragon_key):
            continue
        confidence = min(95, 35 + count * 20)
        candidates.append(YakuPlan(yaku_key, confidence, frozenset({dragon_key}), wanted_keys=frozenset({dragon_key})))
    return candidates


def wind_candidates(state: HandState) -> list[YakuPlan]:
    tiles = state.all_known_tiles
    counts = canonical_counter(tiles)
    discarded_counts = canonical_counter(state.discarded_tiles)
    candidates = []
    seat_key = seat_wind_key(state)
    if seat_key and counts[seat_key] > 0:
        needed = max(0, 3 - counts[seat_key])
        if needed <= visible_remaining_for_key(state, seat_key):
            penalty = discarded_counts[seat_key] * 12
            confidence = max(0, min(92, 35 + counts[seat_key] * 22 - penalty))
            candidates.append(YakuPlan("seat_wind", confidence, frozenset({seat_key}), wanted_keys=frozenset({seat_key})))

    for wind_key in ("wind_east",):
        count = counts[wind_key]
        if count == 0:
            continue
        needed = max(0, 3 - count)
        if needed > visible_remaining_for_key(state, wind_key):
            continue
        penalty = discarded_counts[wind_key] * 12
        confidence = max(0, min(92, 35 + count * 22 - penalty))
        candidates.append(YakuPlan("prevalent_wind", confidence, frozenset({wind_key}), wanted_keys=frozenset({wind_key})))
    return candidates


def seven_pairs_candidate(state: HandState) -> list[YakuPlan]:
    if not state.is_closed:
        return []
    counts = canonical_counter(state.hand_tiles)
    visible_counts = canonical_counter(state.all_visible_tiles)
    paired_keys = {key for key, count in counts.items() if count >= 2}
    pairable_single_keys = {
        key
        for key, count in counts.items()
        if count == 1 and visible_counts[key] < 4 and len(paired_keys) < 7
    }
    if len(paired_keys) + len(pairable_single_keys) < 7:
        return []
    useful_keys = set(pairable_single_keys)
    useful_keys |= paired_keys
    single_progress = min(7 - len(paired_keys), len(useful_keys - paired_keys)) * 0.15
    confidence = round(100 * min(7, len(paired_keys) + single_progress) / 7)
    wanted = frozenset(pairable_single_keys)
    return [YakuPlan("chitoitsu", confidence, frozenset(useful_keys), wanted_keys=wanted)]


def pure_straight_candidates(state: HandState) -> list[YakuPlan]:
    tiles = state.all_known_tiles
    visible_counts = canonical_counter(state.all_visible_tiles)
    candidates = []
    needed = {1, 2, 3, 4, 5, 6, 7, 8, 9}
    for suit in SUITS:
        suit_values = {int(tile.value) for tile in tiles if tile.is_suited and tile.suit == suit}
        present = needed & suit_values
        missing = needed - present
        if any(visible_counts[f"{suit}_{value}"] >= 4 for value in missing):
            continue
        segment_hits = [len(set(segment) & present) for segment in ((1, 2, 3), (4, 5, 6), (7, 8, 9))]
        if min(segment_hits) == 0:
            continue
        segment_scores = [hits / 3 for hits in segment_hits]
        confidence = round(100 * sum(segment_scores) / 3)
        if len(present) < 5:
            confidence = min(confidence, 42)
        elif len(present) == 5:
            confidence = min(confidence, 58)
        if len(missing & {4, 5, 6}) >= 2:
            confidence = min(confidence, 48)
        open_sequences = [
            meld
            for meld in state.open_melds
            if meld.is_sequence
            and meld.tiles
            and meld.tiles[0].is_suited
            and meld.tiles[0].suit == suit
        ]
        if state.open_melds and not open_sequences:
            confidence = min(confidence, 45)
        elif open_sequences:
            confidence = min(92, confidence + 12)
        terminal_count = int(1 in present) + int(9 in present)
        if terminal_count == 0:
            confidence = min(confidence, 35)
        elif terminal_count == 1:
            confidence = min(confidence, 65)
        useful = frozenset(f"{suit}_{value}" for value in present)
        wanted = frozenset(f"{suit}_{value}" for value in missing)
        candidates.append(YakuPlan("ittsu", confidence, useful, wanted_keys=wanted))
    return candidates


def riichi_candidates(state: HandState) -> list[YakuPlan]:
    if not state.is_closed or state.missing_count:
        return []

    shanten = best_shanten_number(state.hand_tiles, state.open_melds)
    bonus_han = sum(_han for _name, _han in bonus_han_items(state))
    if shanten < 0:
        confidence = 100
    elif shanten == 0:
        confidence = 96
    elif shanten == 1:
        confidence = 74
    elif shanten == 2 and bonus_han:
        confidence = 54
    else:
        return []

    confidence = min(100, confidence + min(12, bonus_han * 4))
    useful = frozenset(tile.base_key for tile in state.all_known_tiles)
    wanted = riichi_wanted_keys(state, shanten)
    reason = "riichi_button" if state.riichi_button_visible else "riichi_path"
    return [YakuPlan("riichi", confidence, useful, reason, wanted)]


def riichi_wanted_keys(state: HandState, current_shanten: int) -> frozenset[str]:
    if current_shanten < 0:
        return frozenset()

    standard_shanten = standard_shanten_number(state.hand_tiles, state.open_melds)
    standard_improving = {
        tile.base_key
        for tile, _available in improving_tiles_for_completion(state, state.hand_tiles, standard_shanten)[:12]
    }
    if not state.is_closed:
        return frozenset(standard_improving)

    chitoi_shanten = seven_pairs_shanten_number(state.hand_tiles)
    if chitoi_shanten > standard_shanten:
        return frozenset(standard_improving)

    visible_counts = canonical_counter(state.all_visible_tiles)
    hand_counts = canonical_counter(state.hand_tiles)
    pair_improving = {
        key
        for key, count in hand_counts.items()
        if count == 1 and visible_counts[key] < 4
    }
    return frozenset((*standard_improving, *pair_improving))


def helpful_missing_tiles_from_plans(plans: list[YakuPlan], state: HandState) -> list[TileNeed]:
    visible_counts = canonical_counter(state.all_visible_tiles)
    scores: Counter[str] = Counter()
    reasons: dict[str, list[str]] = {}

    for plan in plans:
        yaku_name = yaku_by_key(plan.yaku_key).name
        for key in plan.wanted_keys:
            available = max(0, 4 - visible_counts[key])
            if available <= 0:
                continue
            score = max(1, round(plan.confidence * (available / 4)))
            previous_reasons = reasons.setdefault(key, [])
            bonus = min(15, len(previous_reasons) * 5)
            scores[key] = max(scores[key], score + bonus)
            previous_reasons.append(yaku_name)

    needs = []
    for key, score in scores.most_common(8):
        try:
            tile = representative_tile(key)
        except ValueError:
            continue
        reason = "+".join(dict.fromkeys(reasons.get(key, [])[:2]))
        needs.append(TileNeed(tile, min(100, score), reason or "plano"))
    return needs


def missing_keys_for_yaku(yaku_key: str, state: HandState) -> frozenset[str]:
    counts = canonical_counter(state.all_known_tiles)
    if yaku_key in {"haku", "hatsu", "chun"}:
        dragon_key = {"haku": "dragon_white", "hatsu": "dragon_green", "chun": "dragon_red"}[yaku_key]
        return frozenset({dragon_key}) if counts[dragon_key] < 3 else frozenset()
    if yaku_key == "yakuhai_dragon":
        return frozenset(key for key in DRAGONS if 0 < counts[key] < 3)
    if yaku_key == "seat_wind":
        key = seat_wind_key(state)
        return frozenset({key}) if key and 0 < counts[key] < 3 else frozenset()
    if yaku_key == "prevalent_wind":
        return frozenset({"wind_east"}) if 0 < counts["wind_east"] < 3 else frozenset()
    if yaku_key == "ittsu":
        plans = pure_straight_candidates(state)
        if not plans:
            return frozenset()
        return frozenset().union(*(plan.wanted_keys for plan in plans))
    if yaku_key in {"honitsu", "chinitsu"}:
        plans = [plan for plan in flush_candidates(state) if plan.yaku_key == yaku_key]
        if not plans:
            return frozenset()
        best = max(plans, key=lambda plan: plan.confidence)
        return best.wanted_keys
    return generic_wanted_keys(yaku_key, state.all_known_tiles)


def generic_wanted_keys(yaku_key: str, tiles: list[Tile]) -> frozenset[str]:
    if yaku_key == "tanyao":
        return frozenset(f"{suit}_{value}" for suit in SUITS for value in range(2, 9))
    if yaku_key == "honroutou":
        return frozenset(
            [*(f"{suit}_{value}" for suit in SUITS for value in (1, 9)), *HONORS]
        )
    if yaku_key == "chanta":
        return frozenset(
            [*(f"{suit}_{value}" for suit in SUITS for value in (1, 2, 3, 7, 8, 9)), *HONORS]
        )
    if yaku_key == "junchan":
        return frozenset(f"{suit}_{value}" for suit in SUITS for value in (1, 2, 3, 7, 8, 9))
    return frozenset(tile.base_key for tile in tiles)


def flush_wanted_keys(suit: str, allow_honors: bool) -> frozenset[str]:
    keys = {f"{suit}_{value}" for value in range(1, 10)}
    if allow_honors:
        keys.update(HONORS)
    return frozenset(keys)


def chii_options_for_hand(hand_tiles: list[Tile], discarded_tile: Tile | None) -> list[ChiiOption]:
    if discarded_tile is None or not discarded_tile.is_suited:
        return []

    counts = canonical_counter(hand_tiles)
    suit = discarded_tile.suit
    value = int(discarded_tile.value)
    options: list[ChiiOption] = []
    for start in (value - 2, value - 1, value):
        sequence_values = [start, start + 1, start + 2]
        if start < 1 or start > 7 or value not in sequence_values:
            continue
        needed_values = [item for item in sequence_values if item != value]
        needed_keys = [f"{suit}_{item}" for item in needed_values]
        if all(counts[key] > 0 for key in needed_keys):
            needed_tiles = tuple(representative_tile(key) for key in needed_keys)
            sequence = tuple(representative_tile(f"{suit}_{item}") for item in sequence_values)
            options.append(ChiiOption(discarded_tile, needed_tiles, sequence))
    return options


def kan_options_for_hand(hand_tiles: list[Tile], discarded_tile: Tile | None, source_player: str | None) -> list[KanOption]:
    counts = canonical_counter(hand_tiles)
    options: list[KanOption] = []
    for key, count in counts.items():
        if count >= 4:
            tile = representative_tile(key)
            options.append(KanOption(tile, "fechado", tuple(tile for _ in range(4))))

    if discarded_tile is not None and source_player not in (None, "principal") and counts[discarded_tile.base_key] >= 3:
        matching = [tile for tile in hand_tiles if tile.base_key == discarded_tile.base_key][:3]
        options.append(KanOption(discarded_tile, source_player or "oponente", tuple([discarded_tile, *matching])))
    return options


def call_decisions_for_state(state: HandState) -> list[CallDecision]:
    decisions: list[CallDecision] = []
    before_score = call_plan_score(state)
    if state.chii_button_visible:
        decisions.append(best_call_decision("Chii", state, state.chii_options, before_score))
    if state.pon_button_visible:
        decisions.append(best_call_decision("Pon", state, state.pon_options, before_score))
    if state.kan_button_visible:
        decisions.append(best_call_decision("Kan", state, state.kan_options, before_score))
    return decisions


CALL_SEQUENCE_YAKU_KEYS = {
    "tanyao",
    "chanta",
    "junchan",
    "honitsu",
    "chinitsu",
    "ittsu",
    "sanshoku_doujun",
}


def best_call_decision(action: str, state: HandState, options, before_score: int) -> CallDecision:
    if not options:
        return CallDecision(action, False, 0, "-", "sem combinacao valida na mao")

    if chitoitsu_plan_locked(state):
        return CallDecision(action, False, 0, options[0].compact, "objetivo Seven Pairs ativo; chamada abriria e mataria chitoitsu")

    simulations: list[tuple[int, int, str, object, HandState, list[YakuPlan], list[YakuPlan]]] = []
    for option in options:
        simulated = simulate_call(action, state, option)
        if simulated is None:
            continue
        simulated.likely_yaku = likely_yaku(simulated)
        plans = call_candidate_plans(simulated)
        relevant_plans = [plan for plan in plans if call_plan_relevant_to_option(action, option, plan)]
        simulations.append(
            (
                call_plan_score_from_plans(plans),
                call_plan_score_from_plans(relevant_plans),
                option.compact,
                option,
                simulated,
                plans,
                relevant_plans,
            )
        )

    if not simulations:
        return CallDecision(action, False, 0, options[0].compact, "chamada quebraria a mao detectada")

    after_score, relevant_after_score, option_text, option, simulated, after_plans, relevant_after_plans = max(
        simulations,
        key=lambda item: (item[1], item[0]),
    )
    delta = after_score - before_score
    blocked_closed = any(match.reason == "mao aberta" and match.yaku.closed_only for match in simulated.blocked_yaku)
    complete_open_yaku = [
        plan
        for plan in relevant_after_plans
        if plan.confidence >= 100
        and not yaku_by_key(plan.yaku_key).closed_only
        and not yaku_by_key(plan.yaku_key).situational
    ]
    viable_open_yaku = [
        plan
        for plan in after_plans
        if plan.confidence >= 70
        and not yaku_by_key(plan.yaku_key).closed_only
        and not yaku_by_key(plan.yaku_key).situational
    ]
    viable_relevant_open_yaku = [
        plan
        for plan in relevant_after_plans
        if plan.confidence >= 70
        and not yaku_by_key(plan.yaku_key).closed_only
        and not yaku_by_key(plan.yaku_key).situational
    ]
    before_shanten = best_shanten_number(state.hand_tiles, state.open_melds)
    after_shanten = best_shanten_number(simulated.hand_tiles, simulated.open_melds)
    shanten_gained = after_shanten < before_shanten
    shanten_preserved = after_shanten <= before_shanten

    if action == "Chii":
        # Chii opens the hand and commonly kills menzen paths, so require a
        # clear gain in the called sequence itself. Unrelated yakuhai already
        # present must not make a random Chii look like it "completes" dragons.
        recommended = bool(complete_open_yaku) or (
            bool(viable_relevant_open_yaku)
            and relevant_after_score >= 82
            and after_score >= 84
            and delta >= 16
            and shanten_gained
        )
    else:
        # Pon/Kan are allowed when the simulated future keeps or improves the
        # best open-yaku plan. This prevents rejecting yakuhai/dragon calls just
        # because closed-only yakus become unavailable.
        recommended = bool(complete_open_yaku) or (
            bool(viable_relevant_open_yaku)
            and relevant_after_score >= 82
            and after_score >= 82
            and delta >= 10
            and shanten_preserved
        )

    reason = f"score {before_score}->{after_score}"
    if complete_open_yaku:
        names = "+".join(yaku_by_key(plan.yaku_key).name for plan in complete_open_yaku[:2])
        reason += f"; completa {names}"
    elif action == "Chii" and not relevant_after_plans:
        reason += "; sequencia nao ajuda nenhum plano aberto"
    elif action == "Chii" and not shanten_gained and not complete_open_yaku:
        reason += "; Chii nao melhora shanten e abre a mao"
    elif action != "Chii" and not shanten_preserved and not complete_open_yaku:
        reason += "; chamada piora o shanten"
    elif not viable_relevant_open_yaku:
        reason += "; chamada nao deixa yaku aberto relevante viavel"
    elif not viable_open_yaku and not complete_open_yaku:
        reason += "; abrir deixaria sem yaku aberto viavel"
    elif blocked_closed and not recommended:
        reason += "; abre mao e futuro fica pior"
    elif delta > 0:
        reason += "; melhora planos ativos"
    elif recommended:
        reason += "; mantem yaku aberto viavel"
    else:
        reason += "; perde muita compatibilidade"
    return CallDecision(action, recommended, max(0, min(100, after_score)), option_text, reason)


def call_plan_relevant_to_option(action: str, option, plan: YakuPlan) -> bool:
    touched_keys = call_option_touched_keys(action, option)
    if not touched_keys or not (touched_keys & set(plan.useful_keys)):
        return False
    if action == "Chii":
        return plan.yaku_key in CALL_SEQUENCE_YAKU_KEYS
    return True


def call_option_touched_keys(action: str, option) -> set[str]:
    if action == "Chii":
        return {tile.base_key for tile in option.sequence}
    if action == "Pon":
        return {option.discarded_tile.base_key}
    if action == "Kan":
        return {option.tile.base_key}
    return set()


def chitoitsu_plan_locked(state: HandState) -> bool:
    if not state.is_closed:
        return False
    plans = top_yaku_plans(state, limit=4)
    if not plans:
        return False
    best = plans[0]
    if best.yaku_key != "chitoitsu" or best.confidence < 45:
        return False
    standard = standard_shanten_number(state.hand_tiles, state.open_melds)
    chitoi = seven_pairs_shanten_number(state.hand_tiles)
    return chitoi <= standard


def call_candidate_plans(state: HandState) -> list[YakuPlan]:
    return [
        plan
        for plan in top_yaku_plans(state, limit=6)
        if not yaku_by_key(plan.yaku_key).situational
    ]


def call_plan_score(state: HandState) -> int:
    return call_plan_score_from_plans(call_candidate_plans(state))


def call_plan_score_from_plans(plans: list[YakuPlan]) -> int:
    if not plans:
        return 0
    best = plans[0].confidence
    support = sum(plan.confidence for plan in plans[1:3]) // 5
    return max(0, min(100, best + support))


def simulate_call(action: str, state: HandState, option) -> HandState | None:
    hand_tiles = list(state.hand_tiles)
    if action == "Chii":
        remaining = remove_matching_tiles(hand_tiles, option.needed_tiles)
        if remaining is None:
            return None
        meld = Meld(MeldKind.CHI, tuple(sort_tiles(option.sequence)))
    elif action == "Pon":
        remaining = remove_matching_tiles(hand_tiles, option.matching_tiles)
        if remaining is None:
            return None
        meld = Meld(MeldKind.PON, tuple(sort_tiles([option.discarded_tile, *option.matching_tiles])))
    elif action == "Kan":
        if option.source_player == "fechado":
            remaining = remove_matching_tiles(hand_tiles, option.tiles)
        else:
            remaining = remove_matching_tiles(hand_tiles, option.tiles[1:])
        if remaining is None:
            return None
        meld = Meld(MeldKind.KAN, tuple(sort_tiles(option.tiles)))
    else:
        return None

    simulated = HandState(
        hand_tiles=remaining,
        open_melds=[*state.open_melds, meld],
        likely_yaku=[],
        discarded_tiles=state.discarded_tiles,
        discarded_by_player=state.discarded_by_player,
        opponent_open_tiles=state.opponent_open_tiles,
        dora_indicators=state.dora_indicators,
        player_winds=state.player_winds,
    )
    return simulated


def remove_matching_tiles(hand_tiles: list[Tile], required_tiles: Iterable[Tile]) -> list[Tile] | None:
    remaining = list(hand_tiles)
    for required in required_tiles:
        for index, tile in enumerate(remaining):
            if tile.base_key == required.base_key:
                remaining.pop(index)
                break
        else:
            return None
    return remaining


YAKU_REGISTRY: list[Yaku] = [
    Yaku("riichi", "Riichi", closed_only=True, situational=True),
    Yaku("double_riichi", "Double Riichi", closed_only=True, situational=True),
    Yaku("menzen_tsumo", "Fully Concealed Hand", closed_only=True, situational=True),
    Yaku("ippatsu", "Ippatsu", closed_only=True, situational=True),
    Yaku("pinfu", "Pinfu", closed_only=True, matcher=has_pinfu_like_shape),
    Yaku("iipeikou", "Pure Double Sequence", closed_only=True, matcher=has_iipeikou_like_shape),
    Yaku("ryanpeikou", "Twice Pure Double Sequence", closed_only=True, matcher=has_twice_pure_double_sequence),
    Yaku("tanyao", "All Simples", matcher=has_all_simples_shape),
    Yaku("seat_wind", "Seat Wind", matcher=has_seat_wind_triplet),
    Yaku("prevalent_wind", "Prevalent Wind", matcher=lambda state: has_triplet_of(state, {"wind_east"})),
    Yaku("yakuhai_dragon", "Dragons", matcher=lambda state: has_triplet_of(state, set(DRAGONS))),
    Yaku("haku", "White Dragon", matcher=lambda state: has_triplet_of(state, {"dragon_white"})),
    Yaku("hatsu", "Green Dragon", matcher=lambda state: has_triplet_of(state, {"dragon_green"})),
    Yaku("chun", "Red Dragon", matcher=lambda state: has_triplet_of(state, {"dragon_red"})),
    Yaku("rinshan_kaihou", "After a Kan", situational=True),
    Yaku("chankan", "Robbing a Kan", situational=True),
    Yaku("haitei", "Under the Sea", situational=True),
    Yaku("houtei", "Under the River", situational=True),
    Yaku("nagashi_mangan", "Mangan at Draw", situational=True),
    Yaku("toitoi", "All Triplets", matcher=has_toitoi_like_shape),
    Yaku("sanankou", "Three Concealed Triplets", matcher=has_three_concealed_triplets),
    Yaku("sankantsu", "Three Quads", matcher=has_three_quads),
    Yaku("honroutou", "All Terminals and Honors", matcher=has_all_terminals_and_honors),
    Yaku("shousangen", "Little Three Dragons", matcher=has_little_three_dragons),
    Yaku("sanshoku_doujun", "Mixed Triple Sequence", matcher=has_mixed_triple_sequence),
    Yaku("sanshoku_doukou", "Triple Triplets", matcher=has_three_same_number_triplets),
    Yaku("ittsu", "Pure Straight", matcher=has_pure_straight),
    Yaku("chanta", "Half Outside Hand", matcher=has_half_outside_hand),
    Yaku("junchan", "Fully Outside Hand", matcher=has_fully_outside_hand),
    Yaku("chitoitsu", "Seven Pairs", closed_only=True, matcher=has_seven_pairs),
    Yaku("honitsu", "Half Flush"),
    Yaku("chinitsu", "Full Flush"),
    Yaku("dora", "Dora", situational=True),
    Yaku("aka_dora", "Red Five", situational=True, matcher=has_red_five),
    Yaku("kita", "Kita", situational=True),
    Yaku("tsubame_gaeshi", "Local Yaku: Tsubame-gaeshi", situational=True),
    Yaku("kokushi_musou", "Thirteen Orphans"),
    Yaku("kokushi_musou_13", "Thirteen-wait Thirteen Orphans"),
    Yaku("suuankou", "Four Concealed Triplets"),
    Yaku("suuankou_tanki", "Single-wait Four Concealed Triplets"),
    Yaku("daisangen", "Big Three Dragons"),
    Yaku("shousuushii", "Four Little Winds"),
    Yaku("daisuushii", "Four Big Winds"),
    Yaku("tsuuiisou", "All Honors"),
    Yaku("chinroutou", "All Terminals"),
    Yaku("ryuuiisou", "All Green"),
    Yaku("chuuren_poutou", "Nine Gates", closed_only=True),
    Yaku("junsei_chuuren_poutou", "True Nine Gates", closed_only=True),
    Yaku("suukantsu", "Four Quads"),
    Yaku("tenhou", "Blessing of Heaven", closed_only=True, situational=True),
    Yaku("chiihou", "Blessing of Earth", closed_only=True, situational=True),
    Yaku("renhou", "Blessing of Man", closed_only=True, situational=True),
]


def likely_yaku(state: HandState) -> list[YakuMatch]:
    state.refresh_missing_count()
    state.blocked_yaku = blocked_yaku_matches(state)
    state.furiten_waits = []
    fast_defense_reason = immediate_defense_reason(state)
    if fast_defense_reason:
        plans = []
        state.attack_candidates = []
        state.attack_objective = None
        state.strategy_mode = "defense"
        state.strategy_reason = fast_defense_reason
        active_plans = []
    else:
        plans = top_yaku_plans(state, limit=6)
        state.attack_candidates = attack_objectives_from_plans(state, plans)
        choose_strategy_mode(state)
        active_plans = active_strategy_plans(state, plans)
    matches = [YakuMatch(yaku_by_key(plan.yaku_key), plan.reason, plan.confidence) for plan in active_plans]
    plan_discards = plan_discards_for_plans(state, active_plans[:3])
    efficiency_discards = inefficient_tiles_for_completion(state)
    guaranteed_yaku = has_guaranteed_yaku(state, matches)
    tenpai_discards = tenpai_lock_discard_candidates(state, active_plans[:3], guaranteed_yaku)
    completion_discards = completion_discard_candidates(state) if guaranteed_yaku or not plan_discards else []
    if state.strategy_mode == "defense":
        state.discard_candidates = defense_discard_candidates(state)
        state.helpful_missing_tiles = []
        state.discard_reason = "risco extremo: prioriza a peca menos perigosa detectada"
    elif tenpai_discards:
        state.discard_candidates = tenpai_discards
        state.discard_reason = "tenpai lock: preserva tenpai, espera boa e evita furiten"
    else:
        state.discard_candidates = blended_discard_candidates(
            state,
            active_plans[:3],
            completion_discards,
            efficiency_discards,
            plan_discards,
            guaranteed_yaku,
        )
        state.discard_reason = blended_discard_reason(state, guaranteed_yaku)
        if state.discard_candidates:
            state.helpful_missing_tiles = helpful_missing_tiles_from_plans(active_plans[:3], state)
            if guaranteed_yaku:
                state.helpful_missing_tiles = helpful_tiles_for_completion_after_discard(state, state.discard_candidates[0])
    if not state.discard_candidates:
        state.discard_candidates = ranked_discard_candidates(state, top_yaku_plans(state, limit=3))
        state.discard_reason = "menor contribuicao estimada entre as pecas conhecidas"
    if not state.discard_candidates and state.hand_tiles:
        state.discard_candidates = guaranteed_fallback_discard_candidates(state)
        state.discard_reason = "fallback: sempre mostrar descarte para manter o AutoPlay destravado"
    if state.strategy_mode == "defense":
        state.furiten_waits = []
        state.current_furiten_waits = []
    else:
        update_furiten_summary(state)
    state.discard_explanations = explain_discard_candidates(state, active_plans[:3])
    return matches


def immediate_defense_reason(state: HandState) -> str | None:
    if state.missing_count >= 3:
        return f"leitura incompleta demais ({state.missing_count} pecas faltando)"
    if any(player != "principal" for player in state.riichi_players):
        progress = hand_completion_progress(state)
        if progress.shanten >= 2:
            return f"oponente em Riichi; defesa imediata ({progress.label})"
    return None


def guaranteed_fallback_discard_candidates(state: HandState) -> list[Tile]:
    counts = canonical_counter(state.hand_tiles)
    dora_keys = dora_base_keys(state)
    current_shape = hand_shape_profile(state.hand_tiles, state.open_melds)
    scored: list[tuple[int, int, Tile]] = []
    for index, tile in enumerate(state.hand_tiles):
        remaining = remove_one_tile_instance(state.hand_tiles, tile)
        shanten = standard_shanten_number(remaining, state.open_melds)
        score = shanten * 100
        if tile.red:
            score += 80
        if tile.base_key in dora_keys:
            score += 70
        if counts[tile.base_key] >= 2:
            score += 36
        if tile.is_suited and suited_tile_has_connection(tile, counts):
            score += 22
        if tile.is_honor and counts[tile.base_key] == 1:
            score -= 18
        if tile.is_suited and not suited_tile_has_connection(tile, counts):
            score -= 12
        shape_after = hand_shape_profile(remaining, state.open_melds)
        score -= round(shape_after.score * 0.25 + (shape_after.score - current_shape.score))
        scored.append((score, index, tile))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [tile for _score, _index, tile in scored[:4]]


def attack_objectives_from_plans(state: HandState, plans: list[YakuPlan]) -> list[AttackObjective]:
    objectives: list[AttackObjective] = []
    bonus_han = sum(_han for _name, _han in bonus_han_items(state))
    progress = hand_completion_progress(state)
    progress_bonus = max(0, 28 - max(0, progress.shanten) * 5)
    for plan in plans:
        yaku = yaku_by_key(plan.yaku_key)
        if yaku.situational and plan.yaku_key != "riichi":
            continue
        han = yaku_han_value(plan.yaku_key, state.is_closed)
        if han <= 0:
            continue
        probability = max(0, min(100, plan.confidence))
        total_han = han + bonus_han
        value_bonus = attack_value_bonus(total_han)
        dora_synergy_bonus = attack_dora_synergy_bonus(state, plan, bonus_han)
        feasibility_penalty = attack_feasibility_penalty(state, plan)
        score = round(
            probability
            + value_bonus
            + dora_synergy_bonus
            + progress_bonus
            - feasibility_penalty
        )
        wanted = tuple(representative_tile(key).compact for key in sorted(plan.wanted_keys)[:8])
        reason = (
            f"{probability}% factivel, {han}+{bonus_han} han, "
            f"valor +{value_bonus}, dora +{dora_synergy_bonus}, custo -{feasibility_penalty}"
        )
        objectives.append(AttackObjective(plan.yaku_key, yaku.name, probability, han, score, reason, wanted))
    flexible = flexible_hand_objective(state, plans, objectives, bonus_han, progress)
    if flexible is not None:
        objectives.append(flexible)
    objectives.sort(key=lambda item: (item.score, item.probability, item.han), reverse=True)
    return objectives[:6]


def flexible_hand_objective(
    state: HandState,
    plans: list[YakuPlan],
    objectives: list[AttackObjective],
    bonus_han: int,
    progress: HandProgress,
) -> AttackObjective | None:
    if not state.hand_tiles:
        return None
    if state.open_melds and not open_hand_has_viable_yaku_plan(state, plans):
        return None

    usable_objectives = [
        objective
        for objective in objectives
        if objective.yaku_key not in {"honitsu", "chinitsu", "chanta", "junchan"}
        or objective.probability >= 78
    ]
    best_plan = max(usable_objectives, key=lambda item: item.score, default=None)
    best_probability = max((plan.confidence for plan in plans), default=0)
    shape = hand_shape_profile(state.hand_tiles, state.open_melds)
    yaku_support = min(28, sum(max(0, plan.confidence - 45) for plan in plans[:4]) // 4)
    dora_bonus = min(24, bonus_han * 7)
    riichi_bonus = 18 if any(plan.yaku_key == "riichi" for plan in plans) else 0
    low_value_push = 10 if progress.shanten <= 1 and (bonus_han or best_probability >= 55) else 0
    shape_bonus = max(-12, min(18, (shape.score - 52) // 2))
    score = progress.percent + yaku_support + dora_bonus + riichi_bonus + low_value_push + shape_bonus
    if best_plan is not None:
        score += min(18, best_plan.score // 8)

    probability = max(35, min(96, round((progress.percent * 0.72) + (best_probability * 0.28))))
    han = max(1, best_plan.han if best_plan is not None else 0)
    wanted_keys = flexible_hand_wanted_keys(state, plans)
    wanted = tuple(representative_tile(key).compact for key in sorted(wanted_keys)[:8])
    reason = (
        f"{probability}% fechamento flexivel, {han}+{bonus_han} han possivel, "
        f"progresso {progress.label}, forma {shape.score} ({shape.summary}), "
        f"suporte +{yaku_support}, dora +{dora_bonus}"
    )
    return AttackObjective("flexible_hand", "Mao flexivel", probability, han, round(score), reason, wanted)


def flexible_hand_wanted_keys(state: HandState, plans: list[YakuPlan]) -> frozenset[str]:
    shanten = standard_shanten_number(state.hand_tiles, state.open_melds)
    improving = {
        tile.base_key
        for tile, _available in improving_tiles_for_completion(state, state.hand_tiles, shanten)[:10]
    }
    planned = set().union(*(set(plan.wanted_keys) for plan in plans[:3])) if plans else set()
    dora_keys = dora_base_keys(state)
    red_keys = {tile.base_key for tile in state.all_known_tiles if tile.red}
    return frozenset((*improving, *planned, *dora_keys, *red_keys))


def attack_value_bonus(total_han: int) -> int:
    if total_han <= 1:
        return 0
    return min(40, (total_han - 1) * 8)


def attack_dora_synergy_bonus(state: HandState, plan: YakuPlan, bonus_han: int) -> int:
    if bonus_han <= 0:
        return 0
    dora_keys = dora_base_keys(state)
    red_keys = {tile.base_key for tile in state.all_known_tiles if tile.red}
    protected_bonus_keys = dora_keys | red_keys
    kept_bonus = len(protected_bonus_keys & plan.useful_keys)
    # Dora should help a practical path, especially All Simples, but should not
    # make a low-probability flush override hand completion by itself.
    return min(24, kept_bonus * 6 + min(3, bonus_han) * 3)


def attack_feasibility_penalty(state: HandState, plan: YakuPlan) -> int:
    counts = canonical_counter(state.all_known_tiles)
    shape = hand_shape_profile(state.hand_tiles, state.open_melds)
    useful = set(plan.useful_keys)
    off_plan_suited = [
        tile
        for tile in state.all_known_tiles
        if tile.is_suited and tile.base_key not in useful
    ]
    penalty = 0
    if plan.yaku_key == "tanyao":
        off_plan = [tile for tile in state.all_known_tiles if not tile.is_simple]
        penalty += len(off_plan) * 10
    if plan.yaku_key == "junchan":
        honors = [tile for tile in state.all_known_tiles if tile.is_honor]
        middle_tiles = [tile for tile in state.all_known_tiles if tile.is_suited and 4 <= int(tile.value) <= 6]
        penalty += len(honors) * 18 + len(middle_tiles) * 8
        if state.missing_count:
            penalty += state.missing_count * 8
    if plan.yaku_key == "chanta":
        middle_tiles = [tile for tile in state.all_known_tiles if tile.is_suited and 4 <= int(tile.value) <= 6]
        penalty += len(middle_tiles) * 6
        if state.missing_count:
            penalty += state.missing_count * 5
    if plan.yaku_key in {"honitsu", "chinitsu"}:
        penalty += len(off_plan_suited) * 5
        if state.open_melds and plan.confidence < 75:
            penalty += 12
        plan_shape = plan_specific_shape_profile(state, useful)
        if plan_shape.blocks < 4:
            penalty += (4 - plan_shape.blocks) * 12
        if plan_shape.complete_blocks + plan_shape.ryanmen + plan_shape.ryankan < 2:
            penalty += 10
        if len(off_plan_suited) >= 4 and plan.confidence < 65:
            penalty += 10
        if shape.blocks < 5:
            penalty += (5 - shape.blocks) * 9
        if shape.ryanmen + shape.ryankan == 0 and shape.kanchan + shape.penchan >= 2:
            penalty += 10
        if plan.confidence < 70 and shape.score < 58:
            penalty += 12
    if plan.yaku_key in {"chanta", "junchan"}:
        if shape.blocks < 5:
            penalty += (5 - shape.blocks) * 7
        if shape.penchan >= 2:
            penalty += 8
    if plan.yaku_key in {"haku", "hatsu", "chun", "seat_wind", "prevalent_wind", "ittsu", "chitoitsu"}:
        for key in plan.wanted_keys:
            if counts[key] == 0 and visible_remaining_for_key(state, key) <= 0:
                penalty += 18
    if plan.yaku_key == "chitoitsu":
        singles = sum(1 for count in canonical_counter(state.hand_tiles).values() if count == 1)
        if singles >= 7:
            penalty += 16
    if plan.yaku_key == "ittsu":
        useful = set(plan.useful_keys)
        suit_counts = Counter(key.split("_", 1)[0] for key in useful if "_" in key)
        if suit_counts:
            _suit, present_count = suit_counts.most_common(1)[0]
            if present_count < 5:
                penalty += 20
    return min(55, penalty)


def open_hand_has_viable_yaku_objective(state: HandState, objectives: list[AttackObjective]) -> bool:
    return any(
        not yaku_by_key(objective.yaku_key).closed_only
        and not yaku_by_key(objective.yaku_key).situational
        and objective.probability >= (70 if state.open_melds else 60)
        for objective in objectives
    )


def open_hand_has_viable_yaku_plan(state: HandState, plans: list[YakuPlan]) -> bool:
    return any(
        not yaku_by_key(plan.yaku_key).closed_only
        and not yaku_by_key(plan.yaku_key).situational
        and plan.confidence >= 70
        for plan in plans
    )


def plan_specific_shape_profile(state: HandState, useful_keys: set[str]) -> HandShapeProfile:
    plan_hand_tiles = [tile for tile in state.hand_tiles if tile.base_key in useful_keys]
    plan_open_melds = [
        meld
        for meld in state.open_melds
        if all(tile.base_key in useful_keys for tile in meld.tiles)
    ]
    return hand_shape_profile(plan_hand_tiles, plan_open_melds)


def choose_strategy_mode(state: HandState) -> None:
    if state.missing_count >= 3:
        state.strategy_mode = "defense"
        state.strategy_reason = f"leitura incompleta demais ({state.missing_count} pecas faltando)"
        state.attack_objective = state.attack_candidates[0] if state.attack_candidates else None
        return

    best = choose_attack_objective(state)
    own_discards = len(state.discarded_by_player.get("principal", []))
    progress = hand_completion_progress(state)
    opponent_riichi = any(player != "principal" for player in state.riichi_players)
    if best is None:
        state.strategy_mode = "defense"
        state.strategy_reason = "nenhum objetivo de ataque viavel detectado"
        state.attack_objective = None
        return
    has_confirmed_attack_yaku = any(candidate.probability >= 100 for candidate in state.attack_candidates)
    risk_pressure = table_risk_pressure(state)
    if opponent_riichi and (progress.shanten >= 2 or (progress.shanten >= 1 and best.han <= 1 and best.probability < 85)):
        state.strategy_mode = "defense"
        state.strategy_reason = "oponente em Riichi; mao longe/barata demais para empurrar"
        state.attack_objective = best
        return
    if best.probability < 35 and risk_pressure >= 45:
        state.strategy_mode = "defense"
        state.strategy_reason = f"plano fraco ({best.probability}%) com risco alto"
        state.attack_objective = best
        return
    if own_discards >= 14 and progress.shanten >= 3 and best.probability < 60:
        state.strategy_mode = "defense"
        state.strategy_reason = f"mao atrasada no fim ({progress.label}, {own_discards} descartes)"
        state.attack_objective = best
        return

    state.attack_objective = best
    if opponent_riichi or risk_pressure >= 65:
        state.strategy_mode = "cautious"
        state.strategy_reason = f"ponderar {best.name}: {best.probability}% x {best.han} han x risco {risk_pressure}%"
    elif risk_pressure >= 35 or (not has_confirmed_attack_yaku and best.han <= 1 and progress.shanten >= 1):
        state.strategy_mode = "balanced"
        state.strategy_reason = f"equilibrar {best.name}: {best.probability}% x {best.han} han x risco {risk_pressure}%"
    else:
        state.strategy_mode = "attack"
        state.strategy_reason = f"empurrar {best.name}: {best.probability}% x {best.han} han x risco {risk_pressure}%"


def choose_attack_objective(state: HandState) -> AttackObjective | None:
    if not state.attack_candidates:
        return None
    best = state.attack_candidates[0]
    non_flexible = [
        objective
        for objective in state.attack_candidates
        if objective.yaku_key != "flexible_hand"
    ]
    if not non_flexible:
        return best

    committed = non_flexible[0]
    if best.yaku_key == "flexible_hand":
        if state.open_melds and not open_hand_has_viable_yaku_objective(state, non_flexible):
            return committed
        if committed.han >= 2 and committed.probability >= 55 and committed.score >= best.score - 18:
            return committed
        if committed.yaku_key in {"ittsu", "honitsu", "chinitsu", "yakuhai_dragon", "haku", "hatsu", "chun"} and committed.probability >= 70:
            return committed
    return best


def active_strategy_plans(state: HandState, plans: list[YakuPlan]) -> list[YakuPlan]:
    if state.attack_objective is None:
        return plans[:3]
    if state.attack_objective.yaku_key == "flexible_hand":
        return plans[:4]
    primary = [plan for plan in plans if plan.yaku_key == state.attack_objective.yaku_key]
    support = [plan for plan in plans if plan.yaku_key != state.attack_objective.yaku_key]
    return [*primary, *support][:3]


def table_risk_pressure(state: HandState) -> int:
    own_discards = len(state.discarded_by_player.get("principal", []))
    opponent_discards = [
        len(tiles)
        for player, tiles in state.discarded_by_player.items()
        if player != "principal"
    ]
    latest_round = max([own_discards, *opponent_discards], default=own_discards)
    phase_pressure = min(42, latest_round * 3)
    riichi_pressure = 48 * sum(1 for player in state.riichi_players if player != "principal")
    open_pressure = min(18, sum(len(tiles) for tiles in state.opponent_open_tiles.values()) * 2)
    return max(0, min(100, phase_pressure + riichi_pressure + open_pressure))


def build_danger_context(state: HandState) -> DangerContext:
    opponent_discards = {
        player: {discard.base_key for discard in tiles}
        for player, tiles in state.discarded_by_player.items()
        if player != "principal"
    }
    riichi_threats = tuple(player for player in state.riichi_players if player != "principal")
    riichi_all_safe: dict[str, set[str]] = {}
    riichi_after_safe: dict[str, set[str]] = {}
    for player in riichi_threats:
        discards = state.discarded_by_player.get(player, [])
        mark = max(0, min(len(discards), state.riichi_discard_marks.get(player, len(discards))))
        riichi_all_safe[player] = {discard.base_key for discard in discards}
        riichi_after_safe[player] = {discard.base_key for discard in discards[mark:]}
    return DangerContext(
        visible_counts=canonical_counter(state.all_visible_tiles),
        opponent_discards=opponent_discards,
        riichi_threats=riichi_threats,
        riichi_all_safe=riichi_all_safe,
        riichi_after_safe=riichi_after_safe,
        dora_keys=frozenset(dora_base_keys(state)),
    )


def defense_discard_candidates(state: HandState) -> list[Tile]:
    hand_index = {id(tile): index for index, tile in enumerate(state.hand_tiles)}
    danger_context = build_danger_context(state)
    danger_by_key: dict[str, int] = {}
    scored = [
        (
            cached_tile_danger_score(state, tile, danger_context, danger_by_key),
            hand_index.get(id(tile), 999),
            tile,
        )
        for tile in state.hand_tiles
    ]
    scored.sort(key=lambda item: (item[0], item[1]))
    return [tile for _score, _index, tile in scored[:4]]


def blended_discard_reason(state: HandState, guaranteed_yaku: bool) -> str:
    objective = state.attack_objective
    target = objective.name if objective is not None else "sem alvo"
    risk = table_risk_pressure(state)
    if guaranteed_yaku:
        return f"yaku garantido; pondera fechar a mao x risco {risk}%"
    return f"pondera estrategia {target} x han x risco {risk}%"


def blended_discard_candidates(
    state: HandState,
    plans: list[YakuPlan],
    completion_discards: list[Tile],
    efficiency_discards: list[Tile],
    plan_discards: list[Tile],
    guaranteed_yaku: bool,
) -> list[Tile]:
    if not state.hand_tiles:
        return []

    hand_index = {id(tile): index for index, tile in enumerate(state.hand_tiles)}
    risk_pressure = table_risk_pressure(state)
    risk_weight = {
        "attack": 0.28,
        "balanced": 0.52,
        "cautious": 0.82,
        "defense": 1.15,
    }.get(state.strategy_mode, 0.52)
    risk_weight += risk_pressure / 160
    danger_context = build_danger_context(state)

    primary_objective = state.attack_objective
    primary_plan = None
    if plans and primary_objective is not None and primary_objective.yaku_key == plans[0].yaku_key:
        primary_plan = plans[0]
    flexible_mode = (
        primary_plan is None
        and primary_objective is not None
        and primary_objective.yaku_key == "flexible_hand"
    )
    support_plans = plans[1:] if primary_plan is not None else plans
    completion_keys = {tile.base_key for tile in completion_discards}
    efficiency_keys = {tile.base_key for tile in efficiency_discards}
    plan_discard_keys = {tile.base_key for tile in plan_discards}
    counts = canonical_counter(state.hand_tiles)
    dora_keys = dora_base_keys(state)
    non_dora_exists = any(tile.base_key not in dora_keys for tile in state.hand_tiles)
    non_red_exists = any(not tile.red for tile in state.hand_tiles)
    current_shape = hand_shape_profile(state.hand_tiles, state.open_melds)

    if primary_plan is not None and primary_plan.yaku_key == "chitoitsu":
        chitoi_candidates = chitoitsu_discard_candidates(state, support_plans)
        if chitoi_candidates:
            return chitoi_candidates[:4]

    scored: list[tuple[float, int, Tile]] = []
    danger_by_key: dict[str, int] = {}
    for tile in state.hand_tiles:
        score = 0.0
        key = tile.base_key

        if primary_plan is not None and primary_objective is not None:
            plan_weight = 0.75 + primary_objective.han * 0.22 + primary_objective.probability / 140
            if key in primary_plan.useful_keys:
                score -= 70 * plan_weight
            else:
                score += 78 * plan_weight
            if key in primary_plan.wanted_keys:
                score -= 18 * plan_weight

        for plan in support_plans:
            support_han = yaku_han_value(plan.yaku_key, state.is_closed)
            support_weight = 0.22 + support_han * 0.06
            if flexible_mode:
                support_weight *= max(0.35, min(1.0, plan.confidence / 85))
                if plan.yaku_key in {"honitsu", "chinitsu", "chanta", "junchan"} and plan.confidence < 78:
                    support_weight *= 0.45
            if key in plan.useful_keys:
                score -= 16 * support_weight
            else:
                score += 8 * support_weight

        if guaranteed_yaku:
            if key in completion_keys:
                score += 44
            else:
                score -= 18
        if key in efficiency_keys:
            score += 38 if primary_plan is None else 24
        if key in plan_discard_keys:
            score += 14 if primary_plan is None else 20

        remaining = remove_one_tile_instance(state.hand_tiles, tile)
        shanten_after = standard_shanten_number(remaining, state.open_melds)
        improving = improving_tiles_for_completion(state, remaining, shanten_after)
        ukeire_after = sum(available for _tile, available in improving)
        shape_after = hand_shape_profile(remaining, state.open_melds)
        shape_delta = shape_after.score - current_shape.score
        score -= max(0, shanten_after) * (34 if primary_plan is None else 24)
        score += min(56 if primary_plan is None else 40, ukeire_after * (1.9 if primary_plan is None else 1.4))
        score += shape_after.score * (0.38 if primary_plan is None else 0.28)
        score += shape_delta * 1.6

        if tile.red and non_red_exists:
            score -= 70
        if key in dora_keys and non_dora_exists:
            score -= 62
        if primary_plan is not None:
            if primary_plan.yaku_key == "honroutou":
                score += 92 if not is_terminal_or_honor(tile) else -58
                if tile.is_honor and counts[key] >= 2:
                    score -= 38
            elif primary_plan.yaku_key in {"yakuhai_dragon", "haku", "hatsu", "chun", "seat_wind", "prevalent_wind"}:
                if key in primary_plan.useful_keys:
                    score -= 72
                elif tile.is_honor and counts[key] >= 2:
                    score -= 46
                elif tile.is_honor:
                    score += 8
            elif primary_plan.yaku_key == "ittsu":
                if key in primary_plan.useful_keys:
                    score -= 44
                elif tile.is_honor:
                    score += 28
        if counts[key] >= 2:
            score -= 30
        if counts[key] >= 3:
            score -= 18
        if tile.is_suited and suited_tile_has_connection(tile, counts):
            score -= 10

        score -= furiten_discard_penalty(state, tile) * 1.2
        danger = cached_tile_danger_score(state, tile, danger_context, danger_by_key)
        score -= danger * risk_weight

        scored.append((score, -hand_index.get(id(tile), 999), tile))

    scored.sort(reverse=True)
    candidates = [tile for _score, _index, tile in scored]
    return avoid_furiten_discards_when_possible(state, candidates)[:4]


def chitoitsu_discard_candidates(state: HandState, support_plans: list[YakuPlan]) -> list[Tile]:
    if not state.is_closed:
        return []

    counts = canonical_counter(state.hand_tiles)
    visible_counts = canonical_counter(state.all_visible_tiles)
    dora_keys = dora_base_keys(state)
    support_useful = set().union(*(set(plan.useful_keys) for plan in support_plans)) if support_plans else set()
    hand_index = {id(tile): index for index, tile in enumerate(state.hand_tiles)}
    scored: list[tuple[int, int, Tile]] = []

    for tile in state.hand_tiles:
        key = tile.base_key
        score = 0
        if counts[key] >= 2:
            score -= 120
        else:
            remaining = max(0, 4 - visible_counts[key])
            score += 55 - remaining * 12
            if tile.is_honor or tile.is_terminal:
                score -= 16
            if key in support_useful:
                score -= 10
        if tile.red:
            score -= 90
        if key in dora_keys:
            score -= 80
        score -= furiten_discard_penalty(state, tile)
        scored.append((score, -hand_index.get(id(tile), 999), tile))

    scored.sort(reverse=True)
    candidates = [tile for _score, _index, tile in scored]
    return avoid_furiten_discards_when_possible(state, candidates)[:4]


def tile_danger_score(state: HandState, tile: Tile, context: DangerContext | None = None) -> int:
    context = context or build_danger_context(state)
    key = tile.base_key
    visible_counts = context.visible_counts
    opponent_discards = context.opponent_discards
    danger = 50
    for player in context.riichi_threats:
        all_safe = context.riichi_all_safe.get(player, set())
        after_riichi_safe = context.riichi_after_safe.get(player, set())
        if key in after_riichi_safe:
            danger -= 46
        elif key in all_safe:
            danger -= 34
        else:
            danger += 26
    if opponent_discards:
        genbutsu_players = sum(1 for keys in opponent_discards.values() if key in keys)
        danger -= genbutsu_players * 20
        if genbutsu_players == len(opponent_discards):
            danger -= 18
    if tile.is_honor:
        danger -= visible_counts[key] * 13
        if visible_counts[key] >= 3:
            danger -= 25
    elif tile.is_suited:
        value = int(tile.value)
        if value in {1, 9}:
            danger -= 12
        elif value in {2, 8}:
            danger -= 5
        elif value == 5:
            danger += 12
        else:
            danger += 5
        if any(suji_protected_by_discards(tile, keys) for keys in opponent_discards.values()):
            danger -= 8
        for player in context.riichi_threats:
            discarded_keys = context.riichi_all_safe.get(player, set())
            if key not in discarded_keys and suji_protected_by_discards(tile, discarded_keys):
                danger -= 10
    danger += max(0, 4 - visible_counts[key]) * 3
    if tile.red:
        danger += 18
    if key in context.dora_keys:
        danger += 22
    return max(0, min(100, danger))


def cached_tile_danger_score(
    state: HandState,
    tile: Tile,
    context: DangerContext,
    cache: dict[str, int],
) -> int:
    key = tile.base_key
    if key not in cache:
        cache[key] = tile_danger_score(state, tile, context)
    return cache[key]


def suji_protected_by_discards(tile: Tile, discarded_keys: set[str]) -> bool:
    if not tile.is_suited:
        return False
    value = int(tile.value)
    suit = tile.suit
    suji_map = {
        4: (1, 7),
        5: (2, 8),
        6: (3, 9),
    }
    return any(f"{suit}_{number}" in discarded_keys for number in suji_map.get(value, ()))


def has_guaranteed_yaku(state: HandState, matches: list[YakuMatch]) -> bool:
    """Return true when the current visible hand already carries an open-safe yaku.

    Shape estimates such as Half Flush should not override hand completion once
    a yakuhai triplet is already locked in. Those yakus survive any normal draw
    and discard, so the next objective becomes finishing four groups and a pair.
    """

    guaranteed_keys = {
        "yakuhai_dragon",
        "haku",
        "hatsu",
        "chun",
        "seat_wind",
        "prevalent_wind",
    }
    return any(match.confidence >= 100 and match.yaku.key in guaranteed_keys for match in matches)


def own_discard_keys(state: HandState) -> set[str]:
    return set(canonical_counter(state.discarded_by_player.get("principal", [])).keys())


def winning_wait_keys_for_hand(state: HandState, hand_tiles: list[Tile]) -> frozenset[str]:
    if best_shanten_number(hand_tiles, state.open_melds) != 0:
        return frozenset()

    seen_counts = canonical_counter(state.all_visible_tiles)
    waits = []
    for key in ALL_BASE_KEYS:
        if seen_counts[key] >= 4:
            continue
        tile = representative_tile(key)
        if best_shanten_number([*hand_tiles, tile], state.open_melds) == -1:
            waits.append(key)
    return frozenset(waits)


def current_furiten_wait_keys(state: HandState) -> frozenset[str]:
    own_keys = own_discard_keys(state)
    if not own_keys:
        return frozenset()
    return winning_wait_keys_for_hand(state, state.hand_tiles) & own_keys


def winning_wait_keys_after_discard(state: HandState, discarded_tile: Tile) -> frozenset[str]:
    remaining = remove_one_tile_instance(state.hand_tiles, discarded_tile)
    return winning_wait_keys_for_hand(state, remaining)


def riichi_discard_candidates(state: HandState) -> list[Tile]:
    if not state.is_closed or state.missing_count:
        return []

    dora_keys = dora_base_keys(state)
    seen: set[str] = set()
    scored: list[tuple[int, int, int, int, Tile]] = []
    for index, tile in enumerate(state.hand_tiles):
        if tile.base_key in seen:
            continue
        seen.add(tile.base_key)
        remaining = remove_one_tile_instance(state.hand_tiles, tile)
        if best_shanten_number(remaining, state.open_melds) != 0:
            continue
        waits = winning_wait_keys_for_hand(state, remaining)
        if not waits:
            continue
        furiten_waits = waits & own_discard_keys(state)
        visible_counts = canonical_counter(state.all_visible_tiles)
        ukeire = sum(max(0, 4 - visible_counts[key]) for key in waits)
        keep_penalty = 0
        if tile.red:
            keep_penalty += 80
        if tile.base_key in dora_keys:
            keep_penalty += 70
        scored.append((len(furiten_waits), -ukeire, keep_penalty, index, tile))

    scored.sort(key=lambda item: item[:4])
    no_furiten = [item for item in scored if item[0] == 0]
    selected = no_furiten or scored
    return [tile for _furiten, _ukeire, _keep_penalty, _index, tile in selected[:4]]


def furiten_wait_keys_after_discard(state: HandState, discarded_tile: Tile) -> frozenset[str]:
    own_keys = own_discard_keys(state)
    if not own_keys:
        return frozenset()
    return winning_wait_keys_after_discard(state, discarded_tile) & own_keys


def furiten_discard_penalty(state: HandState, discarded_tile: Tile) -> int:
    waits = furiten_wait_keys_after_discard(state, discarded_tile)
    if not waits:
        return 0
    return min(120, 80 + 12 * (len(waits) - 1))


def update_furiten_summary(state: HandState) -> None:
    current_waits = current_furiten_wait_keys(state)
    state.current_furiten_waits = [
        TileNeed(
            representative_tile(key),
            100,
            "ja esta nos seus descartes; a mao atual esta em furiten",
        )
        for key in sorted(current_waits)
    ]

    if not state.discard_candidates:
        state.furiten_waits = []
        return

    wait_reasons: dict[str, list[str]] = {}
    for discarded_tile in state.discard_candidates[:4]:
        for key in sorted(furiten_wait_keys_after_discard(state, discarded_tile)):
            wait_reasons.setdefault(key, []).append(f"apos descartar {discarded_tile.compact}")
    state.furiten_waits = [
        TileNeed(
            representative_tile(key),
            100,
            f"esta nos seus descartes; {'; '.join(reasons[:3])}",
        )
        for key, reasons in sorted(wait_reasons.items())
    ]
    if (state.current_furiten_waits or state.furiten_waits) and "furiten" not in state.discard_reason.lower():
        state.discard_reason = f"{state.discard_reason}; risco de furiten se aceitar esta espera"


def avoid_furiten_discards_when_possible(state: HandState, candidates: list[Tile]) -> list[Tile]:
    if not candidates:
        return []
    no_furiten = [tile for tile in candidates if furiten_discard_penalty(state, tile) == 0]
    return no_furiten or candidates


def suppress_weaker_matches(matches: list[YakuMatch]) -> list[YakuMatch]:
    keys = {match.yaku.key for match in matches}
    suppressed = set()
    if "junchan" in keys:
        suppressed.add("chanta")
    if "chinitsu" in keys:
        suppressed.add("honitsu")
    if "ryanpeikou" in keys:
        suppressed.add("iipeikou")
    return [match for match in matches if match.yaku.key not in suppressed]


def tenpai_lock_discard_candidates(
    state: HandState,
    plans: list[YakuPlan],
    guaranteed_yaku: bool,
) -> list[Tile]:
    progress = hand_completion_progress(state)
    if progress.shanten != 0 or not state.hand_tiles:
        return []
    if state.open_melds and not guaranteed_yaku and not open_hand_has_viable_yaku_plan(state, plans):
        return []

    dora_keys = dora_base_keys(state)
    visible_counts = canonical_counter(state.all_visible_tiles)
    danger_context = build_danger_context(state)
    danger_by_key: dict[str, int] = {}
    useful_keys = set().union(*(set(plan.useful_keys) for plan in plans)) if plans else set()
    hand_index = {id(tile): index for index, tile in enumerate(state.hand_tiles)}
    non_dora_exists = any(tile.base_key not in dora_keys for tile in state.hand_tiles)
    non_red_exists = any(not tile.red for tile in state.hand_tiles)
    scored: list[tuple[int, int, int, int, int, Tile]] = []

    for tile in state.hand_tiles:
        remaining = remove_one_tile_instance(state.hand_tiles, tile)
        if best_shanten_number(remaining, state.open_melds) != 0:
            continue
        waits = winning_wait_keys_for_hand(state, remaining)
        if not waits:
            continue
        furiten_count = len(waits & own_discard_keys(state))
        ukeire = sum(max(0, 4 - visible_counts[key]) for key in waits)
        protection_penalty = 0
        if tile.red and non_red_exists:
            protection_penalty += 90
        if tile.base_key in dora_keys and non_dora_exists:
            protection_penalty += 80
        if tile.base_key in useful_keys:
            protection_penalty += 18
        danger = cached_tile_danger_score(state, tile, danger_context, danger_by_key)
        scored.append(
            (
                furiten_count,
                -ukeire,
                danger,
                protection_penalty,
                hand_index.get(id(tile), 999),
                tile,
            )
        )

    if not scored:
        return []
    scored.sort(key=lambda item: item[:5])
    best_furiten = scored[0][0]
    selected = [item for item in scored if item[0] == best_furiten]
    return [tile for *_rest, tile in selected[:4]]


def completion_discard_candidates(state: HandState) -> list[Tile]:
    tiles = list(state.hand_tiles)
    if not tiles:
        return []

    dora_keys = dora_base_keys(state)
    counts = canonical_counter(tiles)
    visible_counts = canonical_counter(state.all_visible_tiles)
    non_dora_exists = any(tile.base_key not in dora_keys for tile in tiles)
    non_red_exists = any(not tile.red for tile in tiles)
    current_shape = hand_shape_profile(tiles, state.open_melds)
    scored: list[tuple[int, int, int, int, int, int, Tile]] = []

    for index, tile in enumerate(tiles):
        remaining = remove_one_tile_instance(tiles, tile)
        shanten = standard_shanten_number(remaining, state.open_melds)
        improving = improving_tiles_for_completion(state, remaining, shanten)
        ukeire = sum(available for _tile, available in improving)
        shape_after = hand_shape_profile(remaining, state.open_melds)
        shape_loss = current_shape.score - shape_after.score
        furiten_penalty = furiten_discard_penalty(state, tile)
        protection_penalty = 0
        if tile.red and non_red_exists:
            protection_penalty += 80
        if tile.base_key in dora_keys and non_dora_exists:
            protection_penalty += 70
        if tile.is_honor and counts[tile.base_key] >= 2 and max(0, 4 - visible_counts[tile.base_key]) > 0:
            protection_penalty += 90
        scored.append((shanten, furiten_penalty, -ukeire, shape_loss, protection_penalty, index, tile))

    scored.sort(key=lambda item: item[:6])
    best_shanten, best_furiten_penalty, best_negative_ukeire = scored[0][0], scored[0][1], scored[0][2]
    best_ukeire = -best_negative_ukeire
    candidates = [
        tile
        for shanten, furiten_penalty, negative_ukeire, _shape_loss, penalty, _index, tile in scored
        if shanten == best_shanten and -negative_ukeire >= max(0, best_ukeire - 4) and penalty <= 80
        and furiten_penalty <= best_furiten_penalty
    ][:4]
    return avoid_furiten_discards_when_possible(state, candidates)[:4]


def helpful_tiles_for_completion_after_discard(state: HandState, discarded_tile: Tile) -> list[TileNeed]:
    remaining = remove_one_tile_instance(state.hand_tiles, discarded_tile)
    current_shanten = standard_shanten_number(remaining, state.open_melds)
    improving = improving_tiles_for_completion(state, remaining, current_shanten)
    needs = []
    for tile, available in improving[:8]:
        score = min(100, max(10, available * 25 + max(0, 3 - current_shanten) * 10))
        reason = f"avanca shanten; restam {available}"
        needs.append(TileNeed(tile, score, reason))
    return needs


def improving_tiles_for_completion(
    state: HandState,
    hand_tiles: list[Tile],
    current_shanten: int,
) -> list[tuple[Tile, int]]:
    seen_counts = canonical_counter(state.all_visible_tiles)
    improvements: list[tuple[Tile, int, int]] = []
    for key in ALL_BASE_KEYS:
        available = max(0, 4 - seen_counts[key])
        if available <= 0:
            continue
        tile = representative_tile(key)
        next_shanten = standard_shanten_number([*hand_tiles, tile], state.open_melds)
        if next_shanten < current_shanten:
            improvements.append((tile, available, next_shanten))

    improvements.sort(key=lambda item: (item[2], -item[1], item[0].base_key))
    return [(tile, available) for tile, available, _shanten in improvements]


def standard_shanten_number(hand_tiles: list[Tile], open_melds: list[Meld]) -> int:
    open_meld_count = count_open_melds_for_shanten(open_melds)
    counts = canonical_counter(hand_tiles)
    count_tuple = tuple(counts[key] for key in ALL_BASE_KEYS)
    return standard_shanten_from_counts(count_tuple, open_meld_count)


def best_shanten_number(hand_tiles: list[Tile], open_melds: list[Meld]) -> int:
    standard = standard_shanten_number(hand_tiles, open_melds)
    if open_melds:
        return standard
    return min(standard, seven_pairs_shanten_number(hand_tiles))


def hand_has_complete_shape(state: HandState) -> bool:
    return best_shanten_number(state.hand_tiles, state.open_melds) < 0


def seven_pairs_shanten_number(hand_tiles: list[Tile]) -> int:
    counts = canonical_counter(hand_tiles)
    pair_count = sum(1 for count in counts.values() if count >= 2)
    unique_count = sum(1 for count in counts.values() if count > 0)
    return 6 - pair_count + max(0, 7 - unique_count)


def count_open_melds_for_shanten(open_melds: list[Meld]) -> int:
    return sum(
        1
        for meld in open_melds
        if meld.kind in (MeldKind.CHI, MeldKind.PON, MeldKind.KAN)
        or (meld.kind == MeldKind.UNKNOWN and len(meld.tiles) in (3, 4))
    )


def hand_completion_progress(state: HandState) -> HandProgress:
    shanten = best_shanten_number(state.hand_tiles, state.open_melds)
    if shanten < 0:
        return HandProgress(shanten, 100, "completa")
    if shanten == 0:
        return HandProgress(shanten, 90, "tenpai")
    percent = max(0, min(89, round((8 - shanten) * 100 / 9)))
    return HandProgress(shanten, percent, f"{shanten}-shanten")


def estimated_hand_value(state: HandState) -> HandValueEstimate:
    yakus = confirmed_scoring_yakus(state)
    bonus_items = bonus_han_items(state)
    yaku_han = sum(value for _name, value in yakus)
    bonus_han = sum(value for _name, value in bonus_items)
    total_han = yaku_han + bonus_han
    fu = estimate_fu(state, yakus)
    dealer = state.player_winds.get("principal") == "east"
    if yaku_han <= 0:
        return HandValueEstimate(
            0,
            fu,
            tuple(),
            bonus_han,
            tuple(bonus_items),
            0,
            "-",
            "",
            dealer,
            "sem yaku confirmado ainda",
        )

    ron_points, tsumo_points, limit_name = score_points(total_han, fu, dealer)
    return HandValueEstimate(
        total_han,
        fu,
        tuple(yakus),
        bonus_han,
        tuple(bonus_items),
        ron_points,
        tsumo_points,
        limit_name,
        dealer,
        "estimativa: fu simplificado; riichi conta quando declaravel/planejado em tenpai",
    )


def confirmed_scoring_yakus(state: HandState) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    seen: set[str] = set()
    include_planned_riichi = riichi_counts_for_value_estimate(state)
    specific_dragon_keys = {
        match.yaku.key
        for match in state.likely_yaku
        if match.confidence >= 100 and match.yaku.key in {"haku", "hatsu", "chun"}
    }
    for match in state.likely_yaku:
        if match.yaku.key == "riichi":
            if not include_planned_riichi or match.confidence < 90 or match.yaku.key in seen:
                continue
            items.append((match.yaku.name, yaku_han_value(match.yaku.key, state.is_closed)))
            seen.add(match.yaku.key)
            continue
        if match.confidence < 100 or match.yaku.situational:
            continue
        if match.yaku.key in {"dora", "aka_dora"} or match.yaku.key in seen:
            continue
        if match.yaku.key == "yakuhai_dragon" and specific_dragon_keys:
            continue
        han = yaku_han_value(match.yaku.key, state.is_closed)
        if han <= 0:
            continue
        items.append((match.yaku.name, han))
        seen.add(match.yaku.key)
    return items


def riichi_counts_for_value_estimate(state: HandState) -> bool:
    if not state.is_closed:
        return False
    if state.riichi_button_visible:
        return True
    return (
        state.attack_objective is not None
        and state.attack_objective.yaku_key == "riichi"
        and hand_completion_progress(state).shanten == 0
    )


def yaku_han_value(yaku_key: str, closed: bool) -> int:
    open_adjusted = {
        "chanta": (2, 1),
        "junchan": (3, 2),
        "sanshoku_doujun": (2, 1),
        "ittsu": (2, 1),
        "honitsu": (3, 2),
        "chinitsu": (6, 5),
    }
    fixed = {
        "riichi": 1,
        "double_riichi": 2,
        "menzen_tsumo": 1,
        "ippatsu": 1,
        "pinfu": 1,
        "iipeikou": 1,
        "ryanpeikou": 3,
        "tanyao": 1,
        "seat_wind": 1,
        "prevalent_wind": 1,
        "yakuhai_dragon": 1,
        "haku": 1,
        "hatsu": 1,
        "chun": 1,
        "toitoi": 2,
        "sanankou": 2,
        "sankantsu": 2,
        "honroutou": 2,
        "shousangen": 2,
        "sanshoku_doukou": 2,
        "chitoitsu": 2,
    }
    yakuman = {
        "kokushi_musou",
        "kokushi_musou_13",
        "suuankou",
        "suuankou_tanki",
        "daisangen",
        "shousuushii",
        "daisuushii",
        "tsuuiisou",
        "chinroutou",
        "ryuuiisou",
        "chuuren_poutou",
        "junsei_chuuren_poutou",
        "suukantsu",
        "tenhou",
        "chiihou",
        "renhou",
    }
    if yaku_key in open_adjusted:
        closed_han, open_han = open_adjusted[yaku_key]
        return closed_han if closed else open_han
    if yaku_key in yakuman:
        return 13
    return fixed.get(yaku_key, 0)


def bonus_han_items(state: HandState) -> list[tuple[str, int]]:
    items: list[tuple[str, int]] = []
    known_tiles = state.all_known_tiles
    red_count = sum(1 for tile in known_tiles if tile.red)
    if red_count:
        items.append(("Aka dora", red_count))
    dora_keys = dora_base_keys(state)
    dora_count = sum(1 for tile in known_tiles if tile.base_key in dora_keys)
    if dora_count:
        items.append(("Dora", dora_count))
    return items


def estimate_fu(state: HandState, yakus: list[tuple[str, int]]) -> int:
    yaku_names = {name for name, _han in yakus}
    if "Seven Pairs" in yaku_names:
        return 25
    fu = 30
    counts = canonical_counter(state.all_known_tiles)
    for key in (*HONORS, *(f"{suit}_{value}" for suit in SUITS for value in (1, 9))):
        count = counts[key]
        if count >= 3:
            fu += 4 if key in HONORS else 2
    return max(30, round_up_10(fu))


def score_points(han: int, fu: int, dealer: bool) -> tuple[int, str, str]:
    base_points, limit_name = base_points_for_score(han, fu)
    ron_multiplier = 6 if dealer else 4
    ron = round_up_100(base_points * ron_multiplier)
    if dealer:
        each = round_up_100(base_points * 2)
        tsumo = f"{each} all"
    else:
        non_dealer = round_up_100(base_points)
        dealer_payment = round_up_100(base_points * 2)
        tsumo = f"{non_dealer}/{dealer_payment}"
    return ron, tsumo, limit_name


def base_points_for_score(han: int, fu: int) -> tuple[int, str]:
    if han >= 13:
        return 8000, "Yakuman"
    if han >= 11:
        return 6000, "Sanbaiman"
    if han >= 8:
        return 4000, "Baiman"
    if han >= 6:
        return 3000, "Haneman"
    if han >= 5 or (han == 4 and fu >= 40) or (han == 3 and fu >= 70):
        return 2000, "Mangan"
    return fu * (2 ** (han + 2)), ""


def round_up_10(value: int) -> int:
    return ((value + 9) // 10) * 10


def round_up_100(value: int) -> int:
    return ((value + 99) // 100) * 100


@lru_cache(maxsize=20000)
def standard_shanten_from_counts(count_tuple: tuple[int, ...], open_meld_count: int) -> int:
    counts = list(count_tuple)
    best = 8

    def visit(melds: int, taatsu: int, pairs: int) -> None:
        nonlocal best
        max_closed_melds = max(0, 4 - open_meld_count)
        melds = min(melds, max_closed_melds)
        effective_taatsu = min(taatsu, max(0, max_closed_melds - melds))
        pair_bonus = 1 if pairs else 0
        best = min(best, 8 - 2 * (open_meld_count + melds) - effective_taatsu - pair_bonus)

    def first_nonzero_index() -> int | None:
        return next((index for index, count in enumerate(counts) if count > 0), None)

    seen: set[tuple[tuple[int, ...], int, int, int]] = set()

    def dfs(melds: int, taatsu: int, pairs: int) -> None:
        visit(melds, taatsu, pairs)
        memo_key = (tuple(counts), melds, taatsu, pairs)
        if memo_key in seen:
            return
        seen.add(memo_key)

        index = first_nonzero_index()
        if index is None:
            return

        key = ALL_BASE_KEYS[index]
        is_suited_key = key[:3] in SUITS
        suit = key[:3] if is_suited_key else ""
        value = int(key.split("_", 1)[1]) if is_suited_key else 0

        if counts[index] >= 3:
            counts[index] -= 3
            dfs(melds + 1, taatsu, pairs)
            counts[index] += 3

        if is_suited_key:
            key2 = f"{suit}_{value + 1}"
            key3 = f"{suit}_{value + 2}"
            index2 = BASE_KEY_INDEX.get(key2, -1)
            index3 = BASE_KEY_INDEX.get(key3, -1)
            if value <= 7 and counts[index2] > 0 and counts[index3] > 0:
                counts[index] -= 1
                counts[index2] -= 1
                counts[index3] -= 1
                dfs(melds + 1, taatsu, pairs)
                counts[index] += 1
                counts[index2] += 1
                counts[index3] += 1

        if counts[index] >= 2:
            counts[index] -= 2
            dfs(melds, taatsu, pairs + 1)
            counts[index] += 2

        if is_suited_key:
            for offset in (1, 2):
                other_value = value + offset
                other_key = f"{suit}_{other_value}"
                if other_value <= 9 and other_key in BASE_KEY_INDEX:
                    other_index = BASE_KEY_INDEX[other_key]
                    if counts[other_index] <= 0:
                        continue
                    counts[index] -= 1
                    counts[other_index] -= 1
                    dfs(melds, taatsu + 1, pairs)
                    counts[index] += 1
                    counts[other_index] += 1

        counts[index] -= 1
        dfs(melds, taatsu, pairs)
        counts[index] += 1

    dfs(0, 0, 0)
    return max(-1, best)


def remove_one_tile_instance(tiles: list[Tile], discarded_tile: Tile) -> list[Tile]:
    remaining = list(tiles)
    for index, tile in enumerate(remaining):
        if tile is discarded_tile:
            remaining.pop(index)
            return remaining
    for index, tile in enumerate(remaining):
        if tile.base_key == discarded_tile.base_key:
            remaining.pop(index)
            return remaining
    return remaining


def hand_shape_profile(hand_tiles: list[Tile], open_melds: list[Meld]) -> HandShapeProfile:
    counts = canonical_counter(hand_tiles)
    count_tuple = tuple(counts[key] for key in ALL_BASE_KEYS)
    open_blocks = count_open_melds_for_shanten(open_melds)
    return hand_shape_profile_from_counts(count_tuple, open_blocks)


@lru_cache(maxsize=8192)
def hand_shape_profile_from_counts(count_tuple: tuple[int, ...], open_blocks: int) -> HandShapeProfile:
    suit_profiles = [
        best_suit_shape_counts(tuple(count_tuple[BASE_KEY_INDEX[f"{suit}_{value}"]] for value in range(1, 10)))
        for suit in SUITS
    ]
    honors = honor_shape_counts(tuple(count_tuple[BASE_KEY_INDEX[key]] for key in HONORS))
    shape = ShapeCounts(complete=open_blocks)
    for suit_profile in suit_profiles:
        shape += suit_profile
    shape += honors

    complete_blocks = shape.complete
    blocks = complete_blocks + shape.pairs + shape.ryanmen + shape.ryankan + shape.kanchan + shape.penchan
    useful_taatsu = shape.ryanmen + shape.ryankan + shape.kanchan + shape.penchan
    excess_pairs = max(0, shape.pairs - max(1, 5 - complete_blocks - useful_taatsu))
    block_shortage = max(0, 5 - blocks)
    bad_taatsu = shape.kanchan + shape.penchan
    isolated_penalty = shape.isolated_honor * 7 + shape.isolated_terminal * 5 + shape.isolated_middle * 3
    raw_score = (
        42
        + complete_blocks * 10
        + min(shape.pairs, 1) * 7
        + max(0, shape.pairs - 1) * 2
        + shape.ryanmen * 9
        + shape.ryankan * 8
        + shape.kanchan * 4
        + shape.penchan * 2
        - block_shortage * 13
        - excess_pairs * 5
        - bad_taatsu * 2
        - isolated_penalty
    )
    score = max(0, min(100, raw_score))
    summary_parts = [
        f"{blocks} blocos",
        f"{complete_blocks} grupos",
        f"{shape.ryanmen} ryanmen",
        f"{shape.kanchan} kanchan",
        f"{shape.penchan} penchan",
    ]
    if shape.ryankan:
        summary_parts.append(f"{shape.ryankan} ryankan")
    if shape.pairs:
        summary_parts.append(f"{shape.pairs} par(es)")
    isolated = shape.isolated_middle + shape.isolated_terminal + shape.isolated_honor
    if isolated:
        summary_parts.append(f"{isolated} isolada(s)")
    if block_shortage:
        summary_parts.append(f"faltam {block_shortage} bloco(s)")
    return HandShapeProfile(
        score=score,
        blocks=blocks,
        complete_blocks=complete_blocks,
        ryanmen=shape.ryanmen,
        ryankan=shape.ryankan,
        kanchan=shape.kanchan,
        penchan=shape.penchan,
        pairs=shape.pairs,
        excess_pairs=excess_pairs,
        isolated_middle=shape.isolated_middle,
        isolated_terminal=shape.isolated_terminal,
        isolated_honor=shape.isolated_honor,
        summary=", ".join(summary_parts),
    )


@lru_cache(maxsize=4096)
def best_suit_shape_counts(counts: tuple[int, ...]) -> ShapeCounts:
    candidates = suit_shape_candidates(counts)
    return max(candidates, key=shape_counts_rank, default=ShapeCounts())


@lru_cache(maxsize=4096)
def suit_shape_candidates(counts: tuple[int, ...]) -> tuple[ShapeCounts, ...]:
    seen: set[tuple[int, ...]] = set()
    results: list[ShapeCounts] = []

    def dfs(current_counts: tuple[int, ...], shape: ShapeCounts) -> None:
        key = (*current_counts, *shape.__dict__.values())
        if key in seen:
            return
        seen.add(key)

        try:
            index = next(i for i, count in enumerate(current_counts) if count > 0)
        except StopIteration:
            results.append(shape)
            return

        value = index + 1

        def consume(items: tuple[int, ...], added: ShapeCounts) -> None:
            mutable = list(current_counts)
            for item in items:
                if not 0 <= item < 9 or mutable[item] <= 0:
                    return
                mutable[item] -= 1
            dfs(tuple(mutable), shape + added)

        if current_counts[index] >= 3:
            consume((index, index, index), ShapeCounts(complete=1))
        if value <= 7 and current_counts[index + 1] and current_counts[index + 2]:
            consume((index, index + 1, index + 2), ShapeCounts(complete=1))
        if value <= 5 and current_counts[index + 2] and current_counts[index + 4]:
            consume((index, index + 2, index + 4), ShapeCounts(ryankan=1))
        if current_counts[index] >= 2:
            consume((index, index), ShapeCounts(pairs=1))
        if value <= 8 and current_counts[index + 1]:
            if value == 1 or value == 8:
                consume((index, index + 1), ShapeCounts(penchan=1))
            else:
                consume((index, index + 1), ShapeCounts(ryanmen=1))
        if value <= 7 and current_counts[index + 2]:
            consume((index, index + 2), ShapeCounts(kanchan=1))

        single = ShapeCounts(isolated_terminal=1) if value in TERMINAL_NUMBERS else ShapeCounts(isolated_middle=1)
        consume((index,), single)

    dfs(counts, ShapeCounts())
    unique = list({shape: None for shape in results}.keys())
    unique.sort(key=shape_counts_rank, reverse=True)
    return tuple(unique[:80])


def shape_counts_rank(shape: ShapeCounts) -> tuple[int, int, int, int, int, int]:
    blocks = shape.complete + shape.pairs + shape.ryanmen + shape.ryankan + shape.kanchan + shape.penchan
    bad = shape.kanchan + shape.penchan
    isolated = shape.isolated_middle + shape.isolated_terminal + shape.isolated_honor
    score = (
        shape.complete * 12
        + shape.ryanmen * 8
        + shape.ryankan * 7
        + shape.pairs * 5
        + shape.kanchan * 3
        + shape.penchan
        - bad * 2
        - isolated * 4
    )
    return (score, blocks, shape.complete, shape.ryanmen + shape.ryankan, -bad, -isolated)


def honor_shape_counts(counts: tuple[int, ...]) -> ShapeCounts:
    shape = ShapeCounts()
    for count in counts:
        complete, remainder = divmod(count, 3)
        shape += ShapeCounts(complete=complete)
        if remainder == 2:
            shape += ShapeCounts(pairs=1)
        elif remainder == 1:
            shape += ShapeCounts(isolated_honor=1)
    return shape


def tile_shape_role(tile: Tile, counts: Counter[str]) -> str:
    key = tile.base_key
    if counts[key] >= 3:
        return "trinca/grupo completo"
    if counts[key] >= 2:
        return "par"
    if tile.is_honor:
        return "honra isolada"
    if not tile.is_suited:
        return "isolada"
    value = int(tile.value)
    suit = tile.suit
    has = lambda number: 1 <= number <= 9 and counts[f"{suit}_{number}"] > 0
    if has(value - 1) and 2 <= value - 1 <= 7:
        return "ryanmen"
    if has(value + 1) and 2 <= value <= 7:
        return "ryanmen"
    if (value in {1, 2} and (has(1) and has(2))) or (value in {8, 9} and (has(8) and has(9))):
        return "penchan"
    if has(value - 2) or has(value + 2):
        if (has(value - 2) and has(value + 2)) or (has(value - 4) and has(value - 2)) or (has(value + 2) and has(value + 4)):
            return "ryankan"
        return "kanchan"
    if value in TERMINAL_NUMBERS:
        return "terminal isolado"
    return "isolada"


def tile_in_good_shape(tile: Tile, counts: Counter[str]) -> bool:
    return tile_shape_role(tile, counts) in {"trinca/grupo completo", "par", "ryanmen", "ryankan"}


def inefficient_tiles_for_completion(state: HandState) -> list[Tile]:
    """Return tiles that are least connected to completing the visible hand.

    This is hand-efficiency guidance, not yaku detection. A yaku can already be
    present, such as a dragon triplet, while unrelated isolated tiles still make
    it harder to close the hand. We avoid marking tiles that are already part of
    visible pairs/triplets or useful suited connections.
    """

    tiles = list(state.hand_tiles)
    if not tiles:
        return []

    counts = canonical_counter(tiles)
    dora_keys = dora_base_keys(state)
    weak_tiles: list[Tile] = []
    for tile in tiles:
        if tile.red:
            continue
        if tile.base_key in dora_keys:
            continue
        if tile_in_good_shape(tile, counts):
            continue
        if tile.is_honor:
            weak_tiles.append(tile)
            continue
        if tile.is_suited and tile_shape_role(tile, counts) in {"isolada", "terminal isolado", "penchan"}:
            weak_tiles.append(tile)

    return weak_tiles


def ranked_discard_candidates(state: HandState, plans: list[YakuPlan]) -> list[Tile]:
    tiles = list(state.hand_tiles)
    if not tiles:
        return []

    counts = canonical_counter(tiles)
    useful_keys = set().union(*(set(plan.useful_keys) for plan in plans)) if plans else set()
    useful_plan_count = Counter(key for plan in plans for key in plan.useful_keys)
    dora_keys = dora_base_keys(state)
    non_dora_exists = any(tile.base_key not in dora_keys for tile in tiles)
    non_red_exists = any(not tile.red for tile in tiles)
    current_shape = hand_shape_profile(tiles, state.open_melds)
    scored: list[tuple[int, int, Tile]] = []

    for index, tile in enumerate(tiles):
        score = 0
        if tile.red and non_red_exists:
            score -= 80
        if tile.base_key in dora_keys and non_dora_exists:
            score -= 70
        if useful_keys and tile.base_key not in useful_keys:
            score += 45
        else:
            score -= 8 * useful_plan_count[tile.base_key]

        if tile.is_honor:
            score += 28
            if counts[tile.base_key] >= 2:
                score -= 55
        elif tile.is_suited:
            if not suited_tile_has_connection(tile, counts):
                score += 30
            if tile.is_terminal:
                score += 4

        if counts[tile.base_key] >= 2:
            score -= 24
        if counts[tile.base_key] >= 3:
            score -= 18

        shape_after = hand_shape_profile(remove_one_tile_instance(tiles, tile), state.open_melds)
        score -= round(shape_after.score * 0.35 + (shape_after.score - current_shape.score) * 1.4)
        score -= discard_future_score(state, tile) // 4
        score -= furiten_discard_penalty(state, tile)

        scored.append((score, -index, tile))

    if not scored:
        return []

    scored.sort(reverse=True)
    best_score = scored[0][0]
    candidates = [tile for score, _index, tile in scored if score >= best_score - 6][:4]
    return avoid_furiten_discards_when_possible(state, candidates)[:4]


def sort_discard_candidates_by_badness(state: HandState, candidates: list[Tile], plans: list[YakuPlan]) -> list[Tile]:
    if not candidates:
        return []

    hand_index = {id(tile): index for index, tile in enumerate(state.hand_tiles)}
    counts = canonical_counter(state.hand_tiles)
    useful_keys = set().union(*(set(plan.useful_keys) for plan in plans)) if plans else set()
    useful_plan_count = Counter(key for plan in plans for key in plan.useful_keys)
    dora_keys = dora_base_keys(state)
    non_dora_exists = any(tile.base_key not in dora_keys for tile in state.hand_tiles)
    non_red_exists = any(not tile.red for tile in state.hand_tiles)
    current_shape = hand_shape_profile(state.hand_tiles, state.open_melds)
    scored: list[tuple[int, int, Tile]] = []

    for tile in candidates:
        index = hand_index.get(id(tile), len(state.hand_tiles))
        score = 0
        if tile.red and non_red_exists:
            score -= 80
        if tile.base_key in dora_keys and non_dora_exists:
            score -= 70
        if useful_keys and tile.base_key not in useful_keys:
            score += 45
        else:
            score -= 8 * useful_plan_count[tile.base_key]

        if tile.is_honor:
            score += 28
            if counts[tile.base_key] >= 2:
                score -= 55
        elif tile.is_suited:
            if not suited_tile_has_connection(tile, counts):
                score += 30
            if tile.is_terminal:
                score += 4

        if counts[tile.base_key] >= 2:
            score -= 24
        if counts[tile.base_key] >= 3:
            score -= 18

        shape_after = hand_shape_profile(remove_one_tile_instance(state.hand_tiles, tile), state.open_melds)
        score -= round(shape_after.score * 0.35 + (shape_after.score - current_shape.score) * 1.4)
        score -= discard_future_score(state, tile) // 4
        score -= furiten_discard_penalty(state, tile)

        scored.append((score, -index, tile))

    scored.sort(reverse=True)
    candidates = [tile for _score, _index, tile in scored]
    return avoid_furiten_discards_when_possible(state, candidates)


def explain_discard_candidates(state: HandState, plans: list[YakuPlan]) -> list[DiscardExplanation]:
    if not state.discard_candidates:
        return []

    defense_state = state.strategy_mode == "defense"
    defense_shanten = standard_shanten_number(state.hand_tiles, state.open_melds) if defense_state else 0
    counts = canonical_counter(state.hand_tiles)
    visible_counts = canonical_counter(state.all_visible_tiles)
    discarded_counts = canonical_counter(state.discarded_tiles)
    dora_keys = dora_base_keys(state)
    current_shape = hand_shape_profile(state.hand_tiles, state.open_melds)
    danger_context = build_danger_context(state)
    danger_by_key: dict[str, int] = {}
    useful_plan_count = Counter(key for plan in plans for key in plan.useful_keys)
    plan_names_by_key: dict[str, list[str]] = {}
    for plan in plans:
        name = yaku_by_key(plan.yaku_key).name
        for key in plan.useful_keys:
            plan_names_by_key.setdefault(key, []).append(name)

    explanations: list[DiscardExplanation] = []
    for rank, tile in enumerate(state.discard_candidates[:4], start=1):
        reasons: list[str] = []
        safeguards: list[str] = []
        defense_mode = state.strategy_mode == "defense"
        pressure = (
            cached_tile_danger_score(state, tile, danger_context, danger_by_key) - 35
            if defense_mode
            else 0
        )
        key = tile.base_key
        hand_count = counts[key]
        visible_count = visible_counts[key]
        discarded_count = discarded_counts[key]
        remaining_count = max(0, 4 - visible_count)
        shape_role = tile_shape_role(tile, counts)

        if defense_mode:
            reasons.append("modo defesa: menor risco relativo entre as pecas da mao")
            for player in danger_context.riichi_threats:
                all_safe = danger_context.riichi_all_safe.get(player, set())
                after_safe = danger_context.riichi_after_safe.get(player, set())
                if key in after_safe:
                    safeguards.append(f"genbutsu contra Riichi de {player}: descartada depois do Riichi")
                elif key in all_safe:
                    safeguards.append(f"genbutsu contra Riichi de {player}: descartada antes do Riichi")
                else:
                    reasons.append(f"nao e genbutsu contra Riichi de {player}")
            if any(key in keys for keys in danger_context.opponent_discards.values()):
                safeguards.append("genbutsu: ja apareceu em descarte de oponente")
            if tile.is_honor and visible_count >= 3:
                safeguards.append("honra quase esgotada visivelmente")
        elif useful_plan_count[key]:
            names = ", ".join(list(dict.fromkeys(plan_names_by_key.get(key, [])))[:2])
            safeguards.append(f"contribui para {useful_plan_count[key]} plano(s): {names}")
            pressure -= 12 * useful_plan_count[key]
        elif plans:
            reasons.append("nao aparece nos yakus/plano principal ativos")
            pressure += 34

        if tile.red:
            safeguards.append("aka dora: vale ponto extra")
            pressure -= 80
        if key in dora_keys:
            safeguards.append("dora visivel: vale ponto extra")
            pressure -= 70

        if tile.is_honor:
            if hand_count >= 3:
                safeguards.append("ja forma trinca; normalmente nao deveria ser descarte")
                pressure -= 85
            elif hand_count == 2:
                if remaining_count > 0:
                    safeguards.append(
                        f"tem par; ainda pode virar pon/yakuhai se aparecer mais 1 "
                        f"({remaining_count} copia(s) nao vista(s))"
                    )
                    pressure -= 64
                else:
                    reasons.append("par de honra sem copias restantes visiveis para evoluir")
                    pressure += 6
            elif discarded_count:
                reasons.append(f"honra isolada; {discarded_count} copia(s) ja foram descartadas")
                pressure += 28 + discarded_count * 8
            else:
                reasons.append("honra isolada; nao encaixa em sequencia")
                pressure += 22
        elif tile.is_suited:
            if shape_role in {"isolada", "terminal isolado"}:
                reasons.append("isolada: sem vizinho/par forte na mao")
                pressure += 28
            elif shape_role == "penchan":
                reasons.append("forma ruim: penchan (espera de ponta)")
                pressure += 14
            elif shape_role == "kanchan":
                reasons.append("forma media: kanchan (espera fechada)")
                pressure += 8
            elif shape_role in {"ryanmen", "ryankan"}:
                safeguards.append(f"forma boa: {shape_role}")
                pressure -= 18
            if tile.is_terminal:
                reasons.append("terminal tem menos conexoes de sequencia")
                pressure += 6
            if hand_count >= 2:
                safeguards.append("tem par; pode servir como par da mao")
                pressure -= 28

        if defense_mode:
            shanten_after = defense_shanten
            ukeire_after = 0
            safeguards.append("defesa: prioriza seguranca; ukeire nao pesa nesta decisao")
        else:
            remaining = remove_one_tile_instance(state.hand_tiles, tile)
            shanten_after = standard_shanten_number(remaining, state.open_melds)
            improving = improving_tiles_for_completion(state, remaining, shanten_after)
            ukeire_after = sum(available for _tile, available in improving)
            shape_after = hand_shape_profile(remaining, state.open_melds)
            shape_delta = shape_after.score - current_shape.score
            shape_text = f"forma {shape_after.score} ({shape_after.summary})"
            if shape_delta >= 3:
                safeguards.append(f"melhora {shape_text}")
                pressure -= min(16, shape_delta)
            elif shape_delta <= -6:
                reasons.append(f"quebra forma da mao: {shape_text}")
                pressure += min(18, -shape_delta)
            else:
                safeguards.append(shape_text)
            if ukeire_after <= 4:
                reasons.append(f"apos descartar, poucas compras melhoram ({ukeire_after})")
                pressure += 10
            else:
                safeguards.append(f"apos descartar, ainda ha {ukeire_after} compras que melhoram")
                pressure -= min(18, ukeire_after // 2)

            furiten_penalty = furiten_discard_penalty(state, tile)
            if furiten_penalty:
                reasons.append("pode criar espera em furiten")
                pressure += furiten_penalty

        if not reasons:
            reasons.append(state.discard_reason or "menor contribuicao estimada para fechar a mao")

        color = "blue" if defense_mode else ("red" if rank == 1 else "yellow")
        explanations.append(
            DiscardExplanation(
                tile=tile,
                rank=rank,
                color=color,
                pressure=max(0, min(100, pressure + 35)),
                shanten_after=shanten_after,
                ukeire_after=ukeire_after,
                visible_count=visible_count,
                remaining_count=remaining_count,
                reasons=tuple(list(dict.fromkeys(reasons))[:4]),
                safeguards=tuple(list(dict.fromkeys(safeguards))[:4]),
            )
        )

    return explanations


def discard_future_score(state: HandState, discarded_tile: Tile) -> int:
    remaining = list(state.hand_tiles)
    for index, tile in enumerate(remaining):
        if tile is discarded_tile:
            remaining.pop(index)
            break
    else:
        for index, tile in enumerate(remaining):
            if tile.base_key == discarded_tile.base_key:
                remaining.pop(index)
                break

    simulated = HandState(
        hand_tiles=remaining,
        open_melds=list(state.open_melds),
        likely_yaku=[],
        discarded_tiles=state.discarded_tiles,
        discarded_by_player=state.discarded_by_player,
        opponent_open_tiles=state.opponent_open_tiles,
        dora_indicators=state.dora_indicators,
        player_winds=state.player_winds,
    )
    simulated.refresh_missing_count()
    plans = top_yaku_plans(simulated, limit=4)
    if not plans:
        return 0

    best = plans[0].confidence
    support = sum(plan.confidence for plan in plans[1:4]) // 6
    shape = hand_shape_profile(remaining, state.open_melds)
    shape_bonus = max(-12, min(18, (shape.score - 52) // 3))
    return max(0, min(100, best + support + shape_bonus))


def suited_tile_has_connection(tile: Tile, counts: Counter[str]) -> bool:
    if not tile.is_suited:
        return False
    value = int(tile.value)
    suit = tile.suit
    neighbor_offsets = (-2, -1, 1, 2)
    return any(1 <= value + offset <= 9 and counts[f"{suit}_{value + offset}"] > 0 for offset in neighbor_offsets)


def pon_options_for_hand(hand_tiles: list[Tile], discarded_tile: Tile | None, source_player: str | None) -> list[PonOption]:
    if discarded_tile is None or source_player in (None, "principal"):
        return []

    matching = [tile for tile in hand_tiles if tile.base_key == discarded_tile.base_key]
    if len(matching) < 2:
        return []
    return [PonOption(discarded_tile, source_player, tuple(matching[:2]))]


def inferred_value_pon_options(
    hand_tiles: list[Tile],
    source_player: str | None,
    player_winds: dict[str, str] | None = None,
) -> list[PonOption]:
    if source_player in (None, "principal"):
        return []

    counts = canonical_counter(hand_tiles)
    value_keys = set(DRAGONS)
    value_keys.add("wind_east")
    if player_winds:
        seat_key = WIND_VALUE_TO_KEY.get(player_winds.get("principal", ""))
        if seat_key:
            value_keys.add(seat_key)

    candidates = [key for key, count in counts.items() if count >= 2 and key in value_keys]
    # The Pon button already proves some pair is callable. When the detected
    # discard is unreliable, infer only a single unambiguous value pair.
    if len(candidates) != 1:
        return []

    discarded_tile = representative_tile(candidates[0])
    matching = [tile for tile in hand_tiles if tile.base_key == discarded_tile.base_key][:2]
    if len(matching) < 2:
        return []
    return [PonOption(discarded_tile, source_player, tuple(matching))]
