export type Row=Record<string,unknown>;
export type Page={data:Row[];pagination:{limit:number;offset:number;total:number;has_more:boolean;next_offset?:number|null}};
export type Summary={counts:Record<string,number>;profiles:Row[];capabilities:Record<string,boolean>;adapter:{provider:string;task:string;models:string[]};usage_note:string};
export class ApiError extends Error{constructor(public status:number,message:string){super(message)}}
export async function request<T>(path:string,options:RequestInit={}):Promise<T>{
 const response=await fetch(path,{...options,credentials:'same-origin',headers:{Accept:'application/json',...(options.body?{'Content-Type':'application/json'}:{}),...options.headers},cache:'no-store'});
 let body:unknown;try{body=await response.json()}catch{throw new ApiError(response.status,'invalid_response')}
 if(!response.ok){const detail=(body as {detail?:unknown}).detail;throw new ApiError(response.status,typeof detail==='string'?detail:response.status===422?'validation_failed':'request_failed')}
 return body as T;
}
export const backend=<T>(path:string,options:RequestInit={})=>request<T>('/portal-api/backend'+path,options);
export const rec=(value:unknown):Row=>value!==null&&typeof value==='object'&&!Array.isArray(value)?value as Row:{};
export const text=(value:unknown):string=>value===null||value===undefined?'—':typeof value==='object'?JSON.stringify(value):String(value);
export const when=(value:unknown)=>typeof value==='string'&&!Number.isNaN(Date.parse(value))?new Date(value).toLocaleString('vi-VN',{timeZone:'Asia/Ho_Chi_Minh'}):'—';
