import {useState} from 'react';
import {Check,ChevronRight,Copy,LayoutGrid,List} from 'lucide-react';
import {Badge,Button} from './components/ui';
import {text,type Row} from './api';
import type {Report} from './report-types';
import {colorFor,DataTable,fmt} from './report-ui';
import './catalog.css';

export function CatalogViewToggle({value,onChange}:{value:'grid'|'table';onChange:(v:'grid'|'table')=>void}){return <div className="catalog-view-toggle" aria-label="Kiểu hiển thị"><Button variant="ghost" size="icon" aria-label="Hiển thị thẻ" aria-pressed={value==='grid'} onClick={()=>onChange('grid')}><LayoutGrid size={15}/></Button><Button variant="ghost" size="icon" aria-label="Hiển thị bảng" aria-pressed={value==='table'} onClick={()=>onChange('table')}><List size={15}/></Button></div>}

export default function ModelCatalog({offerings,report,onInspect,loading=false}:{offerings:Row[];report?:Report;onInspect:(kind:string,row:Row)=>void;loading?:boolean}){
 const [view,setView]=useState<'grid'|'table'>('grid'),[provider,setProvider]=useState('all'),[task,setTask]=useState('all');
 const providers=[...new Set(offerings.map(o=>text(o.provider)))].sort(),tasks=[...new Set(offerings.map(o=>text(o.task)))].sort();
 const rows=offerings.filter(o=>(provider==='all'||text(o.provider)===provider)&&(task==='all'||text(o.task)===task));
 return <section className="catalog-workspace" aria-label="Model catalog"><div className="catalog-toolbar"><div><b>{rows.length} models</b><span>Snapshot hiện tại</span></div><div className="catalog-toolbar-controls"><select aria-label="Lọc provider trong catalog" value={provider} onChange={e=>setProvider(e.target.value)}><option value="all">Tất cả providers</option>{providers.map(p=><option key={p}>{p}</option>)}</select><select aria-label="Lọc task trong catalog" value={task} onChange={e=>setTask(e.target.value)}><option value="all">Tất cả tasks</option>{tasks.map(t=><option key={t}>{t}</option>)}</select><CatalogViewToggle value={view} onChange={setView}/></div></div>
 {loading&&!offerings.length?<div className="catalog-empty" role="status">Đang tải catalog…</div>:!rows.length?<div className="catalog-empty">Không có model khớp bộ lọc.{(provider!=='all'||task!=='all')&&<Button variant="ghost" onClick={()=>{setProvider('all');setTask('all')}}>Xóa bộ lọc</Button>}</div>:view==='grid'?<div className="catalog-grid">{rows.map(o=><ModelCard key={text(o.id)} offering={o} report={report} inspect={()=>onInspect('offering',o)}/>)}</div>:<DataTable headers={['Model / provider','Task / region','Nguồn','Trạng thái','Chi tiết']} rows={rows.map(o=>[<><b>{text(o.upstream_model)}</b><small>{text(o.provider)}</small></>,`${text(o.task)} / ${text(o.region)}`,Array.isArray(o.source_ids)?o.source_ids.join(', '):'Xem evidence',<Badge>Catalog only</Badge>,<Button variant="outline" size="sm" onClick={()=>onInspect('offering',o)}>Evidence</Button>])}/>}
 <p className="catalog-scope">Catalog chưa cấp quyền gọi live. Giá input / output / cached, uptime và TPS chưa được xác minh.{report&&` Chỉ số quan sát: ${report.filters.mode} · ${report.window.days} ngày UTC.`}</p></section>
}

function ModelCard({offering:o,report,inspect}:{offering:Row;report?:Report;inspect:()=>void}){
 const [copyState,setCopyState]=useState<'idle'|'copied'|'error'>('idle');
 const model=text(o.upstream_model),provider=text(o.provider),sources=Array.isArray(o.source_ids)?o.source_ids:[];
 // Current inference adapter handles chat. Do not attribute chat observations to embedding/rerank offerings.
 const metrics=o.task==='chat'?report?.models.find(m=>m.provider===provider&&m.upstream_model===model):undefined;
 const copy=async()=>{try{await navigator.clipboard.writeText(model);setCopyState('copied')}catch{setCopyState('error')}};
 return <article className="catalog-card model-square-card"><div className="catalog-card-head"><span className="catalog-mark" style={{color:colorFor(provider)}} aria-hidden="true">{provider.slice(0,1).toUpperCase()}</span><div><h3 title={model}>{model}</h3><span>{provider}</span></div><Button variant="ghost" size="icon" aria-label={`Sao chép model ${model}`} onClick={()=>void copy()}>{copyState==='copied'?<Check size={14}/>:<Copy size={14}/>}</Button></div>
 {copyState!=='idle'&&<span className={`catalog-copy-status ${copyState==='error'?'error':''}`} role="status">{copyState==='copied'?'Đã sao chép model ID.':'Không sao chép được. Mở chi tiết để chọn model ID.'}</span>}
 <p className="catalog-model-description">{o.task==='chat'?'Model hội thoại trong catalog Gateway.':o.task==='embedding'?'Model embedding trong catalog Gateway.':'Model được phát hiện từ nguồn catalog.'}</p>
 <div className="catalog-tags"><Badge>{text(o.task)}</Badge><span>{text(o.region)}</span><span>Catalog metadata</span></div>
 <div className="catalog-pricing-label">Pricing <span>Chưa xác minh</span></div><dl className="catalog-price-grid">{['Input','Output','Cached'].map(label=><div key={label}><dt>{label}</dt><dd>— <small>/ 1M tokens</small></dd></div>)}</dl>
 <div className="catalog-card-meta"><span>Nguồn <b>{sources.length?`${sources.length} sources`:'Xem evidence'}</b></span><span>Endpoint <b title={text(o.base_url)}>{typeof o.base_url==='string'?safeHost(o.base_url):'Chưa xác định'}</b></span></div>
 <div className="catalog-card-bottom"><div className="catalog-observed-status"><span>Status {metrics&&<b>{fmt(metrics.success_rate,'%')}</b>}</span><small className="model-status-label"><i/>{metrics?`${report?.filters.mode} · ${metrics.attempts} attempts`:'Catalog only'}</small></div><div><span>P95 elapsed</span><b>{fmt(metrics?.latency_p95_ms,' ms')}</b></div><div><span>TPS</span><b title="Chưa có verified throughput telemetry">—</b></div><Button variant="ghost" size="sm" onClick={inspect}>Details<ChevronRight size={14}/></Button></div>
 </article>
}
function safeHost(value:string){try{return new URL(value).host||'Xem evidence'}catch{return 'Xem evidence'}}
