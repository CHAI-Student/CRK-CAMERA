
def unix_timestamp_to_iso8601(timestamp: float) -> str:
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat()

def read_json_file(path: str) -> dict:
    import json
    with open(path, 'r') as f:
        return json.load(f)