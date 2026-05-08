from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


TIME_RE = re.compile(r"^\d\d:\d\d:\d\d ")
KV_RE = re.compile(r"(\w+)=([^\s]+)")


@dataclass
class Frame:
    ts: str
    data: dict[str, str]
    line: str

    @property
    def seconds(self) -> int:
        return seconds_from_ts(self.ts)

    @property
    def hand_count(self) -> int:
        return value_count(self.data.get("MP", "-"))

    @property
    def discard_count(self) -> int:
        return value_count(self.data.get("DP", "-"))


@dataclass
class Event:
    ts: str
    event_type: str
    context: dict
    line: str

    @property
    def seconds(self) -> int:
        return seconds_from_ts(self.ts)


@dataclass
class RoundSegment:
    index: int
    start: Frame
    end_ts: str
    frames: list[Frame] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)


def seconds_from_ts(ts: str) -> int:
    hour, minute, second = (int(part) for part in ts.split(":"))
    return hour * 3600 + minute * 60 + second


def value_count(value: str) -> int:
    return 0 if value in {"", "-"} else len(value.split(","))


def parse_log(path: Path) -> tuple[list[Frame], list[Event]]:
    frames: list[Frame] = []
    events: list[Event] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not TIME_RE.match(line):
            continue
        ts = line[:8]
        if " EV=FRAME " in line:
            compact = line.split(" DT=", 1)[0]
            data = {key: value for key, value in KV_RE.findall(compact[9:])}
            frames.append(Frame(ts, data, compact))
            continue
        event_match = re.search(r"EV=(\w+)\s+CONTEXT=(.*)$", line)
        if event_match:
            try:
                context = json.loads(event_match.group(2))
            except json.JSONDecodeError:
                context = {"raw": event_match.group(2)}
            events.append(Event(ts, event_match.group(1), context, line))
        elif " EV=SESSION_END" in line:
            events.append(Event(ts, "SESSION_END", {}, line))
    return frames, events


def previous_frame(frames: list[Frame], second: int) -> Frame | None:
    previous = None
    for frame in frames:
        if frame.seconds > second:
            break
        previous = frame
    return previous


def infer_round_starts(frames: list[Frame]) -> list[Frame]:
    starts: list[Frame] = []
    first_full = next((frame for frame in frames if frame.hand_count >= 12), None)
    if first_full is not None:
        starts.append(first_full)
    for index, frame in enumerate(frames):
        if frame.hand_count < 12:
            continue
        previous = next((frames[item] for item in range(index - 1, -1, -1) if frames[item].hand_count >= 8), None)
        blank_gap = previous is None or frame.seconds - previous.seconds > 8
        discard_reset = previous is not None and frame.discard_count < previous.discard_count - 3
        recent_discard_reset = (
            frame.discard_count <= 1
            and any(
                2 <= frame.seconds - old.seconds <= 18 and old.discard_count >= 6
                for old in frames[max(0, index - 25) : index]
            )
        )
        hand_changed = previous is not None and tile_overlap(frame.data.get("MP", "-"), previous.data.get("MP", "-")) <= 4
        score_reset = (
            previous is not None
            and frame.data.get("SC") != previous.data.get("SC")
            and frame.seconds - previous.seconds > 5
            and frame.discard_count <= 2
        )
        if (blank_gap or discard_reset or recent_discard_reset or hand_changed or score_reset) and (
            not starts or frame.seconds - starts[-1].seconds > 30
        ):
            starts.append(frame)
    return starts


def tile_overlap(left: str, right: str) -> int:
    if left in {"", "-"} or right in {"", "-"}:
        return 0
    left_counts: dict[str, int] = {}
    for tile in left.split(","):
        left_counts[tile] = left_counts.get(tile, 0) + 1
    overlap = 0
    for tile in right.split(","):
        if left_counts.get(tile, 0) > 0:
            left_counts[tile] -= 1
            overlap += 1
    return overlap


def segment_rounds(frames: list[Frame], events: list[Event]) -> list[RoundSegment]:
    starts = infer_round_starts(frames)
    segments: list[RoundSegment] = []
    for index, start in enumerate(starts, start=1):
        end_second = starts[index].seconds if index < len(starts) else 10**9
        segment_frames = [frame for frame in frames if start.seconds <= frame.seconds < end_second]
        segment_events = [event for event in events if start.seconds <= event.seconds < end_second]
        end_ts = segment_frames[-1].ts if segment_frames else start.ts
        segments.append(RoundSegment(index, start, end_ts, segment_frames, segment_events))
    return segments


def click_target(event: Event) -> str:
    context = event.context
    if context.get("kind") == "skip":
        return "/".join(context.get("skipped_actions") or []) or "-"
    if context.get("kind") == "chii_option":
        signature = context.get("signature")
        if isinstance(signature, list):
            return "option " + ",".join(signature)
        return str(signature or "-")
    return (
        context.get("tile_label")
        or context.get("tile_key")
        or context.get("action")
        or context.get("button")
        or "-"
    )


def iter_action_rows(segment: RoundSegment) -> Iterable[tuple[Event, Frame | None]]:
    for event in segment.events:
        if event.event_type != "AUTO_CLICK":
            continue
        kind = event.context.get("kind")
        if kind not in {"discard", "riichi_discard", "action", "chii_option", "skip"}:
            continue
        yield event, previous_frame(segment.frames, event.seconds)


def print_summary(path: Path, frames: list[Frame], events: list[Event], segments: list[RoundSegment]) -> None:
    print(f"Arquivo: {path}")
    print(f"Frames: {len(frames)} | Eventos: {len(events)} | Rodadas estimadas: {len(segments)}")
    if frames:
        print(f"Intervalo: {frames[0].ts} - {frames[-1].ts}")
    print()

    for segment in segments:
        start = segment.start
        print("=" * 80)
        print(f"Rodada {segment.index}: {start.ts} - {segment.end_ts}")
        print(
            "Inicio "
            f"MP={start.data.get('MP', '-')} "
            f"MA={start.data.get('MA', '-')} "
            f"SH={start.data.get('SH', '-')} "
            f"OBJ={start.data.get('OBJ', '-')} "
            f"DR={start.data.get('DR', '-')} "
            f"SC={start.data.get('SC', '-')}"
        )
        for event, frame in iter_action_rows(segment):
            kind = event.context.get("kind", event.event_type)
            target = click_target(event)
            if frame is None:
                print(f"{event.ts} {kind:<14} {target}")
                continue
            print(
                f"{event.ts} {kind:<14} {target:<18} "
                f"OBJ={frame.data.get('OBJ', '-')} "
                f"ST={frame.data.get('ST', '-')} "
                f"SH={frame.data.get('SH', '-')} "
                f"RK={frame.data.get('RK', '-')} "
                f"MP={frame.data.get('MP', '-')} "
                f"MA={frame.data.get('MA', '-')} "
                f"DS={frame.data.get('DS', '-')}"
            )
        print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse MahjongMaster autoplay .log files.")
    parser.add_argument("log", nargs="?", type=Path, help="Caminho do arquivo .log")
    parser.add_argument("--json", action="store_true", help="Imprime estrutura resumida em JSON")
    args = parser.parse_args()

    log_path = args.log or latest_log()
    frames, events = parse_log(log_path)
    segments = segment_rounds(frames, events)

    if args.json:
        payload = {
            "log": str(log_path),
            "frames": len(frames),
            "events": len(events),
            "segments": [
                {
                    "index": segment.index,
                    "start": segment.start.ts,
                    "end": segment.end_ts,
                    "initial_hand": segment.start.data.get("MP", "-"),
                    "initial_objective": segment.start.data.get("OBJ", "-"),
                    "actions": [
                        {
                            "ts": event.ts,
                            "kind": event.context.get("kind", event.event_type),
                            "target": click_target(event),
                        }
                        for event, _frame in iter_action_rows(segment)
                    ],
                }
                for segment in segments
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print_summary(log_path, frames, events, segments)


def latest_log() -> Path:
    log_dir = Path("logs") / "autoplay"
    logs = sorted(log_dir.glob("autoplay_*.log"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not logs:
        raise SystemExit("Nenhum log encontrado em logs/autoplay.")
    return logs[0]


if __name__ == "__main__":
    main()
