"""MQTT 발행. 브로커가 없어도 판정은 계속 돈다.

브로커를 기다리다 루프가 막히면 센서 프레임이 쌓이고, 그 사이 판정은 과거를 본다.
자세 판정은 지속 시간으로 엎드림과 꾸벅임을 가르므로(SLUMP_HOLD_S, NOD_*) 시간축이
밀리는 것이 값이 안 나가는 것보다 나쁘다. 그래서 연결은 전부 비동기로 두고, 발행은
실패해도 예외를 위로 올리지 않는다.
"""
from __future__ import annotations

import json
import threading
from typing import Any

SENSOR_TOPIC = "deskmate/sensor/tof/{node}"
HEALTH_TOPIC = "deskmate/health/{node}"
FEEDBACK_TOPIC = "deskmate/feedback/user"


class NullPublisher:
    """`--broker` 를 안 줬을 때. 호출부가 None 검사를 하지 않아도 되게 한다."""

    connected = False

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        pass

    def close(self) -> None:
        pass


class MqttPublisher:
    def __init__(self, host: str, port: int = 1883, *, client_id: str,
                 keepalive: int = 30) -> None:
        import paho.mqtt.client as mqtt

        # paho 2.x 는 콜백 API 버전을 요구하고 1.x 는 그 인자를 모른다. Pi 의 apt
        # 패키지는 아직 1.6 이고 pip 는 2.x 를 준다 - 둘 다 만난다.
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
        except (AttributeError, TypeError):
            client = mqtt.Client(client_id=client_id)

        self._client = client
        self._lock = threading.Lock()
        self.connected = False
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        # 60 초까지 물러나며 혼자 다시 붙는다. 호출부는 연결 상태를 몰라도 된다.
        client.reconnect_delay_set(min_delay=1, max_delay=60)
        client.connect_async(host, port, keepalive)
        client.loop_start()

    # paho 1.x 는 (client, userdata, flags, rc), 2.x 는 뒤에 properties 가 더 붙는다.
    def _on_connect(self, client, userdata, flags, rc, properties=None) -> None:
        with self._lock:
            self.connected = rc == 0

    def _on_disconnect(self, client, userdata, rc, properties=None, reason=None) -> None:
        with self._lock:
            self.connected = False

    def publish(self, topic: str, payload: dict[str, Any]) -> None:
        try:
            # allow_nan=False 로 NaN 을 여기서 막는다. envelope 이 이미 걸러내지만,
            # 새 필드가 생겼을 때 조용히 깨진 JSON 을 내보내는 것보다 여기서 터지고
            # 아래에서 삼키는 편이 낫다 - 발행은 못 해도 판정은 안 멈춘다.
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False)
        except ValueError:
            return
        try:
            self._client.publish(topic, body, qos=0, retain=False)
        except Exception:
            pass

    def close(self) -> None:
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass


def make_publisher(broker: str | None, port: int, *, client_id: str):
    """브로커 주소가 없거나 paho 가 없으면 NullPublisher 로 떨어진다."""
    if not broker:
        return NullPublisher()
    try:
        return MqttPublisher(broker, port, client_id=client_id)
    except ImportError:
        print("paho-mqtt 가 없어 MQTT 발행을 건너뜁니다 "
              "(pip install paho-mqtt). 판정과 /api/state 는 그대로 돕니다.")
        return NullPublisher()
