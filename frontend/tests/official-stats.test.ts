import {describe,it,expect} from 'vitest';
import {officialStat} from '@/components/studio/official-stats';
describe('official count availability',()=>{
 it('shows verified zero, paired coverage and raw recovery',()=>{
  const p={minutes:90,official_by_match:[{match_id:'a',stats:{totalCross:0},stat_status:{totalCross:'zero_omitted'}},{match_id:'b',stats:{},availability:'source_stat_list_missing'}]};
  const x=officialStat(p,'totalCross');expect(x.value).toBe(0);expect(x.coverage).toBe(1);expect(x.text).toContain('另1场未计入');expect(x.detail).toContain('已还原0');
 });
 it('does not convert missing values or unused substitutes to zero',()=>{
  expect(officialStat({minutes:0},'totalPass').text).toBe('无出场样本');
  expect(officialStat({minutes:90,official_by_match:[{match_id:'a',stats:{},availability:'source_stat_list_missing'}]},'saves').text).toBe('原始统计列表缺失');
 });
});
