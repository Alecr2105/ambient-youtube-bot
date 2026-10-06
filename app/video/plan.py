"""Pure planning (no FFmpeg): segment variants and the timeline that strings them together."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.visuals.matcher import VisualChoice

MIN_PIECE_S = 45.0
MAX_PIECE_S = 150.0
INNER_CROSSFADE_S = (3.0, 6.0)


@dataclass(frozen=True)
class Piece:
    path: str
    start: float
    duration: float
    mirror: bool
    is_image: bool
    loop: bool = False  # a short animated scene repeated for the whole piece


@dataclass(frozen=True)
class Motion:
    zoom_base: float
    zoom_amplitude: float
    zoom_period: float
    zoom_phase: float
    pan_x: float
    pan_y: float
    pan_period: float
    pan_phase: float


@dataclass(frozen=True)
class Grade:
    brightness: float
    contrast: float
    saturation: float
    gamma_r: float
    gamma_b: float


#: No zoom and no pan: the picture is left exactly as it is.
STILL = Motion(zoom_base=1.0, zoom_amplitude=0.0, zoom_period=1.0, zoom_phase=0.0,
               pan_x=0.0, pan_y=0.0, pan_period=1.0, pan_phase=0.0)


def _grade(rng: np.random.Generator) -> Grade:
    return Grade(
        brightness=float(rng.uniform(-0.03, 0.02)),
        contrast=float(rng.uniform(0.98, 1.05)),
        saturation=float(rng.uniform(0.95, 1.10)),
        gamma_r=float(rng.uniform(0.97, 1.03)),
        gamma_b=float(rng.uniform(0.97, 1.03)),
    )


@dataclass(frozen=True)
class SegmentVariant:
    index: int
    length: float  # head + body + tail
    pieces: tuple[Piece, ...]
    inner_crossfades: tuple[float, ...]
    motion: Motion
    grade: Grade


@dataclass
class Timeline:
    edge: float
    body: float
    order: list[int]
    total: float = field(init=False)

    def __post_init__(self) -> None:
        n = len(self.order)
        self.total = 2 * self.edge + n * self.body + (n - 1) * self.edge


def _pieces_for(length: float, visuals: list[VisualChoice], rng: np.random.Generator, used: dict[str, list[tuple[float, float]]]) -> tuple[list[Piece], list[float]]:
    videos = [v for v in visuals if v.type == "video"]
    if not videos:
        # One photo (or animated loop) per segment, taken from the least used ones, so a video
        # shows as many different scenes as it has segments before repeating any of them.
        fewest = min(len(used.get(v.path, ())) for v in visuals)
        pool = [v for v in visuals if len(used.get(v.path, ())) == fewest]
        scene = pool[int(rng.integers(len(pool)))]
        used.setdefault(scene.path, []).append((0.0, length))
        mirror = bool(scene.allow_mirror and rng.random() < 0.3)
        return [Piece(scene.path, 0.0, length, mirror, scene.type == "image", scene.type == "loop")], []

    pieces: list[Piece] = []
    fades: list[float] = []
    covered = 0.0
    while covered < length:
        visual = videos[int(rng.integers(len(videos)))]
        duration = float(visual.duration or 0)
        fade = float(rng.uniform(*INNER_CROSSFADE_S)) if pieces else 0.0
        remaining = length - covered + fade
        piece_len = min(float(rng.uniform(MIN_PIECE_S, MAX_PIECE_S)), duration, remaining)
        if length - (covered + piece_len - fade) < MIN_PIECE_S / 3 and duration >= remaining:
            piece_len = remaining  # avoid a tiny last piece
        start = _pick_start(duration, piece_len, used.setdefault(visual.path, []), rng)
        used[visual.path].append((start, start + piece_len))
        mirror = bool(visual.allow_mirror and rng.random() < 0.3)
        pieces.append(Piece(visual.path, round(start, 3), round(piece_len, 3), mirror, False))
        if fade:
            fades.append(round(fade, 3))
        covered += piece_len - fade
    return pieces, fades


def _pick_start(duration: float, piece_len: float, used: list[tuple[float, float]], rng: np.random.Generator) -> float:
    latest = max(duration - piece_len, 0.0)
    best, best_overlap = 0.0, math.inf
    for _ in range(16):
        start = float(rng.uniform(0, latest)) if latest > 0 else 0.0
        overlap = sum(max(0.0, min(start + piece_len, e) - max(start, s)) for s, e in used)
        if overlap < best_overlap:
            best, best_overlap = start, overlap
        if overlap == 0:
            break
    return best


def plan_variants(visuals: list[VisualChoice], count: int, body: float, edge: float, seed: int, sleep: bool,
                  moving: bool = True) -> list[SegmentVariant]:
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0x51DE0]))
    used: dict[str, list[tuple[float, float]]] = {}
    length = body + 2 * edge
    variants = []
    still_grade = _grade(rng)  # with nothing moving, one look for the whole video instead of one per segment
    for index in range(count):
        pieces, fades = _pieces_for(length, visuals, rng, used)
        is_image = pieces[0].is_image
        motion = STILL if not moving else Motion(
            zoom_base=float(rng.uniform(1.03, 1.06)),
            zoom_amplitude=float(rng.uniform(0.04, 0.08) if is_image else rng.uniform(0.01, 0.025)),
            zoom_period=float(rng.uniform(60, 150)),
            zoom_phase=float(rng.uniform(0, 2 * math.pi)),
            pan_x=float(rng.uniform(0, 30)),
            pan_y=float(rng.uniform(0, 12)),
            pan_period=float(rng.uniform(80, 200)),
            pan_phase=float(rng.uniform(0, 2 * math.pi)),
        )
        grade = still_grade if not moving else _grade(rng)
        variants.append(SegmentVariant(index, length, tuple(pieces), tuple(fades), motion, grade))
    if sleep and moving:
        # Progressive darkening: later variants in the timeline are darker.
        steps = np.linspace(0.0, -0.08, count)
        variants = [
            SegmentVariant(v.index, v.length, v.pieces, v.inner_crossfades, v.motion,
                           Grade(v.grade.brightness + float(steps[i]), v.grade.contrast, v.grade.saturation * (1 - 0.03 * i / max(count - 1, 1)), v.grade.gamma_r, v.grade.gamma_b))
            for i, v in enumerate(variants)
        ]
    return variants


def is_animated(variants: list[SegmentVariant]) -> bool:
    """True when every scene is an animated loop: little moves, so it needs less bitrate than footage."""
    return bool(variants) and all(p.loop for v in variants for p in v.pieces)


def is_still(variants: list[SegmentVariant]) -> bool:
    """True when the whole video is a fixed picture: no zoom, no pan and no footage."""
    return bool(variants) and all(v.motion == STILL for v in variants) and all(p.is_image for v in variants for p in v.pieces)


def plan_timeline(duration: float, body: float, edge: float, variant_count: int, seed: int, sleep: bool) -> Timeline:
    n = max(1, math.ceil((duration - edge) / (body + edge)))
    rng = np.random.default_rng(np.random.SeedSequence([seed, 0x71E]))
    if sleep:
        order = [min(int(i * variant_count / n), variant_count - 1) for i in range(n)]
    else:
        order: list[int] = []
        while len(order) < n:
            cycle = list(rng.permutation(variant_count))
            if order and variant_count > 1 and cycle[0] == order[-1]:
                cycle[0], cycle[-1] = cycle[-1], cycle[0]
            order.extend(int(c) for c in cycle)
        order = order[:n]
    return Timeline(edge=edge, body=body, order=order)
