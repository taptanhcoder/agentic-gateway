export type Source={id:string;name:string;connector:string;status:string;snapshot:string;interval:number|null;enabled:boolean;count:number;fixtureName?:string};
export type Offering={id:string;name:string;provider:string;task:string;region:string;source:string;evidence:string};
export type Deployment={id:string;offering:string;account:string;generation:number;epoch:number;enabled:boolean;secret:string};
export type Job={id:string;resource:string;type:'sync'|'qualification';status:string;progress:string;error:string;time:string;mode:string};
export type Verdict={id:string;deployment:string;generation:number;epoch:number;mode:string;decision:string;cases:string;validity:string};
export type Audit={id:string;event:string;resource:string;result:string;time:string};
export const initialSources:Source[]=[
 {id:'nvidia/hosted',name:'NVIDIA hosted catalog',connector:'Official documentation',status:'succeeded',snapshot:'snapshot-nv-r2',interval:3600,enabled:true,count:2},
 {id:'siliconflow/cn',name:'SiliconFlow China',connector:'Official catalog',status:'failed',snapshot:'snapshot-sf-r2',interval:3600,enabled:true,count:2},
 {id:'fixture/catalog-a',name:'Fixture catalog A',connector:'Packaged fixture',status:'succeeded',snapshot:'snapshot-fx-r2',interval:null,enabled:true,count:2}
];
export const offerings:Offering[]=[
 {id:'off-nv-01',name:'meta/llama-3.3-70b-instruct',provider:'NVIDIA',task:'chat',region:'global',source:'nvidia/hosted',evidence:'official_observed'},
 {id:'off-nv-02',name:'qwen/qwen2.5-coder-32b-instruct',provider:'NVIDIA',task:'code',region:'global',source:'nvidia/hosted',evidence:'official_observed'},
 {id:'off-sf-01',name:'deepseek-ai/DeepSeek-V3',provider:'SiliconFlow',task:'chat',region:'cn',source:'siliconflow/cn',evidence:'official_observed'},
 {id:'off-sf-02',name:'BAAI/bge-m3',provider:'SiliconFlow',task:'embedding',region:'cn',source:'siliconflow/cn',evidence:'metadata_only'},
 {id:'off-fx-01',name:'alpha/chat',provider:'Fixture',task:'chat',region:'local',source:'fixture/catalog-a',evidence:'synthetic_fixture'},
 {id:'off-fx-02',name:'gamma/chat',provider:'Fixture',task:'chat',region:'local',source:'fixture/catalog-a',evidence:'synthetic_fixture'}
];
export const initialDeployments:Deployment[]=[
 {id:'demo-nvidia-text',offering:'off-nv-01',account:'operator-local',generation:1,epoch:0,enabled:true,secret:'env:NVIDIA_API_KEY'},
 {id:'demo-nvidia-coder',offering:'off-nv-02',account:'operator-local',generation:2,epoch:0,enabled:true,secret:'env:NVIDIA_API_KEY'},
 {id:'demo-fixture-text',offering:'off-fx-01',account:'fixture-account',generation:1,epoch:0,enabled:true,secret:'fixture:no-secret'}
];
export const initialJobs:Job[]=[
 {id:'job-sync-003',resource:'siliconflow/cn',type:'sync',status:'failed',progress:'1 / 2 pages',error:'catalog_http_429',time:'10:44:12',mode:'demo'},
 {id:'job-qual-002',resource:'demo-nvidia-coder',type:'qualification',status:'succeeded',progress:'2 / 2 cases',error:'',time:'10:42:30',mode:'fixture'},
 {id:'job-sync-002',resource:'nvidia/hosted',type:'sync',status:'succeeded',progress:'2 / 2 pages',error:'',time:'10:40:18',mode:'demo'},
 {id:'job-sync-001',resource:'fixture/catalog-a',type:'sync',status:'succeeded',progress:'2 / 2 pages',error:'',time:'10:38:09',mode:'fixture'}
];
export const initialVerdicts:Verdict[]=[
 {id:'verdict-fixture-002',deployment:'demo-nvidia-coder',generation:2,epoch:0,mode:'fixture',decision:'passed',cases:'2 / 2',validity:'Fixture only'},
 {id:'verdict-fixture-001',deployment:'demo-fixture-text',generation:1,epoch:0,mode:'fixture',decision:'passed',cases:'2 / 2',validity:'Fixture only'}
];
export const services=[
 {id:'agentic-gateway-api',role:'FastAPI · admin + health',address:'127.0.0.1:8000',state:'not_checked',contract:'GET /health/live · GET /health/ready'},
 {id:'agentic-gateway-worker',role:'Sync + qualification jobs',address:'Compose internal',state:'not_checked',contract:'DB-backed worker'},
 {id:'agentic-gateway-scheduler',role:'Scheduled sync enqueue',address:'Compose internal',state:'not_checked',contract:'Opt-in schedules'},
 {id:'db',role:'PostgreSQL 16',address:'db:5432 · internal',state:'not_checked',contract:'Persistent postgres_data volume'},
 {id:'migrate',role:'Alembic + catalog seeds',address:'One-shot process',state:'not_checked',contract:'0006_qualifications · source checkout'}
];
export const contracts=[
 {area:'Source registry',status:'HTTP API',location:'src/agentic_gateway/catalog/admin.py',detail:'List, fixture registration, sync, schedule, job/run detail.'},
 {area:'Offerings & evidence',status:'HTTP API',location:'src/agentic_gateway/catalog/admin.py',detail:'Filtered offerings + per-offering evidence.'},
 {area:'Qualification',status:'HTTP API',location:'src/agentic_gateway/qualification/admin.py',detail:'Enqueue + run detail; list endpoint chưa có.'},
 {area:'Pinned routes',status:'HTTP API',location:'src/agentic_gateway/qualification/admin.py',detail:'Publish, read by ID, snapshot, CAS rollback; list endpoint chưa có.'},
 {area:'Deployments & accounts',status:'CLI / service',location:'src/agentic_gateway/providers/inference/deployments.py',detail:'Versioning, account/secret refs, enable flags; cần admin HTTP contracts.'},
 {area:'Inference attempts',status:'DB / service',location:'src/agentic_gateway/providers/inference/deployments.py',detail:'Attempt reports tồn tại; cần read API cho Portal.'},
 {area:'System health',status:'Partial API',location:'src/agentic_gateway/api.py',detail:'Liveness/readiness có; worker/scheduler telemetry cần bổ sung.'},
 {area:'Agent keys & quotas',status:'Planned',location:'Chưa có API',detail:'Cần access scopes, key lifecycle, accounting và enforcement.'},
 {area:'Audit explorer',status:'Planned',location:'Chưa có API tổng hợp',detail:'Có actor / logs theo từng module; cần audit query contract.'},
 {area:'Public inference',status:'Planned',location:'Chưa có public route',detail:'/v1/responses là mục tiêu, chưa expose trong app factory.'}
];
