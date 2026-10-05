/* Analysis snapshot -> validated report -> CSV; never sends another model call for downloads. */
(() => {
  if (window.__sentinelAnalysisCSV) return;
  window.__sentinelAnalysisCSV = true;
  const form = document.querySelector('#chat-form');
  if (!form) { console.warn('Sentinel analysis: #chat-form not found'); return; }
  document.querySelector('#deep-analysis')?.remove();
  const button = document.createElement('button');
  button.id = 'deep-analysis'; button.type = 'button';
  button.textContent = '현재 조건 심층 분석';
  button.style.marginTop = '12px';
  const note = document.createElement('p');
  note.style.cssText = 'font-size:14px;line-height:1.6';
  note.textContent = '분석 범위는 상단 현재 조회 조건입니다. 최대 60건 표본을 분석합니다.';
  const progress = document.createElement('p');
  progress.setAttribute('role', 'status'); progress.style.fontSize = '14px';
  const results = document.createElement('section');
  results.id = 'analysis-results';
  form.append(button, note, progress);
  form.after(results);

  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      return await fetch(url, {...options, signal: controller.signal, cache: 'no-store'});
    } finally { clearTimeout(timer); }
  }
  async function json(url, options = {}) {
    const response = await request(url, options);
    const type = response.headers.get('content-type') || '';
    if (!type.includes('application/json')) throw Error(`API 응답 오류 (HTTP ${response.status}). gateway를 확인하세요.`);
    const data = await response.json();
    if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  function message(text) {
    if (typeof say === 'function') { say('assistant', text); return; }
    const p = document.createElement('pre');
    p.style.whiteSpace = 'pre-wrap'; p.textContent = text; results.append(p);
  }
  function evidenceView(data) {
    const details = document.createElement('details');
    const title = document.createElement('summary'); title.textContent = '이번 분석에 제공된 근거 로그';
    const pre = document.createElement('pre');
    pre.style.cssText = 'white-space:pre-wrap;overflow-wrap:anywhere;max-height:300px;overflow:auto;font-size:13px';
    pre.textContent = data.logs.map(row =>
      `[${row.id}] timestamp_ns=${row.timestamp_ns}\n` + JSON.stringify(row.labels || {}) + '\n' + row.line +
      (row.truncated ? '\n[원문 일부 생략]' : '')).join('\n\n') || '원문 표본이 없습니다.';
    details.append(title, pre); return details;
  }
  function completed(result, jobId) {
    const e = result.evidence;
    const format = n => new Date(n * 1000).toLocaleString('ko-KR', {timeZone:'Asia/Seoul'});
    message('[심층 분석]\n모델: ' + result.model + '\n' +
      `범위(KST): ${format(e.start_unix)} ~ ${format(e.end_unix)}\n` +
      `기간: 최근 ${typeof formatDuration === 'function' ? formatDuration(e.plan.minutes) : e.plan.minutes + '분'}\n` +
      `조건: ${JSON.stringify(Object.fromEntries(Object.entries(e.plan).filter(([key]) => key !== 'minutes')))}\n전체 ${e.total.toLocaleString()}건 / 표본 ${e.sample_count}건 / 대표 그룹 ${Math.min(30,result.group_count||0)}개\n${e.sampling}\n\n` + result.answer);
    results.replaceChildren();
    const download = document.createElement('button');
    download.type = 'button'; download.textContent = '분석 결과 CSV 다운로드';
    const info = document.createElement('p');
    info.style.fontSize = '13px';
    info.textContent = '완료 후 최대 15분 보관(메모리). agent 재시작·보관 한도 초과 시 사라집니다. 지금 저장하세요.';
    const state = document.createElement('p'); state.setAttribute('role','status');
    download.onclick = async () => {
      download.disabled = true;
      try {
        // Build the same-origin path locally rather than trusting a model-generated URL.
        const response = await request('/api/analysis/' + encodeURIComponent(jobId) + '/csv');
        if (!response.ok) {
          const data = await response.json(); throw Error(data.error || '다운로드 실패');
        }
        if (!(response.headers.get('content-type') || '').includes('text/csv')) throw Error('CSV 응답이 아닙니다.');
        const url = URL.createObjectURL(await response.blob());
        const a = document.createElement('a');
        a.href = url; a.download = `sentinel-analysis-${jobId}.csv`;
        document.body.append(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 60000);
        state.textContent = 'CSV 저장을 요청했습니다. 다운로드 폴더를 확인하세요.';
      } catch (error) { state.textContent = error.message; }
      finally { download.disabled = false; }
    };
    const detailButton=document.createElement('button');detailButton.type='button';detailButton.textContent='같은 표본 상세 설명';
    detailButton.onclick=async()=>{detailButton.disabled=true;await startAnalysis(true);detailButton.disabled=false;};
    const timing=document.createElement('p');timing.style.fontSize='13px';
    timing.textContent=result.timings?`소요 시간: 로그 ${(result.timings.collect_ms/1000).toFixed(1)}초 · 모델 ${((result.timings.bedrock_ms||0)/1000).toFixed(1)}초 · 전체 ${(result.timings.total_ms/1000).toFixed(1)}초`:'';
    results.append(download,detailButton,timing, info, state, evidenceView(e));
    progress.textContent = result.model_called
      ? `분석 완료 · 입력 ${result.usage.inputTokens ?? '?'} / 출력 ${result.usage.outputTokens ?? '?'} 토큰`
      : '원문 표본이 없어 모델을 호출하지 않았습니다.';
  }
  let previousJob=null, previousResult=null;
  async function startAnalysis(detail=false) {
    if (typeof current === 'undefined') { progress.textContent = '현재 조회 조건을 읽을 수 없습니다.'; return; }
    const plan = JSON.parse(JSON.stringify(detail && previousResult ? previousResult.evidence.plan : current));
    const question = (detail && previousResult ? previousResult.question : document.querySelector('#question')?.value.trim()) ||
      '현재 조회 범위의 오류와 공격 의심 정황을 분석하고 판단 불가 사항도 설명해줘.';
    if (question.length > 500) { progress.textContent = '질문은 500자 이하로 입력하세요.'; return; }
    button.disabled = true; progress.textContent = '분석 요청 중…';
    try {
      const started = await json('/api/analyze', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({plan, question,detail, ...(detail?{source_job_id:previousJob}:{snapshot_id:window.sentinelSnapshot?.()?.snapshot_id})})});
      const deadline = Date.now() + 15 * 60 * 1000;
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 3000));
        const result = await json('/api/analysis/' + encodeURIComponent(started.job_id));
        if (result.state === 'error') throw Error(result.error);
        if (result.state === 'done') { previousJob=started.job_id;previousResult=result;completed(result, started.job_id);if(started.cached)progress.textContent+=' · 저장된 결과 재사용';return; }
        progress.textContent = '로그 조회 → 표본 정리 → 모델 분석 → 결과 검증 · 현재: '+(result.stage || '대기') + (result.progress?.total_chunks ? ` (${result.progress.done_chunks}/${result.progress.total_chunks} 구간)` : '');
      }
      throw Error('화면 대기 시간이 초과되었습니다. 서버 작업은 취소되지 않았습니다. agent 로그를 확인하세요.');
    } catch (error) {
      progress.textContent = error.name === 'AbortError' ? 'API 연결 시간이 초과되었습니다.' : error.message;
    } finally { button.disabled = false; }
  };
  button.onclick=()=>startAnalysis(false);
  json('/api/config').then(config => {
    note.textContent = '분석 모델: ' + (config.analysis_model_id || '미설정') +
      ' · 상단 현재 조건을 사용합니다. 최대 60건 표본을 30개 이하 대표 그룹으로 요약합니다. 기본 발견 3개, 상세 설명은 별도 호출입니다.';
  }).catch(() => {});
})();
