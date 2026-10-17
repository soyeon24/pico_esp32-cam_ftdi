"""RP2040 브리지의 Vision Stream 포트를 찾는다.

보드는 CDC 두 개짜리 복합 장치라 두 포트가 같은 VID:PID 로 올라오고 설명도 같다.
**인터페이스 번호만이 둘을 가른다** - 0 은 esptool 이 쓰는 브리지고, 프레임은 2 에서만
나온다. 엉뚱한 쪽을 열면 포트는 멀쩡히 열리는데 프레임이 영영 안 오므로, 사람이
고르게 두지 않고 여기서 정한다. Pi 는 부팅할 때마다 ttyACM 번호가 바뀔 수 있어서
번호로 적어 두는 것 자체가 위험하다.

Linux 는 그 번호를 두 군데에 적는다.

* `/sys/class/tty/ttyACM*/device/interface` - 펌웨어가 준 이름 그대로 `Vision Stream`.
  pyserial 이 `ListPortInfo.interface` 로 읽어 준다. 이게 있으면 제일 확실하다.
* `location` 끝자리 - `1-1:1.2` 의 `.2`. 문자열 디스크립터를 못 읽었을 때의 차선책.

Windows 는 `hwid` 의 `MI_02` 에 적는다. 개발 PC 에서도 같은 코드가 돌아야 하므로
(보드를 옮기기 전 검증은 PC 에서 한다) 양쪽을 다 본다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

VID, PID = 0x1209, 0x0001
VISION_ITF = 2          # CDC 1 = usb_descriptors.c 의 ITF_NUM_CDC1
BRIDGE_ITF = 0          # CDC 0 = esptool / ESP32 로그
VISION_NAME = "vision stream"

_MI = re.compile(r"MI_(\d+)", re.I)
_LOC_TAIL = re.compile(r"[.:](\d+)$")


@dataclass(frozen=True)
class Candidate:
    device: str
    description: str
    interface: int | None
    is_bridge: bool

    @property
    def is_vision(self) -> bool:
        return self.interface == VISION_ITF


def interface_number(port) -> int | None:
    """포트의 USB 인터페이스 번호. 못 알아내면 None."""
    name = (getattr(port, "interface", None) or "").strip().lower()
    if name == VISION_NAME:
        return VISION_ITF

    # location 은 `1-1:1.2` 또는 `1-1.2`. 앞쪽은 허브 경로라 끝자리만 본다.
    location = getattr(port, "location", None) or ""
    hwid = (getattr(port, "hwid", None) or "").upper()

    match = _MI.search(hwid)
    if match:
        return int(match.group(1))
    match = _LOC_TAIL.search(location)
    if match:
        return int(match.group(1))
    match = re.search(r"LOCATION=\S*[.:](\d+)", hwid)
    if match:
        return int(match.group(1))
    return None


def _is_board(port) -> bool:
    if getattr(port, "vid", None) == VID and getattr(port, "pid", None) == PID:
        return True
    hwid = (getattr(port, "hwid", None) or "").upper()
    return "1209:0001" in hwid or "1209&PID_0001" in hwid


def candidates(ports=None) -> list[Candidate]:
    """붙어 있는 브리지 포트를 전부 훑는다. `ports` 는 시험용 주입구다."""
    if ports is None:
        try:
            import serial.tools.list_ports as lp
        except ImportError as exc:
            # 검사는 pyserial 없이 돌아간다(포트 목록을 주입한다). 실행만 필요하다.
            raise SystemExit("pyserial 이 없습니다: pip install -r pi/requirements.txt") from exc
        ports = lp.comports()
    found = []
    for port in ports:
        if not _is_board(port):
            continue
        itf = interface_number(port)
        found.append(Candidate(port.device, port.description or "", itf,
                               is_bridge=itf == BRIDGE_ITF))
    return found


def find_vision_port(ports=None) -> str | None:
    """Vision Stream 포트 하나. 없으면 None.

    브리지 포트(인터페이스 0)는 **절대 돌려주지 않는다.** 그쪽에서는 ESP 로그 텍스트만
    나오고 프레임은 안 나오는데, 열기는 멀쩡히 열려서 '연결됐는데 화면이 안 바뀐다'로만
    보인다. 그 실패는 로그로도 잘 안 드러나므로 아예 후보에서 뺀다.
    """
    for candidate in candidates(ports):
        if candidate.is_vision:
            return candidate.device
    return None


def describe(ports=None) -> list[str]:
    """`--list` 가 찍을 줄들."""
    found = candidates(ports)
    if not found:
        return ["브리지 포트를 못 찾았습니다 (VID:PID 1209:0001).",
                "  USB 케이블과 RP2040 펌웨어를 확인하세요."]
    lines = []
    for candidate in found:
        role = ""
        if candidate.is_vision:
            role = "   <- Vision Stream. 이걸 씁니다"
        elif candidate.is_bridge:
            role = "   <- 브리지(esptool). 이거 말고"
        lines.append(f"  {candidate.device:14s} {candidate.description}{role}")
    return lines
