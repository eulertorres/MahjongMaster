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
    furiten_waits: list[TileNeed] = field(default_factory=list)
    current_furiten_waits: list[TileNeed] = field(default_factory=list)
    discarded_tiles: list[Tile] = field(default_factory=list)
    discarded_by_player: dict[str, list[Tile]] = field(default_factory=dict)
    opponent_open_tiles: dict[str, list[Tile]] = field(default_factory=dict)
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
    return all_tiles_simple(state)


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
    return len(tiles) >= 14 and all(is_terminal_or_honor(tile) for tile in tiles)


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
    useful_keys = set().union(*(set(plan.useful_keys) for plan in plans))
    state.helpful_missing_tiles = helpful_missing_tiles_from_plans(plans, state)
    dora_keys = dora_base_keys(state)
    discards = [
        tile
        for tile in state.hand_tiles
        if tile.base_key not in useful_keys and not tile.red and tile.base_key not in dora_keys
    ]
    return matches, discards


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

    candidates.extend(flush_candidates(tiles))
    candidates.extend(dragon_candidates(tiles, state.discarded_tiles))
    candidates.extend(wind_candidates(state))
    candidates.extend(seven_pairs_candidate(state))
    candidates.extend(pure_straight_candidates(tiles))

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
    useful = frozenset(tile.base_key for tile in contributing)
    wanted = generic_wanted_keys(key, tiles)
    return YakuPlan(key, confidence, useful, wanted_keys=wanted)


def flush_candidates(tiles: list[Tile]) -> list[YakuPlan]:
    candidates = []
    for suit in SUITS:
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


def dragon_candidates(tiles: list[Tile], discarded_tiles: list[Tile]) -> list[YakuPlan]:
    key_map = {
        "dragon_white": "haku",
        "dragon_green": "hatsu",
        "dragon_red": "chun",
    }
    counts = canonical_counter(tiles)
    discarded_counts = canonical_counter(discarded_tiles)
    candidates = []
    for dragon_key, yaku_key in key_map.items():
        count = counts[dragon_key]
        if count == 0:
            continue
        remaining_unseen = max(0, 4 - discarded_counts[dragon_key])
        needed = max(0, 3 - count)
        if needed > remaining_unseen:
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
        remaining_unseen = max(0, 4 - discarded_counts[seat_key])
        needed = max(0, 3 - counts[seat_key])
        if needed <= remaining_unseen:
            penalty = discarded_counts[seat_key] * 12
            confidence = max(0, min(92, 35 + counts[seat_key] * 22 - penalty))
            candidates.append(YakuPlan("seat_wind", confidence, frozenset({seat_key}), wanted_keys=frozenset({seat_key})))

    for wind_key in ("wind_east",):
        count = counts[wind_key]
        if count == 0:
            continue
        remaining_unseen = max(0, 4 - discarded_counts[wind_key])
        needed = max(0, 3 - count)
        if needed > remaining_unseen:
            continue
        penalty = discarded_counts[wind_key] * 12
        confidence = max(0, min(92, 35 + count * 22 - penalty))
        candidates.append(YakuPlan("prevalent_wind", confidence, frozenset({wind_key}), wanted_keys=frozenset({wind_key})))
    return candidates


def seven_pairs_candidate(state: HandState) -> list[YakuPlan]:
    if not state.is_closed:
        return []
    counts = canonical_counter(state.hand_tiles)
    paired_keys = {key for key, count in counts.items() if count >= 2}
    useful_keys = {key for key, count in counts.items() if count == 1 and len(paired_keys) < 7}
    useful_keys |= paired_keys
    single_progress = min(7 - len(paired_keys), len(useful_keys - paired_keys)) * 0.15
    confidence = round(100 * min(7, len(paired_keys) + single_progress) / 7)
    wanted = frozenset(key for key, count in counts.items() if count == 1)
    return [YakuPlan("chitoitsu", confidence, frozenset(useful_keys), wanted_keys=wanted)]


def pure_straight_candidates(tiles: list[Tile]) -> list[YakuPlan]:
    candidates = []
    needed = {1, 2, 3, 4, 5, 6, 7, 8, 9}
    for suit in SUITS:
        suit_values = {int(tile.value) for tile in tiles if tile.is_suited and tile.suit == suit}
        present = needed & suit_values
        confidence = round(100 * len(present) / 9)
        useful = frozenset(f"{suit}_{value}" for value in present)
        wanted = frozenset(f"{suit}_{value}" for value in needed - present)
        candidates.append(YakuPlan("ittsu", confidence, useful, wanted_keys=wanted))
    return candidates


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
        return frozenset().union(*(pure_straight_candidates(state.all_known_tiles)[index].wanted_keys for index in range(3)))
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


def best_call_decision(action: str, state: HandState, options, before_score: int) -> CallDecision:
    if not options:
        return CallDecision(action, False, 0, "-", "sem combinacao valida na mao")

    simulations: list[tuple[int, str, HandState, list[YakuPlan]]] = []
    for option in options:
        simulated = simulate_call(action, state, option)
        if simulated is None:
            continue
        simulated.likely_yaku = likely_yaku(simulated)
        plans = call_candidate_plans(simulated)
        simulations.append((call_plan_score_from_plans(plans), option.compact, simulated, plans))

    if not simulations:
        return CallDecision(action, False, 0, options[0].compact, "chamada quebraria a mao detectada")

    after_score, option_text, simulated, after_plans = max(simulations, key=lambda item: item[0])
    delta = after_score - before_score
    blocked_closed = any(match.reason == "mao aberta" and match.yaku.closed_only for match in simulated.blocked_yaku)
    complete_open_yaku = [
        plan
        for plan in after_plans
        if plan.confidence >= 100
        and not yaku_by_key(plan.yaku_key).closed_only
        and not yaku_by_key(plan.yaku_key).situational
    ]

    if action == "Chii":
        # Chii opens the hand and commonly kills menzen paths, so require a
        # clear future gain unless it completes a non-closed yaku immediately.
        recommended = bool(complete_open_yaku) or (after_score >= 60 and delta >= 12)
    else:
        # Pon/Kan are allowed when the simulated future keeps or improves the
        # best open-yaku plan. This prevents rejecting yakuhai/dragon calls just
        # because closed-only yakus become unavailable.
        recommended = bool(complete_open_yaku) or (after_score >= 50 and delta >= -4)

    reason = f"score {before_score}->{after_score}"
    if complete_open_yaku:
        names = "+".join(yaku_by_key(plan.yaku_key).name for plan in complete_open_yaku[:2])
        reason += f"; completa {names}"
    elif blocked_closed and not recommended:
        reason += "; abre mao e futuro fica pior"
    elif delta > 0:
        reason += "; melhora planos ativos"
    elif recommended:
        reason += "; mantem yaku aberto viavel"
    else:
        reason += "; perde muita compatibilidade"
    return CallDecision(action, recommended, max(0, min(100, after_score)), option_text, reason)


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
    matches, plan_discards = estimate_shape_yaku(state)
    efficiency_discards = inefficient_tiles_for_completion(state)
    guaranteed_yaku = has_guaranteed_yaku(state, matches)
    completion_discards = completion_discard_candidates(state) if guaranteed_yaku or not plan_discards else []
    if guaranteed_yaku and completion_discards:
        state.discard_candidates = completion_discards
        state.helpful_missing_tiles = helpful_tiles_for_completion_after_discard(state, completion_discards[0])
        state.discard_reason = "yaku garantido; prioriza shanten/ukeire para fechar a mao"
    elif plan_discards:
        efficiency_keys = {tile.base_key for tile in efficiency_discards}
        state.discard_candidates = [tile for tile in plan_discards if tile.base_key in efficiency_keys or not efficiency_discards]
        state.discard_reason = "nao contribui para os yakus ativos e/ou esta fraca para fechar a mao"
    else:
        state.discard_candidates = completion_discards or efficiency_discards
        state.discard_reason = "peca isolada ou pouco conectada para completar grupos"
    if not state.discard_candidates:
        state.discard_candidates = ranked_discard_candidates(state, top_yaku_plans(state, limit=3))
        state.discard_reason = "menor contribuicao estimada entre as pecas conhecidas"
    if state.discard_candidates and not guaranteed_yaku:
        state.discard_candidates = sort_discard_candidates_by_badness(
            state,
            state.discard_candidates,
            top_yaku_plans(state, limit=3),
        )
    update_furiten_summary(state)
    state.discard_explanations = explain_discard_candidates(state, top_yaku_plans(state, limit=3))
    return matches


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
    if standard_shanten_number(hand_tiles, state.open_melds) != 0:
        return frozenset()

    seen_counts = canonical_counter(state.all_visible_tiles)
    waits = []
    for key in ALL_BASE_KEYS:
        if seen_counts[key] >= 4:
            continue
        tile = representative_tile(key)
        if standard_shanten_number([*hand_tiles, tile], state.open_melds) == -1:
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


def completion_discard_candidates(state: HandState) -> list[Tile]:
    tiles = list(state.hand_tiles)
    if not tiles:
        return []

    dora_keys = dora_base_keys(state)
    counts = canonical_counter(tiles)
    visible_counts = canonical_counter(state.all_visible_tiles)
    non_dora_exists = any(tile.base_key not in dora_keys for tile in tiles)
    non_red_exists = any(not tile.red for tile in tiles)
    scored: list[tuple[int, int, int, int, int, Tile]] = []

    for index, tile in enumerate(tiles):
        remaining = remove_one_tile_instance(tiles, tile)
        shanten = standard_shanten_number(remaining, state.open_melds)
        improving = improving_tiles_for_completion(state, remaining, shanten)
        ukeire = sum(available for _tile, available in improving)
        furiten_penalty = furiten_discard_penalty(state, tile)
        protection_penalty = 0
        if tile.red and non_red_exists:
            protection_penalty += 80
        if tile.base_key in dora_keys and non_dora_exists:
            protection_penalty += 70
        if tile.is_honor and counts[tile.base_key] >= 2 and max(0, 4 - visible_counts[tile.base_key]) > 0:
            protection_penalty += 90
        scored.append((shanten, furiten_penalty, -ukeire, protection_penalty, index, tile))

    scored.sort(key=lambda item: item[:5])
    best_shanten, best_furiten_penalty, best_negative_ukeire = scored[0][0], scored[0][1], scored[0][2]
    best_ukeire = -best_negative_ukeire
    return [
        tile
        for shanten, furiten_penalty, negative_ukeire, penalty, _index, tile in scored
        if shanten == best_shanten and -negative_ukeire >= max(0, best_ukeire - 4) and penalty <= 80
        and furiten_penalty <= best_furiten_penalty
    ][:4]


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
    open_meld_count = sum(1 for meld in open_melds if meld.kind in (MeldKind.CHI, MeldKind.PON, MeldKind.KAN))
    counts = canonical_counter(hand_tiles)
    count_tuple = tuple(counts[key] for key in ALL_BASE_KEYS)
    return standard_shanten_from_counts(count_tuple, open_meld_count)


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
        if counts[tile.base_key] >= 2:
            continue
        if tile.is_honor:
            weak_tiles.append(tile)
            continue
        if tile.is_suited and not suited_tile_has_connection(tile, counts):
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

        score -= discard_future_score(state, tile) // 4
        score -= furiten_discard_penalty(state, tile)

        scored.append((score, -index, tile))

    if not scored:
        return []

    scored.sort(reverse=True)
    best_score = scored[0][0]
    return [tile for score, _index, tile in scored if score >= best_score - 6][:4]


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

        score -= discard_future_score(state, tile) // 4
        score -= furiten_discard_penalty(state, tile)

        scored.append((score, -index, tile))

    scored.sort(reverse=True)
    return [tile for _score, _index, tile in scored]


def explain_discard_candidates(state: HandState, plans: list[YakuPlan]) -> list[DiscardExplanation]:
    if not state.discard_candidates:
        return []

    counts = canonical_counter(state.hand_tiles)
    visible_counts = canonical_counter(state.all_visible_tiles)
    discarded_counts = canonical_counter(state.discarded_tiles)
    dora_keys = dora_base_keys(state)
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
        pressure = 0
        key = tile.base_key
        hand_count = counts[key]
        visible_count = visible_counts[key]
        discarded_count = discarded_counts[key]
        remaining_count = max(0, 4 - visible_count)

        if useful_plan_count[key]:
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
            if not suited_tile_has_connection(tile, counts):
                reasons.append("isolada: sem vizinho/par forte na mao")
                pressure += 28
            if tile.is_terminal:
                reasons.append("terminal tem menos conexoes de sequencia")
                pressure += 6
            if hand_count >= 2:
                safeguards.append("tem par; pode servir como par da mao")
                pressure -= 28

        remaining = remove_one_tile_instance(state.hand_tiles, tile)
        shanten_after = standard_shanten_number(remaining, state.open_melds)
        improving = improving_tiles_for_completion(state, remaining, shanten_after)
        ukeire_after = sum(available for _tile, available in improving)
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

        color = "red" if rank == 1 else "yellow"
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
    return max(0, min(100, best + support))


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
