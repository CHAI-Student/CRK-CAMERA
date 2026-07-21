"""연결된 카메라 목록 조회 스크립트.

현재 연결된 V4L2 캡처 장치들을 mapping.json과 같은 구조의 JSON으로
출력한다. 실행: `uv run src/list_devices.py`

주의: 출력에는 role(top/side)과 zones가 없으므로, mapping.json으로
사용할 때는 각 항목의 mapping에 role을 직접 채워야 한다
(형식은 src/utils/mapping.py docstring 참조).
"""

import json

import pyudev

from utils.device import iter_capture_device_serials


def main():
    """캡처 장치들을 나열해 mapping.json 형식의 JSON으로 출력한다."""
    context = pyudev.Context()

    # 같은 serial이 여러 캡처 노드로 잡히는 경우 장치 index(0, 1, ...)를 매긴다
    ref_cnt = {}
    obj = []

    for i, serial in enumerate(iter_capture_device_serials(context)):
        obj.append({
            "device": {
                "serial": serial,
                "index": ref_cnt.setdefault(serial, 0),
            },
            "mapping": {
                "index": i,
            }
        })
        ref_cnt[serial] += 1
    
    print(json.dumps(obj, indent=2))


if __name__ == "__main__":
    main()
