"""DESKMATE 자세 노드 - Raspberry Pi 4 에서 헤드리스로 도는 판정기.

`tools/posture.py` 의 판정은 **한 줄도 건드리지 않는다.** 이 패키지가 더하는 것은
보드를 찾고, 사람 없이 캘리브레이션을 끝내고, 결과를 팀 계약대로 내보내는 것뿐이다.

    시리얼(Vision Stream) -> esp_source.EspZoneSource -> posture.PostureTracker
                                                              |
                          MQTT deskmate/sensor/tof/<node> <----+----> HTTP /api/state

PC 에서는 `tools/posture_viewer.py` 가 창을 띄우고 키로 캘리브레이션을 몰았다.
Pi 에는 창도 키보드도 없으므로 그 두 가지를 시간과 HTTP 로 대신한다.
"""
from __future__ import annotations

import sys
from pathlib import Path

# `posture.py` 와 `esp_source.py` 는 저장소 `tools/` 에 평평하게 놓여 있고 서로를
# 최상위 모듈로 import 한다(`from posture import ...`). 복사본을 만들면 판정이
# 두 군데로 갈라지므로, 사본 대신 그 디렉터리를 경로에 넣어 원본을 그대로 쓴다.
TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"


def use_repo_tools() -> Path:
    """`tools/` 를 import 경로에 올리고 그 경로를 돌려준다. 여러 번 불러도 안전하다."""
    path = str(TOOLS_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return TOOLS_DIR


use_repo_tools()

__all__ = ["TOOLS_DIR", "use_repo_tools"]
