"""mapping.json 카메라 배치(layout) 해석 유틸리티.

mapping.json의 각 항목은 물리 장치(device)와 논리 배치(mapping)를 정의한다.
mapping에는 두 가지 형식이 있다.

[role 형식 — 권장]
    각 항목의 mapping에 role을 명시한다.
    - {"index": 0, "role": "top"}                          : top 카메라 (1대 필수)
    - {"index": 1, "role": "side", "zones": [1, 2, 3, 4, 5]}: side 카메라와 담당 zone
    zone 1~5는 각각 정확히 하나의 side 카메라에 배정되어야 한다.

    예) 냉동고: top 1대 + side 1대(zone 1~5 공유) = 총 2대
        냉장고: top 1대 + zone별 side 5대("zones": [k]) = 총 6대

[legacy 형식 — 하위 호환]
    role이 하나도 없으면 구형(냉동고 초기) 배치로 간주한다:
    index 0 = top, index 1 = 모든 zone(1~5)의 side.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# 자판기 zone 구성 (loadcell 배치와 동일하게 5개 고정)
ZONES = range(1, 6)


@dataclass(frozen=True)
class CameraLayout:
    """해석된 카메라 배치.

    :param top_index: top 카메라의 논리 index
    :param zone_side_indices: zone 번호 → side 카메라 논리 index
    """

    top_index: int
    zone_side_indices: dict[int, int]


def parse_camera_layout(mapping: list) -> CameraLayout:
    """mapping.json 목록에서 top/side 카메라 배치를 해석한다.

    :param mapping: mapping.json을 파싱한 항목 list
    :raises ValueError: 형식 오류(role 혼용, top 중복/누락, zone 중복/누락 등)
    """
    if not mapping:
        raise ValueError("mapping.json이 비어 있습니다")

    indices = [entry["mapping"]["index"] for entry in mapping]
    if len(indices) != len(set(indices)):
        raise ValueError(f"mapping.index가 중복됩니다: {sorted(indices)}")

    with_role = [entry for entry in mapping if "role" in entry["mapping"]]

    # legacy 형식: role이 전혀 없으면 구형 배치(0=top, 1=공유 side)로 해석
    if not with_role:
        logger.warning(
            "mapping.json에 role이 없어 legacy 배치로 해석합니다 "
            "(index 0=top, index 1=모든 zone의 side). "
            "role 형식으로 갱신을 권장합니다."
        )
        index_set = set(indices)
        if 0 not in index_set or 1 not in index_set:
            raise ValueError(
                "legacy 형식에는 index 0(top)과 1(side)이 모두 필요합니다: "
                f"{sorted(index_set)}"
            )
        return CameraLayout(top_index=0, zone_side_indices={zone: 1 for zone in ZONES})

    if len(with_role) != len(mapping):
        raise ValueError(
            "mapping.json에 role이 있는 항목과 없는 항목이 섞여 있습니다. "
            "모든 항목에 role을 명시하거나 전부 생략(legacy)하세요."
        )

    # role 형식 검증 및 해석
    top_indices: list[int] = []
    zone_side_indices: dict[int, int] = {}
    for entry in mapping:
        m = entry["mapping"]
        role = m["role"]
        if role == "top":
            if "zones" in m:
                raise ValueError(f"top 카메라(index {m['index']})에는 zones를 지정할 수 없습니다")
            top_indices.append(m["index"])
        elif role == "side":
            zones = m.get("zones")
            if not isinstance(zones, list) or not zones:
                raise ValueError(
                    f"side 카메라(index {m['index']})에는 비어 있지 않은 zones 목록이 필요합니다"
                )
            for zone in zones:
                if zone not in ZONES:
                    raise ValueError(
                        f"side 카메라(index {m['index']})의 zone {zone}은 유효 범위(1~5)를 벗어납니다"
                    )
                if zone in zone_side_indices:
                    raise ValueError(
                        f"zone {zone}이 여러 side 카메라(index "
                        f"{zone_side_indices[zone]}, {m['index']})에 중복 배정됐습니다"
                    )
                zone_side_indices[zone] = m["index"]
        else:
            raise ValueError(f"알 수 없는 role입니다: {role!r} (index {m['index']})")

    if len(top_indices) != 1:
        raise ValueError(f"top 카메라는 정확히 1대여야 합니다 (현재 {len(top_indices)}대)")

    missing_zones = sorted(set(ZONES) - set(zone_side_indices))
    if missing_zones:
        raise ValueError(f"side 카메라가 배정되지 않은 zone이 있습니다: {missing_zones}")

    return CameraLayout(top_index=top_indices[0], zone_side_indices=zone_side_indices)
