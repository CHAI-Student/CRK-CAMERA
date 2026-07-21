"""V4L2 캡처 장치 탐색/열기/리셋 유틸리티.

pyudev로 video4linux 장치를 나열해 serial 기준으로 캡처 장치를 찾고,
linuxpy Device로 열거나 USB 수준에서 리셋한다.
"""

import os
from typing import Iterable

from linuxpy.ioctl import ioctl
from linuxpy.video.device import Device as LinuxPyDevice
import pyudev


class DeviceNotFoundError(Exception):
    """지정한 serial의 캡처 장치를 찾지 못했을 때 발생한다."""

    pass


def _parse_list(list_str: str | None) -> list[str]:
    """udev 속성의 콜론 구분 목록 문자열(예: ":capture:")을 list로 파싱한다."""
    if list_str is None:
        return []
    else:
        return list_str.strip(":").split(":")


def iter_capture_device_serials(ctx: pyudev.Context) -> Iterable[str]:
    """연결된 모든 V4L2 캡처 장치의 serial을 순회한다.

    캡처 노드가 여러 개인 장치는 serial이 중복해서 나올 수 있다.
    """
    for dev in ctx.list_devices(subsystem="video4linux"):
        V4L_CAPABILITIES = _parse_list(dev.properties.get("ID_V4L_CAPABILITIES"))
        if not "capture" in V4L_CAPABILITIES:
            continue
        ID_SERIAL = dev.properties.get("ID_SERIAL")
        if ID_SERIAL is None or not isinstance(ID_SERIAL, str):
            continue
        yield ID_SERIAL


def capture_device_from_serial(ctx: pyudev.Context, serial: str, index: int) -> LinuxPyDevice:
    """serial과 index로 캡처 장치를 찾아 linuxpy Device로 돌려준다.

    :param serial: 장치의 udev ID_SERIAL
    :param index: 같은 serial의 캡처 노드 중 몇 번째를 쓸지의 index
    :raises DeviceNotFoundError: 해당 장치를 찾지 못한 경우
    """
    i = 0
    for pyudev_dev in ctx.list_devices(subsystem="video4linux", ID_SERIAL=serial):
        V4L_CAPABILITIES = _parse_list(pyudev_dev.properties.get("ID_V4L_CAPABILITIES"))
        if not "capture" in V4L_CAPABILITIES:
            continue
        if i < index:
            i += 1
            continue
        linuxpy_dev = LinuxPyDevice(pyudev_dev.device_node)
        return linuxpy_dev
    raise DeviceNotFoundError(f"Capture device with serial {serial} not found")

def reset_device(device_path):
    """USBDEVFS_RESET ioctl로 USB 장치를 리셋한다."""
    USBDEVFS_RESET = 21780
    # 장치 노드는 텍스트 모드 open()이 아니라 O_WRONLY fd로 직접 연다.
    # (기존 open(path, 'w', os.O_WRONLY)는 세 번째 인자가 buffering으로
    #  해석되는 오용이었음)
    fd = os.open(device_path, os.O_WRONLY)
    try:
        ioctl(fd, USBDEVFS_RESET, 0)
    finally:
        os.close(fd)

def reset_device_from_serial(ctx: pyudev.Context, serial: str, index: int):
    """serial과 index로 캡처 장치를 찾아 그 상위 USB 장치를 리셋한다.

    :raises DeviceNotFoundError: 해당 장치를 찾지 못한 경우
    """
    i = 0
    for pyudev_dev in ctx.list_devices(subsystem="video4linux", ID_SERIAL=serial):
        V4L_CAPABILITIES = _parse_list(pyudev_dev.properties.get("ID_V4L_CAPABILITIES"))
        if not "capture" in V4L_CAPABILITIES:
            continue
        if i < index:
            i += 1
            continue
        pyudev_dev_usb = pyudev_dev.find_parent("usb")
        assert pyudev_dev_usb is not None
        reset_device(pyudev_dev_usb.device_node)
        return
    raise DeviceNotFoundError(f"Device with serial {serial} not found")
