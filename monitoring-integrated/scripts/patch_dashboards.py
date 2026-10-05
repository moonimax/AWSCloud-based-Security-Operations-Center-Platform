"""Update existing dashboard JSON in-place, with backups; no UID/layout changes."""
import argparse
import json
import re
from datetime import datetime
from pathlib import Path


def patch(raw):
    dash=raw.get('dashboard',raw)
    if not isinstance(dash,dict) or 'panels' not in dash:raise ValueError('Not a classic dashboard JSON')
    dash['refresh']=''
    variables=dash.setdefault('templating',{}).setdefault('list',[])
    variables[:]=[v for v in variables if v.get('name')!='sentinel_interval']
    variables.append({'name':'sentinel_interval','label':'조회 간격','type':'custom','hide':2,
                      'query':'1m,5m,15m,1h','current':{'text':'1m','value':'1m'},
                      'options':[{'text':x,'value':x,'selected':x=='1m'} for x in ['1m','5m','15m','1h']]})
    changed=0;warnings=[]
    def panels(items):
        nonlocal changed
        for p in items:
            panels(p.get('panels',[]))
            targets=p.get('targets',[])
            if p.get('type')=='logs':
                for t in targets:t['maxLines']=100
            if p.get('type')!='timeseries':continue
            ready=True;found=False
            for t in targets:
                if t.get('hide'):continue
                ds=t.get('datasource',p.get('datasource',{}))
                is_loki=isinstance(ds,dict) and ds.get('type')=='loki'
                if not is_loki:ready=False;continue
                expr=t.get('expr','')
                if '$__range' in expr:ready=False;continue
                t['expr']=re.sub(r'\[\s*(?:\$__interval|\$__auto|\d+[smhd])\s*\]', '[${sentinel_interval}]',expr)
                t['interval']='${sentinel_interval}'
                t['step']='${sentinel_interval}'
                found=True
            p['sentinelPerformance']=bool(ready and found)
            if ready and found:
                p['interval']='${sentinel_interval}';p['maxDataPoints']=300
                p['description']=(p.get('description','').split('\n[Sentinel 성능]')[0]+
                    '\n[Sentinel 성능] 조회 기간에 따라 집계 창과 그래프 간격이 변경됩니다. 7일은 1시간. count는 간격당 건수입니다.')
                changed+=1
            else:warnings.append(str(p.get('title','')))
    panels(dash['panels'])
    return changed,warnings


def main():
    parser=argparse.ArgumentParser();parser.add_argument('directories',nargs='+');args=parser.parse_args()
    paths=set()
    for name in args.directories:
        p=Path(name)
        paths.update(p.glob('*.json') if p.is_dir() else [p])
    for p in sorted(paths):
        if p.is_symlink():continue
        raw=json.loads(p.read_text(encoding='utf-8-sig'));changed,warnings=patch(raw)
        backup=p.with_name(p.name+'.backup-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backup.write_bytes(p.read_bytes());p.write_text(json.dumps(raw,ensure_ascii=False,indent=2),encoding='utf-8')
        print(f'{p}: {changed}개 추세 패널 적용; 장기간 자동 조회 제외: {warnings}; backup={backup}')
if __name__=='__main__':main()
