"""Minimal, idempotent fail-closed SSO guard patch for Konnaxion.

No migration/DB/OIDC or environment changes. Preview by default.
"""
from __future__ import annotations
import argparse
import os
import shutil
import tempfile
from pathlib import Path

BASE_ANCHOR='COMMON_OIDC_ENABLED = env.bool("COMMON_OIDC_ENABLED", default=False)'
BASE_PATCH=("# Tandem SSO: require explicit federated user linking by default.\n"
  'COMMON_OIDC_ALLOW_AUTO_SIGNUP = env.bool("COMMON_OIDC_ALLOW_AUTO_SIGNUP", default=False)')
OLD_METHOD='''    def is_open_for_signup(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
    ) -> bool:
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", False)
'''
NEW_METHOD='''    def is_open_for_signup(
        self,
        request: HttpRequest,
        sociallogin: SocialLogin,
    ) -> bool:
        # Tandem SSO: never provision a Konnaxion user from an OIDC assertion
        # without an explicit account link. Local signup policy is unchanged.
        if getattr(settings, "COMMON_OIDC_ENABLED", False) and not getattr(
            settings, "COMMON_OIDC_ALLOW_AUTO_SIGNUP", False
        ):
            return False
        return getattr(settings, "ACCOUNT_ALLOW_REGISTRATION", False)
'''


def transform(base:bytes,adapter:bytes)->tuple[bytes,bytes,dict]:
    b=base.decode('utf-8-sig').replace('\r\n','\n')
    a=adapter.decode('utf-8-sig').replace('\r\n','\n')
    statuses={}
    if BASE_PATCH in b:
        statuses['base']='already';
    elif b.count(BASE_ANCHOR)==1 and 'COMMON_OIDC_ALLOW_AUTO_SIGNUP' not in b:
        b=b.replace(BASE_ANCHOR,BASE_ANCHOR+'\n'+BASE_PATCH,1)
        statuses['base']='patched'
    else:raise ValueError('Settings OIDC inattendus ou en conflit; rien modifie')
    if NEW_METHOD in a:
        statuses['adapter']='already'
    elif a.count(OLD_METHOD)==1 and 'COMMON_OIDC_ALLOW_AUTO_SIGNUP' not in a:
        a=a.replace(OLD_METHOD,NEW_METHOD,1)
        statuses['adapter']='patched'
    else:raise ValueError('Adaptateur social modifie/inattendu; rien modifie')
    def encode(src:bytes,content:str):
        newline='\r\n' if b'\r\n' in src else '\n'
        normalized=content.replace('\r\n','\n').replace('\n',newline)
        bom=b'\xef\xbb\xbf' if src.startswith(b'\xef\xbb\xbf') else b''
        return bom+normalized.encode('utf-8')
    return encode(base,b),encode(adapter,a),statuses


def apply(root:Path, apply_changes:bool, test_source:Path)->dict:
    root=root.resolve()
    if not (root/'backend/manage.py').is_file():raise ValueError('Racine Konnaxion invalide')
    b=root/'backend/config/settings/base.py';a=root/'backend/konnaxion/users/adapters.py'
    test=root/'backend/konnaxion/users/tests/test_common_oidc_signup_policy.py'
    original_b,original_a=b.read_bytes(),a.read_bytes()
    new_b,new_a,status=transform(original_b,original_a)
    test_data=test_source.read_bytes()
    if test.exists() and test.read_bytes()!=test_data:raise ValueError('Test de politique existant different; refus decrasement')
    changes=[(b,original_b,new_b),(a,original_a,new_a)]
    if not test.exists(): changes.append((test,None,test_data))
    targets=[p for p,old,new in changes if old!=new]
    if not apply_changes:
        return {'ok':True,'mode':'preview','files_to_change':[str(x.relative_to(root)) for x in targets],
                'statuses':status,'no_database_writes':True,'no_env_changes':True}
    prepared=[]
    try:
        for path,old,new in changes:
            if old==new:continue
            path.parent.mkdir(parents=True,exist_ok=True)
            fd,tmp=tempfile.mkstemp(prefix='.koa-sso-',dir=path.parent)
            with os.fdopen(fd,'wb') as stream:stream.write(new);stream.flush();os.fsync(stream.fileno())
            if old is not None:os.chmod(tmp,path.stat().st_mode)
            prepared.append((path,old,tmp))
        # Two source files must both be verified before any replacement.
        for p,old,_ in prepared:
            if old is not None and p.read_bytes()!=old:raise ValueError('Fichier modifie pendant preflight : refus')
        applied=[]
        try:
            for p,old,tmp in prepared:
                os.replace(tmp,p);applied.append((p,old))
        except OSError:
            # Roll back to exact preimages. No secret material is involved.
            for p,old in reversed(applied):
                if old is None:p.unlink(missing_ok=True)
                else:p.write_bytes(old)
            raise
    finally:
        for _,_,tmp in prepared:
            if os.path.exists(tmp):os.unlink(tmp)
    return {'ok':True,'mode':'apply','changed_files':[str(x.relative_to(root)) for x in targets],
            'no_database_writes':True,'no_env_changes':True,
            'note':'Restart Uvicorn only if it was running; OIDC still off.'}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        result=apply(args.root,args.apply,Path(__file__).with_name('test_common_oidc_signup_policy.py'))
        import json;print(json.dumps(result,indent=2,ensure_ascii=False))
    except (OSError,UnicodeError,ValueError) as err:
        print('[REFUS] '+str(err));return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
