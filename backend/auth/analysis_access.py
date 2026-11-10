"""Independent analysis-product rights. Transaction ownership stays with callers."""
import hashlib
import json
from datetime import datetime, timezone

from backend.db.util import new_uuid


class AccessConflict(ValueError):
    pass


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def db_now(conn):
    return conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ','now')").fetchone()[0]


def ensure_trial(conn, user_id):
    if not conn.in_transaction:
        raise RuntimeError('trial requires authentication transaction')
    row = conn.execute('SELECT status FROM users WHERE id=?', (user_id,)).fetchone()
    if row is None or row['status'] != 'active':
        raise ValueError('account unavailable')
    # INSERT SELECT avoids touching an existing immutable trial. BEGIN IMMEDIATE
    # on login serializes concurrent first authentication across site sessions.
    conn.execute('''INSERT INTO analysis_trials(user_id,starts_at,ends_at)
        SELECT ?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                 strftime('%Y-%m-%dT%H:%M:%fZ','now','+72 hours')
        WHERE NOT EXISTS(SELECT 1 FROM analysis_trials WHERE user_id=?)''', (user_id, user_id))


def resolve_access(conn, user_id):
    owns_transaction = not conn.in_transaction
    if owns_transaction:
        conn.execute('BEGIN')
    try:
        return _resolve_access(conn, user_id)
    finally:
        if owns_transaction:
            conn.rollback()


def _resolve_access(conn, user_id):
    now = db_now(conn)
    row = conn.execute('SELECT status FROM users WHERE id=?', (user_id,)).fetchone()
    result = {'product': 'analysis', 'allowed': False, 'basis': 'none', 'checked_at': now,
              'trial_ends_at': None, 'membership_ends_at': None, 'membership_revision': 0,
              'membership_status': None}
    if row is None or row['status'] != 'active':
        return {**result, 'basis': 'account_disabled'}
    trial = conn.execute('SELECT * FROM analysis_trials WHERE user_id=?', (user_id,)).fetchone()
    member = conn.execute('SELECT * FROM analysis_memberships WHERE user_id=?', (user_id,)).fetchone()
    if trial:
        result['trial_ends_at'] = trial['ends_at']
    if member:
        result.update(membership_ends_at=member['ends_at'], membership_revision=member['revision'],
                      membership_status=member['status'])
    def active(record):
        return bool(record and conn.execute('SELECT julianday(?)<=julianday(?) AND julianday(?)<julianday(?)',
                         (record['starts_at'], now, now, record['ends_at'])).fetchone()[0])
    if member and member['status'] == 'active' and active(member):
        return {**result, 'allowed': True, 'basis': 'membership'}
    if active(trial):
        return {**result, 'allowed': True, 'basis': 'trial'}
    return result


def change_membership(conn, *, actor_id, user_id, action, ends_at, expected_revision,
                      request_key, reason):
    if not conn.in_transaction:
        raise RuntimeError('membership requires transaction')
    actor = conn.execute('SELECT role,status FROM users WHERE id=?', (actor_id,)).fetchone()
    if actor is None or actor['role'] != 'admin' or actor['status'] != 'active':
        raise PermissionError('active admin required')
    if action not in ('grant', 'revoke') or not reason.strip() or not request_key.strip():
        raise ValueError('invalid membership command')
    if type(expected_revision) is not int or expected_revision < 0:
        raise ValueError('invalid expected revision')
    if action == 'grant':
        parsed = datetime.fromisoformat(ends_at.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('explicit expiry timezone required')
        ends_at = parsed.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    elif ends_at is not None:
        raise ValueError('revoke must not include expiry')
    payload = {'user_id': user_id, 'action': action, 'ends_at': ends_at,
               'expected_revision': expected_revision, 'reason': reason.strip()}
    digest = hashlib.sha256(_json(payload).encode()).hexdigest()
    previous = conn.execute('SELECT request_sha256,after_json FROM analysis_membership_events WHERE actor_id=? AND request_key=?',
                            (actor_id, request_key)).fetchone()
    if previous:
        if previous['request_sha256'] != digest:
            raise AccessConflict('idempotency key reused with different request')
        return json.loads(previous['after_json'])
    if conn.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone() is None:
        raise ValueError('target user not found')
    row = conn.execute('SELECT * FROM analysis_memberships WHERE user_id=?', (user_id,)).fetchone()
    before = dict(row) if row else None
    revision = before['revision'] if before else 0
    if revision != expected_revision:
        raise AccessConflict('membership revision changed')
    now = db_now(conn)
    if action == 'grant':
        if not conn.execute('SELECT julianday(?)>julianday(?)', (ends_at, now)).fetchone()[0]:
            raise ValueError('expiry must be in the future')
        after = {'user_id': user_id, 'status': 'active', 'starts_at': now,
                 'ends_at': ends_at, 'revision': revision + 1, 'updated_at': now}
    else:
        if before is None:
            raise AccessConflict('membership does not exist')
        after = {**before, 'status': 'revoked', 'revision': revision + 1, 'updated_at': now}
    conn.execute('''INSERT INTO analysis_memberships (user_id,status,starts_at,ends_at,revision,updated_at)
        VALUES(:user_id,:status,:starts_at,:ends_at,:revision,:updated_at)
        ON CONFLICT(user_id) DO UPDATE SET status=excluded.status,starts_at=excluded.starts_at,
        ends_at=excluded.ends_at,revision=excluded.revision,updated_at=excluded.updated_at''', after)
    conn.execute('''INSERT INTO analysis_membership_events
        (id,user_id,actor_id,request_key,request_sha256,revision,action,reason,before_json,after_json,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?)''', (new_uuid(), user_id, actor_id, request_key, digest,
        after['revision'], action, reason.strip(), _json(before), _json(after), now))
    return after
