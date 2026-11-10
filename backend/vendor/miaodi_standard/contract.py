"""Versioned normalization primitives. Missing, invalid and zero stay distinct."""
import datetime as dt
import math
import re
import unicodedata
import uuid

VERSION = 'miaodi-standard.v1'
NAMESPACE = uuid.UUID('43a49684-404f-45a8-b17c-688c93ac2bed')
STATUS = {
    'fotmob': {'Finish':'completed','NotStarted':'scheduled','Cancelled':'cancelled',
               'Postponed':'postponed','Abandoned':'abandoned','Live':'live'},
    'opta': {'Played':'completed','Fixture':'scheduled','Postponed':'postponed',
             'Cancelled':'cancelled','Abandoned':'abandoned','Playing':'live'},
}


def source_id(value):
    if isinstance(value,bool) or value is None:raise ValueError('invalid_source_id')
    value=str(value).strip()
    if not value or len(value)>128 or value in {'0','-1','None','null'}:raise ValueError('invalid_source_id')
    return value


def stable_id(kind,provider,value):
    return str(uuid.uuid5(NAMESPACE,kind+':'+provider+':'+source_id(value)))


def text(value):
    if not isinstance(value,str):raise ValueError('text_required')
    result=unicodedata.normalize('NFC',value.strip())
    if not result:raise ValueError('empty_text')
    return result


def name_or_none(value,source_reference):
    """Optional display text follows the same normalization as entity names.

    A raw identifier used as a missing-name placeholder is not a public name.
    This does not allocate or merge identities from names.
    """
    try:result=text(value)
    except ValueError:return None
    return None if result==str(source_reference).strip() else result


def utc(value):
    if not isinstance(value,str) or 'T' not in value:raise ValueError('explicit_timezone_required')
    try:result=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise ValueError('invalid_timestamp') from None
    if result.utcoffset() is None:raise ValueError('explicit_timezone_required')
    return result.astimezone(dt.timezone.utc).isoformat().replace('+00:00','Z')


def date(value):
    if not isinstance(value,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',value):raise ValueError('invalid_date')
    return dt.date.fromisoformat(value).isoformat()


def number(value,kind='number',low=None,high=None):
    if value is None:return None
    if isinstance(value,bool):raise ValueError('boolean_not_number')
    if not isinstance(value,(int,float,str)):raise ValueError('invalid_number')
    try:n=float(value)
    except (ValueError,TypeError):raise ValueError('invalid_number') from None
    if not math.isfinite(n):raise ValueError('non_finite_number')
    if low is not None and n<low or high is not None and n>high:raise ValueError('number_out_of_range')
    if kind=='count':
        if not n.is_integer():raise ValueError('non_integer_count')
        return int(n)
    return n


def status(provider,value):
    try:return STATUS[provider][value]
    except KeyError:raise ValueError('unmapped_match_status') from None


def season(value):
    value=text(value)
    if re.fullmatch(r'\d{4}',value):return value
    if re.fullmatch(r'\d{4}/\d{4}',value) and int(value[5:])==int(value[:4])+1:return value
    raise ValueError('unsupported_season_label')
