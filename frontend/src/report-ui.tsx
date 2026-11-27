import {PROVIDER_COLORS} from './brand';
import type {ReactNode} from 'react';
import {ChartColumn, ChevronRight, Database, Boxes, Layers, GitBranch} from 'lucide-react';
import {Badge, Card} from './components/ui';
import type {Metrics, Report} from './report-types';

export const fmt=(n:number|null|undefined,unit='')=>n==null?'—':`${new Intl.NumberFormat('vi-VN',{maximumFractionDigits:2}).format(n)}${unit}`;
const colors=PROVIDER_COLORS;
export const colorFor=(id:string)=>colors[Array.from(id).reduce((n,c)=>n+c.charCodeAt(0),0)%colors.length];
const labels:Record<string,string>={attention_required:'Cần xử lý',snapshot_available:'Có snapshot',not_configured:'Chưa cấu hình',not_observed:'Chưa ghi nhận',fixture_only:'Chỉ fixture',observed_success:'Live thành công',not_synced:'Chưa đồng bộ',syncing:'Đang đồng bộ',paused:'Tạm dừng'};
export function StateBadge({value}:{value:string}){return <Badge tone={['attention_required','failed','blocked','unusable'].includes(value)?'amber':['observed_success','snapshot_available','succeeded','usable','eligible'].includes(value)?'green':'neutral'}>{labels[value]||value.replaceAll('_',' ')}</Badge>}
export function Box({title,sub,children,action}:{title:string;sub?:string;children:ReactNode;action?:ReactNode}){return <Card><div className="op-section-head"><div><h2>{title}</h2>{sub&&<span>{sub}</span>}</div>{action}</div>{children}</Card>}
export function DataTable({headers,rows,empty='Không có bản ghi trong phạm vi đã chọn.'}:{headers:string[];rows:ReactNode[][];empty?:string}){return <div className="table-scroll"><table><thead><tr>{headers.map(h=><th key={h}>{h}</th>)}</tr></thead><tbody>{rows.length?rows.map((r,i)=><tr key={i}>{r.map((c,j)=><td key={j}>{c}</td>)}</tr>):<tr><td colSpan={headers.length} className="op-empty-row">{empty}</td></tr>}</tbody></table></div>}
export function Empty({message='Chưa có attempts trong phạm vi đã chọn.'}:{message?:string}){return <div className="report-empty"><ChartColumn size={21}/><strong>{message}</strong><span>Thử đổi khoảng thời gian hoặc chế độ fixture / live.</span></div>}
export function MetricsStrip({value}:{value:Metrics}){return <div className="report-metrics">{[
 {label:'Adapter attempts',value:fmt(value.attempts),sub:'Qualification / CLI'},
 {label:'Tỷ lệ succeeded',value:fmt(value.success_rate,'%'),sub:`${value.succeeded} / ${value.attempts} attempts · gồm not sent`},
 {label:'Tokens đã ghi nhận',value:fmt(value.total_tokens),sub:`${value.known_usage_attempts} có usage · ${value.unknown_usage_attempts} chưa rõ`},
 {label:'P95 elapsed',value:fmt(value.latency_p95_ms,' ms'),sub:`${value.latency_samples} mẫu · thời gian adapter`}
 ].map(m=><div key={m.label}><span>{m.label}</span><b>{m.value}</b><small>{m.sub}</small></div>)}</div>}

export function ResourceMap({report,navigate}:{report:Report;navigate:(id:string)=>void}){
 const ps=report.providers.filter(p=>!report.filters.provider||p.id===report.filters.provider);
 const sum=(key:'sources'|'current_offerings'|'deployments')=>ps.reduce((n,p)=>n+p[key],0);
 const nodes=[{page:'discovery',name:'Sources',value:sum('sources'),icon:Database},{page:'models',name:'Offerings',value:sum('current_offerings'),icon:Boxes},{page:'models',name:'Deployments',value:sum('deployments'),icon:Layers},{page:'routing',name:'Pinned routes',value:report.route_total,icon:GitBranch}];
 return <details className="report-resource-map"><summary><GitBranch size={16}/><b>Sơ đồ tài nguyên Gateway</b><span>Inventory hiện tại</span><ChevronRight size={15}/></summary><div className="report-flow">{nodes.map((n,i)=><div className="report-flow-step" key={n.name}>{i>0&&<ChevronRight size={16}/>}<button onClick={()=>navigate(n.page)}><n.icon size={16}/><b>{n.value}</b><span>{n.name}</span></button></div>)}</div><div className="report-map-note"><b>Qualification · trong khoảng chọn</b><span>{report.verdicts.reduce((n,v)=>n+v.count,0)} runs · {report.verdicts.filter(v=>v.mode==='live'&&v.decision==='eligible').reduce((n,v)=>n+v.count,0)} live eligible decisions</span><small>Đây là quan hệ cấu hình, không phải lưu lượng hay tỷ lệ chuyển đổi. Route vẫn phải qua publication gate hiện hành.</small></div></details>
}
