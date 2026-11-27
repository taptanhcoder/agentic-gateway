export type GatewayModel={id:string;name:string;upstream:string;provider:string;type:string;endpoint:string;enabled:boolean;qualified:boolean;inputRate:number;outputRate:number;credential:string;context:string};
export type GatewayAgent={id:string;name:string;team:string;owner:string;environment:string;enabled:boolean;models:string[];rpm:number;tokenLimit:number;monthlyBudget:number};
export type VirtualKey={id:string;label:string;agent:string;version:number;revoked:boolean;models:string[];budget:number};
export type GatewayRoute={id:string;primary:string;fallback:string;enabled:boolean;strategy:string};
export type RequestLog={id:string;time:number;agent:string;key:string;model:string;resolved:string;provider:string;endpoint:string;status:number;reason:string;input:number;output:number;latency:number;cost:number;fallback:boolean};
export const seedModels:GatewayModel[]=[
 {id:'ag/research',name:'Research · Llama 3.3',upstream:'meta/llama-3.3-70b-instruct',provider:'NVIDIA',type:'Chat',endpoint:'/v1/responses',enabled:true,qualified:true,inputRate:.2,outputRate:.4,credential:'cred-nvidia-dev',context:'128k'},
 {id:'ag/coder',name:'Code assistant',upstream:'gpt-4.1',provider:'OpenAI',type:'Chat',endpoint:'/v1/responses',enabled:true,qualified:true,inputRate:2,outputRate:8,credential:'cred-openai-dev',context:'128k'},
 {id:'ag/fast',name:'Fast assistant',upstream:'gemini-2.5-flash',provider:'Google',type:'Chat',endpoint:'/v1/responses',enabled:true,qualified:true,inputRate:.3,outputRate:2.5,credential:'cred-google-dev',context:'128k'},
 {id:'ag/embedding',name:'Search embeddings',upstream:'text-embedding-3-small',provider:'OpenAI',type:'Embedding',endpoint:'/v1/embeddings',enabled:true,qualified:true,inputRate:.02,outputRate:0,credential:'cred-openai-dev',context:'8k'},
 {id:'ag/reasoning',name:'Reasoning candidate',upstream:'claude-sonnet-4',provider:'Anthropic',type:'Chat',endpoint:'/v1/responses',enabled:false,qualified:false,inputRate:3,outputRate:15,credential:'cred-anthropic-dev',context:'128k'}
];
export const seedAgents:GatewayAgent[]=[
 {id:'research-bot',name:'Research assistant',team:'Research',owner:'Nghia',environment:'development',enabled:true,models:['ag/research','ag/fast'],rpm:60,tokenLimit:100000,monthlyBudget:20},
 {id:'coding-bot',name:'Coding copilot',team:'Engineering',owner:'Engineering lead',environment:'development',enabled:true,models:['ag/coder','ag/fast'],rpm:120,tokenLimit:200000,monthlyBudget:50},
 {id:'support-bot',name:'Support assistant',team:'Customer Ops',owner:'Support lead',environment:'staging',enabled:true,models:['ag/fast','ag/embedding'],rpm:30,tokenLimit:100000,monthlyBudget:15},
 {id:'indexer-bot',name:'Document indexer',team:'Research',owner:'Data team',environment:'development',enabled:false,models:['ag/embedding'],rpm:30,tokenLimit:100000,monthlyBudget:10}
];
export const seedKeys:VirtualKey[]=seedAgents.map((a,i)=>({id:`vk-${i+1}`,label:`${a.id}-dev`,agent:a.id,version:1,revoked:false,models:[...a.models],budget:a.monthlyBudget}));
export const seedRoutes:GatewayRoute[]=[{id:'ag/research',primary:'ag/research',fallback:'ag/fast',enabled:true,strategy:'Failover'},{id:'ag/coder',primary:'ag/coder',fallback:'ag/fast',enabled:true,strategy:'Failover'},{id:'ag/fast',primary:'ag/fast',fallback:'',enabled:true,strategy:'Single target'},{id:'ag/embedding',primary:'ag/embedding',fallback:'',enabled:true,strategy:'Single target'}];
export const costFor=(model:GatewayModel,input:number,output:number)=>(input*model.inputRate+output*model.outputRate)/1000000;
export function makeSeedLogs(now=Date.now()):RequestLog[]{return Array.from({length:96},(_,i)=>{const agent=seedAgents[i%3];const key=seedKeys[i%3];const model=seedModels.find(m=>m.id===agent.models[i%2])!;const denied=i%17===0;const input=180+i*19%850,output=model.type==='Embedding'?0:70+i*11%270;return {id:`demo-req-${String(96-i).padStart(4,'0')}`,time:now-i*37*60*1000,agent:agent.id,key:key.id,model:model.id,resolved:model.id,provider:model.provider,endpoint:model.endpoint,status:denied?429:200,reason:denied?'rate_limit_exceeded':'ok',input:denied?0:input,output:denied?0:output,latency:denied?8:190+i*57%1400,cost:denied?0:costFor(model,input,output),fallback:false}})}
export type SimulatorInput={agentId:string;keyId:string;modelId:string;endpoint:string;now:number;input:number;output:number};
export function simulateGatewayRequest(p:SimulatorInput,models:GatewayModel[],agents:GatewayAgent[],keys:VirtualKey[],routes:GatewayRoute[],logs:RequestLog[],fallbackAllowed=true):Omit<RequestLog,'id'>{
 const a=agents.find(a=>a.id===p.agentId),k=keys.find(k=>k.id===p.keyId),m=models.find(m=>m.id===p.modelId);const r=routes.find(r=>r.id===p.modelId);let resolved=m;let usedFallback=false;
 const result=(status:number,reason:string):Omit<RequestLog,'id'>=>({time:p.now,agent:p.agentId,key:p.keyId,model:p.modelId,resolved:resolved?.id||p.modelId,provider:resolved?.provider||'—',endpoint:p.endpoint,status,reason,input:status===200?p.input:0,output:status===200?p.output:0,latency:status===200?420:7,cost:status===200&&resolved?costFor(resolved,p.input,p.output):0,fallback:usedFallback});
 if(!a||!k||k.agent!==a.id)return result(401,'invalid_agent_key');
 if(!a.enabled)return result(403,'agent_paused');if(k.revoked)return result(401,'key_revoked');
 if(!a.models.includes(p.modelId)||!k.models.includes(p.modelId))return result(403,'model_scope_denied');
 if(!m||!r||!r.enabled)return result(404,'route_unavailable');if(m.endpoint!==p.endpoint)return result(400,'endpoint_mismatch');
 if(!m.enabled&&r.fallback&&fallbackAllowed){const f=models.find(x=>x.id===r.fallback);if(f?.enabled&&f.qualified&&f.type===m.type&&f.endpoint===m.endpoint&&a.models.includes(f.id)&&k.models.includes(f.id)){resolved=f;usedFallback=true}}
 if(!resolved?.enabled)return result(503,'deployment_disabled');if(!resolved.qualified)return result(503,'qualification_required');
 const month=new Date(p.now);month.setUTCDate(1);month.setUTCHours(0,0,0,0);
 const recent=logs.filter(l=>l.agent===a.id&&l.time>p.now-60000&&l.time<=p.now);const monthly=logs.filter(l=>l.time>=month.getTime()&&l.time<=p.now);
 if(recent.length>=a.rpm)return result(429,'rate_limit_exceeded');
 const usedTokens=monthly.filter(l=>l.agent===a.id).reduce((n,l)=>n+l.input+l.output,0);if(usedTokens+p.input+p.output>a.tokenLimit)return result(429,'token_quota_exceeded');
 const cost=costFor(resolved,p.input,p.output);if(monthly.filter(l=>l.agent===a.id).reduce((n,l)=>n+l.cost,0)+cost>a.monthlyBudget||monthly.filter(l=>l.key===k.id).reduce((n,l)=>n+l.cost,0)+cost>k.budget)return result(429,'budget_exceeded');
 return result(200,usedFallback?'fallback_succeeded':'ok');
}
