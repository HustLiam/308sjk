'use strict';
const $ = id => document.getElementById(id);
const assetDialogs=['aml','library'].map(name=>$(name+'-dialog'));
for(const name of ['aml','library']){
  const dialog=$(name+'-dialog');
  $('open-'+name+'-details').onclick=()=>{
    dialog.showModal();dialog.querySelector('.asset-dialog-body').scrollTop=0;
    document.documentElement.classList.add('asset-dialog-open');
  };
  dialog.querySelector('[data-asset-close]').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>{
    if(!assetDialogs.some(item=>item.open))document.documentElement.classList.remove('asset-dialog-open');
  });
  dialog.addEventListener('click',event=>{
    if(event.target!==dialog)return;
    const rect=dialog.getBoundingClientRect();
    if(event.clientX<rect.left || event.clientX>rect.right || event.clientY<rect.top || event.clientY>rect.bottom)dialog.close();
  });
}
const ns = 'http://www.w3.org/2000/svg';
let state = null, config = null, working = false, selected = new Set(), timer = null, lastMessages = '', lastLogs = '';
let planning = Promise.resolve();
let lastAssetKey='';
const sceneViewers=new Map();
let runtimeState=null,runtimeTimer=null,runtimeLoading=false;
let motionTimer=null,motionLoading=false;
let studioData=null,studioLoading=false,hmiKey='',recordKey='';
let deviceSceneTask=null,deviceSceneRecord=null,pendingDeviceScene=null,deviceSyncVersion=0;
const knownDeviceScenes=new Map();
const deviceScenePrefix='device-scene:';
function deviceSceneFile(record,path){return '/device-scene-file?'+new URLSearchParams({task:record.id,path:'scene/'+path});}
function renderSimulationTasks(){
  const select=$('simulation-task'),jobs=state?.jobs.filter(j=>j.status==='final')||[];
  const key=JSON.stringify([jobs.map(j=>[j.id,j.source]),[...knownDeviceScenes],deviceSceneTask,deviceSceneRecord?.device_ir?.station]);
  if(select.dataset.options!==key){
    select.replaceChildren();
    const devices=new Map(knownDeviceScenes);if(deviceSceneTask&&!devices.has(deviceSceneTask))devices.set(deviceSceneTask,deviceSceneRecord?.device_ir?.station||deviceSceneRecord?.name||deviceSceneTask.slice(-8));
    for(const [id,name] of devices)select.append(new Option((id===deviceSceneTask&&!deviceSceneRecord?.model_frozen?'当前设备 · ':'冻结设备 · ')+name,deviceScenePrefix+id));
    for(const job of [...jobs].reverse())select.append(new Option((job.source==='external'?'外部导入':'生成产物')+' · '+job.id.slice(-8),job.id));
    select.dataset.options=key;
  }
  select.value=deviceSceneTask?deviceScenePrefix+deviceSceneTask:state?.workflow?.task||'';
}
function renderDeviceStudio(){
  if(!deviceSceneTask)return;
  const record=deviceSceneRecord,ready=!!record?.model_frozen,notice=$('studio-model-notice');
  $('studio-runtime-metrics').hidden=true;$('runtime-connection').hidden=true;$('import-simulation').hidden=true;
  const waiting=record?.device_ir?'当前 AML 已解析。请在设备场景页完成规则生成、实例化、装配、检查及冻结。':record?.parse_pending?'已更换 AML，正在重新解析设备描述。':'正在读取当前设备…';
  notice.hidden=false;notice.textContent=record?.sync_error?'当前设备不可用：'+record.sync_error:ready?'已同步当前 AML 的冻结模型。PLC 尚未关联，当前显示模型预览。':waiting;
  if(record?.source_sha256)notice.textContent=record.name+' · AML '+record.source_sha256.slice(0,8)+' · 设备版本 '+record.id.slice(-8)+'。'+notice.textContent;
  $('scene-status').textContent=ready?'模型已冻结':record?.sync_error?'模型不可用':'等待当前设备冻结';
  $('studio-view-title').textContent=ready?'冻结模型预览':'当前设备 · 待冻结';$('build-scene').textContent=ready?'已加载冻结模型':'请先生成并冻结设备';
  if(!ready){$('runtime-viewport').hidden=true;$('scene-empty').hidden=false;$('scene-empty').textContent=record?.sync_error||waiting;$('studio-view-state').textContent=record?.sync_error?'加载失败':'待冻结';}
  $('studio-task').textContent=(record?.device_ir?.station||record?.name||deviceSceneTask.slice(-8))+' · '+(ready?'冻结设备':'当前 AML');
  $('runtime-status').textContent='未关联 PLC';$('hmi-status').textContent='设备 IO · 只读';
  $('simulation-motion-state').textContent='未运行';$('studio-elapsed').textContent='运行时间 —';
  $('motion-error').hidden=true;$('runtime-operation').hidden=true;$('runtime-checks').hidden=true;
  $('runtime-logs').hidden=true;
  $('scene-download').hidden=!ready;$('scene-source').hidden=!ready;$('scene-library').hidden=!ready;
  if(ready){$('scene-download').href=deviceSceneFile(record,'scene.xml');$('scene-source').href=deviceSceneFile(record,'scene.physical.json');$('scene-library').href=deviceSceneFile(record,'model.freeze.json');}
  $('scene-source').textContent='设备场景 JSON';$('scene-library').textContent='模型冻结清单';
  $('scene-logs').hidden=false;$('scene-logs').textContent=(record?.logs||[]).map(row=>new Date(row.time*1000).toLocaleTimeString()+'  '+row.message).join('\n');
  const key=JSON.stringify([deviceSceneTask,record?.model_xml_sha256,record?.device_ir?.facts_sha256,record?.sync_error]);
  if(hmiKey!==key){
    for(const id of ['hmi-buttons','hmi-lamps','hmi-gauges','trace-signal','trace-chart'])$(id).replaceChildren();
    $('hmi-empty').hidden=!!record?.device_ir;$('hmi-empty').textContent='等待冻结设备 IO';
    if(record?.device_ir)assetTable($('hmi-gauges'),['信号','方向','类型','地址'],record.device_ir.io.map(p=>[p.name,globalThis.DeviceSceneText.direction(p.dir),p.type,p.address]));
    $('trace-chart').textContent='该设备尚无 PLC 采样数据';
    $('studio-history').textContent='该设备尚无 PLC 运行记录';
    renderVerification(null);$('verification-status').textContent='PLC 验收尚未运行';
    $('verification-summary').textContent='';hmiKey=key;recordKey='';studioData=null;
  }
  renderSimulationTasks();
}
async function syncDeviceScene(task,{expectedHash=null,expectedSourceHash=null}={}){
  if(!/^[0-9a-f]{32}$/.test(task||''))return;
  if(working||state?.motion?.active||state?.motion?.occupied){
    pendingDeviceScene={task,expectedHash,expectedSourceHash};$('studio-model-notice').hidden=false;
    $('studio-model-notice').textContent='已选择新的设备版本；当前仍在运行旧设备，停止后切换到新设备。';return;
  }
  const version=++deviceSyncVersion;deviceSceneTask=task;deviceSceneRecord=null;
  const view=sceneViewers.get('runtime-viewport');if(view)view.reset();
  $('runtime-viewport').hidden=true;$('scene-empty').hidden=false;$('scene-empty').textContent='正在加载冻结设备模型…';
  renderDeviceStudio();controls();
  try{
    let record=await api('/api/device-scene/state?'+new URLSearchParams({task}));
    if(version!==deviceSyncVersion)return;
    if(record.busy)record={...record,model_frozen:false};
    if(expectedSourceHash&&record.source_sha256!==expectedSourceHash)throw Error('设备来源与当前 AML 不一致，请重新选择设备。');
    if(record.model_frozen&&(!record.all_approved||!record.scene_ready||record.scene_kind!=='scene'||record.busy||record.error))throw Error('所选设备的冻结状态无效，请重新检查。');
    if(expectedHash&&!record.model_frozen)throw Error('所选设备尚未完成有效冻结，请在设备场景页完成审批、检查及冻结。');
    if(expectedHash&&expectedHash!==record.model_xml_sha256)throw Error('冻结模型版本已改变，请重新加载。');
    deviceSceneRecord=record;if(record.model_frozen)knownDeviceScenes.set(task,record.device_ir.station);
    localStorage.setItem('studio-device-scene',task);localStorage.removeItem('studio-frozen-scene');
    if(record.model_frozen)localStorage.setItem('device-scene-last-frozen',task);
    renderDeviceStudio();renderSceneViews();
  }catch(exc){if(version!==deviceSyncVersion)return;deviceSceneRecord={id:task,sync_error:exc.message};renderDeviceStudio();$('studio-view-state').textContent='加载失败';$('scene-empty').textContent=exc.message;}
  finally{if(version===deviceSyncVersion)controls();}
}
function leaveDeviceScene(){
  deviceSyncVersion++;deviceSceneTask=null;deviceSceneRecord=null;pendingDeviceScene=null;hmiKey='';recordKey='';
  localStorage.setItem('studio-device-scene','legacy');localStorage.removeItem('studio-frozen-scene');$('studio-model-notice').hidden=true;
  $('studio-runtime-metrics').hidden=false;$('import-simulation').hidden=false;
  $('scene-source').textContent='场景描述 JSON';$('scene-library').textContent='组件库';$('hmi-empty').textContent='生成 PLC 后加载操作面板';
  $('studio-view-title').textContent='实时仿真';$('build-scene').textContent='生成仿真场景';
}
async function restoreDeviceScene(){
  let selectedSource;try{selectedSource=JSON.parse(localStorage.getItem('device-scene-selection')||'null');}catch{}
  // Migrate old browsers by preferring their current authoring task over an
  // unrelated frozen model left by the previous verification-only UI.
  const saved=localStorage.getItem('studio-device-scene')||(selectedSource?(selectedSource.task||'pending'):localStorage.getItem('device-scene-task'))||localStorage.getItem('studio-frozen-scene');
  if(saved==='legacy'){
    const last=localStorage.getItem('device-scene-last-frozen');
    if(last)try{const record=await api('/api/device-scene/state?'+new URLSearchParams({task:last}));if(record.model_frozen&&record.all_approved)knownDeviceScenes.set(last,record.device_ir.station);renderSimulationTasks();}catch{}
    return;
  }
  if(selectedSource&&(!saved||saved==='pending'||saved===selectedSource.task))return followDeviceSource(selectedSource);
  const task=saved||localStorage.getItem('device-scene-task')||localStorage.getItem('device-scene-last-frozen');
  if(!task)return;
  await syncDeviceScene(task);
}
function followDeviceSource(source){
  if(!source)return;
  localStorage.setItem('studio-device-scene',source.task||'pending');
  if(source.task)return syncDeviceScene(source.task,{expectedHash:source.model_frozen?source.model_xml_sha256:null,expectedSourceHash:source.source_sha256});
  if(working||state?.motion?.active||state?.motion?.occupied){pendingDeviceScene={source};$('studio-model-notice').hidden=false;$('studio-model-notice').textContent='已更换 AML；当前仍在运行旧设备，停止后重新加载。';return;}
  deviceSyncVersion++;deviceSceneTask='pending';deviceSceneRecord={name:source.name,parse_pending:true,sync_error:source.error||null};pendingDeviceScene=null;
  sceneViewers.get('runtime-viewport')?.reset();hmiKey='';renderDeviceStudio();controls();
}
function applyPendingDeviceScene(){const pending=pendingDeviceScene;if(!pending)return;pendingDeviceScene=null;if(pending.source)followDeviceSource(pending.source);else syncDeviceScene(pending.task,pending);}
window.addEventListener('device-scene-selected',event=>followDeviceSource(event.detail));
window.addEventListener('device-scenes-retired',event=>{
  const retired=new Set(event.detail?.task_ids||[]);for(const id of retired)knownDeviceScenes.delete(id);
  if(retired.has(pendingDeviceScene?.task)||retired.has(pendingDeviceScene?.source?.task))pendingDeviceScene=null;
  if(retired.has(deviceSceneTask)){
    leaveDeviceScene();sceneViewers.get('runtime-viewport')?.reset();
    if(studioData)renderStudio();
    else{for(const id of ['hmi-buttons','hmi-lamps','hmi-gauges'])$(id).replaceChildren();$('hmi-empty').hidden=false;$('scene-empty').hidden=false;$('studio-task').textContent='尚未选择 PLC 场景';}
    controls();
  }
  renderSimulationTasks();
});
window.addEventListener('device-scene-frozen',event=>{
  let selectedSource;try{selectedSource=JSON.parse(localStorage.getItem('device-scene-selection')||'null');}catch{}
  if(selectedSource?.task&&selectedSource.task!==event.detail?.task)return;
  syncDeviceScene(event.detail?.task,{expectedHash:event.detail?.model_xml_sha256});
});
window.addEventListener('storage',event=>{if(event.key==='device-scene-selection'&&event.newValue){try{followDeviceSource(JSON.parse(event.newValue));}catch{}}});
let clockOffset=0;
function duration(seconds){
  const total=Math.max(0,Math.floor(seconds||0));
  return (total>=3600?String(Math.floor(total/3600)).padStart(2,'0')+':':'')+
    String(Math.floor(total/60)%60).padStart(2,'0')+':'+String(total%60).padStart(2,'0');
}
function elapsed(record){
  if(!record)return 0;
  if(record.finished_at)return Number(record.elapsed_seconds??((Date.parse(record.finished_at)-Date.parse(record.started_at))/1000));
  return Math.max(0,(Date.now()+clockOffset-Date.parse(record.started_at))/1000);
}
function renderTimers(){
  const job=state?.jobs.filter(job=>job.source!=='external').at(-1),timing=job?.timing;
  $('generation-timers').hidden=!timing;
  if(timing){
    $('generation-elapsed').textContent=duration(elapsed(timing.generation));
    const attempts=timing.simulation||[];
    $('acceptance-elapsed').textContent=attempts.length?duration(attempts.reduce((sum,row)=>sum+elapsed(row),0))+' · '+attempts.length+' 次':'未开始';
  }
}
setInterval(renderTimers,250);
function showStudioTab(name){
  for(const tab of ['result','trace','logs','history']){
    $('studio-'+tab+'-pane').hidden=tab!==name;
    $('studio-'+tab+'-tab').setAttribute('aria-selected',String(tab===name));
  }
  if(name==='trace')renderTrace();
}
for(const tab of ['result','trace','logs','history'])$('studio-'+tab+'-tab').onclick=()=>showStudioTab(tab);
async function refreshStudio(){
  if(deviceSceneTask){renderDeviceStudio();return;}
  if(!state || studioLoading || $('workflow-panel-2').hidden)return;
  const ident=state.id,task=state.workflow?.task;studioLoading=true;
  try{
    const data=await api('/api/studio/state?session='+encodeURIComponent(ident));
    if(deviceSceneTask || state.id!==ident || state.workflow?.task!==task)return;
    studioData=data;renderStudio();controls();
  }catch(exc){$('hmi-status').textContent='读取失败';$('verification-summary').textContent=exc.message;}
  finally{studioLoading=false;}
}
async function sendSignal(element){
  if(!studioData?.writable || working)return;
  working=true;controls();
  try{await api('/api/studio/signal',{session:state.id,revision:state.revision,task:state.workflow.task,signal:element.signal,value:element.type==='trigger'?true:!studioData.values[element.signal]});error();}
  catch(exc){error(exc.message);}
  finally{working=false;controls();setTimeout(refreshStudio,200);}
}
function renderStudio(){
  const data=studioData,elements=data.panel.elements,key=JSON.stringify([data.task,elements]);
  if(key!==hmiKey){
    for(const id of ['hmi-buttons','hmi-lamps','hmi-gauges'])$(id).replaceChildren();
    const gauges={};
    for(const e of elements){
      const node=document.createElement(e.kind==='BUTTON'?'button':'div');node.dataset.signal=e.signal;node.title=e.signal+' · '+e.address;
      if(e.kind==='BUTTON'){node.textContent=e.label;node.onclick=()=>sendSignal(e);$('hmi-buttons').append(node);}
      else if(e.kind==='LAMP'){
        const dot=document.createElement('i'),label=document.createElement('span');label.textContent=e.label;node.append(dot,label);$('hmi-lamps').append(node);
      }else{
        const axis=/^([xyz])_/.exec(e.signal)?.[1]?.toUpperCase()||'其他';
        if(!gauges[axis]){const group=document.createElement('details');group.open=axis!=='其他';const title=document.createElement('summary');title.textContent=axis==='其他'?axis:axis+' 轴';group.append(title);gauges[axis]=group;$('hmi-gauges').append(group);}
        const label=document.createElement('span'),value=document.createElement('strong');label.textContent=e.label;node.append(label,value);gauges[axis].append(node);
      }
    }
    $('hmi-empty').hidden=!!elements.length;
    const old=$('trace-signal').value;$('trace-signal').replaceChildren();
    for(const e of elements){const option=document.createElement('option');option.value=e.signal;option.textContent=e.label+' · '+e.signal;$('trace-signal').append(option);}
    for(const axis of ['x','y','z']){const option=document.createElement('option');option.value='physical_'+axis;option.textContent=axis.toUpperCase()+' 轴实际位置';$('trace-signal').append(option);}
    if(elements.some(e=>e.signal===old))$('trace-signal').value=old;
    else if(elements.some(e=>e.signal==='x_fb'))$('trace-signal').value='x_fb';
    hmiKey=key;
  }
  for(const e of elements){
    const node=[...document.querySelectorAll('[data-signal]')].find(n=>n.dataset.signal===e.signal);if(!node)continue;
    const value=data.values[e.signal];
    if(e.kind==='BUTTON'){node.disabled=!data.writable || working;node.setAttribute('aria-pressed',String(value===true));}
    else if(e.kind==='LAMP'){node.classList.toggle('on',value===true);node.title=e.label+' · '+(value===undefined?'—':value?'ON':'OFF');}
    else node.querySelector('strong').textContent=value===undefined?'—':value+(e.unit?' '+e.unit:'');
  }
  const recordsKey=JSON.stringify([data.task,data.records,data.history,state.motion?.active,state.motion?.run_id]);
  if(recordsKey!==recordKey){
    if(state.motion?.active && state.motion.run_id!==data.records[0]?.id){
      renderVerification(null);$('verification-status').textContent=state.motion.mode==='manual'?'手动调试中':'正在运行验收';
    }else renderVerification(data.records[0]);
    $('studio-history').replaceChildren();
    if(!data.history.length)$('studio-history').textContent='暂无运行记录';
    for(const record of data.history){
      const row=document.createElement('div');row.className='history-row';
      const label=document.createElement('span');label.textContent=new Date(record.created_at*1000).toLocaleString()+' · '+({manual:'手动调试',auto:'模型运行',verify:'验收'})[record.mode]+' · '+({PASS:'通过',FAIL:'未通过',UNVERIFIED:'未完成验收'})[record.verdict];
      label.textContent+=' · 用时 '+duration(record.timing?.elapsed??record.duration_s);
      const button=document.createElement('button');button.textContent='查看结果';button.onclick=()=>{renderVerification(record);showStudioTab('result');};row.append(label,button);$('studio-history').append(row);
    }
    recordKey=recordsKey;
  }
  if(!$('studio-trace-pane').hidden)renderTrace();
}
function renderVerification(record){
  $('verification-results').replaceChildren();$('verification-downloads').replaceChildren();
  $('verification-status').textContent=record?({PASS:'验收通过',FAIL:'验收未通过',UNVERIFIED:'未完成验收'})[record.verdict]:'暂无验收结果';
  $('verification-status').className=record?.verdict==='PASS'?'passed':record?.verdict==='FAIL'?'runtime-failure':'';
  $('verification-summary').textContent=record?record.samples+' 个采样 · 仿真用时 '+duration(record.timing?.elapsed??record.duration_s):'';
  if(!record)return;
  for(const result of record.details){
    const row=document.createElement('details');row.className='verification-row';
    const summary=document.createElement('summary');summary.textContent=(result.pass?'✓ ':'✕ ')+result.desc;
    const evidence=document.createElement('p');evidence.textContent=result.evidence;row.append(summary,evidence);$('verification-results').append(row);
  }
  if(record.error){const p=document.createElement('p');p.className='runtime-failure';p.textContent=record.error;$('verification-results').append(p);}
  for(const [file,label] of [['verdict.json','验收报告'],['trace.json','采样数据'],['report.md','诊断记录']]){
    const a=document.createElement('a');a.href='/file/'+state.id+'/'+record.task+'/simulation/verifications/'+record.id+'/'+file;a.textContent=label;a.download='';$('verification-downloads').append(a);
  }
  if(record.physical_samples){const a=document.createElement('a');a.href='/file/'+state.id+'/'+record.task+'/simulation/verifications/'+record.id+'/physical_trace.json';a.textContent='物理反馈';a.download='';$('verification-downloads').append(a);}
}
function renderTrace(){
  const signal=$('trace-signal').value,trace=(studioData?.trace||[]).filter(s=>s[signal]!==undefined),host=$('trace-chart');host.replaceChildren();
  if(!trace.length){host.textContent='暂无采样数据';return;}
  const t0=trace[0].t,t1=trace.at(-1).t,values=trace.map(s=>Number(s[signal])),lo=Math.min(...values),hi=Math.max(...values);
  const svg=svgNode('svg',{viewBox:'0 0 760 210',role:'img','aria-label':signal+' 实际采样趋势'});
  svg.append(svgNode('path',{d:'M52 15 V175 H745',fill:'none',stroke:'#b6cbd0'}));
  const points=trace.map((s,i)=>[52+(s.t-t0)/Math.max(.001,t1-t0)*690,175-(values[i]-lo)/Math.max(1,hi-lo)*150]);
  svg.append(svgNode('polyline',{points:points.map(p=>p.join(',')).join(' '),fill:'none',stroke:'#008592','stroke-width':2}));
  for(const [x,y,text] of [[7,24,hi],[7,175,lo],[52,200,t0.toFixed(1)+' s'],[690,200,t1.toFixed(1)+' s']]){const node=svgNode('text',{x,y,'font-size':12,fill:'#66808b'});node.textContent=text;svg.append(node);}
  host.append(svg);
}
$('trace-signal').onchange=renderTrace;
function renderMotion(status){
  if(!state)return;
  state.motion=status;
  if(pendingDeviceScene&&!status.active&&!status.occupied)applyPendingDeviceScene();
  if(deviceSceneTask){renderDeviceStudio();controls();return;}
  const names={manual:'手动调试',not_started:'未启动',starting:'连接中',enabling:'使能中',running:'绘制中',stopped:'已停止',completed:'绘制完成',failed:'运动失败'};
  $('simulation-motion-state').textContent=status.occupied?'其他会话正在运行':(names[status.status]||'未启动');
  $('motion-error').hidden=!status.error;$('motion-error').textContent=status.error||'';
  if(status.verification_error){$('motion-error').hidden=false;$('motion-error').textContent=status.verification_error;}
  if(state.workflow_error){$('motion-error').hidden=false;$('motion-error').textContent=state.workflow_error;}
  for(const [id,panel] of [['runtime-viewport',2]]){
    const view=sceneViewers.get(id);
    const frame=status.task===state.workflow?.task?status.sequence:0;
    if(view)view.active=status.task===state.workflow?.task?status.active:false;
  }
  $('studio-elapsed').textContent='运行时间 '+(Number(status.elapsed||0).toFixed(1))+' s';
  $('hmi-status').textContent=status.active?(status.mode==='manual'?'可操作':'自动运行 · 只读'):'运行快照';
  controls();
}
async function refreshMotion(){
  if(deviceSceneTask || motionLoading || !state)return;
  motionLoading=true;const ident=state.id;
  try{const result=await api('/api/motion/status?session='+encodeURIComponent(ident));if(state.id===ident){renderMotion(result);refreshStudio();}}
  catch(exc){$('motion-error').hidden=false;$('motion-error').textContent='运动状态读取失败：'+exc.message;}
  finally{motionLoading=false;scheduleMotion();}
}
function scheduleMotion(){
  if(motionTimer)clearTimeout(motionTimer);motionTimer=null;
  if(!deviceSceneTask && state && (state.motion?.active || state.motion?.occupied || !$('workflow-panel-2').hidden))motionTimer=setTimeout(refreshMotion,650);
}
function showAgentTab(tab) {
  for(const name of ['chat','progress','artifacts']){
    $('agent-'+name+'-pane').hidden=name!==tab;
    $('agent-'+name+'-tab').setAttribute('aria-selected',String(name===tab));
  }
}
for(const name of ['chat','progress','artifacts'])$('agent-'+name+'-tab').onclick=()=>showAgentTab(name);
function showWorkflow(index) {
  for(let i=1;i<=3;i++){
    $('workflow-panel-'+i).hidden=i!==index;
    $('workflow-tab-'+i).setAttribute('aria-selected',String(i===index));
  }
  renderSceneViews();
  if(runtimeTimer)clearTimeout(runtimeTimer);runtimeTimer=null;
  if(index===2 && state)refreshRuntime();
  if(index===2 && deviceSceneTask)syncDeviceScene(deviceSceneTask,{expectedHash:deviceSceneRecord?.model_frozen?deviceSceneRecord.model_xml_sha256:null});
  if(index===3)window.DeviceScenes?.activate();
  if(state){refreshMotion();refreshStudio();}
}
for(let i=1;i<=3;i++)$('workflow-tab-'+i).onclick=()=>showWorkflow(i);
async function refreshRuntime() {
  if(!state || runtimeLoading)return;
  const ident=state.id;runtimeLoading=true;$('refresh-runtime').disabled=true;
  try{
    const status=await api('/api/runtime/status?session='+encodeURIComponent(state.id));
    if(state.id!==ident)return;
    runtimeState=status;
    $('deploy-service-state').textContent=status.service==='online'?'在线':'离线';
    $('plc-runtime-state').textContent=({RUNNING:'运行中',STOPPED:'已停止',COMPILING:'编译中',BUSY:'操作中',UNREACHABLE:'不可达'})[status.runtime]||'未获取';
    $('plc-program-state').textContent=status.program?(status.program+(status.prog_id!==null?' · '+status.prog_id:'')):'—';
    $('runtime-connection').hidden=!!deviceSceneTask||(status.service==='online' && !status.error && !status.deployment_error && status.control==='connected');
    $('runtime-connection').textContent=status.service!=='online'?'lx 部署服务离线':status.error?'OpenPLC Runtime 不可达':status.deployment_error|| (status.control!=='connected'?'部署服务需更新以支持启停与日志':'');
  }catch(exc){runtimeState=null;error('读取 Runtime 状态失败：'+exc.message);}
  finally{
    runtimeLoading=false;$('refresh-runtime').disabled=false;controls();
    if(runtimeTimer)clearTimeout(runtimeTimer);runtimeTimer=null;
    if(!$('workflow-panel-2').hidden)runtimeTimer=setTimeout(refreshRuntime,3000);
  }
}
function renderRuntimeOperation(){
  const failed=state?.runtime_deployment;
  const operation=failed?.task===state?.workflow?.task && failed.status==='failed' && state?.runtime_action?.status!=='running'?failed:state?.runtime_action;
  const current=operation && (operation.action!=='deploy' || operation.task===state.workflow?.task);
  $('runtime-operation').hidden=!current;$('runtime-operation-steps').replaceChildren();
  if(!current)return;
  const names={deploy:'部署并启动 PLC',start:'启动 Runtime',stop:'停止 Runtime'};
  const statuses={running:'进行中…',success:'完成',failed:'失败',interrupted:'结果待确认'};
  $('runtime-operation-title').textContent=names[operation.action]+' · '+(statuses[operation.status]||operation.status);
  const steps={
    'compiler configuration':['编译环境检查成功','编译环境检查失败'],
    'validate & convert XML':['XML 文件读取、校验与转换成功','XML 文件读取、校验或转换失败'],
    'write program.st':['PLC 控制程序保存成功','PLC 控制程序保存失败'],
    'login runtime':['OpenPLC Runtime 连接成功','OpenPLC Runtime 连接失败'],
    'stop before upload':['原有 PLC 程序已停止','原有 PLC 程序停止失败'],
    'upload & trigger compile':['PLC 程序上传成功，开始编译','PLC 程序上传失败'],
    'compile (matiec)':['PLC 程序编译成功','PLC 程序编译失败'],
    'start PLC':['PLC 程序启动成功','PLC 程序启动失败']
  };
  for(const step of operation.steps||[]){
    const p=document.createElement('p');
    const text=(steps[step.step]||['流程步骤已完成','流程步骤未通过'])[step.ok?0:1];
    p.textContent=(step.ok?'✓ ':'✕ ')+text;$('runtime-operation-steps').append(p);
  }
  for(const message of operation.errors||[]){const p=document.createElement('p');p.className='runtime-failure';p.textContent=message;$('runtime-operation-steps').append(p);}
}
async function controlRuntime(name){
  showStudioTab('logs');
  if(await action('/api/runtime/'+name,{task:state.workflow?.task}))refreshRuntime();
}
function renderSceneViews() {
  if(deviceSceneTask){
    const host=$('runtime-viewport'),record=deviceSceneRecord;
    if(!record?.model_frozen){host.hidden=true;return;}
    if($('workflow-panel-2').hidden)return;
    let view=sceneViewers.get('runtime-viewport');
    try{
      if(!view){view=new SceneViewer(host);sceneViewers.set('runtime-viewport',view);}
      const source='/api/device-scene/view?'+new URLSearchParams({task:record.id,v:record.model_xml_sha256});
      view.active=false;view.live=null;view.onSelect=null;view.setSelection([]);
      if(view.source!==source){host.hidden=true;$('scene-empty').hidden=false;$('scene-empty').textContent='正在加载冻结设备模型…';$('studio-view-state').textContent='加载中';view.camera=[120,-32,1.05,0,0];}
      view.load(source).then(()=>{
        if(deviceSceneTask!==record.id||deviceSceneRecord?.model_xml_sha256!==record.model_xml_sha256||view.source!==source)return;
        const ready=!view.status.textContent&&!!view.current?.transforms?.length;
        host.hidden=!ready;$('scene-empty').hidden=ready;$('studio-view-state').textContent=ready?'已加载冻结模型':view.status.textContent==='加载中…'?'加载中':'加载失败';
        if(!ready)$('scene-empty').textContent=view.status.textContent||'模型没有可显示的几何';
      });
    }catch(exc){host.hidden=true;$('scene-empty').hidden=false;$('scene-empty').textContent=exc.message;$('studio-view-state').textContent='加载失败';}
    return;
  }
  const scene=state?.workflow?.simulation;
  for(const [id,panel] of [['runtime-viewport',2]]){
    const host=$(id);host.hidden=!scene;
    if(!scene || $('workflow-panel-'+panel).hidden)continue;
    try{
      let view=sceneViewers.get(id);
      if(!view){view=new SceneViewer(host);sceneViewers.set(id,view);}
      view.load(scene.view_url,scene.live_url);
    }catch(exc){host.textContent=exc.message;}
  }
}
function error(message='') { $('error').textContent=message; $('error').hidden=!message; }
async function api(path, data) {
  const options = data===undefined ? {} : {method:'POST',headers:{'Content-Type':'application/json','X-Agent-Token':config.token},body:JSON.stringify(data)};
  const response = await fetch(path, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || `请求失败 (${response.status})`);
  return result;
}
function svgNode(tag, attrs={}) { const node=document.createElementNS(ns,tag);for(const [key,value] of Object.entries(attrs))node.setAttribute(key,String(value));return node; }
function assetTable(container, headers, rows) {
  const table=document.createElement('table'),thead=document.createElement('thead'),tr=document.createElement('tr');
  for(const header of headers){const th=document.createElement('th');th.textContent=header;tr.append(th);}thead.append(tr);table.append(thead);
  const tbody=document.createElement('tbody');
  for(const row of rows){const tr=document.createElement('tr');for(const cell of row){const td=document.createElement('td');td.textContent=Array.isArray(cell)?cell.join(' / '):String(cell??'—');tr.append(td);}tbody.append(tr);}table.append(tbody);container.append(table);
}
function renderAssets() {
  const assets=state?.assets;if(!assets)return;
  const key=JSON.stringify([assets.aml.id,assets.library.id,assets.svg?.id,assets.aml.name,assets.library.name,assets.svg?.name,assets.library.parts]);
  if(key===lastAssetKey)return;lastAssetKey=key;
  const {aml,library,svg}=assets;
  $('aml-name').textContent=aml.name;$('aml-download').href=aml.url;
  $('aml-details').replaceChildren();
  assetTable($('aml-details'),['设备','类型','角色'],(state.device_model.devices||[]).map(d=>[d.name,d.type,d.role]));
  assetTable($('aml-details'),['轴','行程','单位','速度上限'],aml.axes.map(a=>[a.axis,a.stroke,a.unit,a.limits?.vmax]));
  assetTable($('aml-details'),['IO','类型','地址','方向'],aml.io_points.map(p=>[p.name,p.type,p.address,p.dir]));
  $('library-name').textContent=library.title+' · v'+library.version;
  const types=Object.entries(library.contract.types);
  $('library-download').href=library.url;$('contract-download').href=library.contract_url;$('physics-download').href=library.physics_url;
  $('contract-version').textContent='v'+library.contract.contract_version+(library.parts?.contract?' · '+library.parts.contract:'');
  $('physics-version').textContent='SI · v'+library.physics.schema_version+(library.parts?.physics?' · '+library.parts.physics:'');
  $('contract-details').replaceChildren();
  for(const [name,definition] of types){
    const details=document.createElement('details'),summary=document.createElement('summary');details.className='component-definition';summary.textContent=name+(definition.backends.mjcf?' · MuJoCo':' · 其他后端');details.append(summary);
    assetTable(details,['接口','方向','类型'],definition.quantities.map(q=>[q.name,q.direction,q.dtype]));
    assetTable(details,['参数','类型','范围 / 默认'],definition.params.map(p=>[p.name,p.kind,(p.minimum!==undefined?`${p.exclusive_min?'>':'≥'}${p.minimum} `:'')+(p.maximum!==undefined?'≤'+p.maximum+' ':'')+'默认 '+JSON.stringify(p.default)]));
    $('contract-details').append(details);
  }
  $('physics-details').replaceChildren();
  const labels={mass:'滑块质量 / kg',kp:'位置执行器增益',damping:'关节阻尼',base_half:'底座半尺寸 / m',saddle_half:'鞍座半尺寸 / m',head_half:'滑座半尺寸 / m',slider_half:'Z 滑块半尺寸 / m',pen_length:'笔长度 / m',pen_radius:'笔半径 / m'};
  assetTable($('physics-details'),['参数','数值（向量按 X/Y/Z）'],Object.entries(library.physics.gantry).map(([k,v])=>[labels[k]||k,v]));
  $('filename').textContent=svg?svg.name:'拖入 SVG 文件，或从电脑选择。';
  $('svg-status').textContent=svg?'已校验':'待导入';
  $('svg-download').hidden=!svg;if(svg)$('svg-download').href=svg.url;
}
function currentSettings() {
  const read=id=>{const input=$(id);if(!input.checkValidity())throw new Error('请填写有效的绘图参数');return Number(input.value);};
  return {width:read('width'),height:read('height'),center:[read('center-x'),read('center-y')],tolerance:read('tolerance'),max_points:read('max-points'),flip_y:$('flip-y').checked};
}
function setSettings(settings) {
  if(!settings?.center)return;
  for(const key of ['width','height','tolerance'])$(key).value=settings[key];
  $('center-x').value=settings.center[0];$('center-y').value=settings.center[1];$('max-points').value=settings.max_points;$('flip-y').checked=settings.flip_y;
}
function languageMode(){return state?.drawing_mode==='language';}
function languageMatches(){return $('drawing-description').value.trim()===(state?.drawing_text||'').trim();}
function selectionMatches() { return !!state && (languageMode() || [...selected].sort((a,b)=>a-b).join(',')===state.selected_paths.join(',')); }
function settingsMatch() {if(languageMode())return languageMatches();try{return !!state && JSON.stringify(currentSettings())===JSON.stringify({width:state.settings.width,height:state.settings.height,center:state.settings.center,tolerance:state.settings.tolerance,max_points:state.settings.max_points,flip_y:state.settings.flip_y});}catch{return false;}}
function controls() {
  const motionBusy=!!state?.motion?.active || !!state?.motion?.occupied;
  const busy=working || !!state?.busy || motionBusy;
  const disabled=!state?.assets.svg || busy;
  for(const id of ['select-all','select-none','invert','apply','send','message'])$(id).disabled=disabled;
  $('send').disabled=languageMode()?(!state || busy):(disabled || !selected.size || !state?.trajectory || !selectionMatches());
  $('message').disabled=languageMode()?(!state || busy):(disabled || !selected.size);
  const trajectoryReady=!!state?.trajectory && (languageMode() || !!selected.size) && selectionMatches() && settingsMatch();
  $('generate').disabled=busy || !state?.spec || !trajectoryReady;
  const stoppable=!!state?.can_stop_generation;
  $('generate').hidden=stoppable;
  $('stop-generation').hidden=!stoppable;
  $('stop-generation').disabled=working || state?.phase==='stopping';
  $('stop-generation').textContent=state?.phase==='stopping'?'停止中…':'停止生成';
  for(const id of ['language-source','svg-source','drawing-description','plan-language','square-example','circle-example'])$(id).disabled=!state || busy;
  $('file').disabled=busy;$('example').disabled=busy;
  for(const id of ['aml-file','library-file','contract-file','physics-file'])$(id).disabled=!state || busy;
  $('aml-file').disabled ||= !!window.DeviceScenes?.isBusy();
  for(const input of $('parameters').querySelectorAll('input'))input.disabled=disabled;
  for(const input of $('paths').querySelectorAll('input'))input.disabled=disabled;
  $('preview-tab').disabled=!state?.preview_url || !trajectoryReady;
  $('build-scene').disabled=busy || !state?.workflow?.can_build;
  $('clear-context').disabled=!state || busy;
  $('default-process').disabled=busy || !trajectoryReady;
  const runtimeBusy=busy;
  const available=runtimeState?.service==='online' && ['RUNNING','STOPPED'].includes(runtimeState.runtime);
  const supports=name=>runtimeState?.capabilities?.includes(name);
  $('deploy-runtime').disabled=runtimeBusy || !available || !supports('deploy') || !state?.workflow?.can_build;
  $('start-runtime').disabled=runtimeBusy || !available || !supports('start') || runtimeState.runtime==='RUNNING';
  $('stop-runtime').disabled=runtimeBusy || !available || !supports('stop') || runtimeState.runtime==='STOPPED';
  $('read-runtime-logs').disabled=!available || !supports('logs');
  $('start-motion').disabled=busy || runtimeState?.runtime!=='RUNNING' || !runtimeState?.deployment_matches || !state?.workflow?.can_build || !state?.workflow?.simulation?.runtime_ready;
  $('stop-motion').disabled=working || !state?.motion?.active;
  $('manual-motion').disabled=$('start-motion').disabled;
  $('start-motion').disabled ||= !state?.workflow?.verification_configured;
  $('verify-motion').disabled=$('start-motion').disabled;
  $('verify-motion').textContent=state?.workflow?.verification_configured?'运行验收':'未配置验收';
  $('import-simulation').disabled=busy || !state;
  $('simulation-task').disabled=busy;
  for(const button of $('hmi-buttons').querySelectorAll('button'))button.disabled=!studioData?.writable || working;
  if(deviceSceneTask){
    for(const id of ['build-scene','deploy-runtime','start-runtime','stop-runtime','read-runtime-logs','start-motion','manual-motion','verify-motion']){$(id).disabled=true;$(id).title=deviceSceneRecord?.model_frozen?'该冻结模型尚未关联 PLC 任务':'请先生成并冻结当前 AML 的设备模型';}
    $('verify-motion').textContent=deviceSceneRecord?.model_frozen?'未关联 PLC':'待冻结当前设备';
  }else for(const id of ['build-scene','deploy-runtime','start-runtime','stop-runtime','read-runtime-logs','start-motion','manual-motion','verify-motion'])$(id).removeAttribute('title');
  if(pendingDeviceScene)for(const id of ['build-scene','deploy-runtime','start-runtime','start-motion','manual-motion','verify-motion'])$(id).disabled=true;
  for(const node of $('outlines').querySelectorAll('[data-path]')){
    const chosen=selected.has(Number(node.dataset.path));
    if(node.classList.contains('outline'))node.classList.toggle('selected',chosen);
    node.setAttribute('aria-pressed',String(chosen));
  }
}
function drawCatalog() {
  $('outlines').replaceChildren();$('paths').replaceChildren();$('empty').hidden=!!state?.assets.svg;
  if(!state)return;
  for(const path of state.catalog.paths){
    const d=path.points.map((p,i)=>`${i?'L':'M'}${p[0]},${p[1]}`).join(' ');
    const title=`路径 ${path.id}`;
    const visible=svgNode('path',{d,class:'outline', 'data-path':path.id});
    const hit=svgNode('path',{d,class:'hit',role:'button',tabindex:0,'data-path':path.id,'aria-label':title});
    const label=svgNode('title');label.textContent=title;hit.append(label);
    hit.onclick=()=>togglePath(path.id);
    hit.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();togglePath(path.id);}};
    hit.onmouseenter=()=>visible.classList.add('hover');hit.onmouseleave=()=>visible.classList.remove('hover');
    $('outlines').append(visible,hit);
    if(path.points.length){const p=path.points[0],number=svgNode('text',{x:p[0]+7,y:p[1]-7,class:'path-number'});number.textContent=path.id;$('outlines').append(number);}
    const item=document.createElement('label');item.className='path-item';
    const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.checked=selected.has(path.id);checkbox.dataset.path=path.id;checkbox.setAttribute('aria-label',title);checkbox.onchange=()=>togglePath(path.id);
    const name=document.createElement('span');name.textContent=title;name.title=title;
    item.append(checkbox,name);$('paths').append(item);
  }
}
function showTab(preview) {
  if(preview && $('preview-tab').disabled)return;
  $('selection-view').hidden=preview;$('preview-view').hidden=!preview;
  $('select-tab').setAttribute('aria-selected',String(!preview));$('preview-tab').setAttribute('aria-selected',String(preview));
}
function render(next, replaceSelection=false) {
  const previousJob=state?.jobs.at(-1);
  const newFile=state?.id!==next.id;
  const finishedRuntime=state?.runtime_action?.status==='running' && next.runtime_action?.status!=='running';
  const svgChanged=state?.assets.svg?.id!==next.assets.svg?.id;
  const modeChanged=state?.drawing_mode!==next.drawing_mode;
  state=next;
  clockOffset=state.server_time?Date.parse(state.server_time)-Date.now():0;
  renderTimers();
  const timedJobs=state.jobs.filter(job=>job.timing);
  $('generation-history').hidden=!timedJobs.length;
  $('generation-history-list').replaceChildren();
  for(const job of timedJobs.slice(-20).reverse()){
    const row=document.createElement('p');
    const simulations=job.timing.simulation||[];
    row.textContent=new Date(job.timing.generation.started_at).toLocaleString()+' · '+
      ({running:'进行中',final:'验收通过',cancelled:'已停止',failed:'失败',best_effort:'未通过'})[job.status]+' · 生成 '+
      duration(elapsed(job.timing.generation))+' · 仿真 '+(simulations.length?duration(simulations.reduce((sum,item)=>sum+elapsed(item),0)):'未开始');
    $('generation-history-list').append(row);
  }
  if(newFile){runtimeState=null;$('runtime-logs').hidden=true;$('runtime-logs').textContent='';}
  if(newFile || replaceSelection || modeChanged){selected=new Set(state.selected_paths);setSettings(state.settings);$('drawing-description').value=state.drawing_text||'';}
  if(newFile || svgChanged){drawCatalog();showTab(false);lastMessages='';lastLogs='';}
  localStorage.setItem('svg-workbench-session',state.id);
  renderAssets();
  $('language-source-panel').hidden=!languageMode();$('svg-source-panel').hidden=languageMode();
  $('language-source').setAttribute('aria-pressed',String(languageMode()));$('svg-source').setAttribute('aria-pressed',String(!languageMode()));
  $('select-tab').hidden=languageMode();
  $('language-summary').hidden=!languageMode() || !state.trajectory;
  $('language-summary').textContent=state.drawing_summary||'';
  $('message').placeholder=languageMode()?'例如：画一个正方形，边长40；按启动按钮绘制，完成后抬笔。':'输入工艺要求，或使用默认工艺';
  const phases={draft:'待确认',understanding:'整理需求中',ready:'可生成',generating:'生成中',stopping:'停止中',cancelled:'已停止',completed:'生成完成',failed:'需要调整',interrupted:'任务中断',building_scene:'构建场景中',scene_ready:'场景已生成',runtime_deploy:'部署中',runtime_start:'启动中',runtime_stop:'停止中',runtime_ready:'操作完成',runtime_failed:'操作失败',motion_start:'连接仿真中',motion_ready:'仿真已连接'};
  $('phase').textContent=(languageMode() || state.assets.svg)?(state.phase==='completed' && state.jobs.at(-1)?.validation?.status==='passed'?'验收通过':languageMode() && !state.trajectory?'等待描述':phases[state.phase]||state.phase):'等待 SVG';
  const messagesKey=JSON.stringify(state.messages);
  if(messagesKey!==lastMessages){
    $('messages').replaceChildren();
    for(const message of state.messages){const box=document.createElement('div');box.className='message '+(message.role==='user'?'user':'agent');const role=document.createElement('small');role.textContent=message.role==='user'?'YOU':'AGENT';const text=document.createElement('p');text.textContent=message.text;box.append(role,text);$('messages').append(box);}
    $('messages').scrollTop=$('messages').scrollHeight;lastMessages=messagesKey;
  }
  $('spec-card').hidden=!state.spec;
  $('spec').textContent=state.spec?JSON.stringify(state.spec,null,2):'';
  $('progress-panel').hidden=!state.logs.length && !state.busy;
  $('progress-empty').hidden=!$('progress-panel').hidden;
  $('busy-label').textContent=state.phase==='stopping'?'正在停止，请稍候…':state.busy?'正在执行…':state.phase==='cancelled'?'本轮已停止':'本轮已结束';
  const logsKey=JSON.stringify(state.logs);
  if(logsKey!==lastLogs){$('logs').replaceChildren();for(const entry of state.logs){const line=document.createElement('p');line.textContent=entry.text;$('logs').append(line);}$('logs').scrollTop=$('logs').scrollHeight;lastLogs=logsKey;}
  $('downloads').replaceChildren();
  const latestJob=state.jobs.filter(job=>job.source!=='external').at(-1);
  const simulationJob=state.jobs.find(job=>job.id===state.workflow?.task);
  const resultNames={'final/scene.spec.json':'场景描述 JSON · scene.spec.json','final/plcopen.xml':'最终 PLC XML · plcopen.xml'};
  if(latestJob?.status==='final'){
    for(const [name,label] of Object.entries(resultNames)){
      const file=latestJob.files.find(file=>file.name===name);if(!file)continue;
      const link=document.createElement('a');link.href=file.url;link.textContent=label;link.download='';$('downloads').append(link);
    }
  }
  $('artifacts').hidden=!$('downloads').children.length;
  $('artifacts-empty').hidden=!$('artifacts').hidden;
  if(previousJob?.status==='running' && latestJob?.id===previousJob.id && latestJob.status==='final')showAgentTab('artifacts');
  const scene=state.workflow?.simulation;
  $('scene-status').textContent=state.phase==='building_scene'?'生成中':!state.workflow?.current?'等待当前 PLC 生成':scene?'已生成':state.workflow?.can_build?'待生成':'等待 PLC 生成';
  const sceneSource=state.workflow?.files?.find(file=>file.name==='final/scene.spec.json');
  $('scene-source').hidden=!sceneSource;if(sceneSource)$('scene-source').href=sceneSource.url;
  $('scene-library').hidden=!sceneSource;if(sceneSource)$('scene-library').href='/file/'+state.id+'/'+simulationJob.id+'/assets/component-library.json';
  $('scene-download').hidden=!scene;if(scene)$('scene-download').href=scene.url;
  $('scene-logs').hidden=!['building_scene','scene_ready','failed'].includes(state.phase);$('scene-logs').textContent=state.logs.map(log=>log.text).join('\n')+(state.phase==='failed'?'\n'+state.messages.at(-1).text:'');
  $('runtime-status').textContent=!state.workflow?.current?'等待当前 PLC 生成':!scene?'等待仿真场景':scene.runtime_ready?'映射已就绪':'通道待适配';
  renderRuntimeOperation();
  if(finishedRuntime)refreshRuntime();
  $('runtime-compatibility').replaceChildren();
  $('runtime-checks').hidden=!scene?.runtime_problems.length;
  if(scene)for(const problem of scene.runtime_problems){const p=document.createElement('p');p.textContent=problem;$('runtime-compatibility').append(p);}
  $('scene-empty').hidden=!!scene;
  $('studio-view-state').textContent=scene?'已加载':'等待场景';
  $('studio-task').textContent=simulationJob?(simulationJob.source==='external'?'外部导入':'工作台产物')+' · '+simulationJob.id.slice(-8):'等待 PLC 生成';
  renderSimulationTasks();
  renderSceneViews();
  refreshStudio();
  renderMotion(state.motion||{status:'not_started'});scheduleMotion();
  if(state.preview_url && $('preview-frame').getAttribute('src')!==state.preview_url)$('preview-frame').src=state.preview_url;
  controls();
  if(languageMode())showTab(!!state.preview_url);
  for(const input of $('paths').querySelectorAll('input'))input.checked=selected.has(Number(input.dataset.path));
  if(timer)clearTimeout(timer);timer=null;
  if(state.busy)timer=setTimeout(poll,1000);
}
async function poll() {
  if(!state)return;
  try{render(await api('/api/state?session='+encodeURIComponent(state.id)));error();}
  catch(exc){error('读取进度失败：'+exc.message+'；正在重试。');timer=setTimeout(poll,2500);}
}
async function action(route, extra={}, options={}) {
  working=true;controls();error();
  try{const result=await api(route,{session:state?.id,revision:state?.revision,...extra});render(result,options.replaceSelection);return true;}
  catch(exc){error(exc.message);return false;}
  finally{working=false;controls();if(pendingDeviceScene&&!state?.motion?.active&&!state?.motion?.occupied)applyPendingDeviceScene();}
}
function togglePath(id) {
  if(working || state?.busy || state?.motion?.active || state?.motion?.occupied)return;
  if(selected.has(id))selected.delete(id);else selected.add(id);
  syncSelection();
}
function syncSelection() {
  for(const input of $('paths').querySelectorAll('input'))input.checked=selected.has(Number(input.dataset.path));
  controls();showTab(false);
  queuePlan();
}
function queuePlan() {
  // 请求串行化，避免旧参数或旧选择的响应覆盖新状态。
  planning=planning.then(async()=>{
    if(languageMode()){
      if(!state || state.busy)return false;
      const text=$('drawing-description').value.trim();
      if(languageMatches() && state.trajectory)return true;
      if(!text){error('请描述要绘制的图形，例如“画一个正方形”');return false;}
      return action('/api/drawing/plan',{text},{replaceSelection:true});
    }
    if(!state?.assets.svg || state.busy)return false;
    if(selectionMatches() && settingsMatch() && state.trajectory)return true;
    let settings;try{settings=currentSettings();}catch(exc){error(exc.message);return false;}
    return action('/api/plan',{selected_paths:[...selected].sort((a,b)=>a-b),settings});
  });
  return planning;
}
async function upload(file) {
  if(!file || working || state?.busy)return;
  if(!file.name.toLowerCase().endsWith('.svg') || file.size>2*1024*1024){error('请选择不超过 2 MiB 的 SVG 文件');return;}
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let start=0;start<bytes.length;start+=8192)binary+=String.fromCharCode(...bytes.subarray(start,start+8192));
  await action('/api/import',{name:file.name,data:btoa(binary)},{replaceSelection:true});
  $('file').value='';
}
async function uploadAsset(kind,part,file) {
  if(!file || working || state?.busy)return;
  if(kind==='aml'&&window.DeviceScenes?.isBusy()){error('设备场景任务正在处理，请先完成或停止当前任务后再更换 AML');return;}
  const extension=kind==='aml'?'.aml':'.json';
  if(!file.name.toLowerCase().endsWith(extension) || !file.size || file.size>2*1024*1024){error('请选择非空且不超过 2 MiB 的 '+extension+' 文件');return;}
  const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let start=0;start<bytes.length;start+=8192)binary+=String.fromCharCode(...bytes.subarray(start,start+8192));
  const changed=await action('/api/asset',{kind,part,name:file.name,data:btoa(binary)},{replaceSelection:true});
  if(changed&&kind==='aml')await window.DeviceScenes?.useAml(file);
}
$('aml-file').onchange=event=>uploadAsset('aml',null,event.target.files[0]).finally(()=>event.target.value='');
$('library-file').onchange=event=>uploadAsset('library',null,event.target.files[0]).finally(()=>event.target.value='');
$('contract-file').onchange=event=>uploadAsset('library','contract',event.target.files[0]).finally(()=>event.target.value='');
$('physics-file').onchange=event=>uploadAsset('library','physics',event.target.files[0]).finally(()=>event.target.value='');
$('file').onchange=event=>upload(event.target.files[0]);
$('language-source').onclick=()=>action('/api/drawing/source',{mode:'language'},{replaceSelection:true});
$('svg-source').onclick=()=>action('/api/drawing/source',{mode:'svg'},{replaceSelection:true});
$('language-form').onsubmit=event=>{event.preventDefault();queuePlan();};
$('preview-frame').addEventListener('load',()=>{
  const doc=$('preview-frame').contentDocument;if(!doc)return;
  const style=doc.createElement('style');style.textContent='.preview>header{display:none}.preview main{padding:12px}.preview .card{padding:14px}.preview h2{font-size:15px}';doc.head.append(style);
});
$('drawing-description').addEventListener('input',controls);
for(const [id,text] of [['square-example','画一个正方形，边长40，中心(50,50)'],['circle-example','画一个圆，半径20，中心(50,50)，段数48']])$(id).onclick=()=>{$('drawing-description').value=text;controls();return queuePlan();};
$('example').onclick=()=>action('/api/example',{}, {replaceSelection:true});
for(const name of ['dragenter','dragover'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.add('dragging');});
for(const name of ['dragleave','drop'])$('dropzone').addEventListener(name,event=>{event.preventDefault();$('dropzone').classList.remove('dragging');});
$('dropzone').addEventListener('drop',event=>upload(event.dataTransfer.files[0]));
$('select-tab').onclick=()=>showTab(false);$('preview-tab').onclick=()=>showTab(true);
$('select-all').onclick=()=>{selected=new Set(state.catalog.paths.map(path=>path.id));syncSelection();};
$('select-none').onclick=()=>{selected.clear();syncSelection();};
$('invert').onclick=()=>{selected=new Set(state.catalog.paths.map(path=>path.id).filter(id=>!selected.has(id)));syncSelection();};
$('parameters').onsubmit=event=>{event.preventDefault();queuePlan();};
$('chat-form').onsubmit=async event=>{
  event.preventDefault();if($('send').disabled)return;
  const text=$('message').value.trim();if(!text){error('请先输入工艺要求或修改意见');return;}
  // 先应用参数框中尚未提交的修改，规格始终对应页面显示的尺寸。
  if(!languageMode() || ($('drawing-description').value.trim() && !languageMatches()))if(!await queuePlan())return;
  if(!selectionMatches() || working)return;
  const ok=await action('/api/chat',{message:text},{replaceSelection:true});if(ok)$('message').value='';
};
$('message').onkeydown=event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();$('chat-form').requestSubmit();}};
$('generate').onclick=()=>{showAgentTab('progress');return action('/api/generate');};
$('stop-generation').onclick=()=>{showAgentTab('progress');return action('/api/generate/stop',{task:state.jobs.at(-1)?.id});};
$('clear-context').onclick=async()=>{if(await action('/api/context/clear')){$('message').value='';showAgentTab('chat');}};
$('default-process').onclick=async()=>{if(!await queuePlan())return;if(await action('/api/process/default')){$('message').value='';showAgentTab('chat');}};
$('build-scene').onclick=()=>{if(deviceSceneTask)return;showStudioTab('logs');return action('/api/simulation/build',{task:state.workflow.task});};
$('simulation-task').onchange=async()=>{
  const task=$('simulation-task').value;
  if(task.startsWith(deviceScenePrefix))return syncDeviceScene(task.slice(deviceScenePrefix.length));
  if(await action('/api/simulation/select',{task})){leaveDeviceScene();render(state);}
  else renderSimulationTasks();
};
$('import-simulation').onclick=()=>{$('import-error').hidden=true;$('import-dialog').showModal();};
$('close-import').onclick=()=>$('import-dialog').close();
$('import-form').onsubmit=async event=>{
  event.preventDefault();const files={};$('confirm-import').disabled=true;$('import-error').hidden=true;
  try{
    for(const [key,id] of [['scene','import-scene-file'],['xml','import-plc-file'],['spec','import-spec-file']]){
      const file=$(id).files[0];if(!file)continue;
      if(file.size>2*1024*1024 || !file.size)throw new Error('单个文件须非空且不超过 2 MiB');
      const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let start=0;start<bytes.length;start+=8192)binary+=String.fromCharCode(...bytes.subarray(start,start+8192));
      files[key]={name:file.name,data:btoa(binary)};
    }
    const result=await api('/api/simulation/import',{session:state.id,revision:state.revision,files});
    leaveDeviceScene();render(result);$('import-dialog').close();$('import-form').reset();refreshStudio();
  }catch(exc){$('import-error').hidden=false;$('import-error').textContent=exc.message;}
  finally{$('confirm-import').disabled=false;}
};
$('refresh-runtime').onclick=()=>{refreshRuntime();if(deviceSceneTask)syncDeviceScene(deviceSceneTask,{expectedHash:deviceSceneRecord?.model_frozen?deviceSceneRecord.model_xml_sha256:null});else refreshStudio();};
$('manual-motion').onclick=()=>{if(!deviceSceneTask)return action('/api/motion/start',{task:state.workflow?.task,mode:'manual'});};
$('verify-motion').onclick=()=>{if(deviceSceneTask)return;showStudioTab('result');return action('/api/motion/start',{task:state.workflow?.task,mode:'verify'});};
for(const name of ['start','stop'])$(name+'-motion').onclick=async()=>{if(deviceSceneTask)return;if(await action('/api/motion/'+name,{task:state.workflow?.task}))refreshMotion();};
for(const name of ['deploy','start','stop'])$(name+'-runtime').onclick=()=>{if(!deviceSceneTask)return controlRuntime(name);};
$('read-runtime-logs').onclick=async()=>{
  $('read-runtime-logs').disabled=true;
  try{const result=await api('/api/runtime/logs?session='+encodeURIComponent(state.id));$('runtime-logs').textContent=result.logs||'暂无日志';$('runtime-logs').hidden=false;}
  catch(exc){error(exc.message);}finally{controls();}
};
for(const input of $('parameters').querySelectorAll('input'))input.addEventListener('input',controls);
async function init() {
  try{
    config=await api('/api/config');
    window.DeviceScenes?.configure(config);
    const ident=localStorage.getItem('svg-workbench-session');
    if(ident){try{render(await api('/api/state?session='+encodeURIComponent(ident)),true);}catch(exc){localStorage.removeItem('svg-workbench-session');error(exc.message);}}
    if(!state)render(await api('/api/session',{}),true);
    await restoreDeviceScene();
  }catch(exc){error('无法连接工作台：'+exc.message);}
  controls();
  if(new URLSearchParams(location.search).get('view')==='scenes')showWorkflow(3);
}
init();
