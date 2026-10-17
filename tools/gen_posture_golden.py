#!/usr/bin/env python3
"""자세 판정의 골든 벡터를 뽑는다. Dart 포팅이 같은 숫자를 내는지 채점하는 기준이다.

    python tools/gen_posture_golden.py <출력.json>

판정을 ATLAS 앱(Dart)으로 옮기면서 "옮겼는데 결과가 같은가" 를 확인할 방법이 필요하다.
그래서 여기서 **입력과 기대 출력을 한 파일로 고정**한다. Dart 테스트가 같은 입력을
만들어 같은 숫자가 나오는지 본다.

입력을 타원이 아니라 **사각형 목록**으로 기술하는 이유가 있다. 기존 테스트
(`test_posture.py`)는 타원으로 사람을 그리는데, 그 부동소수 계산을 두 언어에서
비트 단위로 맞추는 건 별개의 위험이다. 사각형은 정수 비교라 어느 언어에서든 같다.
판정이 지나는 길(`mask_to_zone` -> `extract` -> `judge`)은 똑같이 밟는다.

numpy 는 마스크를 만드는 데만 쓴다. 판정 자체는 이미 순수 Python 이다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np

import esp_source as es
from posture import PostureTracker, ZONE_COLS as W, ZONE_ROWS as H

FPS = 12.0          # 센서 실제 프레임률
BG_FRAMES = 20      # 빈 책상
BASE_FRAMES = 40    # 바른 자세 기준


def mask_of(rects) -> np.ndarray:
    """[[행, 열, 높이, 너비], ...] -> 이진 마스크."""
    mask = np.zeros((H, W), np.uint8)
    for r0, c0, h, w in rects:
        mask[max(r0, 0):r0 + h, max(c0, 0):c0 + w] = 1
    return mask


# 사람 하나를 머리 사각형 + 몸통 사각형으로 그린다.
def figure(head_row: int, head_w: int = 9, body_w: int = 26, gap: int = 1,
           head_h: int = 5, body_h: int = 16, hand: str | None = None):
    cx = W // 2
    rects = [
        [head_row, cx - head_w // 2, head_h, head_w],
        [head_row + head_h + gap, cx - body_w // 2, body_h, body_w],
    ]
    if hand == "side":
        rects.append([head_row, cx + 16, 22, 2])
    elif hand == "over":
        rects.append([max(head_row - 6, 0), cx, 8, 2])
    return rects


def nod_row(step: int, base: int = 4, depth: int = 8, period: int = 72) -> int:
    """12fps 기준 6초 주기. 1.5초 하강 · 0.5초 복귀 · 나머지 정지."""
    phase = step % period
    if phase < 18:
        return base + round(depth * phase / 18)
    if phase < 24:
        return base + round(depth * (1.0 - (phase - 18) / 6))
    return base


CASES = [
    ("upright", [figure(4) for _ in range(30)]),
    ("slump", [figure(15) for _ in range(60)]),
    ("recline", [figure(6, head_w=5, body_w=15, body_h=10) for _ in range(60)]),
    ("drowsy", [figure(nod_row(i)) for i in range(360)]),
    ("absent", [[] for _ in range(20)]),
    ("hand_side", [figure(4, hand="side") for _ in range(30)]),
    ("hand_over", [figure(4, hand="over") for _ in range(30)]),
    ("brief_dip", [figure(15) for _ in range(20)]),
    ("wake_up", [figure(nod_row(i)) if i < 240 else figure(4) for i in range(400)]),
    ("drift_away", [figure(4, head_w=9 - i // 40, body_w=26 - i // 15)
                    for i in range(120)]),
]


def record(verdict) -> dict:
    """JSON 으로 남길 판정 한 장. NaN 은 null 로 - JSON 에 NaN 은 없다."""
    def num(value):
        value = float(value)
        return None if value != value else round(value, 9)

    feats = verdict.features
    return {
        "label": verdict.label,
        "present": bool(verdict.present),
        "phi": num(verdict.phi),
        "delta": num(verdict.delta),
        "nod_rate": num(verdict.nod_rate),
        "occupancy": num(feats.occupancy),
        "top_row": num(feats.top_row),
        "centroid_row": num(feats.centroid_row),
        "spread": num(feats.spread),
        "head_mm": num(feats.head_mm),
        "motion": num(feats.motion),
        "head_w": num(feats.head_w),
        "parts": {key: num(value) for key, value in sorted(verdict.parts.items())},
    }


def run_case(live_rects) -> tuple[list, dict]:
    tracker, t = PostureTracker(), 0.0
    tracker.start_background(BG_FRAMES)
    for _ in range(BG_FRAMES):
        t += 1 / FPS
        tracker.update(es.mask_to_zone(mask_of([])), t)
    tracker.start_baseline(BASE_FRAMES)
    for _ in range(BASE_FRAMES):
        t += 1 / FPS
        tracker.update(es.mask_to_zone(mask_of(figure(4))), t)
    if tracker.baseline is None:
        raise SystemExit("baseline 을 못 잡았다 - 기준 자세 사각형을 확인할 것")

    expect = []
    for rects in live_rects:
        t += 1 / FPS
        expect.append(record(tracker.update(es.mask_to_zone(mask_of(rects)), t)))
    base = tracker.baseline
    return expect, {
        "top_row": round(base.top_row, 9),
        "spread": round(base.spread, 9),
        "head_mm": round(base.head_mm, 9),
        "head_w": round(base.head_w, 9),
        "samples": base.samples,
        "clipped": base.clipped,
    }


def main(out_path: str) -> int:
    cases = []
    for name, live in CASES:
        expect, baseline = run_case(live)
        cases.append({"name": name, "live": live, "expect": expect,
                      "baseline": baseline})
        labels = {}
        for item in expect:
            labels[item["label"]] = labels.get(item["label"], 0) + 1
        print(f"{name:12s} {len(live):4d}프레임  {labels}")

    doc = {
        "note": "tools/gen_posture_golden.py 가 만든다. 손으로 고치지 말 것.",
        "meta": {
            "rows": H, "cols": W, "fps": FPS,
            "bg_frames": BG_FRAMES, "baseline_frames": BASE_FRAMES,
            "baseline_rects": figure(4),
        },
        "cases": cases,
    }
    Path(out_path).write_text(json.dumps(doc, indent=1, ensure_ascii=False),
                              encoding="utf-8")
    total = sum(len(c["expect"]) for c in cases)
    print(f"\n{out_path} - {len(cases)}개 시나리오 · {total}프레임")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
