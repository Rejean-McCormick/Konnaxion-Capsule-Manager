import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'orchestrateur.py'
spec=importlib.util.spec_from_file_location('tandem_pipeline',SCRIPT)
pipeline=importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def test_paths_for_drop_root(self):
        self.assertEqual(pipeline.ROOT,Path(__file__).resolve().parents[3])
        self.assertTrue((pipeline.VENDOR/'ik/FINALISER_FAKEPLANT_IK_E2E.ps1').exists())

    def test_scrub_common_secrets(self):
        raw='Bearer secret1234567890\nclient_secret = foo_bar_secret\npostgresql://user:pass@localhost/db'
        s=pipeline.scrub(raw)
        self.assertNotIn('secret1234567890',s)
        self.assertNotIn('foo_bar_secret',s)
        self.assertNotIn('user:pass',s)

    def test_resume_never_installs_source_guard(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(pipeline,'REPORT_DIR',Path(tmp)):
            original={'steps':[{'name':'guard-install','status':'failed'}, {'name':'sso-acl','status':'waiting'}],
                      'failed_steps':['guard-install'],'waiting_steps':['sso-acl']}
            with patch.object(pipeline,'latest',return_value=original), patch.object(pipeline,'inventory',return_value=('ok','Ready',{})):
                calls=[]
                old=dict(pipeline.HANDLERS)
                try:
                    pipeline.HANDLERS['inventory']=lambda:('ok','Ready',{})
                    pipeline.HANDLERS['sso-acl']=lambda:('waiting','Not configured',{})
                    pipeline.HANDLERS['guard-install']=lambda:calls.append('WRITE')
                    with contextlib.redirect_stdout(io.StringIO()):code=pipeline.run('resume')
                finally:pipeline.HANDLERS.clear();pipeline.HANDLERS.update(old)
                self.assertEqual(code,10)
                self.assertEqual(calls,[])
                record=json.loads((Path(tmp)/'LATEST_REPORT.json').read_text())
                self.assertEqual([x['name'] for x in record['steps']],['inventory','sso-acl'])

    def test_failures_logged_without_automatic_fix(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(pipeline,'REPORT_DIR',Path(tmp)):
            old=dict(pipeline.HANDLERS)
            try:
                pipeline.HANDLERS['inventory']=lambda:('ok','ready',{})
                pipeline.HANDLERS['ik-proof']=lambda:('failed','DB unavailable',{'exit_code':124})
                pipeline.HANDLERS['guard-preview']=lambda:('ok','ready',{})
                pipeline.HANDLERS['sso-acl']=lambda:('waiting','OIDC absent',{})
                pipeline.HANDLERS['konfid-inventory']=lambda:('waiting','Konfid service not ready',{})
                with contextlib.redirect_stdout(io.StringIO()):code=pipeline.run('audit')
                self.assertEqual(code,20)
                data=json.loads((Path(tmp)/'LATEST_REPORT.json').read_text())
                self.assertEqual(data['failed_steps'],['ik-proof'])
                self.assertEqual(data['waiting_steps'],['sso-acl','konfid-inventory'])
                self.assertFalse(data['source_guard_installed'])
                with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(pipeline.run('export'),0)
                self.assertEqual(len(list(Path(tmp).glob('DIAGNOSTIC_TANDEM_*.zip'))),1)
            finally:pipeline.HANDLERS.clear();pipeline.HANDLERS.update(old)

    def test_konfid_contract_snapshot(self):
        snapshot=Path('/mnt/data/_konfid_alignment/konfid')
        if not snapshot.is_dir():self.skipTest('Local mounted snapshot unavailable')
        with patch.object(pipeline,'REPO',Path('/nonexistent')),patch.dict('os.environ',{'KOA_KONFID_REPO':str(snapshot)}):
            status,msg,meta=pipeline.konfid_inventory()
            self.assertEqual(status,'waiting')
            self.assertTrue(all(meta['source_contracts'].values()))
            self.assertFalse(meta['live_service_verified'])


if __name__=='__main__':unittest.main()
