export type SimulationPolicy={enabled:boolean;revoked:boolean;allowedAliases:string[];recentRequests:number;rpm:number;usedTokens:number;tokenLimit:number;alias:string;bindingExists:boolean;targetEnabled:boolean};
/** Browser-only policy example. Never authorizes real provider calls. */
export function decideDemoRequest(p:SimulationPolicy):string{
 if(!p.enabled)return 'agent_paused';
 if(p.revoked)return 'agent_key_revoked';
 if(!p.allowedAliases.includes(p.alias))return 'scope_denied';
 if(p.recentRequests>=p.rpm)return 'rate_limit_exceeded';
 if(p.usedTokens+64>p.tokenLimit)return 'token_quota_exceeded';
 if(!p.bindingExists)return 'alias_not_found';
 if(!p.targetEnabled)return 'deployment_disabled';
 if(p.alias!=='ag/sandbox')return 'route_not_published';
 return 'fixture_response_ok';
}
