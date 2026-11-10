"""Read-only alias resolution; no identity allocation or schema creation."""


def resolve(c,entity_id):
    # Compatibility with the first candidate, which has no alias table.
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='identity_aliases' AND type='table'").fetchone():return entity_id
    seen=set()
    for _ in range(32):
        if entity_id in seen:raise ValueError('identity_alias_cycle')
        seen.add(entity_id)
        row=c.execute('SELECT entity_id FROM identity_aliases WHERE alias_id=?',(entity_id,)).fetchone()
        if row is None:return entity_id
        entity_id=row[0]
    raise ValueError('identity_alias_depth')
