"""사람이 키를 누르지 않는 캘리브레이션.

`tools/posture_viewer.py` 는 SPACE 두 번으로 두 단계를 몰았다. Pi 에는 창도 키보드도
없으므로 같은 순서를 시간으로 돌린다. **순서가 핵심이고 그건 그대로 지킨다.**

    clear -> settle -> background -> sit -> baseline -> live
    (비켜)   (센서 b)  (zone 기준)  (앉기)  (자세 기준)

1단계가 두 겹인 이유는 viewer 쪽 주석에 있는 그대로다. 센서 자신의 배경(ESP 는 `b`
한 번에 AEC 재실행 + 배경 재캡처)이 먼저 끝나야 zone 기준 거리를 뜰 수 있다. 사람이
섞인 배경 위에 기준이 굳으면 그 뒤로는 무엇을 해도 사람을 못 찾는다.

`sit` 단계는 PC 경로에 없던 것이다. 거기서는 사람이 앉고 나서 SPACE 를 눌렀지만,
여기서는 눌러 줄 사람이 없으니 앉을 시간을 주는 수밖에 없다.
"""
from __future__ import annotations

from dataclasses import dataclass

CLEAR = "clear"
SETTLE = "settle"
BACKGROUND = "background"
SIT = "sit"
BASELINE = "baseline"
LIVE = "live"

BG_DONE_MARK = "bg captured"   # ESP 가 끝났다고 알리는 문구
SETTLE_MIN_S = 1.5             # 'b' 를 보낸 뒤 최소 대기
SETTLE_MAX_S = 10.0            # 완료 신호가 없어도 이만큼이면 넘어간다


@dataclass
class Calibrator:
    """`tracker` 와 `source` 를 물고 단계를 넘긴다.

    루프에서 `tracker.update()` **다음에** `step()` 을 부른다. `start_background()` 와
    `start_baseline()` 은 다음 프레임부터 효력이 생기므로 순서가 뒤집히면 한 프레임이
    엉뚱한 단계에 들어간다.
    """

    tracker: object
    source: object
    clear_s: float = 8.0
    sit_s: float = 10.0
    bg_frames: int = 30
    baseline_frames: int = 60
    # 사람이 안 앉으면 baseline 은 영원히 안 찬다 (occupancy 문턱을 못 넘는 프레임은
    # 세지 않는다). 포기하지는 않되 - 포기하면 기준 없이 계속 UNKNOWN 이다 - 화면에는
    # 왜 안 끝나는지 말해 준다.
    baseline_warn_s: float = 30.0

    stage: str = CLEAR
    _at: float = 0.0            # 현재 단계에 들어온 시각
    _marks: int = 0             # 'b' 를 보내기 직전의 완료 문구 개수
    _started: bool = False

    def restart(self, now: float) -> None:
        """처음부터 다시. 이전 기준은 버린다.

        남겨 두면 새 배경을 뜨는 동안에도 옛 baseline 으로 판정이 계속 나와서,
        화면은 멀쩡한데 값이 틀린 상태가 된다.
        """
        from posture import BackgroundModel

        self.tracker.background = BackgroundModel()
        self.tracker.baseline = None
        self.tracker.nods.reset()
        self.stage, self._at, self._marks = CLEAR, now, 0
        self._started = True

    def step(self, now: float) -> None:
        if not self._started:
            self.restart(now)
            return
        elapsed = now - self._at

        if self.stage == CLEAR:
            if elapsed >= self.clear_s:
                self._marks = self._count_marks()
                self._send("b")
                self._enter(SETTLE, now)

        elif self.stage == SETTLE:
            # 완료 문구를 기다리되, 안 보내주는 센서도 있으므로 상한을 둔다.
            done = self._count_marks() > self._marks
            if elapsed >= SETTLE_MIN_S and (done or elapsed >= SETTLE_MAX_S):
                self.tracker.start_background(self.bg_frames)
                self._enter(BACKGROUND, now)

        elif self.stage == BACKGROUND:
            if self.tracker.background.captured:
                self._enter(SIT, now)

        elif self.stage == SIT:
            if elapsed >= self.sit_s:
                self.tracker.start_baseline(self.baseline_frames)
                self._enter(BASELINE, now)

        elif self.stage == BASELINE:
            if self.tracker.baseline is not None:
                self._enter(LIVE, now)

    @property
    def live(self) -> bool:
        return self.stage == LIVE

    def hint(self, now: float) -> str:
        """화면에 띄울 한 줄. 남은 초를 세어 준다."""
        left = lambda span: max(span - (now - self._at), 0.0)
        if self.stage == CLEAR:
            return f"1/2 배경 측정 - 자리에서 비켜 주세요 ({left(self.clear_s):.0f}초)"
        if self.stage == SETTLE:
            return "1/2 센서가 노출을 다시 잡는 중입니다"
        if self.stage == BACKGROUND:
            return "1/2 빈 책상의 기준 거리를 재는 중입니다"
        if self.stage == SIT:
            return f"2/2 바른 자세로 앉아 주세요 ({left(self.sit_s):.0f}초)"
        if self.stage == BASELINE:
            waited = now - self._at
            if waited >= self.baseline_warn_s:
                # 앉은 프레임만 세므로, 안 차면 사람이 안 보인다는 뜻이다.
                return "2/2 자세 기준을 못 채우는 중 - 화각 안에 앉아 있는지 확인하세요"
            return "2/2 바른 자세 기준을 재는 중입니다"
        return ""

    def _enter(self, stage: str, now: float) -> None:
        self.stage, self._at = stage, now

    def _send(self, line: str) -> None:
        send = getattr(self.source, "send", None)
        if send is not None:
            send(line)

    def _count_marks(self) -> int:
        lines = getattr(self.source, "log_lines", None)
        if lines is None:
            return 0
        return sum(BG_DONE_MARK in text for text in lines())
