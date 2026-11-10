"""Private projection of scheduler-owned immutable weekly artifacts."""
import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field
from .research import digest


class Coverage(BaseModel):
    expected: int = Field(ge=1,le=64)
    available: int = Field(ge=0,le=64)
    complete: bool


class WeeklyCandidate(BaseModel):
    snapshot_id: str = Field(pattern='^[0-9a-f]{64}$')
    title: str
    limitation: str


class WeeklyRound(BaseModel):
    import_id: str = Field(pattern='^[0-9a-f]{64}$')
    competition: str
    label: str
    coverage: Coverage
    total_goals: float | None
    goals_per_match: float | None
    score_matches: int
    candidates: list[WeeklyCandidate]


class WeeklyResearchDTO(BaseModel):
    week_start: str
    stale: bool
    status: str
    rounds: list[WeeklyRound]


def read_weekly():
    configured = os.environ.get('STUDIO_WEEKLY_RESEARCH')
    if not configured: raise ValueError('weekly_not_configured')
    root = Path(configured).resolve(strict=True)
    def read(relative):
        path=root/relative
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('symlink_not_allowed')
        with path.open('rb') as stream: raw=stream.read(4_000_001)
        if len(raw)>4_000_000:raise ValueError('oversized_weekly_artifact')
        return raw
    index=json.loads(read('index.json'))
    if index.get('schema_version')!='weekly-research-index.v1' or index.get('status') not in ('ready','awaiting_explicit_scopes'):
        raise ValueError('unsupported_weekly_index')
    if len(index['rounds'])>16:raise ValueError('too_many_rounds')
    rounds=[];count=0
    for entry in index['rounds']:
        sha=entry['object_sha256']
        if not re.fullmatch('[0-9a-f]{64}',sha):raise ValueError('invalid_hash')
        raw=read(f'objects/{sha}.json')
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('weekly_hash_mismatch')
        packet=json.loads(raw);selection=packet['selection'];scope=selection['scope']
        if (packet['schema_version']!='weekly-research-dispatch.v1' or packet['review_state']!='draft'
            or packet['dispatch_id']!=entry['dispatch_id'] or packet['week_start']!=index['week_start']
            or packet['selection_id']!=digest(selection) or selection['scope_sha256']!=digest(scope)
            or selection['coverage']!=entry['coverage'] or len(packet['queue'])!=entry['queued']):
            raise ValueError('weekly_identity_mismatch')
        candidates={c['candidate_id']:c for c in selection['candidates']}
        projected=[]
        for item in packet['queue']:
            source=candidates[item['candidate_id']]
            if any(item.get(k)!=source.get(k) for k in ('snapshot_id','title','limitation')) or item['production_state']!='pending_review':
                raise ValueError('weekly_candidate_mismatch')
            projected.append(WeeklyCandidate(snapshot_id=item['snapshot_id'],title=item['title'],limitation=item['limitation']))
        count+=len(projected)
        summary=selection['score_summary']
        rounds.append(WeeklyRound(import_id=sha,competition=scope['competition_code'],label=scope['round_label'],
            coverage=selection['coverage'],total_goals=summary['total_goals'],goals_per_match=summary['goals_per_match'],
            score_matches=summary['available_matches'],candidates=projected))
    if count>20:raise ValueError('weekly_queue_over_limit')
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    current=(today-timedelta(days=today.weekday())).isoformat()
    return WeeklyResearchDTO(week_start=index['week_start'],stale=index['week_start']!=current,status=index['status'],rounds=rounds)
