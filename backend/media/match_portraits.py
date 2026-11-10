"""Match-scoped media references; officials are not canonical player entities."""
import re
from uuid import UUID


def valid_portrait_source(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    if not re.fullmatch(r'[1-9][0-9]{0,18}', str(value)):
        return None
    number = int(value)
    return number if number <= 9223372036854775807 else None


def referee_portrait_url(match_id, referee_source_id) -> str | None:
    if match_id is None or valid_portrait_source(referee_source_id) is None:
        return None
    identity = str(UUID(str(match_id)))
    return f'/api/v1/media/match-portraits/{identity}/referee.png'
