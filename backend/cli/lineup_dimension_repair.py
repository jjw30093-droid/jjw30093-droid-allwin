"""Bounded, reproducible missing-player repair from existing lineup evidence."""
import argparse
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from backend.ingest.player_dimensions import lineup_candidates


def encoded(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()


def plan(conn, match_ids):
    ids=sorted(set(match_ids))
    if not ids or len(ids)>64 or any(type(i)!=int or i<=0 for i in ids):raise ValueError('explicit_1_to_64_matches_required')
    rows=[dict(r) for r in conn.execute('SELECT Match_ID,Team_ID,Player_ID,player_name,is_starter FROM fact_match_lineup WHERE Match_ID IN ('+','.join('?' for _ in ids)+') ORDER BY Match_ID,Player_ID LIMIT 8193',ids)]
    if len(rows)>8192:raise ValueError('lineup_row_limit')
    names,conflicts,invalid=lineup_candidates(rows)
    dimensions={pid:(dict(r) if (r:=conn.execute('SELECT Player_ID,Player_Name FROM dim_player WHERE Player_ID=?',(pid,)).fetchone()) else None) for pid in sorted(names)}
    inserts=[{'Player_ID':pid,'Player_Name':name} for pid,name in sorted(names.items()) if dimensions[pid] is None]
    source={'match_ids':ids,'lineup':rows,'dimensions':dimensions}
    return {'schema_version':'lineup-dimension-repair.v1','source_sha256':hashlib.sha256(encoded(source)).hexdigest(),'match_ids':ids,'lineup_rows':len(rows),'inserts':inserts,'conflicting_ids':conflicts,'invalid_rows':invalid,'source':source}


def apply(conn, expected):
    conn.execute('BEGIN IMMEDIATE')
    try:
        actual=plan(conn,expected['match_ids'])
        if actual['source_sha256']!=expected['source_sha256'] or actual['inserts']!=expected['inserts']:
            raise ValueError('source_changed_replan_required')
        for row in actual['inserts']:
            conn.execute('INSERT INTO dim_player(Player_ID,Player_Name) VALUES(?,?)',(row['Player_ID'],row['Player_Name']))
        conn.commit();return actual['inserts']
    except BaseException:conn.rollback();raise


def snapshot_dimension(conn,directory,proposal):
    directory.mkdir(mode=0o700,parents=True,exist_ok=False)
    path=directory/'dim_player.before.sqlite3'
    with closing(sqlite3.connect(path)) as target:
        target.execute('CREATE TABLE dim_player(Player_ID TEXT PRIMARY KEY,Player_Name TEXT)')
        # Backup the affected small dimension, not another copy of the whole DB.
        count=0
        for row in conn.execute('SELECT Player_ID,Player_Name FROM dim_player'):
            count+=1
            if count>200000:raise ValueError('dimension_backup_limit')
            target.execute('INSERT INTO dim_player VALUES(?,?)',tuple(row))
        target.commit()
        if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('dimension_backup_invalid')
    os.chmod(path,0o600)
    (directory/'proposal.json').write_bytes(encoded(proposal));os.chmod(directory/'proposal.json',0o600)
    return {'table':'dim_player','rows':count,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size':path.stat().st_size}


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',type=Path,required=True)
    p.add_argument('--match-id',type=int,action='append',required=True);p.add_argument('--output',type=Path,required=True)
    p.add_argument('--apply-sha256');p.add_argument('--backup-dir',type=Path);a=p.parse_args()
    if a.output.exists():p.error('output_already_exists')
    if a.apply_sha256 and not a.backup_dir:p.error('backup_required')
    os.umask(0o077)
    with closing(sqlite3.connect(a.database.resolve().as_uri()+('?mode=rw' if a.apply_sha256 else '?mode=ro'),uri=True,timeout=5)) as conn:
        conn.row_factory=sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON')
        report=plan(conn,a.match_id)
        if a.apply_sha256:
            if report['source_sha256']!=a.apply_sha256:raise ValueError('approved_plan_hash_mismatch')
            before=snapshot_dimension(conn,a.backup_dir,report)
            inserted=apply(conn,report)
            report={'plan_sha256':a.apply_sha256,'inserted':inserted,'backup':before,'after_pending':plan(conn,a.match_id)['inserts']}
        with a.output.open('xb') as f:f.write(encoded(report))
    print(json.dumps({'mode':'apply' if a.apply_sha256 else 'plan','output':a.output.name,'inserted' if a.apply_sha256 else 'proposed':len(report['inserted' if a.apply_sha256 else 'inserts'])}))


if __name__=='__main__':main()
