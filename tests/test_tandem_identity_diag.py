"""Mocked contract tests: never connects to actual Orgo Postgres."""
from pathlib import Path
import json, os, subprocess, tempfile, unittest, shutil
ROOT=Path(__file__).resolve().parents[1]
JS=(ROOT/'tools/tandem_v2/inspect_orgo_tenant_readonly.cjs').read_text(encoding='utf8')
MARK='__KOA_ORGO_IDENTITY__='
MOCK=r'''
const current=[];
module.exports.PrismaClient = class {
 async $transaction(f) {
  const tx={
   $executeRaw: async (strings) => {if(strings.join('')!=='SET TRANSACTION READ ONLY') throw Error('not read-only'); current.push('READ_ONLY');},
   organization:{findUnique:async ({where,select}) => {
      if(current[0]!=='READ_ONLY' || where.slug!=='fakeplant' || Object.keys(select).includes('password')) throw Error('tenant leak');
      return process.env.MOCK_ABSENT ? null : {id:'12345678-1234-4234-8234-123456789abc',slug:'fakeplant',display_name:'FakePlant',status:'active'};
   }},
   workflowDefinition:{findMany:async (args) => {
      if(current[0]!=='READ_ONLY' || args.where.OR[0].organization_id!=='12345678-1234-4234-8234-123456789abc') throw Error('wrong scope');
      return [{id:'wf',code:'demoprocess',organization_id:'12345678-1234-4234-8234-123456789abc',versions:[{id:'v1',version:1,published_at:new Date('2026-01-01T00:00:00Z')}]}];
   }}
  };
  if(process.env.MOCK_FAIL) {const e=Error('PASSWORD=confidential');e.code='P1001';throw e;}
  return await f(tx);
 }
 async $disconnect() {}
};
'''
class TestReadOnlyIdentity(unittest.TestCase):
 def run_case(self, mode=None):
  with tempfile.TemporaryDirectory() as temp:
   p=Path(temp)/'node_modules'/'@prisma'/'client';p.mkdir(parents=True)
   (p/'index.js').write_text(MOCK,encoding='utf8')
   env=os.environ.copy();env.pop('MOCK_ABSENT',None);env.pop('MOCK_FAIL',None)
   if mode:env['MOCK_'+mode]='1'
   x=subprocess.run(['node','-'],cwd=temp,input=JS,text=True,capture_output=True,env=env)
   lines=[l[len(MARK):] for l in x.stdout.splitlines() if l.startswith(MARK)]
   self.assertEqual(len(lines),1,(x.stdout,x.stderr));return x,json.loads(lines[0])
 def test_real_uuid_found(self):
  proc,data=self.run_case();self.assertEqual(proc.returncode,0)
  self.assertTrue(data['organization_found']);self.assertEqual(data['organization']['id'],'12345678-1234-4234-8234-123456789abc')
  self.assertEqual(data['published_workflows'][0]['scope'],'organization')
 def test_absent_is_not_provisioned(self):
  proc,data=self.run_case('ABSENT');self.assertEqual(proc.returncode,0)
  self.assertFalse(data['organization_found']);self.assertEqual(data['published_workflows'],[])
 def test_error_redacted(self):
  proc,data=self.run_case('FAIL');self.assertEqual(proc.returncode,3)
  self.assertEqual(data['error_code'],'P1001');self.assertNotIn('confidential',str(data)+proc.stdout)
 def test_power_shell_limited_to_reads(self):
  ps=(ROOT/'tools/tandem_v2/DIAG_ORGO_FAKEPLANT_IDENTITY.ps1').read_text(encoding='utf8')
  self.assertIn('exec -T api node -',ps)
  self.assertNotIn('db:seed',ps);self.assertNotIn('db:migrate',ps)
  self.assertIn('orgo.tandem.json',ps)
if __name__=='__main__':unittest.main()
