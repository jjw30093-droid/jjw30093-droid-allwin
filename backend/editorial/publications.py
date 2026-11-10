"""Transactional publication of explicit, reviewed text and fixed evidence.

No source lookups on reads, no editorial notes or internal source IDs projected.
Responses deliberately use no-store until a versioned cache worker exists.
"""
import json
import re
from backend.db.connections import tx
from backend.db.util import new_uuid, utc_now_iso
from backend.studio import research


class PublicationConflict(ValueError):
    pass


def _actor(conn, actor_id):
    row=conn.execute("SELECT role,status FROM users WHERE id=?",(actor_id,)).fetchone()
    if row is None or row["status"]!="active" or row["role"] not in ("analyst","admin"):
        raise PermissionError("active editor required")


def state(conn, draft_id, actor_id):
    draft = conn.execute('SELECT id FROM content_drafts WHERE id=? AND user_id=?', (draft_id, actor_id)).fetchone()
    if draft is None:
        raise LookupError('draft not found')
    row = conn.execute('SELECT * FROM analysis_articles WHERE draft_id=? AND owner_id=?', (draft_id, actor_id)).fetchone()
    return {'slug': row['slug'] if row else None,
            'publication_version': row['publication_version'] if row else 0,
            'published': bool(row and row['current_revision_id'])}


def publish(conn, *, draft_id, actor_id, expected_revision, expected_publication_version,
            slug, excerpt, body, kind, visibility, reason):
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', slug) or len(slug)>100:
        raise ValueError('invalid slug')
    if not all(isinstance(v,str) and v.strip() for v in (body,excerpt,reason)):
        raise ValueError('publication text required')
    if len(body)>20000 or len(excerpt)>300 or len(reason)>500:
        raise ValueError('publication text too long')
    if kind not in ('prematch','postmatch','round') or visibility not in ('public','member'):
        raise ValueError('invalid publication scope')
    with tx(conn):
        _actor(conn,actor_id)
        current = state(conn,draft_id,actor_id)
        if current['publication_version'] != expected_publication_version:
            raise PublicationConflict('publication changed')
        draft = conn.execute('SELECT * FROM content_drafts WHERE id=?', (draft_id,)).fetchone()
        exported = research.export_packet(conn,draft)
        review, packet = exported['review'], exported['packet']
        if review['state']!='reviewed' or review['revision']!=expected_revision:
            raise PublicationConflict('review is stale or missing')
        # The fixed evidence determines the article kind; editors cannot relabel it.
        if kind != packet.get('kind', 'postmatch'):
            raise ValueError('publication kind must match fixed evidence')
        if kind=='prematch':
            from datetime import datetime, timezone
            from backend.studio.predictions import _time
            if datetime.now(timezone.utc)>=_time(packet['subject']['kickoff_utc']):
                raise ValueError('prematch publication must precede kickoff')
        document = {
            'body': body.strip(), 'data_cutoff_at': packet['cutoff_at'],
            'analysis_mode': packet['analysis_mode'],
            'coverage': packet.get('subject',{}).get('coverage'),
            'metrics': [{key:m.get(key) for key in ('label','value','unit','side','definition_version')}
                        for m in packet['metrics']],
            'teams': ({'round':'本轮样本'} if kind=='round' else {side:packet.get('display_names',{}).get(side) or packet['match'].get(side+'_team','')
                      for side in ('home','away')}),
        }
        # Public copy is explicit. Source IDs, raw packets, operator notes,
        # event exports, actor IDs and claims are never copied into this DTO.
        content = research.canonical(document).decode()
        version = expected_publication_version+1
        now = utc_now_iso()
        article = conn.execute('SELECT * FROM analysis_articles WHERE draft_id=?',(draft_id,)).fetchone()
        if article:
            if slug != article['slug']:
                raise PublicationConflict('published URL is immutable')
            aid=article['id']
        else:
            if conn.execute('SELECT 1 FROM analysis_articles WHERE slug=?',(slug,)).fetchone():
                raise PublicationConflict('URL already used')
            aid=new_uuid()
            conn.execute('INSERT INTO analysis_articles(id,draft_id,owner_id,slug,created_at,updated_at) VALUES(?,?,?,?,?,?)',
                         (aid,draft_id,actor_id,slug,now,now))
        rid=new_uuid()
        conn.execute('''INSERT INTO analysis_article_versions
            (id,article_id,publication_version,source_revision,review_event_id,title,excerpt,kind,visibility,content_json,content_sha256,published_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
            (rid,aid,version,expected_revision,review['event_id'],draft['title'],excerpt.strip(),kind,visibility,content,research.digest(document),now))
        conn.execute('UPDATE analysis_articles SET current_revision_id=?,publication_version=?,updated_at=? WHERE id=?', (rid,version,now,aid))
        conn.execute('INSERT INTO analysis_publication_events VALUES(?,?,?,?,?,?,?,?)',
                     (new_uuid(),aid,version,actor_id,'publish',rid,reason.strip(),now))
        return state(conn,draft_id,actor_id)


def retract(conn, *,draft_id,actor_id,expected_publication_version,reason):
    if not reason.strip() or len(reason)>500: raise ValueError('reason required')
    with tx(conn):
        _actor(conn,actor_id)
        current=state(conn,draft_id,actor_id)
        if current['publication_version']!=expected_publication_version or not current['published']:
            raise PublicationConflict('publication changed or not published')
        row=conn.execute('SELECT * FROM analysis_articles WHERE draft_id=?',(draft_id,)).fetchone()
        version=expected_publication_version+1; now=utc_now_iso()
        conn.execute('UPDATE analysis_articles SET current_revision_id=NULL,publication_version=?,updated_at=? WHERE id=?',(version,now,row['id']))
        conn.execute('INSERT INTO analysis_publication_events VALUES(?,?,?,?,?,?,?,?)',
                     (new_uuid(),row['id'],version,actor_id,'retract',row['current_revision_id'],reason.strip(),now))
        return state(conn,draft_id,actor_id)


QUERY='''SELECT a.slug,a.publication_version,v.* FROM analysis_articles a
 JOIN analysis_article_versions v ON v.article_id=a.id AND v.id=a.current_revision_id'''


def summary(row):
    return {key:row[key] for key in ('slug','publication_version','title','excerpt','kind','visibility','published_at')}


def list_articles(conn, limit=30):
    return [summary(row) for row in conn.execute(QUERY+' ORDER BY a.updated_at DESC,a.id LIMIT ?', (limit,))]


def read_article(conn,slug):
    row=conn.execute(QUERY+' WHERE a.slug=?',(slug,)).fetchone()
    if row is None: raise LookupError('article not found')
    content=json.loads(row['content_json'])
    if research.digest(content)!=row['content_sha256']:
        raise PublicationConflict('stored publication integrity mismatch')
    return summary(row),content
