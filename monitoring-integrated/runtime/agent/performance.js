/* Added after the original UI script. Requires the bundled app/query_engine API. */
(() => {
  const evidence=document.querySelector('#evidence');
  if (!evidence || typeof run!=='function') return;
  let snapshot=null, next=null, poll=0, loading=false, epoch=0;
  // 조건 비교는 키 순서와 무관하게 (서버가 돌려준 plan과 화면의 current는 키 순서가 다를 수 있음)
  const samePlan=(a,b)=>{const k=Object.keys(a||{});return k.length===Object.keys(b||{}).length&&k.every(x=>a[x]===b[x]);};
  window.sentinelSnapshot=()=>snapshot && samePlan(snapshot.plan,current)?snapshot:null;
  const bar=document.createElement('div');bar.className='actions';
  const pageSize=document.createElement('select');pageSize.setAttribute('aria-label','페이지당 원문 건수');
  for(const n of [100,200,500]){const o=document.createElement('option');o.value=n;o.textContent=n+'건씩';pageSize.append(o);}
  const more=document.createElement('button');more.type='button';more.textContent='원문 조회';
  const count=document.createElement('p');count.setAttribute('role','status');
  bar.append(pageSize,more);evidence.append(count,bar);
  evidence.querySelector('summary').textContent='근거 로그 · 페이지별 조회';
  const refresh=document.createElement('button');refresh.type='button';refresh.textContent='현재 조건 새로 조회';
  document.querySelector('.heading').append(refresh);
  refresh.onclick=()=>run({plan:{...current},fresh:true});
  const oldURL=urlFor;
  urlFor=function(p,d=activeDashboard()){
    const u=new URL(oldURL(p,d),location.origin);
    const end=snapshot?.end_unix*1000 || Date.now();
    if(p.minutes>=1440){u.searchParams.set('refresh','');u.searchParams.set('from',String(end-p.minutes*60000));u.searchParams.set('to',String(end));}
    else u.searchParams.set('refresh','1m');
    u.searchParams.set('var-sentinel_interval',p.minutes>=10080?'1h':p.minutes>=1440?'15m':p.minutes>=360?'5m':'1m');
    return u.pathname+u.search;
  };
  const oldClear=clearEvidence;
  clearEvidence=function(){epoch++;clearTimeout(poll);snapshot=null;next=null;oldClear();};
  // 게이트웨이(nginx)가 돌려주는 HTML 오류 페이지(429·502·504 등)를 JSON으로 읽다가 생기는 구문 오류를 막는다.
  async function readJSON(r){
    const t=await r.text();
    try{return JSON.parse(t);}catch{
      const msg={429:'조회 요청이 많습니다. 잠시 후 다시 시도하세요.',502:'서버가 응답하지 않습니다. 잠시 후 다시 시도하세요.',503:'서버가 응답하지 않습니다. 잠시 후 다시 시도하세요.',504:'서버 응답이 지연되고 있습니다. 조회 범위를 줄여 다시 시도하세요.',401:'인증이 필요합니다. 페이지를 새로고침하세요.',413:'요청이 너무 큽니다.'}[r.status]||('서버 응답을 해석할 수 없습니다 (HTTP '+r.status+')');
      return {error:msg};
    }
  }
  async function api(url,body){const r=await fetch(url,{method:body?'POST':'GET',headers:body?{'Content-Type':'application/json'}:{},body:body?JSON.stringify(body):undefined,cache:'no-store'});const d=await readJSON(r);if(!r.ok)throw Error(d.error||'조회 실패');return d;}
  function info(d){count.textContent=d.state==='done'?`전체 ${d.total.toLocaleString()}건 · 기준 ${new Date(d.end_unix*1000).toLocaleString('ko-KR')} · 조회 기록 재사용 가능 5분`:d.state==='error'?d.error:`전체 건수 집계 중 ${d.done_chunks}/${d.total_chunks} 구간 · 원문 먼저 조회 가능`;}
  async function pollCount(sid,ticket){try{const d=await api('/api/snapshot/'+sid);if(ticket!==epoch)return;Object.assign(snapshot,d);info(d);if(d.state==='running')poll=setTimeout(()=>pollCount(sid,ticket),2500);}catch(e){if(ticket===epoch)count.textContent=e.message;}}
  async function loadPage(auto=0){if(loading||!snapshot)return;loading=true;more.disabled=true;const ticket=epoch;const sid=snapshot.snapshot_id;
    try{const d=await api('/api/logs',{snapshot_id:sid,page_size:Number(pageSize.value),cursor:next});if(ticket!==epoch)return;
      // Replace visible page instead of retaining unbounded DOM history.
      document.querySelector('#logs').textContent=d.rows.map(x=>`${x.timestamp_ns} ${x.server}\n${x.line}${x.truncated?'\n[16,000자 이후 생략]':''}`).join('\n\n') || (d.next_cursor?'이 구간에 로그 없음. 더 보기로 이전 구간을 확인하세요.':'조회가 끝났습니다.');
      next=d.next_cursor;more.textContent=next?'이전 로그 더 보기':'조회 완료';more.disabled=!next;
      // 이 구간에 로그가 없으면 사용자가 계속 누르지 않도록 이전 구간을 자동으로 이어서 조회 (최대 20번)
      if(!d.rows.length&&next&&auto<20){loading=false;return await loadPage(auto+1);}
    }catch(e){if(ticket===epoch){status(e.message,true);more.disabled=false;}}
    finally{loading=false;if(ticket===epoch && next)more.disabled=false;}
  }
  more.onclick=()=>loadPage();
  run=async function(body){if(busy)return;busy=true;document.querySelector('#submit').disabled=true;
    clearEvidence();const ticket=epoch;const versionTicket=version;controller=new AbortController();const mine=controller;
    say('user',body.text||'선택 조건 조회');status('조회 조건 준비 중…');
    try{const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body),signal:mine.signal});const d=await readJSON(r);if(!r.ok)throw Error(d.error||'조회 실패');if(ticket!==epoch||versionTicket!==version)return;
      snapshot=d;next=null;panelMinutes.clear();show(d.plan);evidence.hidden=false;
      document.querySelector('#query').textContent=d.query;document.querySelector('#snapshot').textContent='기준 시각 '+new Date(d.end_unix*1000).toLocaleString('ko-KR')+' · 지연 도착 로그는 새로 조회 시 반영될 수 있습니다.';
      more.textContent='원문 조회';more.disabled=false;info(d);pollCount(d.snapshot_id,ticket);
      say('assistant',d.summary);status(current.minutes>=1440?'24시간 이상: 자동 새로고침 꺼짐 · 현재 조건 새로 조회 버튼으로 갱신':'24시간 미만: Grafana 1분 갱신 · 근거/분석은 조회 시점 기준');
      loadPage();
    }catch(e){if(e.name!=='AbortError'&&ticket===epoch){status(e.message,true);say('assistant',e.message);}}
    finally{if(controller===mine){busy=false;document.querySelector('#submit').disabled=false;}}
  };
  show(current,false);
})();
