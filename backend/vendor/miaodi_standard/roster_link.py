"""Report-scoped identity evidence; never adopts global identifiers."""
def match_roster_profile(person,profiles,published_id):
    """Keep published IDs intact; attach a scoped, inspectable roster join receipt."""
    direct=next((p for p in profiles if p['id']==published_id),None) if published_id else None
    if direct:return direct,{'method':'published_player_identity','evidence_player_id':direct['id']}
    import unicodedata
    def name(value):
        value=''.join(c for c in unicodedata.normalize('NFKD',str(value or '')) if not unicodedata.combining(c))
        return ' '.join(''.join(c if c.isalnum() else ' ' for c in value.casefold()).split())
    full=name(person.get('name'));shirt=person.get('shirt_number')
    if not full or shirt is None:return None,{'method':'unresolved'}
    numbered=[p for p in profiles if str(shirt) in {str(n) for n in p.get('shirt_numbers',[])}]
    candidates=[p for p in numbered if full in {name(n) for n in [*p.get('full_names',[]),*p.get('provider_name_aliases',[])]}]
    # Some feeds publish a single given name. Require an exact first token,
    # shirt and mapped team, and retain a scoped receipt rather than merge IDs.
    if not candidates and len(full.split())==1 and len(full)>2:
        mono=[p for p in numbered if full in {name(n).split()[0] for n in p.get('full_names',[]) if name(n)}]
        if len(mono)==1:
            return mono[0],{'method':'exact_mononym_given_shirt_within_mapped_team','evidence_player_id':mono[0]['id'],
                            'scope':'this_report_roster_only','definition_version':'roster-evidence-link.v3'}
    abbreviation=False
    if not candidates:
        # Provider display names abbreviate given names, while legal names may
        # include middle names. Require surname, initial, shirt and team, and
        # retain this report-scoped receipt rather than adopting a global ID.
        compact=full.replace(' ','')
        for p in numbered:
            surname=name(p.get('surname')).replace(' ','')
            initials={name(n)[0] for n in [p.get('name'),*p.get('full_names',[])] if name(n)}
            if len(full.split()[0])==1 and surname and compact.endswith(surname) and len(compact)>len(surname) and compact[0] in initials:
                candidates.append(p)
        abbreviation=True
    if not candidates and len(full.split())>=2 and len(full.split()[0])>1:
        for p in numbered:
            legal=[name(n).split() for n in p.get('full_names',[]) if name(n)]
            surname=name(p.get('surname')).split()
            if surname and full.split()[1:]==surname and any(full.split()[0] in n[:-len(surname)] for n in legal):
                candidates.append(p)
    display_join=False
    if not candidates:
        # Opta's legal surname can include a middle name (Braut Haaland) or
        # second surname (Muñoz Mejía). Match the provider display surname plus
        # the exact full given name, shirt and mapped team, without fuzzy text.
        parts=full.split()
        if len(parts)>=2 and len(parts[0])>1:
            for p in numbered:
                given={name(n).split()[0] for n in p.get('full_names',[]) if name(n)}
                display=[name(n).split() for n in [p.get('name'),*p.get('provider_name_aliases',[])]]
                if parts[0] in given and any(len(d)>=2 and len(d[0])==1 and d[0]==parts[0][0] and d[1:]==parts[1:] for d in display):candidates.append(p)
        display_join=True
    if not candidates and len(full.split()) >= 2:
        prefix=[p for p in numbered if any(name(n).split()[:len(full.split())]==full.split()
                for n in p.get('full_names',[]))]
        if len(prefix)==1:
            return prefix[0],{'method':'exact_legal_given_prefix_shirt_within_mapped_team',
                'evidence_player_id':prefix[0]['id'],'scope':'this_report_roster_only',
                'definition_version':'roster-evidence-link.v4'}
        if prefix:return None,{'method':'ambiguous'}
    if len(candidates)!=1:return None,{'method':'unresolved'}
    method='exact_given_display_surname_shirt_within_mapped_team' if display_join else 'surname_initial_shirt_within_mapped_team' if abbreviation else 'exact_full_name_shirt_within_mapped_team' if full in {name(n) for n in candidates[0].get('full_names',[])} else 'exact_provider_name_shirt_within_mapped_team'
    return candidates[0],{'method':method,'evidence_player_id':candidates[0]['id'],
                          'scope':'this_report_roster_only','definition_version':'roster-evidence-link.v2'}

