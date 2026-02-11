import pyudev

from utils.device import iter_capture_device_serials, capture_device_from_serial


def main():

    context = pyudev.Context()

    for serial in iter_capture_device_serials(context):
        print(serial)


if __name__ == "__main__":
    main()
