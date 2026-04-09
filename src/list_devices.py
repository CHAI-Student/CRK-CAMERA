import json

import pyudev

from utils.device import iter_capture_device_serials, capture_device_from_serial


def main():
    context = pyudev.Context()

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
