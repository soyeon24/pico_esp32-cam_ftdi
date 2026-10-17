"""Pi 5 Atlas 앱이 긁어 가는 HTTP.

허브의 `preview_api.py` 와 같은 계약이다 - 앱 쪽(`state_source.dart`)을 고치지 않고
`DESKMATE_HUB_URL` 만 이 노드로 돌리면 화면이 그대로 뜬다. 최종 통신이 MQTT 로
정해지면 이 어댑터는 빠지고 앱은 안 바뀐다.

`/api/calibrate` 만 허브에 없는 것을 더했다. Pi 에는 SPACE 를 눌러 줄 사람이 없어서
캘리브레이션을 다시 걸 방법이 아예 없었다.
"""
from __future__ import annotations

import json
import threading
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

VERDICTS = {"accept", "reject", "correct"}
MAX_BODY = 4096


class StateStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, Any] | None = None
        self._feedback: deque[dict[str, Any]] = deque(maxlen=64)
        self.calibrate_requested = False

    def publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._state = payload

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return self._state

    def add_feedback(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self._feedback.append(payload)

    def pop_feedback(self) -> dict[str, Any] | None:
        with self._lock:
            return self._feedback.popleft() if self._feedback else None

    def request_calibrate(self) -> None:
        with self._lock:
            self.calibrate_requested = True

    def take_calibrate(self) -> bool:
        """요청이 있었으면 True 를 돌려주고 플래그를 내린다."""
        with self._lock:
            taken, self.calibrate_requested = self.calibrate_requested, False
            return taken


def make_server(host: str, port: int, store: StateStore,
                status: Callable[[], dict[str, Any]]) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
            if self.path == "/health":
                self._json(HTTPStatus.OK, status())
                return
            if self.path == "/api/state":
                state = store.latest()
                if state is None:
                    self._json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "state_not_ready"})
                else:
                    self._json(HTTPStatus.OK, state)
                return
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
            # 본문은 쓸 일이 없어도 **먼저 읽는다.** 안 읽고 응답한 뒤 소켓을 닫으면
            # 커널이 FIN 이 아니라 RST 를 보내고, 클라이언트는 202 를 못 본 채 연결이
            # 끊겼다고만 본다. /api/calibrate 가 실제로 그랬다 - 본문을 안 읽으니
            # curl 과 Dart 양쪽에서 간헐적으로 끊겼다.
            raw = self._read_body()

            if self.path == "/api/calibrate":
                store.request_calibrate()
                self._json(HTTPStatus.ACCEPTED, {"accepted": True})
                return
            if self.path != "/api/feedback":
                self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
                return
            try:
                # Dart 쪽은 Content-Length 를 명시해 보낸다. 없으면 chunked 라
                # 여기서 못 읽는데, 길이 0 을 성공으로 처리하면 화면의 버튼이
                # 눌린 것처럼 보이면서 아무 데도 안 간다.
                if not raw:
                    raise ValueError("empty body")
                body = json.loads(raw.decode("utf-8"))
                if body.get("verdict") not in VERDICTS:
                    raise ValueError("invalid verdict")
                store.add_feedback(body)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, AttributeError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_feedback"})
                return
            self._json(HTTPStatus.ACCEPTED, {"accepted": True})

        def _read_body(self) -> bytes:
            """Content-Length 만큼 읽는다. 없거나 터무니없으면 빈 바이트."""
            try:
                size = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                size = -1
            if size <= 0 or size > MAX_BODY:
                # 다 읽어 줄 수 없는 본문이면 이 연결은 재사용하지 않는다.
                self.close_connection = True
                return b""
            return self.rfile.read(size)

        def log_message(self, format: str, *args: object) -> None:
            return      # 1Hz 폴링이라 그대로 두면 로그가 이것만으로 찬다

        def _json(self, status_code: HTTPStatus, body: dict[str, Any]) -> None:
            data = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
