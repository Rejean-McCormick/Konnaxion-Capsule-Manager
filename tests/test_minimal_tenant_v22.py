import pathlib, tempfile, json, subprocess, os, unittest, shutil

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'tools/tandem_v2/provision_orgo_fakeplant_tenant.cjs'
PS = ROOT / 'tools/tandem_v2/CREATE_ORGO_FAKEPLANT_TENANT.ps1'
CMD = ROOT / 'START_TANDEM_CREER_FAKEPLANT_TENANT.cmd'
MOCK_JS = r'''
const state = process.env.MOCK_STATE || 'absent';
let existing = state === 'present' || state === 'inactive' ? {
  id:'00000000-0000-4000-8000-000000000111',slug:'fakeplant',display_name:'FakePlant Old',
  status:state==='inactive'?'suspended':'active',timezone:'UTC',default_locale:'en-CA'
}:null;
let writes = 0;
class PrismaClient {
  async $queryRaw(){return [{name:'orgo'}]}
  async $transaction(cb){
    return await cb({
      async $executeRaw() {return 1},
      organization:{
        async findUnique() {return existing},
        async create(x) {writes++;existing={id:'00000000-0000-4000-8000-000000000222',...x.data};return existing;}
      }
    })
  }
  organization = {async findUnique(){return existing}};
  async $disconnect(){console.log('__MOCK_WRITES__='+writes)}
}
module.exports = {PrismaClient};
'''
class TestTenantProvision(unittest.TestCase):
  @classmethod
  def setUpClass(cls):
    if not shutil.which('node'): raise unittest.SkipTest('Node not installed')
    cls.d = tempfile.TemporaryDirectory()
    p=pathlib.Path(cls.d.name)
    (p/'node_modules/@prisma/client').mkdir(parents=True)
    (p/'node_modules/@prisma/client/index.js').write_text(MOCK_JS)
    (p/'tenant.cjs').write_bytes(SOURCE.read_bytes())
    cls.p=p
  @classmethod
  def tearDownClass(cls):cls.d.cleanup()
  def run_case(self, mode, state):
    e={**os.environ,'MOCK_STATE':state,'DATABASE_URL':'postgresql://hidden:secret@postgres:5432/orgo'}
    r=subprocess.run(['node',str(self.p/'tenant.cjs'),f'--{mode}'],capture_output=True,text=True,env=e)
    row=[v for v in r.stdout.splitlines() if v.startswith('__KOA_ORGO_TENANT_V2__=')]
    self.assertEqual(len(row),1,(r.stdout,r.stderr))
    return r,json.loads(row[0].split('=',1)[1]),r.stdout
  def test_preview_absent_no_create(self):
    r,x,s=self.run_case('preview','absent');self.assertEqual(r.returncode,0);self.assertTrue(x['would_create']);self.assertIn('__MOCK_WRITES__=0',s)
  def test_preview_existing_no_create(self):
    r,x,s=self.run_case('preview','present');self.assertEqual(r.returncode,0);self.assertFalse(x['would_create']);self.assertIn('__MOCK_WRITES__=0',s)
  def test_create_absent_only_one_org(self):
    r,x,s=self.run_case('apply','absent');self.assertEqual(r.returncode,0);self.assertTrue(x['created']);self.assertTrue(x['verified']);self.assertIn('__MOCK_WRITES__=1',s)
    self.assertEqual(x['organization']['slug'],'fakeplant');self.assertEqual(x['organization']['default_locale'],'fr-CA')
  def test_idempotent_existing(self):
    r,x,s=self.run_case('apply','present');self.assertEqual(r.returncode,0);self.assertFalse(x['created']);self.assertIn('__MOCK_WRITES__=0',s)
  def test_inactive_refuses(self):
    r,x,s=self.run_case('apply','inactive');self.assertNotEqual(r.returncode,0);self.assertEqual(x['error_code'],'EXISTING_INACTIVE_ORG');self.assertIn('__MOCK_WRITES__=0',s)
  def test_no_secrets_leaked(self):
    r,x,s=self.run_case('preview','absent');self.assertNotIn('secret',s);self.assertNotIn('postgresql://',s)
  def test_powershell_has_confirm_and_no_seed(self):
    s=PS.read_text();self.assertIn('CREER fakeplant',s);self.assertIn('if ($confirm -cne',s);self.assertIn("$preview.database -ne 'orgo'",s)
    for bad in ['npm run db:seed','fakeplant-people.mjs --apply','prisma migrate deploy']:
      self.assertNotIn(bad,s)
  def test_cmd_no_paren_comment_problem(self):
    s=CMD.read_text();self.assertIn('pwsh -NoProfile -File',s);self.assertNotIn('(!',s)
if __name__=='__main__':unittest.main(verbosity=2)
