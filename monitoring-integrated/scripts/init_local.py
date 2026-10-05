"""Initialize local/EC2 secrets; preserve credentials and reject directory mounts."""
import os
import re
import secrets
from pathlib import Path
from password_hash import apr1

def initialize(root):
    secret=Path(root)/'secrets'
    secret.mkdir(mode=0o700,exist_ok=True)
    for name in ('htpasswd','credentials.txt','grafana-password'):
        path=secret/name
        if path.exists() and not path.is_file():
            raise ValueError(name+' is a directory. Stop Compose and rename that directory as a backup before retrying.')
    credentials=secret/'credentials.txt';auth=secret/'htpasswd'
    if credentials.exists():
        # Original Windows files may use a local encoding. The generated password is ASCII.
        lines=credentials.read_bytes().splitlines()
        password=lines[-1].split(b':',1)[-1].strip().decode('ascii') if lines else ''
        if not re.fullmatch(r'[0-9a-f]{36}',password):
            raise ValueError('Existing credentials format is not recognized; files were preserved.')
    elif auth.exists():
        raise ValueError('htpasswd exists without credentials.txt; files were preserved. Restore credentials or back up both files before resetting.')
    else:
        password=secrets.token_hex(18)
        credentials.write_text('username: operator\npassword: '+password+'\n',encoding='utf-8')
    if not auth.exists():
        auth.write_text('operator:'+apr1(password)+'\n',encoding='ascii')
    if not (secret/'grafana-password').exists():
        (secret/'grafana-password').write_text(secrets.token_hex(24)+'\n',encoding='ascii')
    for name in ('htpasswd','grafana-password'):
        if not (secret/name).stat().st_size:
            raise ValueError(name+' is empty; files were preserved for inspection.')
    if os.name!='nt':
        credentials.chmod(0o600)
        for name in ('htpasswd','grafana-password'):(secret/name).chmod(0o644)
    print('Initialized. Read runtime/secrets/credentials.txt for the login.')

if __name__=='__main__':
    try:initialize(Path(__file__).resolve().parents[1]/'runtime')
    except (ValueError,OSError) as exc:raise SystemExit(str(exc))
