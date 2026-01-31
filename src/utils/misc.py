from datetime import datetime, timezone

def unix_timestamp_to_iso8601(timestamp: float) -> str:
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.isoformat()