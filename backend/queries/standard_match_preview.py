"""Owned preview and profile adapters over fixed read snapshots."""
from contextlib import closing
from backend.vendor.miaodi_standard.contract import stable_id,utc
from backend.vendor.miaodi_standard.facts import snapshots,read,identity_lookup,match_source
from backend.vendor.miaodi_standard.identity_lookup import resolve
from backend.queries import matches,match_preview
from backend.queries.presentation_text import display_name
from backend.api import schemas as legacy
from backend.api.standard_preview_schema import StandardPreviewResponse,StandardDataProfileResponse
from .standard_preview_people import roster_evidence,issue
from .standard_preview_players import player_windows


def _peer(row,lookup,mid,scope):
    source=row['team_id'];identity=lookup('team',source)
    return {**row,'team_id':identity,'record_id':stable_id('preview_team_record','miaodi',mid+'/'+scope+'/'+str(source)),
            'name':display_name(row['name'],source,'球队名称未收录')}


def canonical_profile(raw,lookup,mid):
    result=legacy.MatchDataProfileDTO.model_validate(raw).model_dump(mode='python')
    for g in result['groups']:
        for side in ('home_peers','away_peers'):
            g[side]=[_peer(r,lookup,mid,'profile/'+g['key']+'/'+side) for r in g[side]]
    return result


def standard_preview(registry,source,odds,identity,*,profile_only=False,max_n=10,venue_mode='recent'):
    with snapshots(registry,source) as (ids,core),closing(read(odds)) as evidence:
        mid=resolve(ids,identity);sid=match_source(ids,mid,'fotmob');match=matches.match_by_id(core,int(sid))
        if match is None:raise LookupError('match_unavailable')
        lookup=identity_lookup(ids,'fotmob')
        if profile_only:
            raw=match_preview.match_data_profile_for(core,match,max_n=max_n,venue_mode=venue_mode)
            return StandardDataProfileResponse.model_validate({'schema_version':'miaodi-website-data-profile.v1','match_id':mid,'profile':canonical_profile(raw,lookup,mid)}).model_dump(mode='json')
        raw=match_preview.build_match_preview(core,evidence,match,include_rosters=False,include_players=False)
        lineups,sidelined,quality=roster_evidence(ids,core,evidence,mid,match,lookup)
        players,keepers=player_windows(ids,core,mid,match,lookup)
        styles=[]
        for view in raw['style_views']:
            row=legacy.MatchPreviewStyleViewDTO.model_validate(view).model_dump(mode='python')
            row['points']=[_peer(p,lookup,mid,'style/'+row['id']) for p in row['points']]
            styles.append(row)
        # Analytical projections stay explicitly typed; arbitrary raw extras stop here.
        attack=legacy.MatchPreviewAttackSourcesDTO.model_validate(raw['attack_sources']).model_dump(mode='python')
        matchup=legacy.MatchPreviewMatchupProfilesDTO.model_validate(raw['matchup_profiles']).model_dump(mode='python')
        body={'schema_version':'miaodi-website-preview.v1','match_id':mid,
            'home_team_id':lookup('team',match['home']['team_id']),'away_team_id':lookup('team',match['away']['team_id']),
            'generated_at':utc(raw['observed_at']),'home_window':raw['home_window'],'away_window':raw['away_window'],
            'lineups':lineups,'sidelined':sidelined,'style_views':styles,'attack_sources':attack,
            'data_profile':canonical_profile(raw['data_profile'],lookup,mid),'matchup_profiles':matchup,
            'key_players':players,'keepers':keepers,'issues':quality}
        return StandardPreviewResponse.model_validate(body).model_dump(mode='json',by_alias=True)
