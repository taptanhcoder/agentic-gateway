import {useCallback,useEffect,useRef,useState,type ReactNode} from 'react';
import {ArrowLeft,ArrowUpRight,Boxes,Database,ChevronRight,Download,RefreshCw,TriangleAlert} from 'lucide-react';
import {Badge,Button,Tabs,TabsContent,TabsList,TabsTrigger} from './components/ui';
import {ApiError,backend,when,type Row} from './api';
import type {Report} from './report-types';
import {ActivityChart,OutcomeChart} from './ReportCharts';
import ProviderDetails from './ProviderDetails';
import ModelCatalog,{CatalogViewToggle} from './ModelCatalog';
import ProviderPicker from './ProviderPicker';
import {Box,colorFor,DataTable,fmt,MetricsStrip,ResourceMap,StateBadge} from './report-ui';
import './reports.css';

type View='overview'|'usage'|'providers';
function initialFilters(){
 const params=new URLSearchParams(location.search),days=Number(params.get('reportDays')||7);
 return {days:[1,7,30,90].includes(days)?days:7,mode:['all','fixture','live'].includes(params.get('reportMode')||'')?params.get('reportMode')!:'all',provider:params.get('reportProvider')||'all'};
}
function updateUrl(key:string,value:string){const url=new URL(location.href);if(value==='all')url.searchParams.delete(key);else url.searchParams.set(key,value);history.replaceState(null,'',url)}

export default function Reporting({view='overview',onNavigate,onInspect,onUnauthorized,offerings=[],offeringPagination,catalogLoading=false}:{view?:View;offerings?:Row[];offeringPagination?:ReactNode;catalogLoading?:boolean;onNavigate:(id:string)=>void;onInspect:(kind:string,row:Row)=>void;onUnauthorized:()=>void}){
 const [filters,setFilters]=useState(initialFilters),[snapshot,setSnapshot]=useState<Report|null>(null);
 const [error,setError]=useState(''),[loading,setLoading]=useState(true),[providerTab,setProviderTab]=useState('summary'),[analysis,setAnalysis]=useState('activity');
 const [providerScreen,setProviderScreen]=useState(()=>new URLSearchParams(location.search).get('reportSection')==='providers'?'providers':'models');
 const requestId=useRef(0);const {days,provider,mode}=filters;
 const report=snapshot&&snapshot.window.days===days&&snapshot.filters.mode===mode&&snapshot.filters.provider===(provider==='all'?null:provider)?snapshot:null;
 const load=useCallback(async(signal?:AbortSignal)=>{
  const id=++requestId.current;setLoading(true);
  try{const params=new URLSearchParams({days:String(days),mode,...(provider!=='all'?{provider}:{})});const result=await backend<Report>('/admin/portal/reports?'+params,{signal});if(!signal?.aborted&&id===requestId.current){setSnapshot(result);setError('')}}
  catch(e){if(signal?.aborted||id!==requestId.current)return;if(e instanceof ApiError&&e.status===401)onUnauthorized();setError(e instanceof Error?e.message:'report_failed')}
  finally{if(!signal?.aborted&&id===requestId.current)setLoading(false)}
 },[days,provider,mode,onUnauthorized]);
 useEffect(()=>{const controller=new AbortController();void load(controller.signal);const timer=setInterval(()=>{if(!document.hidden)void load(controller.signal)},30000);return()=>{controller.abort();clearInterval(timer)}},[load]);
 useEffect(()=>{setProviderTab('summary');setAnalysis('activity')},[view]);
 const choose=(id:string)=>{updateUrl('reportProvider',id);setFilters(f=>({...f,provider:id}));setProviderTab('summary')};
 const drill=(id:string)=>{choose(id);setProviderScreen('providers');updateUrl('reportSection','providers');onNavigate('providers')};
 const inventory=snapshot?.providers||[];
 const current=report?.providers.find(p=>p.id===provider);
 const download=()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=url;a.download=`gateway-${provider}-${days}d-${mode}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000)};
 const loadingPanel=<div className="report-loading" role="status" aria-live="polite">{loading?<span className="pixel-loader" aria-hidden="true"><i/><i/><i/></span>:<TriangleAlert size={17}/>}{loading?'Đang tải báo cáo…':'Không tải được báo cáo. Hãy thử lại.'}</div>;
 return <section className={`report-workspace report-view-${view}`} aria-label="Gateway reports">
  <div className="report-toolbar">
   <div className="report-filter-group">{view!=='providers'&&<label>Provider<select aria-label="Report provider" value={provider} onChange={e=>choose(e.target.value)}><option value="all">Tất cả providers</option>{inventory.map(p=><option key={p.id} value={p.id}>{p.id}</option>)}</select></label>}
   <label>Chế độ<select aria-label="Report mode" value={mode} onChange={e=>{updateUrl('reportMode',e.target.value);setFilters(f=>({...f,mode:e.target.value}))}}><option value="all">Fixture + live</option><option value="fixture">Fixture only</option><option value="live">Live only</option></select></label>
   <label>Khoảng thời gian<select aria-label="Report time window" value={days} onChange={e=>{updateUrl('reportDays',e.target.value);setFilters(f=>({...f,days:Number(e.target.value)}))}}><option value={1}>Hôm nay · UTC</option><option value={7}>7 ngày · UTC</option><option value={30}>30 ngày · UTC</option><option value={90}>90 ngày · UTC</option></select></label></div>
   <div className="report-toolbar-actions"><Button variant="ghost" size="icon" disabled={loading} aria-label="Refresh report" onClick={()=>void load()}><RefreshCw size={15}/></Button><Button variant="outline" disabled={!report||loading} onClick={download}><Download size={14}/><span>Xuất JSON</span></Button></div>
  </div>
  <div className="report-context"><span><i/>Qualification / CLI attempts <b>· {mode==='all'?'fixture + live':mode}</b></span><span>{snapshot?`Cập nhật ${when(snapshot.generated_at)}`:'Đang kết nối…'}</span></div>
  {error&&<div className="op-form-error" role="alert"><TriangleAlert size={15}/>{error} · Báo cáo chưa được cập nhật.<Button size="sm" variant="ghost" onClick={()=>void load()}>Thử lại</Button></div>}

  {view==='providers'&&<div className="model-square-switch" aria-label="Khu vực quản lý"><button aria-pressed={providerScreen==='models'} onClick={()=>{setProviderScreen('models');updateUrl('reportSection','models')}}><Boxes size={16}/>Model Square<span>{offerings.length}</span></button><button aria-pressed={providerScreen==='providers'} onClick={()=>{setProviderScreen('providers');updateUrl('reportSection','providers')}}><Database size={16}/>Provider operations<span>{inventory.length}</span></button></div>}
  {view==='providers'&&providerScreen==='models'?<div className="model-square-surface"><ModelCatalog offerings={offerings} report={report||undefined} loading={catalogLoading} onInspect={onInspect}/>{offeringPagination}</div>:view==='providers'?<div className="report-provider-layout">
   <ProviderPicker providers={inventory} value={provider} onChange={choose}/>
   <div className="report-provider-content" aria-busy={loading}>
    {!report?loadingPanel:provider==='all'?<>
     <div className="report-content-heading"><div><h2>Toàn cảnh providers</h2><p>So sánh catalog và kết quả adapter trong phạm vi đã chọn.</p></div><Badge>{report.providers.length} providers</Badge></div>
     <div className="report-status-summary"><span><i className="report-catalog-dot attention_required"/><b>{report.providers.filter(p=>p.catalog_status==='attention_required').length}</b> catalog cần xử lý</span><span><b>{report.providers.reduce((n,p)=>n+p.enabled_deployments,0)}</b> targets đang bật</span><span><b>{report.totals.attempts}</b> attempts trong kỳ</span></div>
     <ProviderComparison report={report} drill={drill}/>
     <div className="report-guide"><ArrowUpRight size={18}/><div><b>Đi sâu vào một provider</b><p>Chọn tên để xem xu hướng, nguồn đồng bộ, model inventory, attempts và publication gates.</p></div></div>
    </>:current&&report.details?<>
     <div className="report-content-heading"><div><button className="report-back" onClick={()=>choose('all')}><ArrowLeft size={13}/>Tất cả providers</button><h2>{current.id==='unassigned'?'Nguồn chưa gán provider':current.id}</h2><p>{current.sources} sources · {current.accounts} accounts · catalog success {when(current.catalog_last_success_at)}</p></div><StateBadge value={current.catalog_status}/></div>
     <MetricsStrip value={report.totals}/>
     <ProviderDetails report={report} current={current} tab={providerTab} setTab={setProviderTab} inspect={onInspect} navigate={onNavigate}/>
    </>:loadingPanel}
   </div>
  </div>:!report?loadingPanel:<>
   <MetricsStrip value={report.totals}/>
   {view==='overview'?<>
    <div className="report-overview-grid"><ActivityChart report={report} compact/><Attention report={report} drill={drill}/></div>
    <ResourceMap report={report} navigate={onNavigate}/>
    <div className="report-overview-links"><Button variant="ghost" onClick={()=>onNavigate('usage')}>Phân tích sử dụng<ArrowUpRight size={14}/></Button><Button variant="ghost" onClick={()=>onNavigate('providers')}>Provider reports<ArrowUpRight size={14}/></Button></div>
   </>:<Tabs value={analysis} onValueChange={setAnalysis}>
    <TabsList aria-label="Usage report views"><TabsTrigger value="activity">Hoạt động</TabsTrigger><TabsTrigger value="providers">So sánh providers</TabsTrigger><TabsTrigger value="models">Models</TabsTrigger><TabsTrigger value="errors">Outcomes & lỗi</TabsTrigger></TabsList>
    <TabsContent value="activity"><div className="report-chart-grid"><ActivityChart report={report}/><OutcomeChart metrics={report.totals}/></div></TabsContent>
    <TabsContent value="providers"><ProviderComparison report={report} drill={drill}/></TabsContent>
    <TabsContent value="models"><Box title="Model call analytics" sub="Gán theo đúng generation tại thời điểm gọi"><DataTable headers={['Provider / model','Attempts','Succeeded','Known tokens','Usage chưa rõ','P95 ms']} rows={report.models.map(m=>[<>{m.provider}<small>{m.upstream_model}</small></>,m.attempts,fmt(m.success_rate,'%'),fmt(m.total_tokens),m.unknown_usage_attempts,fmt(m.latency_p95_ms)])}/></Box></TabsContent>
    <TabsContent value="errors"><div className="report-chart-grid"><Box title="Error codes" sub="Nhóm theo provider và mã kết quả khác ok"><DataTable headers={['Provider','Error code','Attempts']} rows={report.errors.map(e=>[e.provider,<code>{e.result_code}</code>,e.count])} empty="Không ghi nhận mã lỗi trong phạm vi này."/><div className="report-section-footer"><Button variant="ghost" onClick={()=>onNavigate('logs')}>Mở inference attempts<ArrowUpRight size={13}/></Button></div></Box><OutcomeChart metrics={report.totals}/></div></TabsContent>
   </Tabs>}
  </>}
  <details className="report-definitions"><summary>Phạm vi dữ liệu & cách tính chỉ số</summary><p>Đây là adapter attempts từ qualification / CLI; chưa phải client request traffic. Inventory và catalog status là hiện tại; attempts và qualification được lọc theo thời gian / mode. Các ngày của biểu đồ dùng UTC.</p><p>Tokens chỉ cộng usage do upstream trả về. Dấu — là chưa có dữ liệu. Elapsed là thời gian adapter, không phải TTFT. Chưa có billing ledger, uptime hoặc TPS đã xác minh.</p>{report&&Object.entries(report.definitions).filter(([key])=>['success_rate','inventory','network'].includes(key)).map(([key,value])=><p key={key}><b>{key}:</b> {value}</p>)}</details>
 </section>
}

function ProviderComparison({report,drill}:{report:Report;drill:(id:string)=>void}){
 const [view,setView]=useState<'grid'|'table'>('grid');
 const providers=report.providers.filter(p=>!report.filters.provider||p.id===report.filters.provider);
 return <section className="catalog-workspace" aria-label="So sánh providers"><div className="catalog-toolbar"><div><b>{providers.length} providers</b><span>Catalog & observed performance</span></div><CatalogViewToggle value={view} onChange={setView}/></div>{view==='grid'?<div className="catalog-grid">{providers.map(p=><article className="catalog-card" key={p.id}><div className="catalog-card-head"><span className="catalog-mark" style={{color:colorFor(p.id)}} aria-hidden="true">{p.id.slice(0,1).toUpperCase()}</span><div><h3>{p.id==='unassigned'?'Chưa gán provider':p.id}</h3><span>Gateway provider</span></div><Button variant="ghost" size="icon" aria-label={`Report ${p.id}`} onClick={()=>drill(p.id)}><ArrowUpRight size={14}/></Button></div>
 <div className="provider-card-states"><span>Catalog<StateBadge value={p.catalog_status}/></span><span>Inference<StateBadge value={p.inference_status}/></span></div>
 <div className="provider-card-inventory"><div><b>{p.current_offerings}</b>Models</div><div><b>{p.enabled_deployments}/{p.deployments}</b>Targets bật</div><div><b>{p.accounts}</b>Accounts</div></div>
 <p className="provider-card-sync">Catalog success · {when(p.catalog_last_success_at)}</p><p className="provider-card-scope">{report.filters.mode} · {report.window.days} ngày UTC · adapter attempts</p>
 <div className="catalog-card-bottom"><div><span>Succeeded ratio</span><b>{fmt(p.metrics.success_rate,'%')}</b></div><div><span>P95 elapsed</span><b>{fmt(p.metrics.latency_p95_ms,' ms')}</b></div><div><span>Attempts</span><b>{p.metrics.attempts}</b></div><Button variant="ghost" size="sm" onClick={()=>drill(p.id)}>Chi tiết<ChevronRight size={14}/></Button></div></article>)}</div>:<Box title="So sánh providers" sub="Catalog hiện tại · inference theo thời gian / mode đã chọn"><DataTable headers={['Provider','Catalog','Inference','Attempts','Succeeded','P95 ms','Chi tiết']} rows={providers.map(p=>[<button className="report-provider-link" onClick={()=>drill(p.id)}><i style={{background:colorFor(p.id)}}/>{p.id}<small>{p.current_offerings} models · {p.enabled_deployments}/{p.deployments} targets</small></button>,<StateBadge value={p.catalog_status}/>,<StateBadge value={p.inference_status}/>,p.metrics.attempts,fmt(p.metrics.success_rate,'%'),fmt(p.metrics.latency_p95_ms),<Button variant="ghost" size="icon" aria-label={`Report ${p.id}`} onClick={()=>drill(p.id)}><ArrowUpRight size={14}/></Button>])}/></Box>}</section>
}
function Attention({report,drill}:{report:Report;drill:(id:string)=>void}){
 const providers=report.providers.filter(p=>(!report.filters.provider||p.id===report.filters.provider)&&p.catalog_status==='attention_required');
 return <Box title="Cần xử lý" sub="Latest catalog job · trạng thái hiện tại" action={<Badge>{providers.length}</Badge>}>{providers.length?providers.map(p=><button className="report-attention-row" key={p.id} onClick={()=>drill(p.id)}><TriangleAlert size={16}/><span><b>{p.id}</b><small>Kiểm tra nguồn và lỗi đồng bộ</small></span><ChevronRight size={14}/></button>):<div className="report-attention-empty">Không có catalog ở trạng thái cần xử lý.</div>}<div className="report-section-footer">{report.modes.map(m=><span key={m.mode}><Badge>{m.mode}</Badge> {m.attempts} attempts</span>)}</div></Box>
}
