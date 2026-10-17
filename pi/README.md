# pi — Raspberry Pi 4 자세 노드

`tools/posture_viewer.py` 가 PC 에서 하던 일을 Pi 4 에서 창 없이 한다. 판정 자체는
**`tools/posture.py` 를 그대로 import 해서 쓴다** — 복사본이 없으므로 임계값을 고치면
PC 와 Pi 가 같이 바뀐다.

```text
ESP32-CAM ──UART──> RP2040 브리지 ──USB CDC 1──> Pi 4 (이 패키지)
                                                    │
                        MQTT deskmate/sensor/tof/<node> ──> 허브
                        HTTP :8765 /api/state ──────────> Pi 5 Atlas 화면
```

Pi 4 가 판정하고 Pi 5 가 표시한다. 허브 FSM 이 아직 이 노드를 안 먹어도 Pi 5 화면은
바로 뜬다 — `/api/state` 가 허브 `preview_api.py` 와 같은 모양을 내기 때문이다.

## 설치 (Pi 4)

```bash
sudo apt install -y python3-venv
python3 -m venv /opt/deskmate/venv
/opt/deskmate/venv/bin/pip install -r pi/requirements.txt
sudo usermod -aG dialout "$USER"     # /dev/ttyACM* 접근. 로그아웃 후 다시 로그인
```

OpenCV 는 안 쓴다. 화면을 안 그리므로 필요 없고, 그게 이 경로에서 제일 무거운
설치였다. `tools/posture_viewer.py` 를 Pi 에서 돌릴 때만 따로 깔면 된다.

## 실행

```bash
cd pi
python -m deskmate_posture --list                    # 어느 포트인지 확인
python -m deskmate_posture --broker <허브-IP>        # 포트는 알아서 찾는다
```

`--port` 를 안 주면 **Vision Stream** 쪽을 알아서 고른다. 보드는 CDC 두 개짜리
복합 장치라 포트가 둘 뜨는데, 인터페이스 0(브리지)을 열면 포트는 멀쩡히 열리고
프레임만 영영 안 온다. 그 실패는 로그에도 잘 안 드러나므로 아예 후보에서 뺐다.
Pi 는 부팅마다 `ttyACM` 번호가 바뀔 수 있어서 자동 탐색이 기본값이다.

`--broker` 를 빼면 MQTT 없이 `/api/state` 만 나간다. Pi 5 화면만 먼저 붙일 때 쓴다.

## 캘리브레이션 — 누를 사람이 없다

PC 뷰어는 SPACE 를 두 번 눌러 두 단계를 몰았다. Pi 에는 키보드가 없으므로 같은
순서를 시간으로 돌린다. **순서는 그대로다.**

| 단계 | 하는 일 | 기본 시간 |
|---|---|---|
| `clear` | 자리에서 비켜 줄 시간 | `--clear-s 8` |
| `settle` | 센서에 `b` 를 보내 AEC 재실행 + 배경 재캡처 | `# bg captured` 까지, 최대 10초 |
| `background` | 빈 책상의 zone 기준 거리 | `--bg-frames 30` |
| `sit` | 바른 자세로 앉을 시간 | `--sit-s 10` |
| `baseline` | 바른 자세 기준 | `--baseline-frames 60` |
| `live` | 판정 | |

1단계가 두 겹인 이유는 뷰어 쪽과 같다. 센서 자신의 배경이 먼저 끝나야 zone 기준을
뜰 수 있고, 사람이 섞인 배경 위에 기준이 굳으면 그 뒤로는 무엇을 해도 사람을 못
찾는다. 지금 어느 단계인지와 남은 초는 Pi 5 화면 문구로 그대로 나간다.

다시 걸려면:

```bash
curl -X POST http://<pi4>:8765/api/calibrate
```

`baseline` 단계는 **앉은 프레임만 센다**. 화각 밖에 있으면 영원히 안 끝나므로,
30초가 지나면 화면 문구가 그렇게 말해 준다.

## 내보내는 것

### `deskmate/sensor/tof/<node>` (MQTT, 기본 2Hz)

`docs/data-spec.md` 6.1 `tof_feature` 그대로다. envelope 은 `mqtt-topics.md` 의
`schema_version / ts / node / boot_id / seq / data`.

```json
{"present": true, "posture": "slouch", "posture_detail": "slump",
 "motion_score": 0.03, "head_delta_mm": -104.2, "nod_rate_hz": 0.0,
 "valid_zones": 2268, "grid_width": 54, "grid_height": 42,
 "coverage_ratio": 0.19, "phi": 0.02, "delta": 0.86, "valid": true}
```

계약 밖 필드가 셋 있고 전부 일부러다.

- **`posture_detail`** — 계약 enum 에는 `drowsy` 가 없다. 꾸벅임은 자세가 아니라
  시간 패턴이라 `nod_rate_hz` 가 나르도록 설계된 필드가 이미 있기 때문이다. enum
  에는 기하만 싣고(`DROWSY` → `lean_forward`) 판정이 실제로 뭐라고 했는지는 여기
  남긴다. 좁히기만 하고 버리지는 않는다.
- **`phi` / `delta`** — 허브 `Signal(phi, delta)` 로 바로 들어가는 값이다.
  `posture.judge()` 가 이미 계산하므로 버리면 허브가 같은 것을 다시 만들어야 한다.
- **`valid`** — 기준을 잡는 중(`BASELINE`/`UNKNOWN`)이거나 프레임이 오래됐으면
  내려간다. 허브가 이걸 안 보면 캘리브레이션 30초가 통째로 '바른 자세'로 들어간다.
  **자리 비움(`away`)은 valid 다** — 못 잰 것이 아니라 잰 결과이고, FSM 의
  `present` 가 바로 그 값이다.

`valid_zones` 는 이 경로에서 늘 2268 이다. ESP 는 거리를 재지 않고 마스크에서
겉보기 크기로 되만들기 때문에 무효 zone 이라는 게 없다. 실 VL53L9CX 로 바꾸면
그때부터 의미가 생긴다. **링크 상태는 이 값이 아니라 아래 health 로 본다.**

### `deskmate/health/<node>` (MQTT, 0.1Hz)

끊긴 링크는 살아 있는 링크와 똑같이 보인다 — 마지막 프레임이 계속 재발행되므로.
그래서 `status(ok/stale/error)`, `stage`, `fps`, `frames`, `link_error` 를 따로 낸다.

같은 이유로 **판정은 새 프레임에서만 돌지만 내보내기는 프레임이 멈춰도 계속한다.**
새 프레임이 없으면 갱신할 일도 없어서 화면에 마지막 판정이 그대로 남는데, 그게 정확히
'끊긴 링크가 살아 있어 보이는' 상태다. 1초에 한 번은 내보내므로 화면 문구가 바뀌고
`tof_feature` 의 `valid` 도 내려간다.

### `/api/state` (HTTP, Pi 5 화면용)

Atlas 앱 `state_source.dart` 를 안 고치고 `DESKMATE_HUB_URL` 만 이 노드로 돌리면
화면이 그대로 뜬다.

```bash
# atlas-dev 컨테이너에서
flutter-atlas run -d deskmate_pi5 --release \
  --dart-define=DESKMATE_HUB_URL=http://<Pi4-IP>:8765
```

**이건 허브 FSM 의 출력이 아니다.** 허브가 붙기 전에도 화면이 살아 있게 하려고 자세
하나로 같은 모양을 채운 것이고, 그 사실을 세 군데에 박아 뒀다.

- `fsm_state` 가 `POSTURE_*` — `states.py` 의 어떤 이름과도 겹치지 않는다
- `source` 가 `posture` — 계약 enum(`fsm|classifier|fused`) 밖이다
- 화면 문구가 한국어로 "자세 판정만 표시 중 (허브 미연결)" 이라고 말한다

`context` 는 키보드 신호에서 나오는 값이라 이 노드는 알 수 없다. 앱이 요구하는
필수 필드라 `mixed` 로 채웠지만 그건 측정값이 아니라 자리 채우기다. `co2_ppm` 과
`lux` 는 아예 안 넣는다 — 0 으로 채우면 화면이 "환경 완벽" 으로 잘못 읽힌다.

`/api/feedback` 도 받는다. 자세 노드는 쓸 데가 없지만 받아서 버리면 화면의
수락·거절 버튼이 404 를 받아 오류로 뜬다. 허브가 붙기 전까지 여기가 임시 종착지다.

## systemd

[`deskmate-posture.service`](deskmate-posture.service) 를 `/etc/systemd/system/`
에 두고 경로와 브로커 주소를 맞춘다.

```bash
sudo systemctl enable --now deskmate-posture
journalctl -u deskmate-posture -f
```

링크가 끊기면 프로세스가 1 로 죽고 systemd 가 3초 뒤 다시 띄운다. 안에서 재연결을
하지 않는 이유는, 포트가 사라졌다 다시 나타나면 `ttyACM` 번호가 바뀌어서 어차피
포트를 다시 찾아야 하기 때문이다. 프로세스를 새로 시작하면 그 경로가 하나뿐이다.

## 검증

```bash
python pi/test_node.py
```

보드도 pyserial 도 paho 도 필요 없다. 포트를 고르는 규칙(브리지를 절대 안 고른다),
계약 필드로 옮기는 변환, 키 없이 도는 캘리브레이션 순서, Atlas 앱이 실제로 읽는
HTTP 응답, 그리고 시리얼만 가짜로 바꾼 **노드 전체 루프**까지 본다. 판정 자체는
[`tools/test_posture.py`](../tools/test_posture.py) 가 본다.

## PC 뷰어와 일부러 다른 한 가지

뷰어는 창을 그리느라 초당 150번쯤 도는데 센서는 12fps 라 같은 프레임을 열 번 넘게
판정에 넣었다. 그러면 `extract()` 의 `motion`(직전 프레임 대비 변화)이 대부분 0 이
되고, 그 0 이 `stability` 를 타고 `phi` 를 끌어올린다. `MOTION_SPAN` 은 실제로 바뀐
프레임 사이의 크기로 잡힌 값이라 그 희석은 순전히 루프 속도가 만든 것이다.

그래서 이 노드는 **새 프레임일 때만** 판정에 넣는다. 시간축(꾸벅임 간격, 엎드림
지속)은 전부 timestamp 로 재므로 표본 주기가 센서 주기와 같아져도 임계값의 의미는
그대로다. 다만 **`phi` 는 PC 뷰어보다 낮게 읽힐 것이다** — 그쪽이 부풀어 있던
것이지만, 실기 로그로 한 번 확인할 값이다.

## 아직 확인 안 된 것

- **실기에서 한 번도 안 돌렸다.** 위 검사는 전부 가짜 시리얼 위에서 통과한 것이고,
  Pi 4 에 보드를 꽂은 상태의 실행은 아직이다.
- Pi 4 의 실제 프레임률. PC 에서 12fps 였고 판정은 numpy 뿐이라 Pi 4 에서도 센서가
  병목일 것으로 보지만 재보지 않았다.
- 위의 `phi` 차이를 실기 로그로 확인하는 것.
- MQTT 채택 자체가 미확정이다 (`docs/mqtt-topics.md`). 토픽과 envelope 은 그 문서를
  따랐고, 바뀌면 [`deskmate_posture/publish.py`](deskmate_posture/publish.py) 의
  토픽 상수와 [`envelope.py`](deskmate_posture/envelope.py) 만 고치면 된다.
