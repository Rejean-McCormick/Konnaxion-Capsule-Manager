$ErrorActionPreference='Stop'
Set-StrictMode -Version Latest
$repo='C:\mycode\Orgo\Orgo'
$expected='http://host.docker.internal:8000/api/integrations/ik/konnaxion/interactions/'
if (-not (Test-Path -LiteralPath (Join-Path $repo 'docker-compose.yml'))) { throw 'Orgo introuvable' }
Push-Location -LiteralPath $repo
try {
    # A deliberately invalid envelope cannot reach the INSERT code path.
    # 400 / IK_UNSUPPORTED_PROTOCOL proves authentication without a DB write.
    $js = @'
const url=process.env.KONNAXION_IK_URL||'';
const token=process.env.KONNAXION_IK_TOKEN||'';
if (url!=='http://host.docker.internal:8000/api/integrations/ik/konnaxion/interactions/' || !token) {
  console.error('[REFUS] Configuration Worker non conforme');process.exit(2);
}
(async()=>{
  const response=await fetch(url,{method:'POST',headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json'},body:'{}',signal:AbortSignal.timeout(8000),redirect:'error'});
  const obj=await response.json().catch(()=>({}));
  if(response.status===400 && obj.code==='IK_UNSUPPORTED_PROTOCOL'){
    console.log('[OK] Worker Docker -> Django : Bearer authentifie, requete volontairement rejetee AVANT ecriture (IK_UNSUPPORTED_PROTOCOL).');
    console.log('[FIN] Aucune publication d impact ni workflow cree.');
  } else {
    console.error('[REFUS] Echec test authentification IK (HTTP '+response.status+'; code '+String(obj.code||'absent')+').');
    process.exitCode=3;
  }
})().catch(()=>{console.error('[REFUS] Backend Konnaxion inaccessible par Worker');process.exitCode=4;});
'@
    $js | & docker compose exec -T worker node -
    if ($LASTEXITCODE -ne 0) { throw 'Test IK ingress echoue' }
} finally { Pop-Location }
