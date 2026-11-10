"""Present the source receipt; never infer missing numbers at export time."""
REASONS = {
    'not_played':'未出场',
    'source_stat_list_missing':'原始统计列表缺失',
    'source_stat_list_empty':'原始统计列表为空，无法确认',
    'source_stat_list_invalid':'原始统计列表异常',
    'source_value_invalid':'原始数值无效',
    'source_value_conflict':'原始与标准值冲突',
    'source_field_absent':'原始记录未列此项',
}


def official_stat(player, key):
    all_rows=player.get('official_by_match',[])
    rows=[r for r in all_rows if r.get('participation')!='not_played']
    values=[r['stats'][key] for r in rows if r.get('stats',{}).get(key) is not None]
    reasons=sorted({REASONS.get(r.get('stat_status',{}).get(key,r.get('availability')),'旧快照未保存缺失原因')
                    for r in rows if r.get('stats',{}).get(key) is None})
    if values:
        return {'value':sum(values),'coverage':len(values),'missing':len(rows)-len(values),
                'reasons':reasons,
                'zero_restored':sum(r.get('stat_status',{}).get(key)=='zero_omitted' for r in rows),
                'raw_recovered':sum(r.get('stat_status',{}).get(key)=='raw_recovered' for r in rows)}
    if not rows: reasons=['未出场' if all_rows else '无出场样本' if not player.get('minutes') else '旧快照未保存统计读取凭据']
    return {'value':None,'coverage':0,'missing':len(rows),'reasons':reasons,'zero_restored':0,'raw_recovered':0}


def stat_text(player,key):
    x=official_stat(player,key)
    if x['value'] is None:return '；'.join(x['reasons'])
    return f'{x["value"]:g}（{x["coverage"]}场'+(f'；另{x["missing"]}场未计入' if x['missing'] else '')+'）'


def metric_cell(player,key):
    if player.get(key) is not None:return player[key]
    if not player.get('minutes'):
        rows=player.get('official_by_match',[])
        return '未出场' if rows and all(r.get('participation')=='not_played' for r in rows) else '选区无样本'
    return '未关联'
