"""Freeze combined CC evidence and VIP observed lineups without copying source DBs."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
from uuid import UUID
import tempfile
import time
from backend.studio.prematch import read_report,lineup_context

def freeze(root:Path,report:dict):
    # A revision includes event evidence and the lineup/injury snapshot identities.
    fingerprint=hashlib.sha256(json.dumps([report['revision'],report['lineup']],sort_keys=True).encode()).hexdigest()
    mid=str(UUID(report['match']['id']))
    target=root/mid/(fingerprint+'.json')
    value=dict(report,snapshot_observed_at=datetime.now(timezone.utc).isoformat())
    if target.exists():value=json.loads(target.read_text())
    raw=json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd,temp=tempfile.mkstemp(dir=target.parent,prefix='.pending-')
    try:
        with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        try:os.link(temp,target)
        except FileExistsError:pass
    finally:os.unlink(temp)
    metadata=target.with_name(fingerprint+'.metadata.json')
    if not metadata.exists():
        info={'snapshot_id':fingerprint,'observed_at':value['snapshot_observed_at'],'cutoff_at':value.get('cutoff_at'),
              'lineup_type':value['lineup'].get('type'),'window':value.get('filters',{}).get('window')}
        fd,temp=tempfile.mkstemp(dir=target.parent,prefix='.pending-')
        try:
            with os.fdopen(fd,'w') as f:json.dump(info,f);f.flush();os.fsync(f.fileno())
            try:os.link(temp,metadata)
            except FileExistsError:pass
        finally:os.unlink(temp)
    return target

def run(output):
    fixtures=read_report('fixtures',{})['matches'];built=[]
    for match in fixtures:
        if match.get('mode')=='reconstructed':continue
        for window in (5,10):
            value=lineup_context(read_report('matches/'+match['id'],{'window':window}))
            built.append(str(freeze(output,value)))
    return {'snapshots':len(built),'supplier_requests':0}

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--watch',action='store_true');a=p.parse_args()
    while True:
        print(json.dumps(run(a.output)),flush=True)
        if not a.watch:return
        time.sleep(60)

if __name__=='__main__':main()
