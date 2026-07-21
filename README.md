# CRK-CAMERA

FastAPI 기반의 다중 카메라 캡처 및 녹화 서비스입니다. 시리얼 번호 매핑을 통해 여러 USB 카메라를 동시에 관리합니다.

---

## 사전 요구 사항

실행 전 아래 두 가지 도구가 호스트 시스템에 설치되어 있어야 합니다.

- **[uv](https://github.com/astral-sh/uv)** — Python 패키지 및 프로젝트 관리자 (Astral uv)
- **[ffmpeg](https://ffmpeg.org/)** — 영상 처리에 필요한 멀티미디어 프레임워크

Python 의존성 패키지는 uv가 자동으로 관리하므로 별도로 설치할 필요가 없습니다.

---

## 사용법

### 애플리케이션 실행

프로젝트 루트 디렉터리에서 아래 명령어를 실행합니다.

```bash
uv run src/main.py
```

FastAPI 서버와 함께 설정된 모든 카메라 캡처 서비스가 시작됩니다.

### 연결된 카메라 장치 목록 조회

현재 연결된 캡처 장치의 시리얼 번호 목록을 확인하려면 아래 명령어를 사용합니다.

```bash
uv run src/list_devices.py
```

감지된 카메라의 시리얼 번호와 장치 경로가 출력됩니다. 이 시리얼 번호는 `mapping.json`에서 카메라를 논리적 인덱스에 할당하는 데 사용됩니다.

---

## 설정

### 카메라 해상도 및 프레임 레이트

카메라 파라미터는 `src/main.py`의 `lifespan` 함수 내 `CameraControl` 인스턴스에서 설정합니다.

```python
camera_control = CameraControl(
    width=640,
    height=480,
    format="MJPG",
    fps=30,
    extra={
        "power_line_frequency": 0,
    },
)
```

| 파라미터   | 설명                                  | 설정값  |
|-----------|---------------------------------------|---------|
| `width`   | 프레임 가로 해상도 (픽셀)              | `640`   |
| `height`  | 프레임 세로 해상도 (픽셀)              | `480`   |
| `format`  | 픽셀 포맷 (`MJPG`, `YUYV` 등)         | `MJPG`  |
| `fps`     | 목표 프레임 레이트 (초당 프레임 수)    | `30`    |
| `extra`   | 추가 V4L2 control 값                  | `power_line_frequency=0` |

해상도 640x480은 시스템 전체(모델 서버 포함)가 공유하는 고정값입니다.

### 카메라 시리얼 매핑

카메라와 인덱스 간의 대응 관계는 프로젝트 루트의 `mapping.json`에 정의합니다. 각 항목의 `device`는 물리 장치(serial + 같은 serial 내 캡처 노드 index)를, `mapping`은 논리적 인덱스와 배치(role)를 나타냅니다.

- `role: "top"` — 상단 카메라. 정확히 1대 필요하며 세션 아카이브 녹화에도 사용됩니다.
- `role: "side", "zones": [...]` — 측면 카메라와 담당 zone 목록. zone 1~5는 각각 정확히 하나의 side 카메라에 배정되어야 합니다.

**냉동고 (top 1대 + side 1대 공유, 총 2대):**

```json
[
  {
    "device": { "serial": "Camera01_Camera01", "index": 0 },
    "mapping": { "index": 0, "role": "top" }
  },
  {
    "device": { "serial": "Camera02_Camera02", "index": 0 },
    "mapping": { "index": 1, "role": "side", "zones": [1, 2, 3, 4, 5] }
  }
]
```

**냉장고 (top 1대 + zone별 side 5대, 총 6대):**

```json
[
  { "device": { "serial": "CameraTop_...", "index": 0 }, "mapping": { "index": 0, "role": "top" } },
  { "device": { "serial": "CameraS1_...", "index": 0 }, "mapping": { "index": 1, "role": "side", "zones": [1] } },
  { "device": { "serial": "CameraS2_...", "index": 0 }, "mapping": { "index": 2, "role": "side", "zones": [2] } },
  { "device": { "serial": "CameraS3_...", "index": 0 }, "mapping": { "index": 3, "role": "side", "zones": [3] } },
  { "device": { "serial": "CameraS4_...", "index": 0 }, "mapping": { "index": 4, "role": "side", "zones": [4] } },
  { "device": { "serial": "CameraS5_...", "index": 0 }, "mapping": { "index": 5, "role": "side", "zones": [5] } }
]
```

role이 하나도 없는 구형 mapping.json은 legacy 배치(index 0=top, 1=전 zone 공유 side)로 해석되며 기동 로그에 경고가 남습니다.

연결된 장치의 정확한 시리얼 번호는 `uv run src/list_devices.py` 명령어로 확인할 수 있습니다.

---

## 프로젝트 구조

```
.
├── mapping.json          # 카메라 시리얼-인덱스 매핑 파일
├── pyproject.toml        # 프로젝트 메타데이터 및 의존성
├── src/
│   ├── main.py           # 애플리케이션 진입점
│   ├── list_devices.py   # 연결된 카메라 시리얼 조회 유틸리티
│   ├── api/              # FastAPI 라우터 (management/recording/sampling/test)
│   ├── services/         # 캡처, 녹화(아카이브/trigger), 샘플링, loadcell 서비스
│   └── utils/            # 카메라 제어, 장치, ffmpeg 유틸리티
└── tools/
    ├── test_ffmpeg.py    # 카메라 1대 → ffmpeg 경로 검증 스크립트
    ├── save_serials.py   # 연결된 전체 카메라 녹화 확인 스크립트
    └── gst-nvjpegdec/    # Jetson 하드웨어 JPEG 디코더 (실험용)
```
