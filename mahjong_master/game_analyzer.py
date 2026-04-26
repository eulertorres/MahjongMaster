from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from mahjong_master.mahjong_logic import HandState, Meld, MeldKind, classify_meld, likely_yaku, tile_from_name


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

    def __init__(self, image_width: int, image_height: int) -> None:
        self.image_width = image_width
        self.image_height = image_height

    def analyze(self, detections: list[TileDetection]) -> HandState:
        player_tiles = self.player_area_tiles(detections)
        discarded_tiles = self.discarded_area_tiles(detections)
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
                open_melds.append(classify_meld(tiles))

        discarded = []
        for detection in sorted(discarded_tiles, key=lambda item: (item.center_y, item.center_x)):
            tile = tile_from_name(detection.name)
            if tile is not None:
                discarded.append(tile)

        state = HandState(hand_tiles=hand_tiles, open_melds=open_melds, likely_yaku=[], discarded_tiles=discarded)
        state.likely_yaku = likely_yaku(state)
        return state

    def player_area_tiles(self, detections: list[TileDetection]) -> list[TileDetection]:
        min_y = self.image_height * 0.64
        visible = [
            detection
            for detection in detections
            if detection.center_y >= min_y
            and detection.width > 8
            and detection.height > 12
            and tile_from_name(detection.name) is not None
        ]
        if not visible:
            return []

        # Keep the densest bottom row. This avoids mixing center-table discards
        # with the player's actual hand when the table camera is zoomed out.
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
            return [item for item in visible if abs(item.center_y - row_median_y) <= tolerance]
        return visible

    def discarded_area_tiles(self, detections: list[TileDetection]) -> list[TileDetection]:
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
