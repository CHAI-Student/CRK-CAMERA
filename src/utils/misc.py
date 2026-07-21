"""공용 잡동사니 유틸리티."""

def format_unix_timestamp(timestamp: float) -> str:
    """unix timestamp를 UTC 기준 "YYYY-MM-DD_HH-MM-SS" 문자열로 변환한다."""
    from datetime import datetime, timezone
    dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d_%H-%M-%S")

def read_json_file(path: str) -> dict:
    """JSON 파일을 읽어 파싱한 객체를 돌려준다."""
    import json
    with open(path, 'r') as f:
        return json.load(f)