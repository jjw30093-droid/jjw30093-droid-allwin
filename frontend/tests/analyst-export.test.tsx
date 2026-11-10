import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,describe,expect,it,vi} from 'vitest';
import {ReportWorkflow} from '@/components/studio/report-workflow';
import {clientFetch,getMe} from '@/lib/api-v1';
import {downloadServerExport} from '@/components/studio/api';
vi.mock('@/lib/api-v1',()=>({clientFetch:vi.fn(),getMe:vi.fn()}));
vi.mock('@/components/studio/api',()=>({downloadServerExport:vi.fn()}));
vi.mock('next/dynamic',()=>({default:()=>()=>null}));
vi.mock('@/components/studio/visualization-charts',()=>({Pitch:()=>null}));
vi.mock('@/components/studio/prematch-depth',()=>({AnalystDepth:()=>null}));
vi.mock('@/components/studio/report-analysis-sections',()=>({ReportAnalysisSections:()=>null,XaValue:()=>null}));
vi.mock('@/components/studio/report-tactical-sections',()=>({ReportTacticalSections:()=>null,SnapshotDifferences:()=>null,confirmedGoalkeeperShots:()=>({available:false,shots:[],unclassified:0})}));
const team=(id:string)=>({id,name:id,minutes:0,samples:[],chains:[],players:[],attack:{shots:0,xg:null,known_xg:0},defence:{shots:0,xg:null},methods:[],states:[],restarts:[],progression:{completed:0,third_entries:0,box_entries:0},passes:0,completed_passes:0});
const detail={id:'saved-report',revision:3,status:'draft',judgments:{},editorial_notes:'',previous_draft_id:null,prematch_draft_id:null,
 report:{phase:'prematch',analysis_mode:'reconstructed',revision:'source',cutoff_at:'2026-11-08T14:00:00Z',match:{id:'match',home_id:'home',away_id:'away',home_name:'Home',away_name:'Away',kickoff_at:'2026-11-08T14:00:00Z'},teams:[team('home'),team('away')],hypotheses:[],lineup:{},lineup_changes:{teams:[],note:''},checks:[],quality:{status:'incomplete',warnings:[]},notes:[],references:[]}};
beforeEach(()=>{
 vi.clearAllMocks();window.history.replaceState({},'','/?report=saved-report');
 vi.mocked(getMe).mockResolvedValue({user:{role:'admin'}} as never);
 vi.mocked(clientFetch).mockImplementation(async(path)=>{
  if(path.endsWith('/export'))return {download_url:'/api/v1/studio/exports/file/download'} as never;
  if(path.endsWith('/candidates'))return {matches:[]} as never;
  if(path.endsWith('/jobs'))return {jobs:[]} as never;
  if(path.endsWith('/saved-report'))return detail as never;
  return {reports:[]} as never;
 });
 vi.mocked(downloadServerExport).mockResolvedValue();
});
afterEach(()=>cleanup());
describe('analyst report downloads',()=>{
 it.each([['导出阅读版 PDF','pdf','reader'],['导出独立证据附录','pdf','evidence'],['导出证据 JSON','json','reader']])('downloads %s after asynchronous generation without a popup',async(label,kind,profile)=>{
  const popup=vi.spyOn(window,'open');render(<ReportWorkflow/>);
  fireEvent.click(await screen.findByRole('button',{name:label}));
  await waitFor(()=>expect(downloadServerExport).toHaveBeenCalledWith('/api/v1/studio/exports/file/download',`cc-report-prematch-${kind==='json'?'evidence':profile}-saved-report-v3.${kind}`));
  expect(clientFetch).toHaveBeenCalledWith(expect.stringContaining('/export'),expect.objectContaining({body:expect.objectContaining({kind,pdf_profile:profile})}));
  expect(popup).not.toHaveBeenCalled();popup.mockRestore();
 });
 it('shows a failed file transfer instead of silently reporting success',async()=>{
  vi.mocked(downloadServerExport).mockRejectedValue(new Error('导出下载失败(HTTP 403)'));render(<ReportWorkflow/>);
  fireEvent.click(await screen.findByRole('button',{name:'导出阅读版 PDF'}));
  expect(await screen.findByRole('alert')).toHaveProperty('textContent','Error: 导出下载失败(HTTP 403)');
 });
});
