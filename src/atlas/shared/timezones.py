from typing import Final

DEFAULT_TIMEZONE: Final = "UTC"

# Display standard-time offsets while retaining IANA identifiers for daylight-saving behavior.
TIMEZONE_OPTIONS: Final[tuple[tuple[str, str], ...]] = (
    ("UTC", "Universal Time (UTC+00:00)"),
    ("America/Los_Angeles", "Pacific Time (UTC-08:00)"),
    ("America/Chicago", "Central Time (UTC-06:00)"),
    ("America/New_York", "Eastern Time (UTC-05:00)"),
    ("Pacific/Honolulu", "Hawaii Time (UTC-10:00)"),
    ("Asia/Tokyo", "Tokyo (UTC+09:00)"),
    ("Asia/Seoul", "Seoul (UTC+09:00)"),
    ("Asia/Taipei", "Taiwan (UTC+08:00)"),
)

SUPPORTED_TIMEZONES: Final[frozenset[str]] = frozenset(timezone for timezone, _ in TIMEZONE_OPTIONS)
