from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Iterable


SUITS = ("man", "pin", "sou")
WINDS = ("wind_east", "wind_south", "wind_west", "wind_north")
DRAGONS = ("dragon_white", "dragon_green", "dragon_red")
HONORS = (*WINDS, *DRAGONS)
TERMINAL_NUMBERS = {1, 9}


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
class HandDecomposition:
    melds: tuple[Meld, ...]
    pair: tuple[Tile, Tile]


@dataclass
class HandState:
    hand_tiles: list[Tile]
    open_melds: list[Meld]
    likely_yaku: list[YakuMatch]
    missing_count: int = 0
    discard_candidates: list[Tile] = field(default_factory=list)
    discarded_tiles: list[Tile] = field(default_factory=list)

    @property
    def all_known_tiles(self) -> list[Tile]:
        tiles = list(self.hand_tiles)
        for meld in self.open_melds:
            tiles.extend(meld.tiles)
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

    def refresh_missing_count(self) -> None:
        self.missing_count = max(0, self.expected_visible_min - len(self.all_known_tiles))

    def summary(self) -> str:
        hand_items = [tile.compact for tile in self.hand_tiles]
        hand_items.extend("???" for _ in range(self.missing_count))
        hand = " | ".join(hand_items) or "-"
        melds = " | ".join(meld.compact for meld in self.open_melds)
        yaku = ", ".join(f"{match.yaku.name} {match.confidence}%" for match in self.likely_yaku[:5]) or "-"
        parts = [f"Hand: {hand}"]
        if melds:
            parts.append(melds)
        parts.append(f"Yaku provavel: {yaku}")
        if self.missing_count:
            parts.append(
                f"Analise incompleta: faltam {self.missing_count} peca(s); "
                "numero de pecas insuficiente para decisao confiavel."
            )
        if self.discard_candidates:
            discards = " | ".join(tile.compact for tile in self.discard_candidates)
            parts.append(f"Descartes que nao contribuem: {discards}")
        if self.discarded_tiles:
            visible_discards = " | ".join(tile.compact for tile in self.discarded_tiles[:18])
            parts.append(f"Descartes vistos: {visible_discards}")
        return "\n".join(parts)


@dataclass(frozen=True)
class YakuPlan:
    yaku_key: str
    confidence: int
    useful_keys: frozenset[str]
    reason: str = "estimate"


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
    discards = [tile for tile in state.hand_tiles if tile.base_key not in useful_keys and not tile.red]
    return matches, discards


def top_yaku_plans(state: HandState, limit: int = 3) -> list[YakuPlan]:
    tiles = state.all_known_tiles
    if not tiles:
        return []

    candidates: list[YakuPlan] = []
    for match in suppress_weaker_matches(confirmed_yaku_matches(state)):
        candidates.append(YakuPlan(match.yaku.key, match.confidence, confirmed_useful_keys(state, match.yaku.key), match.reason))

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
    candidates.extend(dragon_candidates(tiles))
    candidates.extend(wind_candidates(tiles, state.discarded_tiles))
    candidates.extend(seven_pairs_candidate(state))
    candidates.extend(pure_straight_candidates(tiles))

    valid = [candidate for candidate in candidates if candidate.confidence >= 45 and candidate.useful_keys]
    if not valid:
        return []

    best_by_key: dict[str, YakuPlan] = {}
    for candidate in valid:
        confidence = min(candidate.confidence, 70) if state.missing_count and candidate.reason != "shape" else candidate.confidence
        candidate = YakuPlan(candidate.yaku_key, confidence, candidate.useful_keys, candidate.reason)
        previous = best_by_key.get(candidate.yaku_key)
        if previous is None or candidate.confidence > previous.confidence:
            best_by_key[candidate.yaku_key] = candidate

    return sorted(best_by_key.values(), key=lambda item: item.confidence, reverse=True)[:limit]


def confirmed_yaku_matches(state: HandState) -> list[YakuMatch]:
    matches: list[YakuMatch] = []
    for yaku in YAKU_REGISTRY:
        if not yaku.enabled or yaku.matcher is None:
            continue
        if yaku.closed_only and not state.is_closed:
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
    if yaku_key in {"seat_wind", "prevalent_wind"}:
        return frozenset(tile.base_key for tile in tiles if tile.suit == "wind")
    return frozenset(tile.base_key for tile in tiles if tile.base_key)


def compatibility_candidate(key: str, tiles: list[Tile], predicate: Callable[[Tile], bool]) -> YakuPlan:
    contributing = [tile for tile in tiles if predicate(tile)]
    confidence = round(100 * len(contributing) / max(1, len(tiles)))
    return YakuPlan(key, confidence, frozenset(tile.base_key for tile in contributing))


def flush_candidates(tiles: list[Tile]) -> list[YakuPlan]:
    candidates = []
    for suit in SUITS:
        half_contrib = [tile for tile in tiles if tile.is_honor or (tile.is_suited and tile.suit == suit)]
        half_confidence = round(100 * len(half_contrib) / max(1, len(tiles)))
        candidates.append(YakuPlan("honitsu", half_confidence, frozenset(tile.base_key for tile in half_contrib)))

        full_contrib = [tile for tile in tiles if tile.is_suited and tile.suit == suit]
        full_confidence = round(100 * len(full_contrib) / max(1, len(tiles)))
        candidates.append(YakuPlan("chinitsu", full_confidence, frozenset(tile.base_key for tile in full_contrib)))
    return candidates


def dragon_candidates(tiles: list[Tile]) -> list[YakuPlan]:
    key_map = {
        "dragon_white": "haku",
        "dragon_green": "hatsu",
        "dragon_red": "chun",
    }
    counts = canonical_counter(tiles)
    candidates = []
    for dragon_key, yaku_key in key_map.items():
        count = counts[dragon_key]
        if count == 0:
            continue
        confidence = min(95, 35 + count * 20)
        candidates.append(YakuPlan(yaku_key, confidence, frozenset({dragon_key})))
    return candidates


def wind_candidates(tiles: list[Tile], discarded_tiles: list[Tile]) -> list[YakuPlan]:
    counts = canonical_counter(tiles)
    discarded_counts = canonical_counter(discarded_tiles)
    candidates = []
    for wind_key in WINDS:
        count = counts[wind_key]
        if count == 0:
            continue
        remaining_unseen = max(0, 4 - count - discarded_counts[wind_key])
        needed = max(0, 3 - count)
        if needed > remaining_unseen:
            continue
        penalty = discarded_counts[wind_key] * 12
        confidence = max(0, min(92, 35 + count * 22 - penalty))
        candidates.append(YakuPlan("seat_wind", confidence, frozenset({wind_key})))
        candidates.append(YakuPlan("prevalent_wind", confidence, frozenset({wind_key})))
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
    return [YakuPlan("chitoitsu", confidence, frozenset(useful_keys))]


def pure_straight_candidates(tiles: list[Tile]) -> list[YakuPlan]:
    candidates = []
    needed = {1, 2, 3, 4, 5, 6, 7, 8, 9}
    for suit in SUITS:
        suit_values = {int(tile.value) for tile in tiles if tile.is_suited and tile.suit == suit}
        present = needed & suit_values
        confidence = round(100 * len(present) / 9)
        useful = frozenset(f"{suit}_{value}" for value in present)
        candidates.append(YakuPlan("ittsu", confidence, useful))
    return candidates


YAKU_REGISTRY: list[Yaku] = [
    Yaku("riichi", "Riichi", closed_only=True, situational=True),
    Yaku("double_riichi", "Double Riichi", closed_only=True, situational=True),
    Yaku("menzen_tsumo", "Fully Concealed Hand", closed_only=True, situational=True),
    Yaku("ippatsu", "Ippatsu", closed_only=True, situational=True),
    Yaku("pinfu", "Pinfu", closed_only=True, matcher=has_pinfu_like_shape),
    Yaku("iipeikou", "Pure Double Sequence", closed_only=True, matcher=has_iipeikou_like_shape),
    Yaku("ryanpeikou", "Twice Pure Double Sequence", closed_only=True, matcher=has_twice_pure_double_sequence),
    Yaku("tanyao", "All Simples", matcher=has_all_simples_shape),
    Yaku("seat_wind", "Seat Wind", matcher=lambda state: has_triplet_of(state, set(WINDS))),
    Yaku("prevalent_wind", "Prevalent Wind", matcher=lambda state: has_triplet_of(state, set(WINDS))),
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
    matches, plan_discards = estimate_shape_yaku(state)
    efficiency_discards = inefficient_tiles_for_completion(state)
    if plan_discards:
        efficiency_keys = {tile.base_key for tile in efficiency_discards}
        state.discard_candidates = [tile for tile in plan_discards if tile.base_key in efficiency_keys or not efficiency_discards]
    else:
        state.discard_candidates = efficiency_discards
    return matches


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
    weak_tiles: list[Tile] = []
    for tile in tiles:
        if tile.red:
            continue
        if counts[tile.base_key] >= 2:
            continue
        if tile.is_honor:
            weak_tiles.append(tile)
            continue
        if tile.is_suited and not suited_tile_has_connection(tile, counts):
            weak_tiles.append(tile)

    return weak_tiles


def suited_tile_has_connection(tile: Tile, counts: Counter[str]) -> bool:
    if not tile.is_suited:
        return False
    value = int(tile.value)
    suit = tile.suit
    neighbor_offsets = (-2, -1, 1, 2)
    return any(1 <= value + offset <= 9 and counts[f"{suit}_{value + offset}"] > 0 for offset in neighbor_offsets)
