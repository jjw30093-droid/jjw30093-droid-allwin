"""Fill lineup-only player dimensions without overwriting known identities."""
import re


def identity(row):
    raw=row.get('Player_ID'); name=row.get('player_name')
    if isinstance(raw,bool) or not re.fullmatch(r'[1-9][0-9]{0,19}',str(raw)):
        return None
    if not isinstance(name,str) or not name.strip() or len(name)>200 or name.strip().casefold() in {'unknown','n/a','null','tbd','-'}:
        return None
    return str(raw),name.strip()


def lineup_candidates(rows):
    names={};invalid=0
    for row in rows:
        pair=identity(row)
        if pair is None:invalid+=1;continue
        pid,name=pair;names.setdefault(pid,set()).add(name)
    return ({pid:next(iter(values)) for pid,values in names.items() if len(values)==1},
            sorted(pid for pid,values in names.items() if len(values)>1),invalid)


def fill_lineup_players(conn,rows):
    candidates,conflicts,invalid=lineup_candidates(rows)
    added=[]
    for pid,name in sorted(candidates.items()):
        changed=conn.execute('INSERT INTO dim_player(Player_ID,Player_Name) VALUES(?,?) ON CONFLICT(Player_ID) DO NOTHING',(pid,name)).rowcount
        if changed:added.append(pid)
    return {'inserted':added,'conflicts':conflicts,'invalid_rows':invalid}
