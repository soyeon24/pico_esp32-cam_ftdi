"""    python -m deskmate_posture --broker 192.168.0.10

포트를 안 주면 Vision Stream 쪽을 알아서 찾는다. Pi 는 부팅마다 ttyACM 번호가
바뀔 수 있어서 그게 기본값이다.
"""
from __future__ import annotations

import argparse
import sys

from . import ports


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="deskmate_posture",
        description="ESP32-CAM 자세 판정 노드 (Raspberry Pi 4)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    link = parser.add_argument_group("보드 링크")
    link.add_argument("--port", help="시리얼 포트. 생략하면 Vision Stream 을 자동으로 찾는다")
    link.add_argument("--baud", type=int, default=921600,
                      help="esp32cam_sender 의 LINK_BAUD 와 맞출 것 (기본: %(default)s)")
    link.add_argument("--list", action="store_true", help="붙어 있는 포트를 찍고 끝낸다")

    out = parser.add_argument_group("내보내기")
    out.add_argument("--node", default="posture-cam", help="토픽의 <node> (기본: %(default)s)")
    out.add_argument("--broker", help="MQTT 브로커 주소. 없으면 MQTT 발행을 건너뛴다")
    out.add_argument("--broker-port", type=int, default=1883)
    out.add_argument("--host", default="0.0.0.0", help="미리보기 API 수신 주소")
    out.add_argument("--port-http", type=int, default=8765,
                     help="Atlas 앱의 DESKMATE_HUB_URL 포트 (기본: %(default)s)")
    out.add_argument("--publish-hz", type=float, default=2.0,
                     help="MQTT 발행 주기. 화면은 매 프레임 갱신된다 (기본: %(default)s)")
    out.add_argument("--log", help="발행한 envelope 을 JSONL 로 남길 경로")

    cal = parser.add_argument_group("캘리브레이션")
    cal.add_argument("--clear-s", type=float, default=8.0, help="자리에서 비켜 줄 시간")
    cal.add_argument("--sit-s", type=float, default=10.0, help="바른 자세로 앉을 시간")
    cal.add_argument("--bg-frames", type=int, default=30)
    cal.add_argument("--baseline-frames", type=int, default=60)

    parser.add_argument("--rate", type=float, default=30.0,
                        help="루프 폴링 Hz. 센서는 12fps 라 이보다 느릴 필요는 없다")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list:
        for line in ports.describe():
            print(line)
        return 0

    if not args.port:
        args.port = ports.find_vision_port()
        if not args.port:
            print("Vision Stream 포트를 못 찾았습니다.", file=sys.stderr)
            for line in ports.describe():
                print(line, file=sys.stderr)
            return 2
        print(f"Vision Stream 포트를 찾았습니다: {args.port}")

    from .node import run
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
