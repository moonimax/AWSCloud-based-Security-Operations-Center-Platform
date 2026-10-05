"""Install reviewed agent changes with backup. Does not touch credentials or IAM."""
import argparse
import shutil
from datetime import datetime
from pathlib import Path

p=argparse.ArgumentParser();p.add_argument('runtime',type=Path);p.add_argument('--keep-ui',action='store_true',help='Keep your index.html and inject only performance.js');a=p.parse_args()
source=Path(__file__).resolve().parents[1]/'runtime/agent'
target=a.runtime/'agent'
if not (target/'app.py').is_file() or not (target/'planner.py').is_file():raise SystemExit('runtime/agent/app.py and planner.py are required')
html=(target/'index.html').read_text(encoding='utf-8')
if a.keep_ui and any(x not in html for x in ['id="evidence"','id="chat-form"','function urlFor','function panelUrl','function setPanelRange','function clearEvidence','function run']):
    raise SystemExit('Custom index.html lacks expected hooks; no files changed. Merge manually.')
backup=a.runtime/('backup-performance-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
backup.mkdir()
files=[f for f in source.iterdir() if f.is_file() and f.name!='planner.py']
for f in files:
    old=target/f.name
    if old.exists():shutil.copy2(old,backup/f.name)
    if f.name=='index.html' and a.keep_ui:
        if '/performance.js' not in html:
            # Ensure it executes before analysis.js if an explicit script already exists.
            tag='<script src="/performance.js"></script>'
            pos=html.find('<script src="/analysis.js"')
            html=html[:pos]+tag+'\n'+html[pos:] if pos>=0 else html.replace('</body>',tag+'\n</body>',1)
        old.write_text(html,encoding='utf-8')
    else:shutil.copy2(f,old)
print('Updated:',target,'\nBackup:',backup)
print('planner.py and all runtime settings preserved. Existing planner.validate must accept 10080 minutes.')
