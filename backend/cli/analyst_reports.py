"""Single-writer report worker. Reads existing feeds only; never trains or collects."""
import argparse
import json
import time
import os
from contextlib import closing
from backend.db.connections import connect_rw, connect_ro
from backend.studio import analyst_workflow as flow
from backend.api.standard_identity import configured_store


def scan(conn,user):
    # Lineup/injury changes are detected per fixture; unrelated fixtures keep their key.
    from backend.studio.prematch import lineup_context
    pending=[]
    for item in flow.source_read('candidates')['matches']:
        prior=flow.latest(conn,user,item['id'],'prematch')
        context={}
        if item['phase']=='prematch':
            # Reuse only roster profiles, never reinterpret prior statistical samples.
            shell={'match':dict((prior or {}).get('report',{}).get('match',{})),
                   'teams':(prior or {}).get('report',{}).get('teams',[{'players':[]},{'players':[]}]),
                   'cutoff_at':flow.utc_now_iso(),'revision':'inventory','findings':[]}
            if prior:
                context=lineup_context(shell).get('lineup',{})
            else:context={'initial':True}
        fingerprint=flow.research.digest([flow.VERSION,item['input_revision'],context,
            prior['revision'] if prior and item['phase']=='postmatch' else None])
        pending.append(flow.enqueue(conn,user,item['id'],item['phase'],fingerprint))
    return pending


def main():
    p=argparse.ArgumentParser();p.add_argument('--owner',required=True);p.add_argument('--watch',action='store_true');p.add_argument('--once',action='store_true');p.add_argument('--scan',action='store_true')
    a=p.parse_args()
    if os.environ.get('STUDIO_REPORT_WORKFLOW_ENABLED')!='1':raise ValueError('报告工作流功能开关未启用')
    with closing(connect_rw('platform')) as c:
        owner=c.execute('SELECT role FROM users WHERE id=?',(a.owner,)).fetchone()
        if not owner or owner['role']!='admin':raise ValueError('工作流所有者必须是已有管理员')
        while True:
            if a.scan or a.watch:
                try:print(json.dumps({'candidate_jobs':len(scan(c,a.owner))}))
                except (OSError,ValueError):print(json.dumps({'scan':'source_unavailable'}))
            for _ in range(100):
                job=flow.run_one(c)
                if not job:break
                print(json.dumps({'processed_job':job}),flush=True)
            if not a.watch:break
            time.sleep(60)


if __name__=='__main__':main()
