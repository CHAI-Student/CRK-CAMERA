from typing import Iterable

from linuxpy.video.device import Device as LinuxPyDevice
import pyudev


class DeviceNotFoundError(Exception):
    pass


def _parse_list(list_str: str | None) -> list[str]:
    if list_str is None:
        return []
    else:
        return list_str.strip(":").split(":")


def iter_capture_device_serials(ctx: pyudev.Context) -> Iterable[str]:
    for dev in ctx.list_devices(subsystem="video4linux"):
        V4L_CAPABILITIES = _parse_list(dev.properties.get("ID_V4L_CAPABILITIES"))
        if not "capture" in V4L_CAPABILITIES:
            continue
        ID_SERIAL = dev.properties.get("ID_SERIAL")
        if ID_SERIAL is None or not isinstance(ID_SERIAL, str):
            continue
        yield ID_SERIAL


def capture_device_from_serial(ctx: pyudev.Context, serial: str) -> LinuxPyDevice:
    for pyudev_dev in ctx.list_devices(subsystem="video4linux", ID_SERIAL=serial):
        V4L_CAPABILITIES = _parse_list(pyudev_dev.properties.get("ID_V4L_CAPABILITIES"))
        if not "capture" in V4L_CAPABILITIES:
            continue
        linuxpy_dev = LinuxPyDevice(pyudev_dev.device_node)
        return linuxpy_dev
    raise DeviceNotFoundError(f"Capture device with serial {serial} not found")
