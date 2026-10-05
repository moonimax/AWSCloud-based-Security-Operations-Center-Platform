const fs=require('fs'),vm=require('vm'),assert=require('assert');
function el(){return {textContent:'',disabled:false,hidden:false,append(){},setAttribute(){},querySelector(){return el()}};}
const nodes=new Map();const get=x=>{if(!nodes.has(x))nodes.set(x,el());return nodes.get(x)};
const context={document:{querySelector:get,createElement:el},window:{},location:{origin:'http://localhost'},
 URL,Date,console,setTimeout,clearTimeout,AbortController,
 current:{minutes:15},activeDashboard:()=>({performance_ready:true,panels:[{id:1,long_safe:true},{id:2,long_safe:false}]}),
 urlFor:()=>'/grafana/d/test?refresh=1m',panelUrl:()=>'/grafana/d-solo/test',setPanelRange(){},
 clearEvidence(){},run(){},show(){},version:0,busy:false};
vm.createContext(context);vm.runInContext(fs.readFileSync(__dirname+'/../runtime/agent/performance.js','utf8'),context);
let u=new URL(context.urlFor({minutes:10080}),'http://localhost');
assert.equal(u.searchParams.get('refresh'),'');assert.equal(u.searchParams.get('var-sentinel_interval'),'1h');
assert.equal(Number(u.searchParams.get('to'))-Number(u.searchParams.get('from')),10080*60000);
assert.equal(new URL(context.urlFor({minutes:1439}),'http://localhost').searchParams.get('refresh'),'1m');
assert.equal(context.panelUrl(2,1440),'about:blank');assert.notEqual(context.panelUrl(1,10080),'about:blank');
assert.notEqual(context.panelUrl(2,1439),'about:blank');
console.log('PASS: long absolute range, refresh off, 1h interval, unsafe panels blocked, short refresh retained');
