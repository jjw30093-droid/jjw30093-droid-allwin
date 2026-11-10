from backend.studio.analyst_stats import official_stat,stat_text


def test_zero_receipt_and_missing_source_are_distinct():
    p={'minutes':90,'official_by_match':[{'stats':{'totalCross':0},'stat_status':{'totalCross':'zero_omitted'}},
       {'stats':{},'availability':'source_stat_list_missing'}]}
    x=official_stat(p,'totalCross')
    assert x['value']==0 and x['coverage']==1 and x['missing']==1 and x['zero_restored']==1
    assert '另1场未计入' in stat_text(p,'totalCross')
    assert official_stat(p,'expectedAssists')['value'] is None


def test_no_appearance_and_legacy_unknown_are_explained():
    assert stat_text({'minutes':0},'totalPass')=='无出场样本'
    assert '旧快照' in stat_text({'minutes':90},'totalPass')
    assert stat_text({'minutes':0,'official_by_match':[{'stats':{},'availability':'not_played'}]},'totalPass')=='未出场'


def test_selected_zero_minutes_does_not_mean_no_actual_appearance():
    from backend.studio.analyst_stats import metric_cell
    p={'minutes':0,'official_by_match':[{'stats':{'totalPass':7},'participation':'played'}]}
    assert metric_cell(p,'model_xt')=='选区无样本'
    p['official_by_match'][0]['participation']='not_played'
    assert metric_cell(p,'model_xt')=='未出场'
