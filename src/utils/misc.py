
def format_unix_timestamp(timestamp: float) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d_%H-%M-%S")

def read_json_file(path: str) -> dict:
    import json
    with open(path, 'r') as f:
        return json.load(f)