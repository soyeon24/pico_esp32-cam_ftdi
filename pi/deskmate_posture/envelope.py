"""`PostureVerdict` -> 팀 데이터 계약.

두 가지를 만든다.

* `tof_feature` — `deskmate/sensor/tof/<node>` 로 나갈 payload. 허브가 먹는다.
* 미리보기 envelope — Pi 5 Atlas 앱이 1초마다 긁는 `/api/state` 모양.

**라벨을 좁히는 곳이 여기 한 군데다.** `posture.py` 는 UPRIGHT/SLUMP/RECLINE/DROWSY 로
판정하는데 계약 enum(`data-spec.md` 6.1)에는 DROWSY 가 없다. 꾸벅임은 자세가 아니라
시간 패턴이라 `nod_rate_hz` 가 나르도록 설계된 필드가 이미 있기 때문이다. 그래서
enum 에는 기하만 싣고(꾸벅이는 머리는 앞으로 내려가 있으므로 `lean_forward`),
판정이 실제로 뭐라고 했는지는 `posture_detail` 에 그대로 남긴다. 좁히기만 하고
버리지는 않는다 - 나중에 로그를 되짚을 때 필요한 건 좁혀진 쪽이 아니다.
"""
from __future__ import annotations

import math
import time
from typing import Any

SCHEMA_VERSION = "1.0"

# data-spec.md 6.1 의 posture enum. 이 다섯 개 말고는 내보내지 않는다.
POSTURE_ENUM = {
    "UPRIGHT": "upright",
    "SLUMP": "slouch",
    "RECLINE": "lean_back",
    "DROWSY": "lean_forward",
    "ABSENT": "away",
    "BASELINE": "unknown",
    "UNKNOWN": "unknown",
}

# 화면 국면. 허브 FSM 의 phase 와 같은 어휘를 쓰되 자세 하나로만 정한 값이다.
PREVIEW_PHASE = {
    "UPRIGHT": "focus",
    "SLUMP": "fatigue",
    "DROWSY": "fatigue",
    "RECLINE": "fatigue",
    "ABSENT": "idle",
    "BASELINE": "start",
    "UNKNOWN": "start",
}

LABEL_TEXT = {
    "UPRIGHT": "바른 자세",
    "SLUMP": "엎드림",
    "RECLINE": "뒤로 젖힘",
    "DROWSY": "졸음 (꾸벅임)",
    "ABSENT": "자리 비움",
    "BASELINE": "기준 측정 중",
    "UNKNOWN": "기준 없음",
}


def _num(value: float) -> float | None:
    """JSON 에 NaN/Inf 를 흘리지 않는다.

    `json.dumps` 는 기본값으로 `NaN` 을 찍는데 그건 JSON 이 아니다. Dart 의
    `jsonDecode` 가 FormatException 을 던지고 화면은 링크가 끊긴 것처럼 보인다.
    머리를 못 찾은 프레임의 `head_mm` 이 실제로 NaN 이라 흔히 밟는다.
    """
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _unit(value: float) -> float:
    value = _num(value)
    return 0.0 if value is None else max(0.0, min(1.0, value))


def tof_feature(verdict, *, valid_zones: int, grid: tuple[int, int],
                stale: bool = False) -> dict[str, Any]:
    """`deskmate/sensor/tof/<node>` 의 data 블록.

    `valid`=False 는 '링크는 살아 있는데 이 샘플은 믿지 말라'는 뜻이다. 기준이 아직
    없거나(BASELINE/UNKNOWN) 프레임이 오래됐으면 여기서 꺼 준다. 허브가 이걸 안 보고
    쓰면 캘리브레이션 중인 30초가 그대로 '바른 자세'로 들어간다.
    """
    feats = verdict.features
    parts = verdict.parts or {}
    label = verdict.label
    cols, rows = grid
    return {
        "present": bool(verdict.present),
        "posture": POSTURE_ENUM.get(label, "unknown"),
        "posture_detail": label.lower(),        # 규약 외 - 좁히기 전 원본 라벨
        "motion_score": _unit(feats.motion),
        "head_delta_mm": _num(parts.get("dist_mm", 0.0)),
        # posture.py 는 분당 횟수를 센다. 계약 단위는 Hz 다.
        "nod_rate_hz": _num(verdict.nod_rate / 60.0),
        "valid_zones": int(valid_zones),
        "grid_width": int(cols),
        "grid_height": int(rows),
        "coverage_ratio": _unit(feats.occupancy),
        # 규약 외 - 허브의 Signal(phi, delta) 로 바로 들어가는 값이다. 여기서
        # 이미 계산해 놓고 버리면 허브가 같은 것을 다시 만들어야 한다.
        "phi": _unit(verdict.phi),
        "delta": _unit(verdict.delta),
        "valid": bool(not stale and label not in ("BASELINE", "UNKNOWN")),
    }


def health(*, stage: str, stale: bool, fps: float, frames: int,
           link_error: str | None) -> dict[str, Any]:
    """`deskmate/health/<node>` 의 data 블록.

    끊긴 링크는 살아 있는 링크와 똑같이 보인다 - 마지막 프레임이 계속 재발행되므로.
    그래서 생존 신호는 판정과 따로 내보낸다.
    """
    return {
        "status": "error" if link_error else ("stale" if stale else "ok"),
        "stage": stage,
        "fps": round(float(fps), 2),
        "frames": int(frames),
        "link_error": link_error,
    }


def wrap(data: dict[str, Any], *, node: str, boot_id: str, seq: int,
         ts: float | None = None) -> dict[str, Any]:
    """공통 envelope (`mqtt-topics.md`)."""
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": time.time() if ts is None else float(ts),
        "node": node,
        "boot_id": boot_id,
        "seq": int(seq),
        "data": data,
    }


def preview_envelope(verdict, *, node: str, boot_id: str, seq: int,
                     scenario: str, feature: dict[str, Any],
                     ts: float | None = None) -> dict[str, Any]:
    """Pi 5 Atlas 앱이 읽는 `/api/state` 모양.

    **이건 허브 FSM 의 출력이 아니다.** 허브가 붙기 전에도 Pi 5 화면이 살아 있게
    하려고 자세 하나로 같은 모양을 채운 것이다. 그 사실을 화면에서 알아볼 수 있게
    세 군데에 박아 둔다 - `fsm_state` 는 `POSTURE_*` 라 states.py 의 어떤 이름과도
    겹치지 않고, `source` 는 enum 밖의 `posture` 이며, `scenario` 가 한국어로 말한다.

    `context` 는 키보드 신호에서 나오는 값이라 이 노드는 알 수 없다. 화면이 요구하는
    필수 필드라 `mixed` 로 채우되, 그건 측정값이 아니라 자리 채우기다.
    """
    label = verdict.label
    summary: dict[str, Any] = {
        "present": bool(verdict.present),
        "scenario": scenario,
        "posture": feature,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "ts": time.time() if ts is None else float(ts),
        "node": node,
        "boot_id": boot_id,
        "seq": int(seq),
        "data": {
            "fsm_state": f"POSTURE_{label}",
            "phase": PREVIEW_PHASE.get(label, "start"),
            "context": "mixed",         # 자리 채우기. 허브가 붙으면 실제 값이 온다
            "c_focus": _unit(verdict.phi),
            "c_fatigue": _unit(verdict.delta),
            "confidence": max(_unit(verdict.phi), _unit(verdict.delta)),
            "source": "posture",        # enum(fsm|classifier|fused) 밖 - 일부러
            "gate": "none",             # 자세 노드는 제어를 게이트하지 않는다
            "cause": "posture" if label in ("SLUMP", "RECLINE", "DROWSY") else None,
            "reasons": reasons_for(verdict),
            "sensor_summary": summary,
        },
    }


def reasons_for(verdict) -> list[str]:
    """화면에 띄울 근거. 판정이 실제로 본 축만 적는다."""
    parts = verdict.parts or {}
    out = ["posture_only"]
    if verdict.label == "SLUMP":
        out.append("head_dropped_and_held")
    elif verdict.label == "RECLINE":
        out.append("moved_away_from_sensor")
    elif verdict.label == "DROWSY":
        out.append(f"nods_per_min_{verdict.nod_rate:.1f}")
    elif verdict.label == "ABSENT":
        out.append("desk_empty")
    if verdict.note:
        out.append(verdict.note)
    if parts.get("headw_ratio"):
        out.append(f"head_width_ratio_{parts['headw_ratio']:.2f}")
    return out


def scenario_text(verdict, *, stale: bool, hint: str) -> str:
    """화면 한 줄. 캘리브레이션 중이면 그 안내가 우선이다."""
    if hint:
        return hint
    if stale:
        return "센서 프레임이 끊겼습니다 - 보드 USB 를 확인하세요"
    return f"{LABEL_TEXT.get(verdict.label, verdict.label)} · 자세 판정만 표시 중 (허브 미연결)"
