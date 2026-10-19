'use strict';
// Executed inside the existing Orgo API container. No writes or HTTP calls.
const {PrismaClient} = require('@prisma/client');
const prisma = new PrismaClient({log:[]});
const ORG='0c2d7244-a0c0-4a10-8f11-0389f275f133';
(async()=>{
 try {
  const result=await prisma.$transaction(async tx=>{
   await tx.$executeRaw`SET TRANSACTION READ ONLY`;
   const org=await tx.organization.findUnique({where:{slug:'fakeplant'},select:{id:true,status:true}});
   if(!org || org.id!==ORG || org.status!=='active')throw Error('TENANT_MISMATCH');
   const where={organization_id:ORG};
   const [total,disabled,active,disabledSynthetic,activeSynthetic,profiles,roles,roleGrants,assignments,links,sessions,identityPermission]=await Promise.all([
    tx.userAccount.count({where}),tx.userAccount.count({where:{...where,status:'disabled'}}),
    tx.userAccount.count({where:{...where,status:'active'}}),
    tx.userAccount.count({where:{...where,status:'disabled',auth_provider:'external_only'}}),
    tx.userAccount.count({where:{...where,status:'active',auth_provider:'external_only'}}),
    tx.personProfile.count({where}),
    tx.role.count({where}),tx.rolePermission.count({where:{role:{organization_id:ORG}}}),
    tx.userRoleAssignment.count({where:{user:{organization_id:ORG},revoked_at:null}}),
    tx.ssoIdentity.findMany({where,select:{user_id:true,issuer:true}}),
    tx.loginSession.count({where:{...where,terminated_at:null,expires_at:{gt:new Date()}}}),
    tx.permission.count({where:{code:'identity:manage'}}),
   ]);
   // User eligibility checked without exposing names, IDs or email addresses.
   const linkUsers=links.length?await tx.userAccount.findMany({
    where:{organization_id:ORG,id:{in:links.map(x=>x.user_id)}},select:{id:true,status:true,auth_provider:true},
   }):[];
   const byId=new Map(linkUsers.map(x=>[x.id,x]));
   const eligible=links.filter(l=>{
    const u=byId.get(l.user_id);
    return !!(u && u.status==='active' && u.auth_provider!=='external_only');
   }).length;
   const issuer=String(process.env.OIDC_ISSUER||'').trim();
   const shared=String(process.env.KOA_SHARED_OIDC_ISSUER||'').trim();
   const expectedMode=String(process.env.KOA_TANDEM_SSO_MODE||'off').trim();
   const target=String(process.env.KOA_TANDEM_ORGANIZATION||'').trim();
   const universe=String(process.env.KOA_TANDEM_UNIVERSE||'').trim();
   const recovery=String(process.env.KOA_SSO_BREAKGLASS_EMAIL||'').trim().toLowerCase();
   const recoveryFound=recovery?await tx.userAccount.count({where:{organization_id:ORG,email:recovery,auth_provider:'local',status:'active'}}):0;
   const origin=String(process.env.ORGO_PUBLIC_URL||'');
   let httpsOrigin=false;
   try{const p=new URL(origin);httpsOrigin=p.protocol==='https:' && !p.username && !p.password && !p.hash;}catch{}
   return {ok:true,organization:'fakeplant',organization_id:ORG,
    users:{total,disabled,active,disabled_synthetic:disabledSynthetic,active_synthetic:activeSynthetic,profiles},acl:{roles,role_grants:roleGrants,active_role_assignments:assignments,identity_manage_permission_registered:identityPermission>0},
    sso:{mode:expectedMode,universe,target,issuer,shared_issuer_matches:!!issuer && issuer===shared,client_configured:!!process.env.OIDC_CLIENT_ID,client_secret_configured:!!process.env.OIDC_CLIENT_SECRET,origin_https:httpsOrigin,
     link_count:links.length,eligible_links:eligible,issuer_matching_links:issuer?links.filter(x=>x.issuer===issuer).length:0,
     breakglass_configured:!!recovery,breakglass_active_local:recoveryFound===1,
     auto_local_login_enabled:process.env.ORGO_LOCAL_AUTO_LOGIN==='true'},
    current_sessions:sessions,read_only:true};
  });
  console.log('KOA_SSO_ORGO_AUDIT='+JSON.stringify(result));
 }catch(err){console.log('KOA_SSO_ORGO_AUDIT='+JSON.stringify({ok:false,error:String(err?.code||'DB_OR_SCHEMA_UNAVAILABLE').slice(0,90)}));process.exitCode=2;}
 finally{await prisma.$disconnect();}
})();
