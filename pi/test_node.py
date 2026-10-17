#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""보드 없이 도는 Pi 자세 노드 검사.

    python pi/test_node.py

`tools/test_posture.py` 가 판정 자체를 보므로 여기서는 **그 위에 얹은 것들만** 본다 -
포트를 고르는 규칙, 계약 필드로 옮기는 변환, 키 없이 도는 캘리브레이션 순서, 그리고
Atlas 앱이 실제로 읽는 HTTP 응답. pyserial 도 paho 도 필요 없다.

첫 실패에서 멈추지 않고 전부 돌린 뒤 실패 개수만큼 비정상 종료한다.
"""
from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from deskmate_posture import calibration, envelope, ports, use_repo_tools
from deskmate_posture.preview import StateStore, make_server

use_repo_tools()
import esp_source as es                                      # noqa: E402
from posture import PostureTracker, ZONE_COLS as W, ZONE_ROWS as H   # noqa: E402

failed = 0
Y, X = np.mgrid[0:H, 0:W]


def check(name: str, ok: bool, detail: str = "") -> None:
    global failed
    if ok:
        print(f"  ok    {name}")
    else:
        failed += 1
        print(f"  FAIL  {name}{('  - ' + detail) if detail else ''}")


def person(head_row: float = 6.0, scale: float = 1.0) -> np.ndarray:
    """머리 하나와 몸통 하나. scale 이 작으면 같은 사람이 더 멀리 있는 것."""
    cx = W / 2
    r = 5.0 * scale
    mask = ((X - cx) ** 2 / r ** 2 + (Y - (head_row + 4 * scale)) ** 2 / r ** 2) <= 1.0
    mask |= (((X - cx) ** 2 / (13.0 * scale) ** 2
              + (Y - (head_row + 20 * scale)) ** 2 / (15.0 * scale) ** 2) <= 1.0)
    return mask.astype(np.uint8)


EMPTY = np.zeros((H, W), np.uint8)


# -- 1. 포트 고르기 --------------------------------------------------------
# 두 포트가 같은 VID:PID 로 올라오고 인터페이스 번호만 다르다. 브리지(0)를 열면
# 포트는 멀쩡히 열리는데 프레임이 영영 안 온다 - 조용히 실패하는 쪽이라 못 고른다.

class FakePort:
    def __init__(self, device, *, vid=0x1209, pid=0x0001, interface=None,
                 location=None, hwid="", description="Pico Bridge"):
        self.device, self.vid, self.pid = device, vid, pid
        self.interface, self.location = interface, location
        self.hwid, self.description = hwid, description


print("1. 포트 고르기")
linux = [
    FakePort("/dev/ttyACM0", interface="ESP32 Bridge", location="1-1:1.0"),
    FakePort("/dev/ttyACM1", interface="Vision Stream", location="1-1:1.2"),
]
check("Linux: 인터페이스 이름으로 Vision Stream 을 고른다",
      ports.find_vision_port(linux) == "/dev/ttyACM1",
      str(ports.find_vision_port(linux)))

# 문자열 디스크립터를 못 읽는 경우가 있다. 그때는 location 끝자리가 남는다.
no_name = [
    FakePort("/dev/ttyACM0", location="1-1:1.0"),
    FakePort("/dev/ttyACM1", location="1-1:1.2"),
]
check("Linux: 이름이 없으면 location 끝자리로 고른다",
      ports.find_vision_port(no_name) == "/dev/ttyACM1",
      str(ports.find_vision_port(no_name)))

windows = [
    FakePort("COM6", hwid=r"USB\VID_1209&PID_0001&MI_00\6&1"),
    FakePort("COM7", hwid=r"USB\VID_1209&PID_0001&MI_02\6&1"),
]
check("Windows: MI_02 를 고른다",
      ports.find_vision_port(windows) == "COM7", str(ports.find_vision_port(windows)))

check("브리지만 있으면 아무것도 안 고른다",
      ports.find_vision_port([linux[0]]) is None)
check("다른 장치는 후보에 안 들어간다",
      ports.find_vision_port([FakePort("/dev/ttyUSB0", vid=0x10C4, pid=0xEA60,
                                       interface="Vision Stream")]) is None)
check("찾은 게 없으면 describe 가 이유를 말한다",
      "못 찾았" in ports.describe([])[0], ports.describe([])[0])


# -- 2. 계약 필드 ----------------------------------------------------------

def calibrated(fps: float = 12.0):
    """두 단계를 다 지난 tracker 와 거기까지 흐른 시계."""
    tracker, t = PostureTracker(), 0.0
    tracker.start_background(20)
    for _ in range(20):
        t += 1 / fps
        tracker.update(es.mask_to_zone(EMPTY), t)
    tracker.start_baseline(40)
    for _ in range(40):
        t += 1 / fps
        tracker.update(es.mask_to_zone(person()), t)
    return tracker, t


print("\n2. 계약 필드 (deskmate/sensor/tof)")
tracker, t = calibrated()
assert tracker.baseline is not None, "baseline 이 안 잡혔다 - 아래 검사가 무의미하다"
zone = es.mask_to_zone(person())
t += 1 / 12.0
verdict = tracker.update(zone, t)
feature = envelope.tof_feature(verdict, valid_zones=int(np.isfinite(zone.depth_mm).sum()),
                               grid=(W, H))

spec_fields = {"present", "posture", "motion_score", "head_delta_mm", "nod_rate_hz",
               "valid_zones", "grid_width", "grid_height", "coverage_ratio"}
check("data-spec 6.1 필드가 다 있다", spec_fields <= set(feature),
      str(sorted(spec_fields - set(feature))))
check("posture 는 계약 enum 안에 있다",
      feature["posture"] in {"upright", "lean_forward", "lean_back", "slouch",
                             "away", "unknown"}, str(feature["posture"]))
check("grid 는 54x42", (feature["grid_width"], feature["grid_height"]) == (54, 42))
check("바른 자세는 upright 로 나간다", feature["posture"] == "upright",
      f"{verdict.label} -> {feature['posture']}")
check("기준이 잡혔으므로 valid", feature["valid"] is True)

# 라벨을 좁히는 규칙 자체. DROWSY 는 enum 에 없어서 기하로 내려가고 원본은 detail 에 남는다.
for label, expect in (("UPRIGHT", "upright"), ("SLUMP", "slouch"),
                      ("RECLINE", "lean_back"), ("DROWSY", "lean_forward"),
                      ("ABSENT", "away"), ("UNKNOWN", "unknown")):
    check(f"{label} -> {expect}", envelope.POSTURE_ENUM[label] == expect)

verdict.nod_rate = 6.0
narrowed = envelope.tof_feature(verdict, valid_zones=2268, grid=(W, H))
check("분당 꾸벅임을 Hz 로 바꾼다", abs(narrowed["nod_rate_hz"] - 0.1) < 1e-9,
      str(narrowed["nod_rate_hz"]))

# 자리 비움 프레임은 머리를 못 찾아 head_mm 이 NaN 이다. json 은 NaN 을 못 싣는다.
t += 1 / 12.0
absent = tracker.update(es.mask_to_zone(EMPTY), t)
absent_feature = envelope.tof_feature(absent, valid_zones=2268, grid=(W, H))
check("자리 비움은 away", absent_feature["posture"] == "away", absent.label)
# 자리 비움은 '못 잰 것'이 아니라 잰 결과다. valid 를 내리면 허브가 그 샘플을 통째로
# 버려서 재실이 False 로 내려가는 순간을 못 본다 - FSM 의 present 가 바로 이 값이다.
check("자리 비움도 유효한 판정이다", absent_feature["valid"] is True)
check("자리 비움은 present=False", absent_feature["present"] is False)
try:
    json.dumps(absent_feature, allow_nan=False)
    ok_nan = True
except ValueError as exc:
    ok_nan, detail = False, str(exc)
check("NaN 이 JSON 으로 새지 않는다", ok_nan, "" if ok_nan else detail)

# 기준을 잡기 전 프레임을 허브가 그냥 먹으면 캘리브레이션 30초가 '바른 자세'가 된다.
fresh = PostureTracker()
fresh.start_background(5)
pending = envelope.tof_feature(fresh.update(es.mask_to_zone(EMPTY), 0.0),
                               valid_zones=2268, grid=(W, H))
check("기준 수집 중에는 valid=False", pending["valid"] is False, pending["posture_detail"])


# -- 3. 화면 계약 (/api/state) --------------------------------------------
# Atlas 앱 display_state.dart 의 fromEnvelope 가 요구하는 것 그대로다. 하나라도
# 비면 앱은 FormatException 을 던지고 화면은 링크가 끊긴 것처럼 보인다.

print("\n3. 화면 계약 (/api/state)")
preview = envelope.preview_envelope(
    verdict, node="posture-cam", boot_id="0badc0de", seq=7,
    scenario=envelope.scenario_text(verdict, stale=False, hint=""), feature=feature)
data = preview["data"]

check("schema_version 이 1.0", preview["schema_version"] == "1.0")
check("seq/ts 가 있다", isinstance(preview["seq"], int) and preview["ts"] > 0)
for key in ("fsm_state", "phase", "context"):
    check(f"{key} 가 비지 않은 문자열", isinstance(data[key], str) and data[key],
          repr(data.get(key)))
for key in ("c_focus", "c_fatigue", "confidence"):
    check(f"{key} 가 0..1", 0.0 <= data[key] <= 1.0, str(data[key]))
check("phase 가 앱이 아는 어휘",
      data["phase"] in {"idle", "start", "focus", "fatigue", "recovery", "end"},
      data["phase"])
check("허브 FSM 인 척하지 않는다 (fsm_state)", data["fsm_state"].startswith("POSTURE_"),
      data["fsm_state"])
check("허브 FSM 인 척하지 않는다 (source)", data["source"] == "posture", data["source"])
check("제어를 게이트하지 않는다", data["gate"] == "none", data["gate"])
check("sensor_summary 에 자세 특징이 들어 있다",
      data["sensor_summary"].get("posture", {}).get("posture") == feature["posture"])
check("없는 센서 값을 0 으로 채우지 않는다",
      "co2_ppm" not in data["sensor_summary"] and "lux" not in data["sensor_summary"])
check("화면 문구가 허브 미연결임을 말한다", "허브" in data["sensor_summary"]["scenario"],
      data["sensor_summary"]["scenario"])
try:
    json.dumps(preview, allow_nan=False)
    ok_preview = True
except ValueError as exc:
    ok_preview, detail = False, str(exc)
check("미리보기도 NaN 없이 직렬화된다", ok_preview, "" if ok_preview else detail)

hint_text = envelope.scenario_text(verdict, stale=False, hint="1/2 비켜 주세요")
check("캘리브레이션 중에는 안내가 우선", hint_text == "1/2 비켜 주세요", hint_text)
check("링크가 끊기면 화면이 그렇게 말한다",
      "끊겼" in envelope.scenario_text(verdict, stale=True, hint=""))


# -- 4. 키 없는 캘리브레이션 ----------------------------------------------
# 순서가 전부다. 센서 배경('b')이 끝나기 전에 zone 기준을 뜨면 사람이 섞인 배경
# 위에 기준이 굳고, 그 뒤로는 무엇을 해도 사람을 못 찾는다.

class FakeSource:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.log: list[str] = []

    def send(self, line: str) -> None:
        self.sent.append(line)
        if line == "b":
            self.log.append("# bg captured")   # 실기의 ESP 가 이 문구를 보낸다

    def log_lines(self) -> list[str]:
        return list(self.log)


print("\n4. 키 없는 캘리브레이션")
source = FakeSource()
tracker = PostureTracker()
cal = calibration.Calibrator(tracker, source, clear_s=2.0, sit_s=1.0,
                             bg_frames=10, baseline_frames=20)
dt, clock, seen = 1 / 30.0, 0.0, []
sent_at = None
for _ in range(2000):
    clock += dt
    mask = person() if cal.stage in (calibration.SIT, calibration.BASELINE,
                                     calibration.LIVE) else EMPTY
    tracker.update(es.mask_to_zone(mask), clock)
    cal.step(clock)
    if not seen or seen[-1] != cal.stage:
        seen.append(cal.stage)
    if sent_at is None and source.sent:
        sent_at = clock
    if cal.live:
        break

check("단계 순서가 그대로다",
      seen == [calibration.CLEAR, calibration.SETTLE, calibration.BACKGROUND,
               calibration.SIT, calibration.BASELINE, calibration.LIVE], str(seen))
check("'b' 를 정확히 한 번 보낸다", source.sent == ["b"], str(source.sent))
check("비켜 줄 시간을 다 준 뒤에 보낸다", sent_at is not None and sent_at >= 2.0,
      str(sent_at))
check("센서 배경이 zone 기준보다 먼저다",
      seen.index(calibration.SETTLE) < seen.index(calibration.BACKGROUND))
check("끝나면 기준이 잡혀 있다", tracker.baseline is not None and cal.live)

# 안내 문구는 남은 시간을 세어 준다 - 화면에 그대로 뜨는 값이다.
fresh_cal = calibration.Calibrator(PostureTracker(), FakeSource(), clear_s=8.0)
fresh_cal.restart(100.0)
check("안내가 남은 초를 센다", "8초" in fresh_cal.hint(100.0), fresh_cal.hint(100.0))
check("판정 중에는 안내가 없다", cal.hint(clock) == "", cal.hint(clock))

# 다시 걸면 옛 기준으로 계속 판정하지 않는다 - 화면은 멀쩡한데 값만 틀린 상태를 막는다.
cal.restart(clock)
check("재시작이 옛 기준을 버린다",
      tracker.baseline is None and not tracker.background.captured)
check("재시작하면 첫 단계로 돌아간다", cal.stage == calibration.CLEAR, cal.stage)


# -- 5. HTTP ---------------------------------------------------------------

def get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def post(url: str, body: dict | None):
    data = b"" if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


print("\n5. HTTP")
store = StateStore()
server = make_server("127.0.0.1", 0, store, lambda: {"status": "ok"})
threading.Thread(target=server.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{server.server_port}"

status, body = get(f"{base}/api/state")
check("상태가 없으면 503", status == 503 and body["error"] == "state_not_ready", str(body))
store.publish(preview)
status, body = get(f"{base}/api/state")
check("발행하면 그대로 돌려준다",
      status == 200 and body["data"]["fsm_state"] == data["fsm_state"], str(status))
check("/health 는 항상 답한다", get(f"{base}/health")[0] == 200)
check("모르는 경로는 404", get(f"{base}/nope")[0] == 404)

check("수락 피드백은 202", post(f"{base}/api/feedback",
                              {"request_id": "atlas-display", "verdict": "accept"}) == 202)
check("피드백이 큐에 쌓인다", (store.pop_feedback() or {}).get("verdict") == "accept")
check("모르는 verdict 는 400",
      post(f"{base}/api/feedback", {"verdict": "maybe"}) == 400)
check("본문이 없으면 400", post(f"{base}/api/feedback", None) == 400)

check("캘리브레이션 요청은 202", post(f"{base}/api/calibrate", {}) == 202)
check("요청을 한 번만 집어 간다",
      store.take_calibrate() is True and store.take_calibrate() is False)

# 본문을 안 읽고 응답한 뒤 소켓을 닫으면 커널이 RST 를 보내고 클라이언트는 202 를
# 못 본다. 타이밍에 걸리는 버그라 한 번 눌러서는 안 잡힌다 - 실제로 세 번에 한 번꼴로
# ConnectionAbortedError 가 났다.
try:
    codes = {post(f"{base}/api/calibrate", {"from": "atlas-display"})
             for _ in range(20)}
    burst_ok, burst_detail = codes == {202}, str(codes)
except OSError as exc:
    burst_ok, burst_detail = False, f"{type(exc).__name__}: {exc}"
check("본문이 있는 요청을 연달아 보내도 끊기지 않는다", burst_ok, burst_detail)
store.take_calibrate()
check("모르는 경로에 본문을 보내도 404 로 답한다",
      post(f"{base}/nope", {"x": 1}) == 404)
server.shutdown()


# -- 6. 노드 전체 (가짜 시리얼) -------------------------------------------
# 위 검사들은 조각을 하나씩 본다. 조각이 다 맞아도 루프가 안 돌 수 있으므로
# 시리얼만 가짜로 바꾸고 `node.py` 를 실제로 띄운다.

class FakeZoneSource:
    """`EspZoneSource` 자리. read() 가 ZoneFrame 을 내는 것만 맞추면 된다."""

    def __init__(self, *_args, **_kwargs) -> None:
        self.sent: list[str] = []
        self.log: list[str] = []
        self.error = None
        self.stale = False
        self.released = False
        self.seated = False
        self.frozen = False       # 링크가 끊긴 척: 같은 프레임만 돌려준다
        self._n = 0
        self._last = None

    def read(self):
        if self.frozen and self._last is not None:
            return self._last
        self._n += 1
        zone = es.mask_to_zone(person() if self.seated else EMPTY)
        # 같은 배열이면 노드가 새 프레임이 아니라고 보고 건너뛴다. 실기의 센서는
        # 프레임마다 값이 흔들리므로 배경 zone 하나만 움직여 그걸 흉내 낸다.
        zone.depth_mm[0, 0] += self._n % 3
        self._last = zone
        return zone

    def send(self, line: str) -> None:
        self.sent.append(line)
        if line == "b":
            self.log.append("# bg captured")

    def log_lines(self) -> list[str]:
        return list(self.log)

    def release(self) -> None:
        self.released = True


print("\n6. 노드 전체 (가짜 시리얼)")
from deskmate_posture import node as node_mod                 # noqa: E402
from deskmate_posture.__main__ import build_parser            # noqa: E402

fake = FakeZoneSource()
es.EspZoneSource = lambda *a, **k: fake        # node.py 가 부를 때 이걸 집는다
args = build_parser().parse_args([
    "--port", "/dev/null", "--host", "127.0.0.1", "--port-http", "0",
    "--clear-s", "0.2", "--sit-s", "0.2", "--bg-frames", "5",
    "--baseline-frames", "10", "--rate", "200", "--publish-hz", "20",
])
live = node_mod.PostureNode(args)
base = f"http://127.0.0.1:{live.server.server_port}"
worker = threading.Thread(target=live.run, daemon=True)
worker.start()

import time                                                    # noqa: E402
deadline = time.monotonic() + 20.0
while time.monotonic() < deadline:
    if live.calibrator.stage == calibration.SIT:
        fake.seated = True          # 앉으라고 했으니 앉는다
    if live.calibrator.live:
        break
    time.sleep(0.05)

check("보드 없이도 판정까지 간다", live.calibrator.live, live.calibrator.stage)
check("노드가 센서에 'b' 를 보냈다", fake.sent == ["b"], str(fake.sent))

status, body = get(f"{base}/api/state")
check("/api/state 가 200 을 준다", status == 200, str(status))
check("살아 있는 판정이 실린다",
      body["data"]["fsm_state"] == "POSTURE_UPRIGHT", body["data"]["fsm_state"])
check("seq 가 올라간다", body["seq"] > 1, str(body["seq"]))
check("판정 중에는 화면 문구가 안내가 아니다",
      "허브" in body["data"]["sensor_summary"]["scenario"],
      body["data"]["sensor_summary"]["scenario"])

status, health_body = get(f"{base}/health")
check("/health 가 프레임 수를 센다", health_body["frames"] > 0, str(health_body))
check("fps 를 잰다", health_body["fps"] > 0, str(health_body["fps"]))

# 끊긴 링크는 살아 있는 링크와 똑같이 보인다 - 새 프레임이 없으면 갱신할 일도
# 없어서 마지막 판정이 화면에 그대로 남는다. 그래서 프레임이 멈춰도 계속 내보낸다.
before = get(f"{base}/api/state")[1]["seq"]
fake.frozen, fake.stale = True, True
frozen_deadline = time.monotonic() + 15.0
while time.monotonic() < frozen_deadline:
    body = get(f"{base}/api/state")[1]
    if body["seq"] > before and "끊겼" in body["data"]["sensor_summary"]["scenario"]:
        break
    time.sleep(0.1)
check("프레임이 멈춰도 화면이 갱신된다", body["seq"] > before,
      f"{before} -> {body['seq']}")
check("끊긴 링크를 화면이 말한다", "끊겼" in body["data"]["sensor_summary"]["scenario"],
      body["data"]["sensor_summary"]["scenario"])
check("끊긴 동안의 샘플은 valid=False",
      body["data"]["sensor_summary"]["posture"]["valid"] is False)
fake.frozen, fake.stale = False, False

# 화면에서 다시 걸 수 있어야 한다 - Pi 에는 SPACE 를 눌러 줄 사람이 없다.
post(f"{base}/api/calibrate", {})
restarted = time.monotonic() + 15.0
while time.monotonic() < restarted and live.calibrator.live:
    time.sleep(0.02)
check("HTTP 로 캘리브레이션을 다시 건다", not live.calibrator.live,
      live.calibrator.stage)

live.stop()
worker.join(timeout=5.0)
live.close()
check("종료하면 시리얼을 놓는다", fake.released)


print(f"\n{'실패 ' + str(failed) if failed else '전부 통과'}")
sys.exit(1 if failed else 0)
