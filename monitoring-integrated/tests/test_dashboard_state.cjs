// No external dependencies. DOM/API stubs verify UI state; not visual/Grafana QA.
const vm = require('node:vm');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const elements = new Map();
class Element {
  constructor() { this.value=''; this.disabled=false; this.hidden=false; this.children=[]; this.textContent=''; this.classList={toggle(){}}; }
  set id(value) { this._id=value; elements.set('#'+value,this); }
  get id() { return this._id; }
  setAttribute() {}
  append(...children) { this.children.push(...children); }
  closest() { return this; }
  reset() { this.value=''; }
}
const document = {
  querySelector(key) { if(!elements.has(key)) elements.set(key,new Element()); return elements.get(key); },
  querySelectorAll() { return []; },
  createElement() { return new Element(); }
};
let pending;
const ctx = vm.createContext({document, URL, Map, AbortController, console,
  window:{location:{origin:'http://sentinel.test'},scrollTo(){}},
  fetch(url) { return url==='/api/config' ? new Promise(()=>{}) : new Promise(resolve=>{pending=resolve;}); }
});
const html=fs.readFileSync(path.join(__dirname,'../runtime/agent/index.html'),'utf8');
vm.runInContext(html.split('<script>')[1].split('</script>')[0],ctx);
const run=code=>vm.runInContext(code,ctx);
const panel=id=>new URL(elements.get('#panel-'+id).src,'http://sentinel.test');
(async()=>{
  assert.equal(document.querySelector('#dashboard').children.length,5);
  run('zoomPanel(5,0.5)');
  assert.equal(panel(5).searchParams.get('from'),'now-8m');
  assert.equal(panel(2).searchParams.get('from'),'now-15m');
  for(let i=0;i<15;i++)run('zoomPanel(5,0.5)');
  assert.equal(panel(5).searchParams.get('from'),'now-1m');
  assert.equal(elements.get('#zoom-in-5').disabled,true);
  for(let i=0;i<15;i++)run('zoomPanel(5,2)');
  assert.equal(panel(5).searchParams.get('from'),'now-1440m');
  assert.equal(elements.get('#zoom-out-5').disabled,true);
  run('applyDashboard("/grafana/d/integrated-multilog?var-status=500&var-status=501&var-log_type=alb",60)');
  run('zoomPanel(5,0.5)');
  assert.deepEqual(panel(5).searchParams.getAll('var-status'),['500','501']);
  assert.equal(panel(5).searchParams.get('from'),'now-30m');
  assert.equal(panel(6).searchParams.get('from'),'now-60m');
  elements.get('#zoom-reset-5').onclick();
  assert.equal(panel(5).searchParams.get('from'),'now-60m');
  document.querySelector('#question').value='ALB';
  const request=run('run({text:"ALB"})');
  let prevented=false;
  elements.get('#home').onclick({button:0,preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  for(const id of [2,3,4,5,6]){
    assert.equal(panel(id).searchParams.get('from'),'now-15m');
    assert.equal(panel(id).searchParams.has('var-status'),false);
  }
  // Even if an aborted request resolves, sequence guard must retain the home view.
  pending({ok:true,json:async()=>({dashboard_url:'/grafana/d/integrated-multilog',plan:{minutes:60}})});
  await request;
  assert.equal(panel(5).searchParams.get('from'),'now-15m');
  assert.equal(document.querySelector('#question').value,'');
  assert.equal(document.querySelector('#evidence').hidden,true);
  assert.equal(document.querySelector('#submit').disabled,false);
  assert.equal(document.querySelector('#applied').textContent,'기본 조건 · 전체 서버 · 최근 15분');
  assert.equal(panel(5).pathname,'/grafana/d-solo/integrated-multilog');
  console.log('PASS: independent panel zoom, limits, repeated filters, panel reset, logo home reset, late response guard');
})().catch(e=>{console.error(e);process.exitCode=1;});
