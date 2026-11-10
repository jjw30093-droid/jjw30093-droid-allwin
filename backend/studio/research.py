"""Private evidence inbox -> existing Studio drafts; no live fact re-query."""
import hashlib
import json
import os
import re
from pathlib import Path

from backend.db.connections import tx
from backend.db.util import new_uuid, utc_now_iso

PROFILE = 'research-evidence-v1'


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                       allow_nan=False) + '\n').encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _hash(value):
    if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
        raise ValueError('无效的证据版本编号')
    return value


def load_packet(snapshot_id):
    """Only operator-configured inbox paths, never caller-provided paths or URLs."""
    _hash(snapshot_id)
    configured = os.environ.get('STUDIO_RESEARCH_INBOX')
    if not configured:
        raise ValueError('研究证据收件目录尚未配置')
    root = Path(configured).resolve(strict=True)

    def read(relative, limit):
        path = root / relative
        if path.is_symlink() or path.parent.is_symlink() or not path.resolve().is_relative_to(root):
            raise ValueError('证据目录不允许符号链接')
        with path.open('rb') as f:
            raw = f.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('证据文件超过大小限制')
        return raw

    manifest = json.loads(read(f'manifests/{snapshot_id}.json', 4096))
    if (manifest.get('schema_version') != 'research-export-manifest.v1'
            or manifest.get('snapshot_id') != snapshot_id or manifest.get('review_state') != 'draft'):
        raise ValueError('证据清单身份不一致')
    objects = {}
    for field, suffix in [('snapshot_id', 'json'), ('video_evidence_sha256', 'json'), ('preview_sha256', 'md')]:
        sha = _hash(manifest[field])
        raw = read(f'objects/{sha}.{suffix}', 25_000_000)
        if hashlib.sha256(raw).hexdigest() != sha:
            raise ValueError('证据文件校验失败')
        if suffix == 'json':
            objects[field] = json.loads(raw)
    snapshot, packet = objects['snapshot_id'], objects['video_evidence_sha256']
    if snapshot.get('schema_version') != 'research-snapshot.v1':
        raise ValueError('不支持的研究快照格式')
    expected = {
        'schema_version': 'video-evidence.v1', 'snapshot_id': snapshot_id,
        'data_version': snapshot_id, 'review_state': 'draft',
        'display_names': snapshot['mapping'].get('display_names', {}),
        **{key: snapshot[key] for key in ('analysis_mode', 'cutoff_at', 'identities', 'match',
                                         'metrics', 'event_timeline', 'source_capture_sha256', 'quality')},
        'claims': snapshot['topic_candidates'],
    }
    if packet != expected or packet['analysis_mode'] not in ('current', 'reconstructed'):
        raise ValueError('视频数据与固定证据不一致')
    if digest(snapshot['mapping']) != snapshot['mapping_sha256']:
        raise ValueError('实体映射校验失败')
    for source in ('opta', 'allwin'):
        if digest(snapshot['sources'][source]) != snapshot['source_capture_sha256'][source]:
            raise ValueError('来源捕获校验失败')
    match_id = int(snapshot['mapping']['fotmob_match_id'])
    if match_id <= 0 or str(match_id) != str(snapshot['sources']['allwin']['match']['Match_ID']):
        raise ValueError('比赛映射不一致')
    refs = [m['evidence_id'] for m in packet['metrics']]
    if len(set(refs)) != len(refs) or any(not set(c['evidence_refs']) <= set(refs) for c in packet['claims']):
        raise ValueError('选题引用缺失或重复')
    # Re-encode to reject non-finite JSON values even in a hash-valid local object.
    canonical(packet)
    return packet, match_id, manifest['video_evidence_sha256']


def make_bundle(packet):
    names, match = packet.get('display_names', {}), packet['match']
    title = packet.get("title") or f"{names.get('home') or match['home_team']} vs {names.get('away') or match['away_team']}"
    sections = [{'id': c['topic_id'], 'title': c['title'], 'text': c['limitation']}
                for c in packet['claims']]
    return title, {'bundle_version': PROFILE, 'bundle_hash': packet['snapshot_id'],
                   'built_at': packet['cutoff_at'], 'data_cutoff_at': packet['cutoff_at'],
                   'model_version': None, 'match': {'home': {'name': names.get('home') or match['home_team']},
                                                  'away': {'name': names.get('away') or match['away_team']}},
                   'probability_source': 'UNAVAILABLE', 'prediction_public': None,
                   'prediction_member': None, 'evidence': [], 'counter_evidence': [],
                   'uncertainty': [{'kind': 'research_review', 'text': '固定赛后证据；请通过研究审核接口编辑和审核。'}],
                   'chart_specs': [], 'source_notes': [], 'odds_timeline': [],
                   'odds_coverage_tier': 'none', 'social_profiles': {},
                   'script_sections': sections, 'subtitle_cues': [],
                   'research_packet': packet}


def _event(conn, row, revision, actor, action):
    content = {'title': row['title'], 'overrides': json.loads(row['overrides_json']),
               'bundle_hash': json.loads(row['bundle_json'])['bundle_hash'], 'status': row['status']}
    conn.execute('INSERT INTO research_draft_events VALUES (?,?,?,?,?,?,?,?)',
                 (new_uuid(), row['id'], actor, revision, action, digest(content),
                  canonical(content).decode(), utc_now_iso()))


def import_draft(conn, user_id, snapshot_id):
    packet, match_id, packet_hash = load_packet(snapshot_id)
    return store_packet(conn, user_id, packet, match_id)


def store_packet(conn, user_id, packet, match_id):
    snapshot_id, packet_hash = packet['snapshot_id'], digest(packet)
    title, bundle = make_bundle(packet)
    with tx(conn):
        existing = conn.execute('SELECT draft_id FROM research_draft_links WHERE user_id=? AND snapshot_id=?',
                                (user_id, snapshot_id)).fetchone()
        if existing:
            row = conn.execute('SELECT * FROM content_drafts WHERE id=?', (existing['draft_id'],)).fetchone()
            return {'draft_id': row['id'], 'title': row['title'], 'bundle_hash': snapshot_id}
        draft_id, now = new_uuid(), utc_now_iso()
        conn.execute("INSERT INTO content_drafts VALUES (?,?,?,?,?,'{}','draft',?,?)",
                     (draft_id, user_id, match_id, title, canonical(bundle).decode(), now, now))
        conn.execute('INSERT INTO research_draft_links VALUES (?,?,?,?,1)',
                     (draft_id, user_id, snapshot_id, packet_hash))
        row = conn.execute('SELECT * FROM content_drafts WHERE id=?', (draft_id,)).fetchone()
        _event(conn, row, 1, user_id, 'import')
    return {'draft_id': draft_id, 'title': title, 'bundle_hash': snapshot_id}


def revision(conn, draft_id):
    row = conn.execute('SELECT revision FROM research_draft_links WHERE draft_id=?', (draft_id,)).fetchone()
    return row['revision'] if row else None


def change(conn, draft_id, user_id, expected_revision, *, title=None, overrides=None, status=None):
    """Serialize revision checks, editorial updates and audit append in one transaction."""
    with tx(conn):
        row = conn.execute('SELECT * FROM content_drafts WHERE id=? AND user_id=?', (draft_id, user_id)).fetchone()
        if row is None:
            raise ValueError('草稿不存在')
        current = revision(conn, draft_id)
        if expected_revision is None or current != expected_revision:
            raise ValueError('草稿版本已变化，请重新读取后审核或编辑')
        if status is not None:
            if status not in ('draft', 'reviewed'):
                raise ValueError('研究草稿尚未接入发布流程')
            if status == row['status']:
                return
            if status == 'reviewed' and row['status'] != 'draft':
                raise ValueError('只允许审核待审草稿')
            action = 'review' if status == 'reviewed' else 'reopen'
            new_title, edited = row['title'], json.loads(row['overrides_json'])
        else:
            # Keep source metrics and claims immutable. Editorial notes are separate.
            allowed={'editorial_notes'}
            analyst=json.loads(row['bundle_json']).get('research_packet',{}).get('analyst_report')
            if analyst:allowed.add('judgments')
            if overrides is not None and (set(overrides) - allowed
                    or not isinstance(overrides.get('editorial_notes', ''), str)
                    or len(overrides.get('editorial_notes', '')) > 20000):
                raise ValueError('研究草稿仅允许编辑 editorial_notes，证据不可覆盖')
            judgments=(overrides or {}).get('judgments',{})
            if not isinstance(judgments,dict) or len(judgments)>5:
                raise ValueError('判断修订无效')
            for key,value in judgments.items():
                if not analyst or key not in {h['id'] for h in analyst['hypotheses']} or not isinstance(value,dict) or set(value)-{'text','video_note','withdrawn','reason'}:
                    raise ValueError('只能修订当前报告中的判断文字')
                if any(not isinstance(value.get(k,''),str) or len(value.get(k,''))>limit for k,limit in [('text',3000),('video_note',3000),('reason',1000)]) or not isinstance(value.get('withdrawn',False),bool) or (value.get('withdrawn') and not value.get('reason','').strip()):
                    raise ValueError('判断修订或撤回原因无效')
            new_title = row['title'] if title is None else title
            edited = json.loads(row['overrides_json'])
            edited.update(overrides or {})
            if new_title == row['title'] and edited == json.loads(row['overrides_json']):
                return
            action, status = 'edit', 'draft'
        conn.execute('UPDATE content_drafts SET title=?,overrides_json=?,status=?,updated_at=? WHERE id=?',
                     (new_title, canonical(edited).decode(), status, utc_now_iso(), draft_id))
        conn.execute('UPDATE research_draft_links SET revision=revision+1 WHERE draft_id=?', (draft_id,))
        updated = conn.execute('SELECT * FROM content_drafts WHERE id=?', (draft_id,)).fetchone()
        _event(conn, updated, current + 1, user_id, action)


def export_packet(conn, row):
    packet = json.loads(row['bundle_json'])['research_packet']
    link = conn.execute('SELECT packet_sha256 FROM research_draft_links WHERE draft_id=?', (row['id'],)).fetchone()
    if link is None or digest(packet) != link['packet_sha256']:
        raise ValueError('固定证据包校验失败')
    current = revision(conn, row['id'])
    event = conn.execute('SELECT * FROM research_draft_events WHERE draft_id=? AND revision=?',
                         (row['id'], current)).fetchone()
    content = {'title': row['title'], 'overrides': json.loads(row['overrides_json']),
               'bundle_hash': packet['snapshot_id'], 'status': row['status']}
    if event is None or event['content_hash'] != digest(content):
        raise ValueError('草稿内容与审核记录不一致')
    return {'schema_version': 'studio-research-export.v1', 'packet': packet,
            'editorial': content, 'review': {'state': row['status'], 'revision': current,
            'event_id': event['id'], 'actor_id': event['actor_id'], 'at': event['created_at'],
            'content_hash': event['content_hash']}}


def inbox_index():
    """Bounded verified inbox discovery; no caller-controlled filesystem paths."""
    from itertools import islice
    configured=os.environ.get('STUDIO_RESEARCH_INBOX')
    if not configured:raise ValueError('研究收件箱尚未配置')
    root=Path(configured).resolve(strict=True)/'manifests'
    if root.is_symlink():raise ValueError('收件箱不允许符号链接')
    entries=[];invalid=[]
    with os.scandir(root) as scan:
        candidates=list(islice((p for p in scan if p.is_file(follow_symlinks=False) and re.fullmatch(r'[0-9a-f]{64}\.json',p.name)),21))
    for entry in candidates[:20]:
        sid=Path(entry.name).stem
        try:
            packet,_,_=load_packet(sid)
            title,_=make_bundle(packet)
            entries.append({'snapshot_id':sid,'title':title,'cutoff_at':packet['cutoff_at'],'metric_count':len(packet['metrics']),'topic_count':len(packet['claims'])})
        except (OSError,ValueError,KeyError,TypeError):invalid.append(sid)
    return {'entries':sorted(entries,key=lambda x:x['cutoff_at'],reverse=True),'invalid_snapshot_ids':invalid,'truncated':len(candidates)>20}
