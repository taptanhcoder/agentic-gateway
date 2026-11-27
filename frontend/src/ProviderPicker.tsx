import {useState} from 'react';
import {Search} from 'lucide-react';
import {Tabs,TabsList,TabsTrigger} from './components/ui';
import type {ProviderReport} from './report-types';
import {StateBadge} from './report-ui';
import './provider-picker.css';

export default function ProviderPicker({providers,value,onChange}:{providers:ProviderReport[];value:string;onChange:(id:string)=>void}){
 const [query,setQuery]=useState('');
 const selected=providers.find(p=>p.id===value);
 const name=(id:string)=>id==='unassigned'?'Chưa gán provider':id;
 const filtered=providers.filter(p=>p.id===value||name(p.id).toLocaleLowerCase('vi').includes(query.trim().toLocaleLowerCase('vi')));
 const modelCount=providers.reduce((n,p)=>n+p.current_offerings,0);
 return <section className="provider-channels" aria-label="Chọn provider">
  <div className="provider-channels-heading"><div><span className="brand-pixel-cluster" aria-hidden="true"/><b>Providers</b><span>{providers.length} kết nối catalog</span></div>{providers.length>6&&<label className="provider-channel-search"><Search size={13}/><input aria-label="Tìm provider" placeholder="Tìm provider…" value={query} onChange={e=>setQuery(e.target.value)}/></label>}</div>
  <Tabs value={value} onValueChange={onChange}><TabsList className="provider-channel-list" aria-label="Chọn phạm vi provider"><TabsTrigger className="provider-channel-tab" value="all"><span className="provider-channel-mark" aria-hidden="true">∷</span><span>Tất cả</span><span className="provider-channel-count" title="Models trong snapshot hiện tại">{modelCount}</span></TabsTrigger>{filtered.map(p=><TabsTrigger className="provider-channel-tab" key={p.id} value={p.id}><span className="provider-channel-mark" aria-hidden="true">{name(p.id).slice(0,1).toUpperCase()}</span><span>{name(p.id)}</span><span className="provider-channel-count" title="Models trong snapshot hiện tại">{p.current_offerings}</span></TabsTrigger>)}</TabsList></Tabs>
  {query&&!filtered.length&&<p className="provider-channel-empty">Không có provider khớp “{query}”.</p>}
  <div className="provider-channel-context" aria-live="polite">{selected?<><StateBadge value={selected.catalog_status}/><span>{selected.current_offerings} models</span><span>{selected.enabled_deployments}/{selected.deployments} targets bật</span></>:<><span className="status-pixel"/><span>Toàn bộ providers · {modelCount} models trong snapshot hiện tại</span></>}<span className="provider-channel-hint">Catalog status · không phải upstream uptime</span></div>
 </section>
}
