from copy import deepcopy
from backend.studio.analyst_pdf_evidence import event_index
from backend.api.routes_analyst_reports import Export
from backend.studio.analyst_pdf_layout import reader_report

def test_export_defaults_reader_and_supports_separate_evidence():
    assert Export(kind='pdf',expected_revision=1).pdf_profile=='reader'
    assert Export(kind='pdf',expected_revision=1,pdf_profile='evidence').pdf_profile=='evidence'

def test_event_appendix_index_deduplicates_without_dropping_ids():
    a={'match_id':'m','event_id':'1','minute':12};b=dict(a,event_id='2')
    assert len(event_index({'report':{'a':[a,a,b],'b':a}}))==2

def test_legacy_reader_alignment_uses_saved_scope_and_does_not_mutate():
    r={'phase':'postmatch','match':{'competition':'EPL'},'checks':[{'scope':{'eleven':True,'penalties':False,'periods':[1,2],'state':'all'}}],
       'regional_baseline':[{'id':'a'}],'teams':[{'id':'a','minutes':1,'chains':[{'period':1,'penalty':False,'home_players':11,'away_players':11}]}],
       'evidence':{'state_intervals':[{'start_match_elapsed_ms':0,'end_match_elapsed_ms':60000,'home_players':11,'away_players':11}]}}
    before=deepcopy(r);projected=reader_report(r)
    assert projected['regional_current'][0]['minutes']==1
    assert r==before
    r['checks'][0]['scope']['state']='leading'
    assert 'regional_current' not in reader_report(r)
