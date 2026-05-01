from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from mahjong_master.mahjong_logic import (
    HandState,
    Meld,
    MeldKind,
    call_decisions_for_state,
    chii_options_for_hand,
    classify_meld,
    kan_options_for_hand,
    likely_yaku,
    pon_options_for_hand,
    sort_tiles,
    tile_from_name,
)


@dataclass(frozen=True)
class TileDetection:
    name: str
    confidence: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def center_x(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def center_y(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


class GameAnalyzer:
    """Turns YOLO tile detections into a conservative player-hand estimate.

    The first pass intentionally focuses on the local player area. Mahjong Soul
    draws the player's concealed hand and open calls near the bottom edge. We
    cluster that bottom row by x gaps: the largest/left-most cluster is treated
    as the concealed hand, and later separated clusters are treated as calls.
    """

    def __init__(self, image_width: int, image_height: int, regions: dict | None = None) -> None:
        self.image_width = image_width
        self.image_height = image_height
        self.regions = regions or {}

    def analyze(
        self,
        detections: list[TileDetection],
        chii_button_visible: bool = False,
        pon_button_visible: bool = False,
        kan_button_visible: bool = False,
        call_source_player: str | None = None,
        player_winds: dict[str, str] | None = None,
    ) -> HandState:
        player_tiles = self.player_area_tiles(detections)
        discarded_tiles = self.discarded_area_tiles(detections)
        discarded_detections_by_player = self.discarded_detections_by_player(discarded_tiles)
        opponent_open_tiles = self.opponent_open_tiles(detections)
        dora_indicators = self.region_tiles(detections, "dora_indicators")
        closed_line_y = self.player_closed_line_y()
        if closed_line_y is not None:
            hand_group, call_groups = self.split_hand_and_calls_by_line(player_tiles, closed_line_y)
        else:
            groups = self.horizontal_groups(player_tiles)
            hand_group, call_groups = self.split_hand_and_calls(groups)

        hand_tiles = []
        for detection in sorted(hand_group, key=lambda item: item.center_x):
            tile = tile_from_name(detection.name)
            if tile is not None:
                hand_tiles.append(tile)

        open_melds: list[Meld] = []
        for group in call_groups:
            tiles = [tile for detection in sorted(group, key=lambda item: item.center_x) if (tile := tile_from_name(detection.name))]
            if tiles:
                if len(tiles) in (3, 4):
                    open_melds.append(classify_meld(tiles))
                else:
                    open_melds.append(Meld(MeldKind.UNKNOWN, tuple(sort_tiles(tiles))))

        discarded_by_player = {}
        for player, player_detections in discarded_detections_by_player.items():
            discarded_by_player[player] = [
                tile
                for detection in self.sort_discards_for_player(player, player_detections)
                if (tile := tile_from_name(detection.name)) is not None
            ]

        discarded = []
        for detection in sorted(discarded_tiles, key=lambda item: (item.center_y, item.center_x)):
            tile = tile_from_name(detection.name)
            if tile is not None:
                discarded.append(tile)

        chii_source_player = "esquerda" if chii_button_visible else call_source_player
        call_source_detections = discarded_detections_by_player.get(chii_source_player or call_source_player or "", [])
        call_latest = self.latest_discard_for_player(chii_source_player or call_source_player or "", call_source_detections)
        call_discard = tile_from_name(call_latest.name) if call_latest is not None else None
        chii_discard = call_discard if chii_button_visible else None
        pon_source_player = call_source_player if call_source_player != "principal" else None
        pon_option_source = pon_source_player
        pon_discard = call_discard if pon_source_player else None

        state = HandState(
            hand_tiles=hand_tiles,
            open_melds=open_melds,
            likely_yaku=[],
            discarded_tiles=discarded,
            discarded_by_player=discarded_by_player,
            opponent_open_tiles=opponent_open_tiles,
            chii_button_visible=chii_button_visible,
            chii_discard=chii_discard,
            chii_options=chii_options_for_hand(hand_tiles, chii_discard) if chii_button_visible else [],
            pon_button_visible=pon_button_visible,
            pon_source_player=pon_option_source,
            pon_discard=pon_discard,
            pon_options=pon_options_for_hand(hand_tiles, pon_discard, pon_option_source) if pon_button_visible else [],
            kan_button_visible=kan_button_visible,
            kan_options=kan_options_for_hand(hand_tiles, pon_discard, pon_option_source) if kan_button_visible else [],
            dora_indicators=[
                tile
                for detection in sorted(dora_indicators, key=lambda item: item.center_x)
                if (tile := tile_from_name(detection.name)) is not None
            ],
            player_winds=player_winds or self.player_wind_placeholders(),
        )
        state.likely_yaku = likely_yaku(state)
        state.call_decisions = call_decisions_for_state(state)
        return state

    def opponent_open_tiles(self, detections: list[TileDetection]) -> dict[str, list]:
        region_keys = {
            "esquerda": "left_opponent_tiles",
            "cima": "top_opponent_tiles",
            "direita": "right_opponent_tiles",
        }
        grouped = {}
        for player, key in region_keys.items():
            tiles = [
                tile
                for detection in self.sort_opponent_tiles(player, self.region_tiles(detections, key))
                if (tile := tile_from_name(detection.name)) is not None
            ]
            grouped[player] = tiles
        return grouped

    @staticmethod
    def sort_opponent_tiles(player: str, detections: list[TileDetection]) -> list[TileDetection]:
        if player == "esquerda":
            return sorted(detections, key=lambda item: (item.center_y, item.center_x))
        if player == "direita":
            return sorted(detections, key=lambda item: (-item.center_y, item.center_x))
        return sorted(detections, key=lambda item: (item.center_x, item.center_y))

    def player_area_tiles(self, detections: list[TileDetection]) -> list[TileDetection]:
        player_region = self.region_bounds("player_hand")
        visible = [
            detection
            for detection in detections
            if (self.contains_detection(player_region, detection) if player_region else detection.center_y >= self.image_height * 0.64)
            and detection.width > 8
            and detection.height > 12
            and tile_from_name(detection.name) is not None
        ]
        if not visible:
            return []
        if self.player_closed_line_y() is not None:
            return visible

        # Keep the densest bottom row as concealed hand, then add plausible
        # opened call groups by size/position. Opened tiles cannot be discarded,
        # so it is better to over-separate valid 3/4 tile groups than to let
        # them enter the closed-hand discard ranking.
        buckets: dict[int, list[TileDetection]] = {}
        bucket_size = max(24, int(self.image_height * 0.035))
        for detection in visible:
            bucket = int(detection.center_y // bucket_size)
            buckets.setdefault(bucket, []).append(detection)

        best_bucket = max(
            buckets,
            key=lambda key: (len(buckets[key]), median(item.center_y for item in buckets[key])),
        )
        row = buckets[best_bucket]
        if len(row) >= 2:
            row_median_y = median(item.center_y for item in row)
            tolerance = max(38, self.image_height * 0.055)
            row_items = [item for item in visible if abs(item.center_y - row_median_y) <= tolerance]
            extra_call_items = self.plausible_open_call_items(visible, row_items)
            return self.unique_detections([*row_items, *extra_call_items])
        return visible

    def player_closed_line_y(self) -> float | None:
        bounds = self.region_bounds("player_closed_line")
        if bounds is None:
            return None
        _x1, y1, _x2, y2 = bounds
        return (y1 + y2) / 2

    def split_hand_and_calls_by_line(
        self,
        detections: list[TileDetection],
        line_y: float,
    ) -> tuple[list[TileDetection], list[list[TileDetection]]]:
        hand_group = [
            detection
            for detection in detections
            if detection.y1 <= line_y <= detection.y2
        ]
        open_tiles = [
            detection
            for detection in detections
            if not detection.y1 <= line_y <= detection.y2
        ]
        return sorted(hand_group, key=lambda item: item.center_x), self.horizontal_groups(open_tiles)

    def plausible_open_call_items(
        self,
        visible: list[TileDetection],
        concealed_row: list[TileDetection],
    ) -> list[TileDetection]:
        if not concealed_row:
            return []

        row_ids = {id(item) for item in concealed_row}
        median_width = median(max(1, item.width) for item in concealed_row)
        median_height = median(max(1, item.height) for item in concealed_row)
        row_median_y = median(item.center_y for item in concealed_row)
        row_right = max(item.x2 for item in concealed_row)
        possible = [
            item
            for item in visible
            if id(item) not in row_ids
            and (
                item.center_x >= row_right - median_width * 0.65
                or item.center_y <= row_median_y - median_height * 0.38
                or item.width <= median_width * 0.88
                or item.height <= median_height * 0.88
            )
        ]
        if not possible:
            return []

        groups = self.horizontal_groups(possible)
        accepted: list[TileDetection] = []
        for group in groups:
            if len(group) not in (3, 4):
                continue
            if self.classify_call_group(group) is None:
                continue
            accepted.extend(group)
        return accepted

    @staticmethod
    def unique_detections(detections: list[TileDetection]) -> list[TileDetection]:
        seen = set()
        unique = []
        for detection in detections:
            key = id(detection)
            if key in seen:
                continue
            seen.add(key)
            unique.append(detection)
        return unique

    def region_bounds(self, key: str) -> tuple[float, float, float, float] | None:
        region = self.regions.get(key)
        if not region or not region.get("enabled", True):
            return None
        x = float(region.get("x", 0))
        y = float(region.get("y", 0))
        return x, y, x + float(region.get("w", 0)), y + float(region.get("h", 0))

    @staticmethod
    def contains_detection(bounds: tuple[float, float, float, float] | None, detection: TileDetection) -> bool:
        if bounds is None:
            return False
        x1, y1, x2, y2 = bounds
        return x1 <= detection.center_x <= x2 and y1 <= detection.center_y <= y2

    def discarded_area_tiles(self, detections: list[TileDetection]) -> list[TileDetection]:
        configured = self.configured_discard_regions()
        if configured:
            return [
                detection
                for detection in detections
                if any(self.contains_detection(bounds, detection) for bounds in configured.values())
                and detection.width > 8
                and detection.height > 12
                and tile_from_name(detection.name) is not None
            ]

        x_min = self.image_width * 0.18
        x_max = self.image_width * 0.82
        y_min = self.image_height * 0.16
        y_max = self.image_height * 0.68
        player_y = self.image_height * 0.64
        return [
            detection
            for detection in detections
            if x_min <= detection.center_x <= x_max
            and y_min <= detection.center_y <= y_max
            and detection.center_y < player_y
            and detection.width > 8
            and detection.height > 12
            and tile_from_name(detection.name) is not None
        ]

    def discarded_detections_by_player(
        self,
        detections: list[TileDetection],
    ) -> dict[str, list[TileDetection]]:
        groups = {"principal": [], "esquerda": [], "cima": [], "direita": []}
        configured = self.configured_discard_regions()
        if configured:
            key_to_player = {
                "discard_player": "principal",
                "discard_left": "esquerda",
                "discard_top": "cima",
                "discard_right": "direita",
            }
            for detection in detections:
                best_player = None
                best_area = 0.0
                for key, bounds in configured.items():
                    if not self.contains_detection(bounds, detection):
                        continue
                    x1, y1, x2, y2 = bounds
                    area = (x2 - x1) * (y2 - y1)
                    if best_player is None or area < best_area:
                        best_player = key_to_player[key]
                        best_area = area
                if best_player:
                    groups[best_player].append(detection)
            return groups

        center_x = self.image_width * 0.5
        center_y = self.image_height * 0.47
        dead_zone_x = self.image_width * 0.08
        dead_zone_y = self.image_height * 0.07

        for detection in detections:
            dx = detection.center_x - center_x
            dy = detection.center_y - center_y
            if abs(dx) <= dead_zone_x and abs(dy) <= dead_zone_y:
                # Around the center, use the dominant direction to avoid
                # mixing the four ponds into a single "center" bucket.
                pass

            if dy > abs(dx) * 0.85:
                groups["principal"].append(detection)
            elif -dy > abs(dx) * 0.85:
                groups["cima"].append(detection)
            elif dx < 0:
                groups["esquerda"].append(detection)
            else:
                groups["direita"].append(detection)
        return groups

    def configured_discard_regions(self) -> dict[str, tuple[float, float, float, float]]:
        regions = {}
        for key in ("discard_player", "discard_left", "discard_top", "discard_right"):
            bounds = self.region_bounds(key)
            if bounds is not None:
                regions[key] = bounds
        return regions

    def region_tiles(self, detections: list[TileDetection], region_key: str) -> list[TileDetection]:
        bounds = self.region_bounds(region_key)
        if bounds is None:
            return []
        return [
            detection
            for detection in detections
            if self.contains_detection(bounds, detection) and tile_from_name(detection.name) is not None
        ]

    def player_wind_placeholders(self) -> dict[str, str]:
        # Regions for reading the four wind letters already exist. Actual
        # OCR/template matching will plug in here; until then, keep the summary
        # explicit instead of pretending certainty.
        return {"principal": "?", "esquerda": "?", "cima": "?", "direita": "?"}

    def latest_discard_for_player(
        self,
        player: str,
        detections: list[TileDetection],
    ) -> TileDetection | None:
        if not detections:
            return None
        row_tolerance, col_tolerance = self.discard_group_tolerances(detections)

        if player == "esquerda":
            left_x = min(item.center_x for item in detections)
            left_column = [item for item in detections if abs(item.center_x - left_x) <= col_tolerance]
            return max(left_column, key=lambda item: item.center_y)

        if player == "direita":
            right_x = max(item.center_x for item in detections)
            right_column = [item for item in detections if abs(item.center_x - right_x) <= col_tolerance]
            return min(right_column, key=lambda item: item.center_y)

        if player == "cima":
            top_y = min(item.center_y for item in detections)
            top_row = [item for item in detections if abs(item.center_y - top_y) <= row_tolerance]
            return min(top_row, key=lambda item: item.center_x)

        if player == "principal":
            bottom_y = max(item.center_y for item in detections)
            bottom_row = [item for item in detections if abs(item.center_y - bottom_y) <= row_tolerance]
            return max(bottom_row, key=lambda item: item.center_x)

        return None

    def latest_opponent_discard(self, grouped: dict[str, list[TileDetection]]) -> TileDetection | None:
        candidates = []
        for player in ("esquerda", "cima", "direita"):
            latest = self.latest_discard_for_player(player, grouped.get(player, []))
            if latest is not None:
                candidates.append(latest)
        if not candidates:
            return None
        center_x = self.image_width * 0.5
        center_y = self.image_height * 0.47
        return min(candidates, key=lambda item: (item.center_x - center_x) ** 2 + (item.center_y - center_y) ** 2)

    def sort_discards_for_player(self, player: str, detections: list[TileDetection]) -> list[TileDetection]:
        center_x = self.image_width * 0.5
        center_y = self.image_height * 0.47

        if player == "principal":
            return sorted(detections, key=lambda item: (item.center_y, item.center_x))
        if player == "cima":
            return sorted(detections, key=lambda item: (-item.center_y, -item.center_x))
        if player == "esquerda":
            return sorted(detections, key=lambda item: (item.center_x, item.center_y))
        if player == "direita":
            return sorted(detections, key=lambda item: (-item.center_x, -item.center_y))

        # Fallback: newest discards in Mahjong Soul tend to be closer to the
        # center than older rows, so use that as a general-purpose order.
        return sorted(detections, key=lambda item: -((item.center_x - center_x) ** 2 + (item.center_y - center_y) ** 2))

    @staticmethod
    def discard_group_tolerances(detections: list[TileDetection]) -> tuple[float, float]:
        row_tolerance = max(22.0, median(max(1, item.height) for item in detections) * 0.72)
        col_tolerance = max(18.0, median(max(1, item.width) for item in detections) * 0.72)
        return row_tolerance, col_tolerance

    def horizontal_groups(self, detections: list[TileDetection]) -> list[list[TileDetection]]:
        if not detections:
            return []

        ordered = sorted(detections, key=lambda item: item.center_x)
        widths = [max(1, item.width) for item in ordered]
        median_width = median(widths)
        gap_threshold = max(median_width * 1.35, 30)

        groups: list[list[TileDetection]] = [[ordered[0]]]
        previous = ordered[0]
        for detection in ordered[1:]:
            gap = detection.x1 - previous.x2
            if gap > gap_threshold:
                groups.append([detection])
            else:
                groups[-1].append(detection)
            previous = detection

        return groups

    def split_hand_and_calls(
        self,
        groups: list[list[TileDetection]],
    ) -> tuple[list[TileDetection], list[list[TileDetection]]]:
        if not groups:
            return [], []

        total_tiles = sum(len(group) for group in groups)
        if total_tiles < 13:
            return self.flatten_groups(groups), []

        best_hand_groups = groups
        best_call_groups: list[list[TileDetection]] = []
        best_score = (999, 0)

        # Open calls in Mahjong Soul are drawn as separated groups after the
        # local hand. Try every right-side suffix as calls and keep only suffixes
        # that still leave a plausible concealed-hand count. A normal visible
        # owned set has at least 13 tiles; each kan adds one visible tile.
        for suffix_start in range(len(groups), -1, -1):
            hand_groups = groups[:suffix_start]
            call_groups = groups[suffix_start:]
            melds = [self.classify_call_group(group) for group in call_groups]
            if any(meld is None for meld in melds):
                continue

            kan_count = sum(1 for meld in melds if meld and meld.kind == MeldKind.KAN)
            expected_visible_min = 13 + kan_count
            expected_hand_min = 13 - (3 * len(call_groups))
            hand_count = sum(len(group) for group in hand_groups)

            if total_tiles < expected_visible_min or hand_count < expected_hand_min:
                continue

            # Prefer layouts that explain calls while leaving 13/14-like hand
            # counts. This prevents a small separated chunk from becoming the
            # whole hand, which was the source of several false summaries.
            hand_extra = abs(hand_count - expected_hand_min)
            call_tiles = sum(len(group) for group in call_groups)
            score = (hand_extra, -call_tiles)
            if score < best_score:
                best_score = score
                best_hand_groups = hand_groups
                best_call_groups = call_groups

        return self.flatten_groups(best_hand_groups), best_call_groups

    def classify_call_group(self, group: list[TileDetection]) -> Meld | None:
        if len(group) not in (3, 4):
            return None
        tiles = [tile for detection in sorted(group, key=lambda item: item.center_x) if (tile := tile_from_name(detection.name))]
        if len(tiles) != len(group):
            return None
        meld = classify_meld(tiles)
        return None if meld.kind == MeldKind.UNKNOWN else meld

    @staticmethod
    def flatten_groups(groups: list[list[TileDetection]]) -> list[TileDetection]:
        return [detection for group in groups for detection in group]

    @staticmethod
    def group_min_x(group: list[TileDetection]) -> float:
        return min(item.x1 for item in group)


def detections_from_yolo_result(result) -> list[TileDetection]:
    names = result.names
    detections: list[TileDetection] = []
    for box in result.boxes:
        x1, y1, x2, y2 = [float(value) for value in box.xyxy[0]]
        class_id = int(box.cls[0])
        confidence = float(box.conf[0])
        name = str(names.get(class_id, class_id))
        detections.append(TileDetection(name, confidence, x1, y1, x2, y2))
    return detections
