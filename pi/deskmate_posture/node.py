"""판정 루프. 시리얼에서 프레임을 받아 판정하고 두 군데로 내보낸다.

    EspZoneSource.read() -> PostureTracker.update() -> envelope -> MQTT + /api/state

**PC 뷰어와 한 가지가 다르고, 그건 일부러다.** `posture_viewer.py` 는 창을 그리느라
초당 150 번쯤 도는데 센서는 12fps 라 같은 프레임을 열 번 넘게 판정에 넣었다. 그러면
`extract()` 의 motion(직전 프레임 대비 변화)이 대부분 0 이 되고, 그 0 이 stability 를
통해 phi 를 끌어올린다. `MOTION_SPAN` 은 실제로 바뀐 프레임 사이의 크기로 잡힌 값이라
그 희석은 순전히 루프 속도가 만든 것이다. 그래서 여기서는 **새 프레임일 때만** 판정에
넣는다. 시간축(꾸벅임·지속 시간)은 전부 timestamp 로 재므로 표본 주기가 센서 주기와
같아져도 임계값의 의미는 그대로다.
"""
from __future__ import annotations

import json
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from . import use_repo_tools
from .calibration import Calibrator
from .envelope import (health, preview_envelope, scenario_text, tof_feature, wrap)
from .preview import StateStore, make_server
from .publish import FEEDBACK_TOPIC, HEALTH_TOPIC, SENSOR_TOPIC, make_publisher

use_repo_tools()

HEALTH_PERIOD_S = 10.0      # mqtt-topics.md 의 health 0.1Hz
# 프레임이 끊기면 새 프레임이 없으니 갱신할 일도 없다 - 그러면 화면에는 마지막 판정이
# 그대로 남아 **끊긴 링크가 살아 있는 링크와 똑같이 보인다.** 그래서 새 프레임이
# 없어도 이 주기로 한 번은 내보낸다. 판정을 다시 돌리는 게 아니라 stale 표시를
# 화면과 허브까지 보내려는 것이다.
STALE_REFRESH_S = 1.0


class PostureNode:
    def __init__(self, args) -> None:
        from posture import PostureTracker, ZONE_COLS, ZONE_ROWS
        from esp_source import EspZoneSource

        self.args = args
        self.grid = (ZONE_COLS, ZONE_ROWS)
        self.node = args.node
        self.boot_id = uuid.uuid4().hex[:8]
        self.seq = 0
        self.frames = 0
        self.fps = 0.0
        # 첫 새 프레임이 오기 전에도 /health 와 health 토픽은 답해야 한다.
        self.verdict = None
        self.feature: dict[str, Any] | None = None
        self._stop = threading.Event()

        print(f"포트 {args.port} 를 {args.baud} baud 로 엽니다...")
        self.source = EspZoneSource(args.port, args.baud)
        self.tracker = PostureTracker()
        self.calibrator = Calibrator(
            self.tracker, self.source,
            clear_s=args.clear_s, sit_s=args.sit_s,
            bg_frames=args.bg_frames, baseline_frames=args.baseline_frames,
        )

        self.store = StateStore()
        self.publisher = make_publisher(args.broker, args.broker_port,
                                        client_id=f"deskmate-{self.node}-{self.boot_id}")
        self.server = make_server(args.host, args.port_http, self.store, self.status)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        print(f"미리보기 API: http://{args.host}:{args.port_http}/api/state")
        if args.broker:
            print(f"MQTT: {args.broker}:{args.broker_port} "
                  f"-> {SENSOR_TOPIC.format(node=self.node)}")

        self._log = open(args.log, "a", encoding="utf-8") if args.log else None

    # ------------------------------------------------------------------ 루프

    def run(self) -> int:
        args = self.args
        period = 1.0 / max(args.rate, 1.0)
        last_depth: np.ndarray | None = None
        last_preview = 0.0
        last_publish = 0.0
        last_health = 0.0
        last_tick = time.monotonic()

        while not self._stop.is_set():
            zone = self.source.read()
            if zone is None:
                return self._link_died()

            now = time.monotonic()
            # 같은 프레임을 다시 판정에 넣지 않는다 (위 모듈 주석 참고).
            fresh = last_depth is None or not np.array_equal(zone.depth_mm, last_depth)
            if fresh:
                last_depth = zone.depth_mm
                self.frames += 1
                self.fps = 0.9 * self.fps + 0.1 / max(now - last_tick, 1e-6)
                last_tick = now
                self.verdict = self.tracker.update(zone, now)
                self.calibrator.step(now)

            # 판정은 새 프레임에서만 돌지만, 내보내기는 프레임이 끊겨도 계속한다.
            refresh = fresh or (self.verdict is not None
                                and now - last_preview >= STALE_REFRESH_S)
            if refresh:
                last_preview = now
                self._refresh_preview(zone, now)

            if self.store.take_calibrate():
                print("캘리브레이션 재시작 요청을 받았습니다.")
                self.calibrator.restart(now)

            self._drain_feedback()

            if refresh and now - last_publish >= 1.0 / max(args.publish_hz, 0.01):
                last_publish = now
                self._publish_sensor()
            if now - last_health >= HEALTH_PERIOD_S:
                last_health = now
                self._publish_health()

            time.sleep(period)
        return 0

    # ------------------------------------------------------------- 내보내기

    def _refresh_preview(self, zone, now: float) -> None:
        stale = bool(getattr(self.source, "stale", False))
        hint = self.calibrator.hint(now)
        self.feature = tof_feature(self.verdict, valid_zones=_valid_zones(zone),
                                   grid=self.grid, stale=stale)
        self.seq += 1
        envelope = preview_envelope(
            self.verdict, node=self.node, boot_id=self.boot_id, seq=self.seq,
            scenario=scenario_text(self.verdict, stale=stale, hint=hint),
            feature=self.feature,
        )
        self.store.publish(envelope)
        if self._log is not None:
            self._log.write(json.dumps(envelope, ensure_ascii=False, allow_nan=False) + "\n")
            self._log.flush()   # 전원을 뽑아 끝나는 일이 잦다. 버퍼에 두지 않는다

    def _publish_sensor(self) -> None:
        if self.feature is None:
            return
        self.publisher.publish(
            SENSOR_TOPIC.format(node=self.node),
            wrap(self.feature, node=self.node, boot_id=self.boot_id, seq=self.seq),
        )

    def _publish_health(self) -> None:
        payload = health(stage=self.calibrator.stage,
                         stale=bool(getattr(self.source, "stale", False)),
                         fps=self.fps, frames=self.frames,
                         link_error=self.source.error)
        self.publisher.publish(
            HEALTH_TOPIC.format(node=self.node),
            wrap(payload, node=self.node, boot_id=self.boot_id, seq=self.seq),
        )

    def _drain_feedback(self) -> None:
        """화면에서 누른 수락·거절을 그대로 흘려 보낸다.

        자세 노드는 이걸 쓸 데가 없지만, 받아서 버리면 Atlas 앱의 버튼이 404 를 받아
        오류로 뜬다. 허브가 붙기 전까지 이 노드가 임시 종착지다.
        """
        while True:
            item = self.store.pop_feedback()
            if item is None:
                return
            self.publisher.publish(
                FEEDBACK_TOPIC,
                wrap(item, node=self.node, boot_id=self.boot_id, seq=self.seq),
            )

    # --------------------------------------------------------------- 상태/종료

    def status(self) -> dict[str, Any]:
        return {
            "status": "error" if self.source.error else "ok",
            "node": self.node,
            "boot_id": self.boot_id,
            "stage": self.calibrator.stage,
            "state_ready": self.store.latest() is not None,
            "mqtt_connected": bool(getattr(self.publisher, "connected", False)),
            "stale": bool(getattr(self.source, "stale", False)),
            "fps": round(self.fps, 2),
            "frames": self.frames,
            "link_error": self.source.error,
        }

    def _link_died(self) -> int:
        error = self.source.error or "수신 스레드가 멈췄습니다"
        print(f"시리얼 링크가 끊겼습니다: {error}")
        self._publish_health()
        # systemd 가 다시 띄운다. 여기서 재연결을 직접 하지 않는 이유는, 포트가
        # 사라졌다 다시 나타나면 ttyACM 번호가 바뀌어서 어차피 포트를 다시 찾아야
        # 하기 때문이다. 프로세스를 새로 시작하는 쪽이 그 경로를 한 번만 갖는다.
        return 1

    def stop(self, *_: object) -> None:
        self._stop.set()

    def close(self) -> None:
        self.source.release()
        self.publisher.close()
        self.server.shutdown()
        if self._log is not None:
            self._log.close()


def _valid_zones(zone) -> int:
    """유효(유한) zone 수.

    ESP 경로에서는 늘 2268 이다 - 거리를 재는 게 아니라 마스크에서 겉보기 크기로
    되만들기 때문에 무효 zone 이라는 게 없다. 실 VL53L9CX 로 바꾸면 그때부터 실제
    의미가 생기므로 계약 필드는 지금부터 채워 둔다. **링크가 살아 있는지는 이 값이
    아니라 health 의 stale 로 본다.**
    """
    return int(np.isfinite(zone.depth_mm).sum())


def run(args) -> int:
    node = PostureNode(args)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, node.stop)
    try:
        return node.run()
    finally:
        node.close()
