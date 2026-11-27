import {useEffect,useMemo,useRef,useState} from 'react';
import type {Metrics,Report} from './report-types';
import {Box,colorFor,DataTable,Empty,fmt} from './report-ui';

type Metric='attempts'|'total_tokens'|'latency_avg_ms';
const names:Record<Metric,string>={attempts:'Attempts',total_tokens:'Known tokens',latency_avg_ms:'Average elapsed (ms)'};
export function ActivityChart({report,compact=false}:{report:Report;compact?:boolean}){
 const [metric,setMetric]=useState<Metric>('attempts'),[style,setStyle]=useState('bar');
 const [hidden,setHidden]=useState<string[]>([]),[focus,setFocus]=useState<number|null>(null);
 const host=useRef<HTMLDivElement>(null);const [width,setWidth]=useState(700);
 useEffect(()=>{if(!host.current)return;const observer=new ResizeObserver(([entry])=>setWidth(Math.max(280,entry.contentRect.width)));observer.observe(host.current);return()=>observer.disconnect()},[]);
 const ids=useMemo(()=>[...new Set(report.daily.filter(r=>r[metric]!==null).map(r=>r.provider))].sort(),[report.daily,metric]);
 const providers=ids.filter(id=>!hidden.includes(id));
 const indexed=useMemo(()=>new Map(report.daily.map(r=>[`${r.day}/${r.provider}`,r])),[report.daily]);
 const days=Array.from({length:report.window.days},(_,i)=>new Date(Date.parse(report.window.start)+i*86400000).toISOString().slice(0,10));
 const value=(day:string,p:string)=>{const row=indexed.get(`${day}/${p}`);return row?.[metric]??(metric==='attempts'?0:null)};
 const line=style==='line'||metric==='latency_avg_ms';
 const maximum=Math.max(1,...days.map(day=>line?Math.max(0,...providers.map(p=>value(day,p)??0)):providers.reduce((sum,p)=>sum+(value(day,p)??0),0)));
 const max=metric==='attempts'?Math.ceil(maximum):maximum*1.1;
 const left=48,right=18,top=18,height=compact?206:246,bottom=height-36,plotH=bottom-top,plotW=Math.max(1,width-left-right);
 const x=(i:number)=>left+(i+.5)*plotW/days.length,y=(n:number)=>bottom-n/max*plotH;
 const ticks=[...new Set([0,.25,.5,.75,1].map(v=>metric==='attempts'?Math.round(max*v):Math.round(max*v*10)/10))];
 const toggle=(id:string)=>setHidden(h=>h.includes(id)?h.filter(p=>p!==id):[...h,id]);
 const focusRows=focus===null?[]:providers.map(id=>({id,value:value(days[focus],id)}));
 return <Box title={compact?'Hoạt động gần đây':'Xu hướng sử dụng'} sub={`Theo ngày UTC · ${report.filters.mode==='all'?'fixture + live':report.filters.mode}`} action={<div className="report-chart-controls"><select aria-label="Chart metric" value={metric} onChange={e=>{setMetric(e.target.value as Metric);setFocus(null)}}><option value="attempts">Attempts</option><option value="total_tokens">Known tokens</option><option value="latency_avg_ms">Average elapsed</option></select>{!compact&&<div className="report-segment"><button aria-pressed={!line} disabled={metric==='latency_avg_ms'} onClick={()=>setStyle('bar')}>Cột</button><button aria-pressed={line} onClick={()=>setStyle('line')}>Đường</button></div>}</div>}>
 <div className="report-chart-host" ref={host}>
 {!ids.length?<Empty message={metric==='attempts'?undefined:metric==='total_tokens'?'Chưa có upstream usage được xác nhận.':'Chưa có latency samples.'}/>:<>
 <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} role="group" aria-label={`${names[metric]} theo ngày UTC`} onPointerLeave={()=>setFocus(null)}>
 <title>{names[metric]} · {report.filters.mode} · daily UTC</title>
 {ticks.map(n=><g key={n}><line x1={left} x2={width-right} y1={y(n)} y2={y(n)} className="report-gridline"/><text x={left-9} y={y(n)+4} textAnchor="end">{fmt(n)}</text></g>)}
 {line?providers.map(id=>{const paths:string[]=[];let path='';days.forEach((day,i)=>{const n=value(day,id);if(n===null){if(path)paths.push(path);path=''}else path+=`${path?'L':'M'}${x(i)} ${y(n)} `});if(path)paths.push(path);return <g key={id}>{paths.map((d,i)=><path key={i} d={d} fill="none" stroke={colorFor(id)} strokeWidth={2}/>)}{days.map((day,i)=>{const n=value(day,id);return n===null?null:<circle key={day} cx={x(i)} cy={y(n)} r={3} fill={colorFor(id)}/>})}</g>}):days.map((day,i)=>{let sum=0;return <g key={day}>{providers.map(id=>{const n=value(day,id)??0;sum+=n;return n>0?<rect key={id} x={x(i)-Math.min(32,plotW/days.length*.6)/2} y={y(sum)} width={Math.min(32,plotW/days.length*.6)} height={n/max*plotH} rx={1} fill={colorFor(id)}/>:null})}</g>})}
 {focus!==null&&<line x1={x(focus)} x2={x(focus)} y1={top} y2={bottom} className="report-focusline"/>}
 {days.map((day,i)=><g key={day}>{(i===0||i===days.length-1||i%Math.max(1,Math.ceil(days.length/(width<450?3:6)))===0)&&<text x={x(i)} y={bottom+23} textAnchor="middle">{day.slice(5)}</text>}<rect className="report-day-hit" x={left+i*plotW/days.length} y={top} width={plotW/days.length} height={plotH} fill="transparent" tabIndex={0} role="button" aria-label={`${day}: ${providers.map(p=>`${p} ${fmt(value(day,p))}`).join(', ')}`} onPointerEnter={()=>setFocus(i)} onFocus={()=>setFocus(i)} onBlur={()=>setFocus(null)} onKeyDown={e=>{if(e.key==='Escape')setFocus(null)}}/></g>)}
 </svg>
 <div className="report-chart-readout" aria-live="polite">{focus===null?<span>Rê chuột hoặc dùng Tab để xem từng ngày · {names[metric]}</span>:<><b>{days[focus]} UTC</b>{focusRows.map(r=><span key={r.id}><i style={{background:colorFor(r.id)}}/>{r.id}<strong>{fmt(r.value)}</strong></span>)}</>}</div>
 <div className="report-legend">{ids.map(id=><button key={id} aria-pressed={!hidden.includes(id)} onClick={()=>toggle(id)}><i style={{background:colorFor(id)}}/>{id}</button>)}</div>
 </>}
 </div>
 {ids.length>0&&<details className="report-accessible"><summary>Xem bảng dữ liệu</summary><DataTable headers={['Ngày UTC','Provider',names[metric],'Usage chưa rõ']} rows={report.daily.map(r=>[r.day,r.provider,fmt(r[metric]),r.unknown_usage_attempts])}/></details>}
 </Box>
}

export function OutcomeChart({metrics}:{metrics:Metrics}){
 const items=[{name:'Succeeded',value:metrics.succeeded,color:'#70a78d'},{name:'Not sent',value:metrics.not_sent,color:'#ffbf00'},{name:'Unknown outcome',value:metrics.unknown,color:'#f59e0b'},{name:'Rejected / other',value:metrics.other,color:'#b489ca'}];
 return <Box title="Kết quả attempts" sub="Phân biệt chưa gửi và chưa rõ kết quả">{!metrics.attempts?<Empty/>:<div className="report-outcomes"><div className="report-outcome-bar" role="img" aria-label={items.map(i=>`${i.name}: ${i.value}`).join(', ')}>{items.filter(i=>i.value>0).map(i=><span key={i.name} style={{width:`${i.value/metrics.attempts*100}%`,background:i.color}}/>)}</div>{items.map(i=><div className="report-outcome-row" key={i.name}><i style={{background:i.color}}/><span>{i.name}</span><b>{i.value}</b><small>{fmt(i.value/metrics.attempts*100,'%')}</small></div>)}<p>Unknown không được tự coi là failed. Tỷ lệ succeeded dùng toàn bộ attempts làm mẫu số.</p></div>}</Box>
}
