"""Plain-language export preserves facts, qualification, and editorial text."""
import re
from backend.studio.analyst_pdf_language import reader_text


def test_wording_preserves_values_and_scope():
    original='非点球 10 射、0.66 xG（覆盖 10/10）；同一覆盖样本；带球代理 4 次；xT -0.0039。'
    got=reader_text(original)
    assert '除点球外 10 射' in got
    assert '10次射门中10次有xG数据' in got
    assert '同一批有数据的射门' in got and '推算的带球次数 4 次' in got
    assert re.findall(r'-?\d+(?:\.\d+)?',original)==re.findall(r'-?\d+(?:\.\d+)?',got)


def test_uncertainty_and_source_identity_are_not_erased():
    assert '无法知道' in reader_text('预计首发未实际出场的反事实无法观察；不以实际进球证明阵容选择因果。')
    identifier='a978965d-16ac-5580-a96f-a6be0f41eaee / fc52d183-9d90-5c1f-8f4b-ade444cabe1e'
    assert reader_text(identifier)==identifier
    assert reader_text('阶段1 35:23')=='上半场 35:23'
    assert reader_text('Goalkeeper')=='门将'
    assert reader_text('reconstructed')=='根据历史资料重建，非当时保存的赛前报告'


def test_lineup_duties_read_naturally_and_cutoff_has_explicit_timezone():
    from backend.studio.analyst_pdf_language import reader_time
    assert reader_text('相关创造／终结职责的接替')=='由谁接替最后一传和射门的任务'
    assert reader_text('在相同区、相同方式被创造 3 次')=='在同一区域、被相同进攻方式制造了 3 次'
    assert reader_time('2026-11-08T14:00:00Z')=='2026年11月08日 22:00 北京时间'
    assert reader_time('暂无时间')=='暂无时间'


def test_real_export_keeps_manual_edits_and_snapshot(tmp_path):
    import json
    from copy import deepcopy
    from pathlib import Path
    import pytest
    from pypdf import PdfReader
    from backend.studio.analyst_pdf import render
    source=Path('/tmp/cc-v4-post-report.json')
    if not source.exists():
        pytest.skip('Opt-in real captured report unavailable')
    value=json.loads(source.read_text());value=deepcopy(value)
    original_text='人工保留：带球代理，xT -0.0039；这句话由分析师确认。'
    value['editorial_notes']=original_text
    before=deepcopy(value);target=tmp_path/'reader.pdf';render(value,target)
    assert value==before
    pdf=PdfReader(target)
    text='\n'.join(p.extract_text() for p in pdf.pages)
    assert original_text in text
    assert '指标怎么读' in text and '根据历史资料重建' not in text  # postmatch
    assert '本场出场93.1分钟' in text and '官方整场xA 0.34' in text
    assert 'current_postmatch' not in text
    appendix=next(i for i,p in enumerate(pdf.pages) if '证据附录 · 专项图表与完整球员统计' in p.extract_text())
    assert appendix<=75  # full analyst report now includes all specialist spreads
    # Related panels must remain together, rather than one chart per page.
    lineup=next(p.extract_text() for p in pdf.pages if '阵容 · 预计与正式首发对照' in p.extract_text())
    assert all(x in lineup for x in ('Sunderland · 此前预计','Sunderland · 当前首发','Chelsea · 此前预计','Chelsea · 当前首发'))
    shots=next(p.extract_text() for p in pdf.pages if '终结 · 射门位置、进攻方式与创造者' in p.extract_text())
    assert shots.count('· 射门图')==2 and shots.count('· 机会来自哪里')==2 and shots.count('· 整场xA前4名')==2
    assert any('压迫、夺回和丢失' in p.extract_text() and p.extract_text().count('· 防守过程')==2 for p in pdf.pages[:appendix])
    assert any('过人、犯规与推进威胁' in p.extract_text() and p.extract_text().count('· 逐动作xT')==2 for p in pdf.pages[:appendix])
    main='\n'.join(p.extract_text() for p in pdf.pages[:appendix])
    for title in ('近期状态 · 逐场走势','两队总览 · 攻防产量','阵型与换人','传球结构','门将 · 扑救质量','对抗 · 球员与对手','xA分项','全部球员','全部射门索引'):
        assert title in main
    # Source bounds and all original qualifications remain in the full export.
    for h in value['report']['hypotheses']:
        assert re.sub(r'\s','',reader_text(h['limitation'])) in re.sub(r'\s','',text)
