import importlib.util
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
module_path = HERE / 'tools' / 'tandem_v2' / 'diag_ik_fakeplant.py'
spec = importlib.util.spec_from_file_location('diag_ik_fakeplant', module_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.kx = {'db_postgresql':True,'db_name':'neondb','ik_out_url':True,'ik_out_token':True,
            'ik_target_matches':True,'ik_inbound_token':True,'strict_world_scope':True}
        self.api = {'workflow_code_configured':True,'workflow_active_version':True,
            'valid_tokens_with_ik_scopes':1}
        self.worker = {'outbound_url':True,'outbound_token':True,'outbound_url_policy_ok':True}
    def test_ready_when_all_conditions_pass(self):
        self.assertEqual([],mod.recommendations(self.kx,self.api,self.worker,True,True))
    def test_konnaxion_missing_target(self):
        self.kx['ik_target_matches']=False
        self.assertTrue(any('sortie IK' in x for x in mod.recommendations(self.kx,self.api,self.worker,True,True)))
    def test_no_workflow_blocks(self):
        self.api['workflow_active_version']=False
        self.assertTrue(any('workflow' in x for x in mod.recommendations(self.kx,self.api,self.worker,True,True)))
    def test_no_scoped_token_blocks(self):
        self.api['valid_tokens_with_ik_scopes']=0
        self.assertTrue(any('jeton IK' in x for x in mod.recommendations(self.kx,self.api,self.worker,True,True)))
    def test_worker_unreachable_blocks(self):
        self.assertTrue(any('host.docker.internal' in x for x in mod.recommendations(self.kx,self.api,self.worker,True,False)))
    def test_worker_insecure_http_without_exception_blocks(self):
        self.worker['outbound_url_policy_ok']=False
        self.assertTrue(any('HTTPS' in x for x in mod.recommendations(self.kx,self.api,self.worker,True,True)))
    def test_db_mismatch_blocks(self):
        self.kx['db_name']='wrongdb'
        self.assertTrue(any('neondb' in x for x in mod.recommendations(self.kx,self.api,self.worker,True,True)))
    def test_script_has_no_write_transactions(self):
        self.assertIn('SET TRANSACTION READ ONLY',mod.ORGO_API_CODE)
        self.assertNotIn('updateMany',mod.ORGO_API_CODE)
        self.assertNotIn('db.create',mod.ORGO_API_CODE)
if __name__=='__main__': unittest.main()
