import {createServer} from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve,extname,dirname} from 'node:path';
import {fileURLToPath} from 'node:url';
import {randomBytes,createHash,timingSafeEqual} from 'node:crypto';

const hash=value=>createHash('sha256').update(value).digest();
export function allowedProxy(method,path,body){
 if(method==='GET')return /^\/(health\/(live|ready)|admin\/(portal\/(summary|reports|deployments|accounts|qualifications|routes|attempts|jobs)|sources|offerings(?:\/[^/]+\/evidence)?|jobs\/[^/]+|sync-runs\/[^/]+|qualifications\/[^/]+|pinned-models\/.+|route-snapshots\/[^/]+))$/.test(path);
 if(method==='POST'&&path==='/admin/qualifications')return body?.mode==='fixture';
 if(method==='POST')return path==='/admin/sources'||path==='/admin/portal/deployments'||path==='/admin/pinned-models'||/^\/admin\/sources\/.+\/sync$/.test(path)||/^\/admin\/pinned-models\/.+\/rollback$/.test(path);
 return method==='PUT'&&(/^\/admin\/sources\/.+\/schedule$/.test(path)||/^\/admin\/portal\/deployment-state\/[^/]+$/.test(path));
}
export function createPortalServer({operatorKey=process.env.OPERATOR_API_KEY||'',backend=process.env.GATEWAY_API_URL||'http://127.0.0.1:8000',secureCookie=process.env.PORTAL_COOKIE_SECURE==='1',dist=resolve(dirname(fileURLToPath(import.meta.url)),'../dist'),fetchImpl=fetch,clock=Date.now}={}){
 const upstream=new URL(backend);if(!['http:','https:'].includes(upstream.protocol)||upstream.username||upstream.password||upstream.search||upstream.hash)throw Error('Invalid backend URL');
 const sessions=new Map(),logins=new Map();const ttl=8*60*60*1000,cookieName=secureCookie?'__Host-ag_portal':'ag_portal';
 const cookie=(value,maxAge)=>`${cookieName}=${value}; HttpOnly; SameSite=Strict; Path=/; Max-Age=${maxAge}${secureCookie?'; Secure':''}`;
 const json=(res,status,data,headers={})=>{res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store',...headers});res.end(JSON.stringify(data));};
 const readJson=async req=>{let size=0;const chunks=[];for await(const chunk of req){size+=chunk.length;if(size>32768)throw Error('body_too_large');chunks.push(chunk)}try{return JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}')}catch{throw Error('invalid_json')}};
 const server=createServer(async(req,res)=>{
  res.setHeader('X-Content-Type-Options','nosniff');res.setHeader('Referrer-Policy','same-origin');res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'");
  try{
   const url=new URL(req.url,'http://portal');const method=req.method||'GET';
   for(const [key,expires] of sessions)if(expires<=clock())sessions.delete(key);
   for(const [key,value] of logins)if(value.expires<=clock())logins.delete(key);
   if(url.pathname.startsWith('/portal-api')){
    if(req.headers['sec-fetch-site']==='cross-site'){json(res,403,{detail:'cross_site_request_denied'});return;}
    if(!['GET','HEAD'].includes(method)){
     const origin=req.headers.origin,host=req.headers.host;
     if(!origin||!host||![`http://${host}`,`https://${host}`].includes(origin)){json(res,403,{detail:'same_origin_required'});return;}
     if(!(req.headers['content-type']||'').startsWith('application/json')){json(res,415,{detail:'json_required'});return;}
    }
    const token=(req.headers.cookie||'').split(';').map(s=>s.trim()).find(s=>s.startsWith(cookieName+'='))?.slice(cookieName.length+1);
    const authenticated=!!token&&sessions.has(token);
    if(url.pathname==='/portal-api/session'){
     if(method==='GET'){json(res,200,{authenticated,configured:operatorKey.length>0,live_inference_from_portal:false});return;}
     if(method==='DELETE'){if(token)sessions.delete(token);json(res,200,{authenticated:false},{'Set-Cookie':cookie('',0)});return;}
     if(method!=='POST'){json(res,405,{detail:'method_not_allowed'});return;}
     if(!operatorKey){json(res,503,{detail:'operator_not_configured'});return;}
     const address=req.socket.remoteAddress||'local';const counter=logins.get(address)||{count:0,expires:clock()+600000};
     if(counter.count>=8){json(res,429,{detail:'login_rate_limited'},{'Retry-After':'600'});return;}
     const body=await readJson(req);const credential=typeof body.credential==='string'?body.credential:'';
     if(!credential||!timingSafeEqual(hash(credential),hash(operatorKey))){counter.count++;if(logins.size<1000||logins.has(address))logins.set(address,counter);json(res,401,{detail:'invalid_operator_credential'});return;}
     if(token)sessions.delete(token);if(sessions.size>=200)sessions.delete(sessions.keys().next().value);
     const session=randomBytes(32).toString('base64url');sessions.set(session,clock()+ttl);logins.delete(address);
     json(res,200,{authenticated:true},{'Set-Cookie':cookie(session,ttl/1000)});return;
    }
    if(!authenticated){json(res,401,{detail:'session_required'});return;}
    if(!url.pathname.startsWith('/portal-api/backend/')){json(res,404,{detail:'not_found'});return;}
    let path;try{path=decodeURIComponent(url.pathname.slice('/portal-api/backend'.length))}catch{json(res,400,{detail:'invalid_path'});return;}
    if(path.includes('..')||/[\\\u0000-\u001f?#]/.test(path)){json(res,400,{detail:'invalid_path'});return;}
    const body=['POST','PUT'].includes(method)?await readJson(req):undefined;
    if(!allowedProxy(method,path,body)){json(res,403,{detail:path==='/admin/qualifications'?'portal_fixture_mode_only':'operation_not_allowed'});return;}
    const target=new URL(upstream.origin);target.pathname=path;
    const permitted=new Set(['limit','offset','provider','source','region','task_type','days','mode']);
    for(const [key,value] of url.searchParams){if(!permitted.has(key)||value.length>200){json(res,400,{detail:'invalid_query'});return;}target.searchParams.append(key,value);}
    let response;try{response=await fetchImpl(target,{method,headers:{Authorization:`Bearer ${operatorKey}`,Accept:'application/json',...(body?{'Content-Type':'application/json'}:{})},...(body?{body:JSON.stringify(body)}:{}),redirect:'error',signal:AbortSignal.timeout(15000)})}catch{json(res,502,{detail:'backend_unavailable'});return;}
    const reader=response.body?.getReader();let size=0;const chunks=[];if(reader){while(true){const {value,done}=await reader.read();if(done)break;size+=value.length;if(size>4*1024*1024){await reader.cancel();json(res,502,{detail:'backend_response_too_large'});return;}chunks.push(value);}}
    let data;try{data=JSON.parse(Buffer.concat(chunks).toString('utf8')||'{}')}catch{json(res,502,{detail:'invalid_backend_response'});return;}
    // Defense in depth: the admin DTO layer must never serialize credentials.
    const serialized=JSON.stringify(data);if(operatorKey&&serialized.includes(operatorKey)){json(res,502,{detail:'unsafe_backend_response'});return;}
    json(res,response.status,data);return;
   }
   if(!['GET','HEAD'].includes(method)){json(res,405,{detail:'method_not_allowed'});return;}
   let pathname;try{pathname=decodeURIComponent(url.pathname)}catch{json(res,400,{detail:'invalid_path'});return;}
   const file=resolve(dist,'.'+(pathname==='/'?'/index.html':pathname));
   if(!file.startsWith(resolve(dist)+'/')&&!file.startsWith(resolve(dist)+'\\')){json(res,404,{detail:'not_found'});return;}
   let content;try{content=await readFile(file)}catch{json(res,404,{detail:'not_found'});return;}
   res.writeHead(200,{'Content-Type':{'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon'}[extname(file)]||'application/octet-stream','Cache-Control':extname(file)==='.html'?'no-store':'public, max-age=3600'});res.end(method==='HEAD'?undefined:content);
  }catch(error){json(res,error.message==='body_too_large'?413:400,{detail:['invalid_json','body_too_large'].includes(error.message)?error.message:'invalid_request'});}
 });return server;
}
if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 createPortalServer().listen(Number(process.env.PORT||3000),process.env.HOST||'127.0.0.1',()=>console.log(`Operator Portal listening on port ${process.env.PORT||3000}`));
}
