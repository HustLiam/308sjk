'use strict';
(() => {
  const $=id=>document.getElementById(id);
  const sceneText=globalThis.DeviceSceneText;
  let config=null,current=null,working=false,loading=false,timer=null,viewer=null;
  let agentSubmitting=false;
  let chosen=new Set(),componentKey='',resultKey='',listingKey='',replay=null,playing=false,replayStart=0,replayOffset=0;
  let tree=null,activeComponent=null,collapsed=new Set(),interfaceChoices=new Map(),editingInterface=null,treeInitialized=false;
  let reviewSelection=new Map(),reviewContext=null;
  let publishedFrozenKey='',previewTask='';
  let visibilityMode='only',visibleComponents=null,visibilityModel='';
  const jointPanel=new JointPreviewPanel($('ds-joint-preview'),api,()=>{playing=false;replay=null;$('ds-play').textContent='播放测试回放';});
  jointPanel.onChange=()=>controls();
  let sourceVersion=0,publishedSourceKey='';
  let testSelection=new Set(),testModulesKey='',testReplayVersion=0;
  let historyRecords=[],historySelection=new Set();
  let defaultRequest='根据 AML 生成面向 PLC 验证的可执行设备模型，保留组件连接、运动约束和 IO，使用简化几何，并依据实际仿真状态生成反馈。';
  function clearRetiredSources(ids=[]){
    const retired=new Set(ids);
    if(!retired.size)return;
    for(const key of ['device-scene-task','device-scene-last-frozen','studio-device-scene','studio-frozen-scene'])if(retired.has(localStorage.getItem(key)))localStorage.removeItem(key);
    try{if(retired.has(JSON.parse(localStorage.getItem('device-scene-selection')||'null')?.task))localStorage.removeItem('device-scene-selection');}catch{}
    const url=new URL(location.href);if(retired.has(url.searchParams.get('scene_task'))){url.searchParams.delete('scene_task');history.replaceState(null,'',url);}
  }
  function publishSource(record,pendingName=null,pendingError=null){
    const key=JSON.stringify([record?.id,record?.source_sha256,record?.phase,record?.busy,record?.model_frozen,record?.model_xml_sha256,pendingName,pendingError]);
    if(key===publishedSourceKey)return;publishedSourceKey=key;
    const frozen=!!(record?.model_frozen&&record?.all_approved&&!record?.busy&&!record?.error);
    const source={task:record?.id||null,name:record?.name||pendingName||'',source_sha256:record?.source_sha256||null,
      device_ir_sha256:record?.device_ir_sha256||null,model_xml_sha256:frozen?record.model_xml_sha256:null,
      model_frozen:frozen,pending:!record,error:pendingError,selected_at:Date.now()};
    localStorage.setItem('device-scene-selection',JSON.stringify(source));
    window.dispatchEvent(new CustomEvent('device-scene-selected',{detail:source}));
  }
  const phaseNames={draft:'待生成',generating:'生成中',editing:'调整中',build:'构建中',check:'检查中',freeze:'冻结中',testing:'测试中',revalidate:'组件复查中',stopping:'停止中',ready:'已生成',failed:'失败',cancelled:'已停止',interrupted:'已中断'};
  const fileNames={'input.aml':'设备 AML','device_ir.json':'DeviceIR','task.json':'任务清单','patch.json':'局部修改补丁','scene.xml':'MuJoCo 模型 XML','scene.physical.json':'设备场景 JSON','io_map.json':'AML IO 绑定','assembly_checks.json':'装配检查报告','behavior_checks.json':'设备行为报告','model.freeze.json':'模型冻结清单'};
  Object.assign(fileNames,{'simulation_tests.json':'生成的仿真测试脚本','simulation_test_results.json':'仿真测试模块结果','robot_io_rules.json':'六轴 IO 绑定校验规则','test_safety.json':'测试防碰撞预检'});
  Object.assign(fileNames,{'verification_parameters.json':'模型校验参数 JSON','io_test.xml':'OpenPLC IO 测试程序','plc_test_contract.json':'PLC 测试通道配置'});
  Object.assign(fileNames,{'package_manifest.json':'AML 资源总清单与缺项','data_destinations.json':'数据去向与节点映射','mesh_manifest.json':'网格尺寸与来源','logic_models.json':'保留的行为模型','body_mapping.json':'仿真组件映射'});
  const layerNames={device_description:'设备描述',component_model:'组件模型',assembly:'装配关系',plc_logic:'PLC 逻辑'};
  layerNames.simulation_test='仿真测试';
  const qs=object=>new URLSearchParams(object).toString();
  function leafIds(record){
    if(record.component_counts)return new Set(record.component_counts.leaf_ids);
    const model=globalThis.DeviceTree.build(record.device_ir);
    return new Set([...model.nodes.values()].filter(n=>n.kind==='component'&&!(model.children.get(n.id)||[]).some(id=>model.nodes.get(id)?.kind==='component')).map(n=>n.id));
  }
  function leafCount(record){
    if(record.component_counts)return record.component_counts.leaf;
    return leafIds(record).size;
  }
  const fileUrl=(path,type)=>'/device-scene-file?'+qs({task:current.id,path,...(type?{type}:{})});
  function showError(id,message='',prefix=''){
    const node=$(id),original=String(message??''),text=sceneText.errorMessage(original);
    node.textContent=prefix+text;node.title=text===original?'':original;node.hidden=!original&&!prefix;
  }
  function error(message=''){showError('ds-error',message);showError('ds-settings-error',message);}
  function element(tag,text,className){const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;}
  function link(host,label,path,type){const a=element('a',label);a.href=fileUrl(path,type);a.download='';host.append(a);}
  function table(host,headers,rows){const t=element('table'),head=element('thead'),tr=element('tr');for(const h of headers)tr.append(element('th',h));head.append(tr);t.append(head);const body=element('tbody');for(const row of rows){const r=element('tr');for(const value of row)r.append(element('td',typeof value==='object'?JSON.stringify(value):String(value??'—')));body.append(r);}t.append(body);host.append(t);}
  async function api(path,data){
    if(!config)config=await (await fetch('/api/config')).json();
    const response=await fetch(path,data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-Agent-Token':config.token},body:JSON.stringify(data)});
    const result=await response.json();if(!response.ok)throw Error(result.error||'设备场景请求失败');return result;
  }
  function blocked(){return working||agentSubmitting||!!current?.busy||!!current?.occupied||!!viewer?.assemblyPromise||!!(jointPanel.plcActive&&jointPanel.frame?.agent);}
  function visibilitySelection(ids=chosen,linked=true){
    const expanded=new Set(ids);
    for(const j of current?.device_ir?.joints||[])if(expanded.has(j.id)){expanded.add(j.parent_body);expanded.add(j.child_body);}
    return DeviceTree.componentSet(current?.device_ir,expanded,linked&&$('ds-linked-visibility').checked?viewer?.componentChildren:{});
  }
  function syncVisibility(){
    if(!viewer)return;
    if(viewer.jointPreviewVersion!==visibilityModel){visibilityModel=viewer.jointPreviewVersion;visibleComponents=null;if(visibilityMode==='custom')visibilityMode='only';}
    if(visibilityMode==='all')visibleComponents=null;
    else if(visibilityMode==='only')visibleComponents=chosen.size?visibilitySelection():null;
    else if(visibilityMode==='station'){
      const byId=new Map((current?.device_ir?.components||[]).map(c=>[c.id,c])),stations=new Set();
      for(const id of chosen){let c=byId.get(id),candidate=c,seen=new Set();while(c&&!seen.has(c.id)){seen.add(c.id);candidate=c;if(/^ST\d/i.test(c.name))break;c=byId.get(c.parent_id);}if(candidate)stations.add(candidate.id);}
      visibleComponents=stations.size?visibilitySelection(stations,false):null;
    }
    viewer.setVisibleComponents(visibleComponents);
    const total=current?.device_ir?.components.length||0;
    $('ds-visible-count').textContent=(visibleComponents?.size??total)+' / '+total;
    for(const button of document.querySelectorAll('[data-ds-visibility]')){const on=button.dataset.dsVisibility===(visibleComponents===null?'all':visibilityMode);button.classList.toggle('is-active',on);button.setAttribute('aria-pressed',String(on));button.disabled=button.dataset.dsVisibility!=='all'&&!chosen.size;}
    for(const button of $('ds-components').querySelectorAll('[data-visibility-component]')){
      const id=button.dataset.visibilityComponent,shown=visibleComponents===null||[...visibilitySelection([id],false)].some(k=>visibleComponents.has(k));
      button.setAttribute('aria-pressed',String(shown));button.title=(shown?'隐藏 ':'显示 ')+(tree?.nodes.get(id)?.name||id);button.setAttribute('aria-label',button.title);button.classList.toggle('is-off',!shown);
    }
  }
  function fitSelection(){if(viewer?.meshes.length)viewer.fitVisible({top:110,bottom:jointPanel.host.hidden?45:jointPanel.host.offsetHeight+50});}
  function changeVisibility(mode){
    if(!current?.scene_ready)return;
    if(mode==='hide'){visibleComponents=new Set(visibleComponents??current.device_ir.components.map(c=>c.id));for(const id of visibilitySelection())visibleComponents.delete(id);visibilityMode='custom';}
    else visibilityMode=mode;
    syncVisibility();fitSelection();
  }
  function filterComponents(){
    const query=$('ds-component-search').value.trim().toLowerCase();
    for(const item of [...$('ds-components').querySelectorAll('[role=treeitem]')].reverse()){
      const own=(tree?.nodes.get(item.dataset.nodeId)?.name||'').toLowerCase().includes(query),descendant=[...item.querySelectorAll(':scope > ul > li')].some(n=>!n.hidden);
      item.hidden=!!query&&!own&&!descendant;
      const group=item.querySelector(':scope > ul');if(group)group.hidden=query?false:collapsed.has(item.dataset.nodeId);
    }
  }
  function syncSelection(scrollId){
    const components=current?.device_ir?.components||[],joints=current?.device_ir?.joints||[],byId=new Map([...components,...joints].map(c=>[c.id,c]));
    const preparing=!!current?.busy&&!current?.device_ir;
    if(!preparing)chosen=new Set([...chosen].filter(id=>byId.has(id)));
    const highlighted=new Set(chosen);
    for(const j of joints)if(chosen.has(j.id)){highlighted.add(j.parent_body);highlighted.add(j.child_body);}
    for(const c of components){const seen=new Set();let parent=tree?.parents.get(c.id);
      while(parent&&!seen.has(parent)){if(highlighted.has(parent)){highlighted.add(c.id);break;}seen.add(parent);parent=tree.parents.get(parent);}}
    if(viewer)viewer.setSelection(highlighted);
    syncVisibility();
    jointPanel.sync(current,chosen,viewer);
    if(scrollId&&chosen.has(scrollId))activeComponent=scrollId;
    if(!chosen.has(activeComponent))activeComponent=[...chosen].at(-1)||null;
    for(const input of $('ds-components').querySelectorAll('input')){input.checked=chosen.has(input.value);input.closest('label').classList.toggle('is-selected',input.checked);input.closest('[role=treeitem]').setAttribute('aria-selected',String(input.checked));}
    for(const ref of $('ds-components').querySelectorAll('.device-tree-reference')){const node=tree.nodes.get(ref.closest('[role=treeitem]').dataset.nodeId);ref.classList.toggle('is-related',chosen.has(tree.parents.get(node.id))||chosen.has(node.targetId));}
    $('ds-selection-summary').textContent=preparing?'正在准备所选组件的新版本…':chosen.size?'已选 '+chosen.size+' 项：'+[...chosen].map(id=>tree?.nodes.get(id)?.name||byId.get(id)?.name||id).join('、'):'未选择组件';
    renderProperties();
    if(scrollId){const seen=new Set();let parent=tree?.parents.get(scrollId);while(parent&&!seen.has(parent)){seen.add(parent);setExpanded(parent,true);parent=tree.parents.get(parent);}
      const input=[...$('ds-components').querySelectorAll('input')].find(i=>i.value===scrollId);
      if(input){const row=input.closest('label').getBoundingClientRect(),box=$('ds-components').getBoundingClientRect();if(row.top<box.top)$('ds-components').scrollTop+=row.top-box.top;else if(row.bottom>box.bottom)$('ds-components').scrollTop+=row.bottom-box.bottom;}}
    controls();
  }
  function selectFromView(id,{additive=false}={}){
    if(blocked())return;
    if(!id){if(!additive)chosen.clear();}
    else if(current?.device_ir?.components.some(c=>c.id===id)){
      if(additive){if(chosen.has(id))chosen.delete(id);else chosen.add(id);}else chosen=new Set([id]);
    }
    syncSelection(id);
  }
  function treeNode(id){return [...$('ds-components').querySelectorAll('[role=treeitem]')].find(n=>n.dataset.nodeId===id);}
  function nodeControl(id){return treeNode(id)?.querySelector(':scope > .device-tree-row')?.querySelector('input,.device-tree-class,.device-tree-reference');}
  function componentReference(targetId,label,className='device-component-reference'){
    const target=tree?.nodes.get(targetId),valid=target?.kind==='component',button=element('button',label||target?.name||targetId||'未指定',className);
    button.type='button';button.dataset.componentTarget=targetId||'';button.disabled=blocked()||!valid;button.title='定位组件：'+(target?.name||targetId||'未指定');
    button.onclick=event=>{if(valid)selectFromView(targetId,{additive:event.ctrlKey||event.metaKey});};return button;
  }
  function setExpanded(id,expanded){
    const node=treeNode(id);if(!node||!tree.children.get(id)?.length)return;
    if(expanded)collapsed.delete(id);else collapsed.add(id);
    node.setAttribute('aria-expanded',String(expanded));node.querySelector(':scope > ul').hidden=!expanded;
    const button=node.querySelector(':scope > .device-tree-row > button');button.textContent=expanded?'▾':'▸';button.setAttribute('aria-label',(expanded?'收起 ':'展开 ')+tree.nodes.get(id).name);
  }
  function renderTree(){
    const ir=current?.device_ir,host=$('ds-components');host.replaceChildren();if(!ir){tree=null;return;}
    tree=DeviceTree.build(ir);
    if(!treeInitialized){for(const [id,children] of tree.children)if(children.length)collapsed.add(id);treeInitialized=true;}
    const build=id=>{const data=tree.nodes.get(id),c=data.component,j=data.joint,children=tree.children.get(id),name=data.name;
      const item=element('li');item.role='treeitem';item.dataset.nodeId=id;item.dataset.nodeKind=data.kind;item.setAttribute('aria-selected',String(chosen.has(id)));
      const row=element('div',undefined,'device-tree-row'),toggle=element('button',children.length?(collapsed.has(id)?'▸':'▾'):'·','device-tree-toggle');toggle.type='button';toggle.disabled=!children.length;toggle.tabIndex=-1;toggle.onclick=()=>setExpanded(id,collapsed.has(id));row.append(toggle);
      if(data.kind==='reference'||data.kind==='interface'){const button=componentReference(data.targetId,name,'device-tree-reference');button.dataset.referenceSide=data.side||'';
        button.onclick=()=>{if(data.frame||data.interfaceId)interfaceChoices.set(data.targetId,data.frame?.id||data.interfaceId);selectFromView(data.targetId);};row.append(button);item.dataset.targetId=data.targetId||'';}
      else if(data.kind==='joint_class'){const button=element('button',name,'device-tree-class');button.type='button';button.onclick=()=>setExpanded(id,collapsed.has(id));row.append(button);}
      else{const label=element('label'),input=document.createElement('input');input.type='checkbox';input.value=id;input.checked=chosen.has(id);
        input.onchange=()=>{if(input.checked)chosen.add(id);else chosen.delete(id);syncSelection(input.checked?id:null);};
        const text=element('span',undefined,'device-tree-caption');text.append(element('span',name),element('small',j?'运动副实例':c.type));text.title=name;text.onclick=e=>{e.preventDefault();selectFromView(id,{additive:e.ctrlKey||e.metaKey});};label.append(input,text);row.append(label);
        if(data.kind==='component'){const eye=element('button','◉','device-tree-eye');eye.type='button';eye.dataset.visibilityComponent=id;
          eye.onclick=()=>{const branch=visibilitySelection([id],false),shown=visibleComponents===null||[...branch].some(k=>visibleComponents.has(k));visibleComponents=new Set(visibleComponents??current.device_ir.components.map(c=>c.id));for(const k of branch)shown?visibleComponents.delete(k):visibleComponents.add(k);visibilityMode='custom';syncVisibility();};row.append(eye);}}
      item.append(row);
      if(children.length){item.setAttribute('aria-expanded',String(!collapsed.has(id)));toggle.setAttribute('aria-label',(collapsed.has(id)?'展开 ':'收起 ')+name);const list=element('ul');list.role='group';list.hidden=collapsed.has(id);for(const child of children)list.append(build(child));item.append(list);}
      return item;};
    const roots=element('ul');roots.role='group';for(const id of tree.roots){if(current.resource_package&&tree.nodes.get(id).kind==='joint_class'&&!tree.children.get(id).length)continue;roots.append(build(id));}host.append(roots);
    $('ds-tree-issues').hidden=!tree.issues.length;$('ds-tree-issues').textContent=tree.issues.length?'组件层级或运动副引用存在错误，请检查设备描述。':'';
    filterComponents();
  }
  function renderProperties(){
    const ir=current?.device_ir,c=ir?.components.find(c=>c.id===activeComponent),joint=ir?.joints.find(j=>j.id===activeComponent),host=$('ds-properties-content');$('ds-properties').hidden=!c&&!joint;host.replaceChildren();if(!c&&!joint)return;
    const byId=new Map(ir.components.map(c=>[c.id,c])),name=id=>byId.get(id)?.name||id||'无';
    $('ds-properties').querySelector(':scope > summary').textContent=joint?'运动副实例属性':'组件属性与接口';
    host.append(element('strong',tree?.nodes.get(activeComponent)?.name||joint?.name||c?.name));
    if(joint){
      const types={slide:'滑动副',hinge:'转动副',ball:'球副',free:'自由副'},rows=[['类型',(types[joint.type]||joint.type)+'（'+joint.type+'）'],['父组件引用',name(joint.parent_body)],['子组件引用',name(joint.child_body)]];
      if(['slide','hinge'].includes(joint.type))rows.push(['轴向（子组件坐标）',joint.axis.join(', ')],['范围',joint.range.join(' ～ ')+(joint.type==='hinge'?' rad':' m')],['初始位置',joint.initial+(joint.type==='hinge'?' rad':' m')]);
      rows.push(['父组件安装点',joint.parent_anchor_m.join(', ')+' m'],['子组件安装点',joint.child_anchor_m.join(', ')+' m']);
      for(const side of ['parent','child']){const port=ir.interfaces.find(f=>f.component_id===joint.id&&f.name===side+'_body');
        const link=ir.connections.find(l=>l.a===port?.id||l.b===port?.id),frame=link?ir.interfaces.find(f=>f.id===(link.a===port?.id?link.b:link.a)):null;
        rows.push([side==='parent'?'父接口连接':'子接口连接',frame?name(frame.component_id)+' / '+frame.name+' ↔ '+joint.name+'.'+side+'_body':'缺少接口连接']);}
      table(host,['属性','值'],rows);const tableNode=host.lastElementChild;tableNode.dataset.jointType=joint.type;
      for(const [index,target] of [[1,joint.parent_body],[2,joint.child_body]])tableNode.tBodies[0].rows[index].lastElementChild.replaceChildren(componentReference(target,name(target)));
      return;
    }
    const frames=ir.interfaces.filter(f=>f.component_id===c.id);
    const action=(id,label,available,handler)=>{const button=element('button',label);button.id=id;button.type='button';button.dataset.dsPropertyAction='';button.dataset.available=String(available);button.disabled=blocked()||!available;button.onclick=handler;host.append(button);};
    if(frames.length){const select=element('select');select.id='ds-interface-select';select.setAttribute('aria-label','所属组件的接口');
      for(const f of frames)select.append(new Option(f.name+' · '+f.type,f.id));
      const selected=interfaceChoices.get(c.id);select.value=frames.some(f=>f.id===selected)?selected:frames[0].id;interfaceChoices.set(c.id,select.value);
      select.onchange=()=>{interfaceChoices.set(c.id,select.value);renderProperties();};host.append(select);
      const frame=frames.find(f=>f.id===select.value),p=current.interface_properties?.[frame.id];
      host.append(element('p',p?.connected?'已连接':'尚未连接','device-help'));
      if(p?.position_m)host.append(element('p','位置：'+p.position_m.map(v=>Number(v.toFixed(6))).join(', ')+' m','device-help'));
      if(p?.rpy_rad)host.append(element('p','旋转：'+p.rpy_rad.map(v=>Number((v*180/Math.PI).toFixed(3))).join(', ')+'°','device-help'));
      if(p?.editable){const required=p.required_ids||[c.id],missing=required.filter(id=>!chosen.has(id));
        if(missing.length){host.append(element('p','编辑此接口需同时勾选：'+missing.map(name).join('、'),'device-help'));action('ds-properties-related','选中关联节点',true,()=>{for(const id of required)chosen.add(id);syncSelection(c.id);});}
        if(!p.position_m)host.append(element('p','设备描述尚未显式定义局部位置，可在此设置。','device-help'));
        action('ds-properties-edit','设置接口位置',!missing.length,()=>openInterface(c,frame,p));
      }
    }else host.append(element('p','当前组件没有接口。','device-help'));
    if(!current.resource_package)action('ds-properties-add','新增机械安装接口',!joint,()=>openInterface(c,null,null));
    const params=element('details');params.className='device-property-parameters';params.append(element('summary','参数与来源'));table(params,['参数','当前值'],Object.entries(c.parameters).map(([k,v])=>[k,typeof v.value==='object'?JSON.stringify(v.value):v.value]));params.append(element('p','来源：'+(c.source?.locator||c.source?.kind||'设备描述'),'device-help'));host.append(params);
  }
  function openInterface(component,frame,description){
    if(blocked())return;
    editingInterface={componentId:component.id,interfaceId:frame?.id||crypto.randomUUID(),adding:!frame,rotatable:!frame||frame.type==='MechanicalMount'||!!description?.rpy_rad,taskId:current.id,base:current.device_ir.facts_sha256};
    $('ds-interface-title').textContent=component.name+' · '+(frame?'接口局部坐标':'新增机械接口');$('ds-interface-name').value=frame?.name||'mount';$('ds-interface-name').readOnly=!!frame;
    $('ds-interface-context').textContent=editingInterface.rotatable?'完整接口坐标相对于 '+component.name+'；接口定义的运动副使用局部 +Z 作为正轴向。':'运动副安装点相对于 '+component.name+'；方向由运动副轴向定义，旋转角不可在此修改。';
    for(const [i,a] of ['x','y','z'].entries()){$('ds-interface-'+a).value=description?.position_m?.[i]??'';const field=$('ds-interface-r'+a);field.disabled=!editingInterface.rotatable;field.required=editingInterface.rotatable;field.value=(description?.rpy_rad?.[i]||0)*180/Math.PI;}
    $('ds-interface-error').hidden=true;$('ds-interface-save').disabled=false;$('ds-interface-dialog').showModal();
  }
  function renderAmlSource(){
    const file=$('ds-aml').files[0],download=$('ds-aml-download');
    $('ds-aml-name').textContent=file?.name||current?.name||'尚未加载设备文件';
    $('ds-aml-status').textContent=file?(current?'待替换':'待生成'):current?'已加载':'未加载';
    $('ds-aml-choose').textContent=file?'重新选择':current?'替换 AML':'选择 AML';
    $('ds-aml-reset').hidden=!file;$('ds-aml-reset').textContent=current?'取消替换':'取消选择';
    download.hidden=!current;
    if(current){download.href=fileUrl('input.aml');download.download=current.name;}
    else{download.removeAttribute('href');download.removeAttribute('download');}
  }
  function controls(){
    const busy=blocked(),ready=!!current?.scene_ready;
    $('ds-package-import').disabled=busy;
    $('ds-tests-run').disabled=busy||!testSelection.size||![...testSelection].every(id=>current?.test_modules?.some(m=>m.id===id&&m.available));
    $('ds-tests-all').disabled=busy||!current?.test_modules?.some(m=>m.available);$('ds-tests-clear').disabled=busy||!testSelection.size;
    $('ds-test-replay').disabled=busy;
    $('ds-tests-view').disabled=busy||![...testSelection].some(id=>current?.test_modules?.some(m=>m.id===id&&m.replay));
    $('ds-history-open').disabled=busy;$('ds-history-close').disabled=working;
    $('ds-history-delete').disabled=busy||!historySelection.size;$('ds-history-all').disabled=busy||!visibleHistory().length;$('ds-history-none').disabled=busy||!historySelection.size;
    for(const input of $('ds-history-list').querySelectorAll('input'))input.disabled=busy;
    for(const input of $('ds-test-modules').querySelectorAll('input'))input.disabled=busy||!current?.test_modules?.find(m=>m.id===input.value)?.available;
    for(const id of ['ds-generate','ds-aml','ds-aml-choose','ds-aml-reset','ds-new'])$(id).disabled=busy;
    $('ds-build').disabled=busy||!current?.can_build;
    $('ds-check').disabled=busy||!ready||!!current?.geometry_only;$('ds-check').title=current?.geometry_only?'组件行为仍有未解决问题，当前仅显示静态几何预览':'';$('ds-revalidate').disabled=busy||!current?.candidates?.length;
    $('ds-freeze').disabled=busy||!current?.all_approved||!current?.can_build||current.model_frozen;
    $('ds-freeze').title=!current?.all_approved?'全部组件审批后可用':!current?.can_build?'当前组件复查未通过，请先修复':'通过整机行为检查后冻结';
    $('ds-stop').hidden=!current?.busy;$('ds-stop').disabled=working||current?.phase==='stopping';
    $('ds-tasks').disabled=working;$('ds-review-close').disabled=working;
    $('ds-edit').disabled=busy||jointPanel.active||!viewer?.jointPreviewVersion||!$('ds-edit-request').value.trim();
    $('ds-clear-selection').disabled=busy||!chosen.size;if(viewer)viewer.selectionEnabled=!busy;
    for(const checkbox of $('ds-components').querySelectorAll('input'))checkbox.disabled=busy||checkbox.dataset.readonly==='true';
    for(const button of $('ds-properties-content').querySelectorAll('[data-ds-property-action]'))button.disabled=busy||button.dataset.available!=='true';
    for(const button of document.querySelectorAll('[data-component-target]'))button.disabled=busy||tree?.nodes.get(button.dataset.componentTarget)?.kind!=='component';
    for(const input of $('ds-candidates').querySelectorAll('input'))input.disabled=busy||!current?.candidates.find(c=>c.type===input.value)?.can_approve;
    for(const button of $('ds-candidates').querySelectorAll('button'))button.disabled=working;
    const eligible=visibleCandidates().filter(c=>c.can_approve);
    $('ds-select-visible').disabled=busy||!eligible.length;$('ds-clear-reviews').disabled=busy||!reviewSelection.size;
    $('ds-batch-review').disabled=busy||!reviewSelection.size;
    $('ds-review-selection').textContent='已选 '+reviewSelection.size+' 项';
    const versionsCurrent=reviewContext?.task===current?.id&&reviewContext?.items.every(item=>current.candidates.some(c=>c.type===item.component_type&&c.review_sha256===item.review_sha256&&c.can_approve));
    $('ds-approve').disabled=busy||!versionsCurrent||!$('ds-review-confirmed').checked||!$('ds-reviewer').value.trim();
  }
  function tab(name){for(const item of ['library','checks','facts','files']){$('ds-pane-'+item).hidden=item!==name;document.querySelector('[data-ds-tab="'+item+'"]').setAttribute('aria-selected',String(item===name));}$('ds-results-title').textContent={library:'人工审批',checks:'检查结果',facts:'设备与 IO',files:'产物与记录'}[name];$('ds-results-dialog').querySelector('.asset-dialog-body').scrollTop=0;}
  for(const button of document.querySelectorAll('[data-ds-tab]'))button.onclick=()=>tab(button.dataset.dsTab);
  for(const button of document.querySelectorAll('[data-ds-open]'))button.onclick=()=>{tab(button.dataset.dsOpen);$('ds-results-dialog').showModal();if(button.dataset.dsOpen==='checks'&&current?.resource_package)poll();};
  $('ds-results-close').onclick=()=>$('ds-results-dialog').close();
  function syncDialogScroll(){document.documentElement.classList.toggle('device-dialog-open',!!document.querySelector('dialog[open]'));}
  $('ds-settings-open').onclick=()=>$('ds-settings').showModal();
  $('ds-settings-close').onclick=()=>$('ds-settings').close();
  for(const dialog of [$('ds-settings'),$('ds-results-dialog'),$('ds-review-dialog'),$('ds-history-dialog')]){dialog.addEventListener('close',syncDialogScroll);new MutationObserver(syncDialogScroll).observe(dialog,{attributes:true,attributeFilter:['open']});}
  async function loadListing(){
    const result=await api('/api/device-scenes'),key=JSON.stringify(result.tasks);
    historyRecords=result.tasks;historySelection=new Set([...historySelection].filter(id=>historyRecords.some(r=>r.id===id)));renderHistory();
    if(result.default_request)defaultRequest=result.default_request;
    if(listingKey!==key){listingKey=key;const select=$('ds-tasks');select.replaceChildren(new Option('选择历史场景',''));
      for(const item of result.tasks)select.append(new Option(new Date(item.created_at*1000).toLocaleString()+' · '+item.name+' · '+(item.parent?'修改版':'初始版'),item.id));
    }
    $('ds-tasks').value=current?.id||'';return result;
  }
  function visibleHistory(){const query=$('ds-history-search').value.trim().toLowerCase();return historyRecords.filter(r=>!query||(r.name+' '+r.id).toLowerCase().includes(query));}
  function renderHistory(){
    const host=$('ds-history-list');host.replaceChildren();
    for(const record of visibleHistory()){
      const row=element('label',undefined,'device-history-row'),input=document.createElement('input');input.type='checkbox';input.value=record.id;input.checked=historySelection.has(record.id);input.disabled=blocked();
      input.onchange=()=>{if(input.checked)historySelection.add(record.id);else historySelection.delete(record.id);historyControls();};
      const caption=element('span');caption.append(element('strong',record.name),element('small',new Date(record.created_at*1000).toLocaleString()+' · '+record.id.slice(0,8)+' · '+(phaseNames[record.phase]||record.phase)+(record.id===current?.id?' · 当前场景':'')));row.append(input,caption);host.append(row);
    }
    if(!host.children.length)host.append(element('p','暂无匹配的场景记录','device-help'));
    historyControls();
  }
  function historyControls(){$('ds-history-selection').textContent='已选 '+historySelection.size+' / '+historyRecords.length+' 条场景记录';$('ds-history-delete').disabled=blocked()||!historySelection.size;$('ds-history-none').disabled=blocked()||!historySelection.size;}
  function retireHistory(ids,fromStorage=false){
    const retired=new Set(ids);clearRetiredSources(ids);
    if(retired.has(current?.id)){sourceVersion++;clearTimeout(timer);playing=false;replay=null;chosen.clear();render(null);}
    window.dispatchEvent(new CustomEvent('device-scenes-retired',{detail:{task_ids:ids}}));
    if(!fromStorage)localStorage.setItem('device-scene-retired-event',JSON.stringify({task_ids:ids,time:Date.now()}));
  }
  async function selectTask(id){
    const version=++sourceVersion;
    playing=false;replay=null;chosen.clear();componentKey='';resultKey='';
    const record=await api('/api/device-scene/state?'+qs({task:id}));if(version!==sourceVersion)return;
    render(record);$('ds-settings').close();await loadListing();
  }
  function schedule(){clearTimeout(timer);timer=null;if(current?.busy)timer=setTimeout(poll,1000);}
  async function poll(){
    if(!current||loading)return;loading=true;const id=current.id;
    try{const result=await api('/api/device-scene/state?'+qs({task:id}));if(current?.id===id){const wasBusy=current.busy;render(result);if(wasBusy&&!result.busy)await loadListing();}}
    catch(exc){error('读取场景进度失败：'+exc.message);timer=setTimeout(poll,2500);}
    finally{loading=false;}
  }
  async function post(action,data={}){
    const version=sourceVersion;
    working=true;controls();error();
    try{const result=await api('/api/device-scene/'+action,{task:current?.id,revision:current?.revision,...data});if(version!==sourceVersion)return false;render(result);if(action==='import-package'||action==='create')$('ds-settings').close();await loadListing();return true;}
    catch(exc){error(exc.message);return false;}
    finally{working=false;controls();}
  }
  function render(record){
    const changed=current?.id!==record?.id;if(changed){reviewSelection.clear();reviewContext=null;$('ds-results-dialog').close();$('ds-review-dialog').close();$('ds-review-search').value='';$('ds-review-filter').value='all';playing=false;replay=null;resultKey='';componentKey='';$('ds-aml').value='';$('ds-edit-request').value='';$('ds-replay-time').value=0;$('ds-replay-clock').textContent='0.00 s';$('ds-play').textContent='播放测试回放';}
    if(changed){testSelection.clear();testModulesKey='';testReplayVersion++;$('ds-test-replay').value='';$('ds-test-signals').textContent='';}
    if(changed){visibilityMode='only';visibleComponents=null;visibilityModel='';$('ds-component-search').value='';if(viewer)viewer.reset();}
    current=record;if(current)localStorage.setItem('device-scene-task',current.id);else localStorage.removeItem('device-scene-task');
    if(changed){const url=new URL(location.href);if(current)url.searchParams.set('scene_task',current.id);else url.searchParams.delete('scene_task');history.replaceState(null,'',url);}
    renderAmlSource();
    renderTestModules();
    $('ds-playback-card').hidden=!current?.scene_ready;
    const modelTreeReady=!!(current?.device_ir&&current?.scene_ready);
    $('ds-results').hidden=!current;$('ds-edit-card').hidden=!modelTreeReady;
    $('ds-visibility-toolbar').hidden=!modelTreeReady;
    $('ds-edit-card').parentElement?.classList.toggle('is-awaiting-model',!modelTreeReady);
    $('ds-pipeline').hidden=!current?.device_ir;
    $('ds-preview-note').hidden=!current?.scene_ready;
    if(!current){chosen.clear();tree=null;$('ds-components').replaceChildren();syncSelection();}
    if(!current){$('ds-title').textContent='等待设备场景';$('ds-status').textContent='未生成';$('ds-stage').textContent='';$('ds-viewer').hidden=true;$('ds-empty').hidden=false;$('ds-metrics').hidden=true;$('ds-replay-controls').hidden=true;$('ds-collision-control').hidden=true;controls();return;}
    $('ds-title').textContent=current.device_ir?.station||current.name;
    const checks=current.reports?.behavior;
    $('ds-status').textContent=current.busy?(current.phase==='parsing'?'解析与查询组件库':phaseNames[current.phase]||'处理中'):current.model_frozen?'模型已冻结':current.error?(phaseNames[current.phase]||'未完成'):current.parse_only&&!current.scene_ready?'AML 已解析 · 待生成装配':checks?.status==='passed'?(current.all_approved?'检查通过 · 已审批':'检查通过 · 待审批'):current.scene_ready?'候选模型':'待完成';
    const leaves=current.device_ir?leafIds(current):new Set(),reused=(current.component_resolution||[]).filter(c=>c.origin==='approved_library'&&leaves.has(c.component_id)).length;
    const fresh=current.generation_policy==='full_regeneration_no_reuse';
    $('ds-pipeline').textContent='AML '+(current.source_sha256||'').slice(0,8)+' → DeviceIR 已解析 → '+(fresh?'完整重生成':'组件库匹配 '+reused+' 个最小组件')+' → '+(current.candidates?.length?'已生成 '+current.candidates.length+' 类候选规则':current.missing_types?.length?'待生成 '+current.missing_types.length+' 类规则':current.busy?'准备规则中':'无需新增规则')+' → '+(current.scene_ready?'已实例化并装配':'等待实例化与装配')+' → '+(current.model_frozen?'已冻结':'待冻结');
    $('ds-preview-note').textContent=current.model_frozen?'设备模型已冻结，可用于后续 PLC 验收；PLC 验收结果单独记录。':current.geometry_only?'当前仅显示候选几何与运动副。组件行为仍有未解决问题，未执行整机动态检查，不能冻结或用于 PLC 验收。':current.all_approved?'组件已审批，仍需通过整机行为检查并冻结，才能用于 PLC 验收。':'当前显示未全部审批的候选模型，用于核对几何、装配和运动；审批并冻结后才能用于 PLC 验收。';
    if(current.resource_package){
      $('ds-status').textContent=current.busy?'正在导入资源':current.scene_ready?'资源装配预览':current.resource_preview_issue?.code==='no_geometry'?'AML 已解析 · 缺少三维资源':'导入未完成';
      $('ds-pipeline').textContent='AML → 资源引用解析 → DAE 组件导入 → MuJoCo 装配 → 数据对账';
      $('ds-preview-note').textContent='使用已导入的组件模型及声明的机械关系。IO、碰撞配置及待确认装配项详见检查结果，当前未冻结。';
    }
    $('ds-preview-note').dataset.frozen=String(current.model_frozen);
    $('ds-stage').textContent=current.stage||'';
    if(current.error)error((layerNames[current.error.layer]||'设备场景')+'：'+current.error.message);else error();
    const metrics=$('ds-metrics');metrics.hidden=!current.device_ir;metrics.replaceChildren();
    if(current.device_ir){for(const [value,label] of [[leafCount(current),'组件'],[current.device_ir.joints.length,'运动副'],[current.device_ir.io.length,'IO'],[current.candidates.filter(c=>c.approved).length+'/'+current.candidates.length,'组件审批']]){const span=element('span');span.append(element('strong',value),document.createTextNode(label));if(label==='组件')span.title='仅统计没有子组件的最小组件；复合组件和运动副不重复计数';metrics.append(span);}}
    if(current.resource_package&&current.device_ir){metrics.replaceChildren();for(const [value,label] of [[current.package_counts.components,'AML 组件'],[current.package_counts.imported_meshes,'网格资源'],[current.package_assembly.movable_joints,'可动关节'],[current.package_counts.connections,'安装连接']]){const span=element('span');span.append(element('strong',value),document.createTextNode(label));metrics.append(span);}}
    if(current.resource_package)$('ds-playback-card').hidden=true;
    const showAssembly=current.scene_ready||current.busy||!!current.assembly_preview;
    $('ds-viewer').hidden=!showAssembly;$('ds-empty').hidden=showAssembly;
    $('ds-empty').textContent=current.busy?'正在构建设备场景，可在下方查看进度':current.parse_only?'AML 已解析，请点击“生成设备场景”':'模型尚未生成，请查看组件检查或错误记录';
    if(!current.busy&&current.resource_preview_issue){
      $('ds-empty').textContent=current.resource_preview_issue.message;
      $('ds-package-path').closest('details').open=true;
      $('ds-stage').textContent='未加载三维几何，尚无可显示的装配场景';
    }
    if(showAssembly){
      if(!viewer){viewer=new SceneViewer($('ds-viewer'));viewer.camera=[120,-32,1.05,0,0];viewer.onSelect=selectFromView;viewer.litSelection=true;viewer.background=[.937,.953,.968,1];viewer.onDraw=vp=>jointPanel.drawLabels(vp);viewer.canvas.setAttribute('aria-label','设备三维场景：单击选中组件，Ctrl 或 Command 加单击多选，Escape 清空；拖动旋转，右键平移，滚轮缩放');}
      if(!viewer.appearance)viewer.appearance=new SceneAppearance(viewer);
      viewer.assemblyEnabled=true;viewer.frameRate=30;
      viewer.appearance.setComponents(current.device_ir?.components||[],!!current.resource_package);
      if(previewTask!==current.id){previewTask=current.id;viewer.camera=[120,-32,current.resource_package ? 2.2 : (current.geometry_only||current.device_ir?.joints.length>1?1.9:1.05),0,0];}
      if(current.assembly_preview&&(current.busy||!current.scene_ready)){
        const id=current.id,preview=current.assembly_preview;
        viewer.load('/api/device-scene/assembly-view?'+qs({task:id,v:preview.token,revision:preview.revision})).then(()=>{
          if(current?.id===id){if(current.error){viewer.status.textContent=current.error.message;if(viewer.assembly)viewer.assembly.failed=true;viewer.host.dataset.assembling='false';}controls();}
        });
      }else if(current.scene_ready){
      const id=current.id,previousVersion=viewer.jointPreviewVersion;viewer.active=false;viewer.load('/api/device-scene/view?'+qs({task:current.id,v:current.preview_revision})).then(()=>{
        if(current?.id!==id)return;
        $('ds-collision-control').hidden=!viewer.meshes.some(m=>m.collisionProxy);
        syncSelection();
        if(previousVersion!==viewer.jointPreviewVersion)fitSelection();
        if(current.resource_package&&previousVersion!==viewer.jointPreviewVersion)poll();
        controls();
      });
      }else{viewer.status.textContent=current.stage||'正在装配…';viewer.host.classList.add('assembly-wait');}
    }
    $('ds-replay-controls').hidden=!current.has_replay&&!current.test_modules?.some(m=>m.replay);
    const ckey=JSON.stringify([current.id,current.device_ir?.facts_sha256,modelTreeReady]);
    if(ckey!==componentKey){
      componentKey=ckey;const ids=new Set([...(current.device_ir?.components||[]),...(current.device_ir?.joints||[])].map(c=>c.id));if(current.device_ir||!current.busy)chosen=new Set([...chosen].filter(id=>ids.has(id)));$('ds-components').replaceChildren();
      if(modelTreeReady)renderTree();else{tree=null;chosen.clear();}
    }
    syncSelection();
    const rkey=JSON.stringify([current.id,current.candidates,current.reports,current.files,current.pending,current.independent_io,current.model_frozen]);
    if(rkey!==resultKey){resultKey=rkey;renderResults();}
    $('ds-logs').textContent=(current.logs||[]).map(row=>new Date(row.time*1000).toLocaleTimeString()+'  '+row.message).join('\n');
    if(changed){$('ds-tasks').value=current.id;$('ds-request').value=current.request;}
    controls();schedule();
    publishSource(current);
    if(current.model_frozen&&current.all_approved&&!current.busy&&!current.error&&current.scene_kind==='scene'){
      const key=current.id+':'+current.model_xml_sha256;
      if(key!==publishedFrozenKey){publishedFrozenKey=key;localStorage.setItem('device-scene-last-frozen',current.id);window.dispatchEvent(new CustomEvent('device-scene-frozen',{detail:{task:current.id,model_xml_sha256:current.model_xml_sha256}}));}
    }
  }
  function renderTestModules(){
    const fixed=current?.test_plan?.generator==='fixed_model_io/1';
    $('ds-plc-test').hidden=!fixed;
    $('ds-plc-run').disabled=blocked()||!current?.scene_ready;
    if(fixed&&current.plc_test_connection&&document.activeElement!==$('ds-plc-host')&&document.activeElement!==$('ds-plc-port')){
      $('ds-plc-host').value=current.plc_test_connection.host;$('ds-plc-port').value=current.plc_test_connection.port;
    }
    const modules=current?.test_modules||[];
    $('ds-tests').hidden=!modules.length;
    const key=JSON.stringify([current?.id,modules]);if(key===testModulesKey)return;testModulesKey=key;
    playing=false;replay=null;testReplayVersion++;$('ds-play').textContent='播放测试回放';
    testSelection=new Set([...testSelection].filter(id=>modules.some(m=>m.id===id)));
    const host=$('ds-test-modules');host.replaceChildren();
    const names={passed:'通过',failed:'失败',blocked:'未覆盖',not_run:'未运行'};
    const robotIO=modules.filter(m=>m.ruleset),scenarioCount=modules.filter(m=>m.failure_category==='scenario_blocked').length,replanCount=modules.filter(m=>m.failure_category==='motion_plan_rejected').length;
    const outcome=module=>module.failure_category==='mechanical_relationship_error'?'连接关系错误':module.failure_category==='motion_plan_rejected'?'路径需重规划':module.failure_category==='scenario_blocked'?'工况受阻':module.failure_category==='response_failed'?'响应未达标':module.failure_category==='io_binding_error'?'绑定错误':names[module.status]||'未运行';
    $('ds-test-summary').textContent=(robotIO.length?'IO 绑定 '+robotIO.filter(m=>m.binding_status==='passed').length+'/'+robotIO.length+' · 工况受阻 '+scenarioCount:modules.filter(m=>m.status==='passed').length+'/'+modules.length+' 项通过')+(replanCount?' · 路径需重规划 '+replanCount:'');
    $('ds-test-scope').textContent=current?.test_plan?.requirements_source==='aml'?'测试范围与容差来自 AML。勾选模块可独立运行，冻结前仍须全部检查通过。':'当前 AML 未声明仿真验证要求；按现有设备事实生成测试，请补充验证范围和容差。';
    if(robotIO.length)$('ds-test-scope').textContent+=' IO 绑定采用局部合法激励；越界值只验证拒绝，反馈注入只验证读数。工况受阻和响应未达标分别记录，不推断机械关系错误。';
    if(current?.test_safety?.collision_free_preflight)$('ds-test-scope').textContent+=' 测试脚本已通过当前模型的部件干涉采样预检，执行中仍检查干涉。';
    if(fixed)$('ds-test-scope').textContent='固定程序检查实际模型尺寸、装配和运动副，以及 OpenPLC 双向 IO。Agent 提供模型与校验参数，不生成测试脚本；未连接测试 Runtime 的 IO 项保持未完成。全行程和额外整机功能测试不在本次范围。';
    for(const module of modules){
      const row=element('details',undefined,'device-test-row'),summary=element('summary'),label=element('label',undefined,'device-check'),input=document.createElement('input');
      input.type='checkbox';input.value=module.id;input.checked=testSelection.has(module.id);input.disabled=blocked()||!module.available;
      input.onchange=()=>{if(input.checked)testSelection.add(module.id);else testSelection.delete(module.id);controls();};
      label.onclick=event=>event.stopPropagation();label.append(input,document.createTextNode(module.label));summary.append(label,element('span',outcome(module),'badge'));row.append(summary);
      if(module.ruleset){row.append(element('p',(fixed?'固定检查器':module.script_origin==='agent'?'Agent 生成脚本':'规则基线脚本')+' · IO 绑定：'+({passed:'通过',failed:'错误',inconclusive:'未完成'}[module.binding_status]||'待验证')+' · 机械关系未由此测试判定','device-help'));
        const script=element('details');script.append(element('summary','查看 IO 校验脚本'),element('pre',JSON.stringify(module.steps,null,2)));row.append(script);}
      if(module.motion_scope==='local_relationship_only')row.append(element('p','局部连接关系：'+({passed:'通过',failed:'失败',inconclusive:'未完成'}[module.mechanical_relationship_status]||'未完成')+'；全行程未覆盖。有干涉的测试动作未执行。','device-help'));
      if(module.steps?.[0]?.op==='joint_configuration'){
        const script=element('details');script.append(element('summary',current?.test_safety?.origin==='agent_geometry_aware'?'查看 Agent 多轴避让脚本':'查看多轴测试路径'),element('pre',JSON.stringify(module.steps,null,2)));row.append(script);
      }
      if(module.uncovered)row.append(element('p',module.uncovered,'device-error'));
      for(const check of module.checks||[]){const detail=element('details');detail.append(element('summary',(check.passed?'✓ ':'✕ ')+sceneText.checkName(check.name)),element('pre',JSON.stringify(check,null,2)));row.append(detail);}
      if(!module.checks?.length)row.append(element('p',module.available?'运行后查看实际状态与判定证据':'可执行模型生成后可运行','device-help'));
      host.append(row);
    }
    const select=$('ds-test-replay'),previous=select.value;select.replaceChildren();
    if(current?.has_replay)select.append(new Option('整机行为测试',''));
    for(const module of modules)if(module.replay)select.append(new Option(module.label+' · '+outcome(module),module.id));
    if([...select.options].some(option=>option.value===previous))select.value=previous;
    $('ds-test-signals').textContent='';
  }
  function renderResults(){
    renderResourceResolution();
    for(const [kind,sha] of reviewSelection)if(!current.candidates.some(c=>c.type===kind&&c.review_sha256===sha&&c.can_approve))reviewSelection.delete(kind);
    renderCandidates();
    const pending=current.candidates.filter(c=>!c.approved).length;
    document.querySelector('[data-ds-open="library"]').textContent='人工审批'+(pending?' · 待处理 '+pending:' · '+(current.all_approved?'已完成':'暂无候选'));
    const checked=current.reports.behavior;
    document.querySelector('[data-ds-open="checks"]').textContent='检查结果'+(checked?' · '+checked.checks.filter(c=>c.passed).length+'/'+checked.checks.length:'');
    const host=$('ds-checks');host.replaceChildren();
    const behavior=current.reports.behavior,assembly=current.reports.assembly;
    const fixed=current.test_plan?.generator==='fixed_model_io/1';
    $('ds-check-summary').replaceChildren(element('p',current.model_frozen?'设备模型已冻结；PLC 验收尚未运行。':current.geometry_only?'仅完成初始几何与装配检查，未执行整机动态检查。请在人工审批中查看并解决组件问题。':behavior?.status==='passed'?'独立设备检查通过；PLC 验收尚未运行。':'独立设备检查与 PLC 验收分别记录。','device-help'));
    if(current.resource_package){
      const io=current.independent_io;
      if(io){
        const row=element('section',undefined,'device-io-status');row.dataset.status=io.status;
        row.append(element('h3','独立 IO 绑定 · '+({bound:'已绑定',configured:'配置已匹配',mismatch:'绑定需修复',not_applicable:'未覆盖当前场景',unavailable:'配置不可用'}[io.status]||'待确认')));
        for(const c of io.components||[])row.append(element('p',c.name+' · '+c.joint_count+' 个关节 · '+c.variable_count+' 个 PLC 变量'));
        row.append(element('p',io.message),element('p','当前连接状态未在此检查；组件 IO 绑定不代表整场景验收通过。','device-help'));host.append(row);
      }
      host.append(element('h3','场景待确认项'),element('p','数量按各类问题分别统计，同一组件或连接可能重复出现。','device-help'));
      for(const pending of current.pending||[]){
        const row=element('details',undefined,'device-pending-row');row.dataset.code=pending.code;
        row.append(element('summary',(pending.title||pending.code)+(pending.count!==undefined?' · '+pending.count+' 项':'')));
        row.append(element('p',pending.message||'请查看原始记录。'),element('p','处理要求：'+(pending.action||'核对来源与缺项。')));
        if(pending.affected_examples?.length){
          row.append(element('p','涉及对象示例：','device-help'));const list=element('ul');
          for(const item of pending.affected_examples){const entry=element('li',item.path||item.name);if(item.detail)entry.append(element('p',item.detail,'device-help'));list.append(entry);}row.append(list);
        }
        const raw=element('details',undefined,'device-pending-raw');raw.append(element('summary','查看原始记录'),element('pre',JSON.stringify(pending.raw||pending,null,2)));row.append(raw);host.append(row);
      }
    }else for(const pending of current.pending||[])host.append(element('p','待确认：'+JSON.stringify(pending),'device-error'));
    if(fixed)$('ds-check-summary').replaceChildren(element('p',current.model_frozen?'模型已通过结构与双向 IO 校验并冻结。':'先检查隔离装配模型，再连接专用 OpenPLC Runtime 完成逐点 IO 校验。缺失事实、通信异常或未覆盖项均不能判定通过。','device-help'));
    for(const [label,report] of [[fixed?'结构与 IO 校验':'设备行为检查',behavior],['装配检查',assembly]]){
      if(!report)continue;host.append(element('h3',label+' · '+report.checks.filter(c=>c.passed).length+'/'+report.checks.length));
      for(const check of report.checks){const row=element('details',undefined,'device-check-row');row.append(element('summary',(check.status==='not_evaluated'?'待确认 · ':check.passed?'✓ ':'✕ ')+sceneText.checkName(check.name)),element('pre',JSON.stringify(check,null,2)));host.append(row);}
    }
    const facts=$('ds-facts');facts.replaceChildren();const ir=current.device_ir;
    if(ir){facts.append(element('p','最小组件 '+leafCount(current)+' · 运动副 '+ir.joints.length+' · 安装连接 '+(ir.assemblies||[]).length+'。IO 名称、方向、类型和地址来自 AML。','device-help'));table(facts,['IO 名称','方向','类型','地址','物理量','写入者'],ir.io.map(p=>[p.name,sceneText.direction(p.dir),p.type,p.address,p.quantity,sceneText.writer(p.writer)]));}
    $('ds-files').replaceChildren();
    if(current.scene_ready){const bundle=element('a','完整模型包（含网格）');bundle.href='/device-scene-bundle?'+qs({task:current.id});bundle.download='';$('ds-files').append(bundle);}
    for(const path of current.files)link($('ds-files'),fileNames[path.split('/').at(-1)]||path,path);
    $('ds-boundary').textContent=current.model_frozen?'设备模型已冻结，PLC 验收尚未执行。':current.geometry_only?'候选几何已生成；行为问题尚未解决，当前不能审批冻结或连接 PLC。':'候选模型可预览和独立检查；全部组件审批、整机行为检查通过后才能冻结。';
    if(fixed)$('ds-boundary').textContent=current.model_frozen?'结构与 IO 校验通过，模型已冻结；未执行额外整机功能测试。':'隔离模型完成结构和双向 IO 校验、组件审批后才能冻结导入。';
    if(current.resource_package){
      $('ds-check-summary').replaceChildren(element('p','导入检查与组件独立 IO 绑定分别记录；整场景尚未完成验收。安装接口未声明容差时不判定通过。','device-help'));
      facts.replaceChildren(element('p','AML 组件、接口、源属性和资源去向保存在下方 package_manifest 与 data_destinations 报告中。PLCopen 行为保留，不自动执行。','device-help'));
      const joints=(current.package_assembly.joint_records||[]).filter(j=>!j.locked);
      const jointDetails=element('details');jointDetails.append(element('summary','查看导入的 '+joints.length+' 个可动关节'));
      table(jointDetails,['名称','类型','范围（m / rad）','初值','源文件'],joints.map(j=>[j.name,j.type,j.range,j.initial,j.source_file]));facts.append(jointDetails);
      $('ds-boundary').textContent='资源装配预览；补齐缺项并完成结构、碰撞和 IO 校验后才能冻结。';
    }
  }
  function renderResourceResolution(){
    const host=$('ds-resource-resolution');host.hidden=!current?.resource_package;host.replaceChildren();
    if(!current?.resource_package)return;
    const rows=current.resource_resolution||[],missing=rows.filter(r=>r.options.length);
    host.append(element('h3','组件资源补全 · '+missing.length+' 项待处理'),element('p','已有资源直接装配。缺模型由你逐项选择导入或临时生成；临时模型仅用于本次任务，新任务不会自动复用。','device-help'));
    const statuses={resolved:'已有几何',structural:'结构节点',missing_geometry:'缺少三维模型',needs_classification:'需确认用途'};
    for(const item of rows.filter(r=>r.options.length||['temporary','import','library'].includes(r.source))){
      const row=element('details'),summary=element('summary',item.name+' · '+statuses[item.status]);row.append(summary);
      row.append(element('p','关键尺寸（m）：'+JSON.stringify(item.dimensions_m)+'；'+item.simplifications,'device-help'));
      const button=(label,action,extra=()=>({}))=>{const b=element('button',label);b.type='button';b.disabled=blocked();b.onclick=()=>{if(!blocked())post('resolve-resource',{component_id:item.component_id,action,...extra()});};return b;};
      if(item.status==='needs_classification')row.append(button('作为实体组件补充模型','physical'),button('确认为纯结构节点','structural'));
      if(item.status==='missing_geometry'){
        const field=(label,node)=>{const wrap=element('label',label);wrap.append(node);row.append(wrap);return node;};
        const path=field('补充模型路径（DAE / 三角化 OBJ）',element('input'));path.type='text';
        const unit=field('OBJ 长度单位',element('select'));for(const [v,t] of [['','请选择'],['m','m'],['mm','mm'],['cm','cm']])unit.add(new Option(t,v));
        const up=field('OBJ 向上轴',element('select'));for(const [v,t] of [['','请选择'],['Z_UP','Z'],['Y_UP','Y'],['X_UP','X']])up.add(new Option(t,v));
        const matrix=field('模型坐标到组件坐标的 4×4 矩阵（平移单位 m；相同坐标时使用单位矩阵）',element('textarea'));matrix.rows=3;matrix.value=JSON.stringify([[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]);
        const take=element('button','确认坐标并导入');take.type='button';take.disabled=blocked();take.onclick=()=>{try{const model_to_component=JSON.parse(matrix.value);post('resolve-resource',{component_id:item.component_id,action:'import',path:path.value.trim(),unit:unit.value,up_axis:up.value,model_to_component});}catch{error('坐标矩阵必须是 JSON 二维数组');}};
        row.append(take,button('Agent 调用工具临时生成','temporary'));
        row.append(element('p','临时生成目前支持 AML 明确声明的简单方盒；缺少尺寸、原点、接口或连接定义时，会列出缺项。','device-help'));
        const library=current.resource_library||[];
        if(library.length){const select=field('选择共享库资源',element('select'));select.add(new Option('请选择',''));for(const e of library)select.add(new Option(e.name+' · '+e.version+' · '+e.kind,e.id));
          const diff=element('pre');row.append(diff);select.onchange=()=>{const e=library.find(e=>e.id===select.value);diff.textContent=e?JSON.stringify({AML尺寸:item.dimensions_m,候选尺寸:e.dimensions_m,候选接口:e.interfaces,候选机械定义:e.mechanics,适用范围:e.scope},null,2):'';};
          row.append(button('选定并绑定该资源','library',()=>({library_id:select.value})));}
      }
      if(item.status==='resolved'&&item.decision?.asset){
        const version=element('input');version.placeholder='版本，例如 1.0.0';version.setAttribute('aria-label','共享库组件版本');
        const scope=element('input');scope.placeholder='适用范围';scope.setAttribute('aria-label','共享库组件适用范围');row.append(version,scope,button('单独加入共享组件库','publish',()=>({version:version.value,scope:scope.value})));
      }
      host.append(row);
    }
  }
  function visibleCandidates(){
    const search=$('ds-review-search').value.trim().toLowerCase(),status=$('ds-review-filter').value;
    return (current?.candidates||[]).filter(c=>(c.type+' '+c.version).toLowerCase().includes(search)&&(status==='all'||status==='pending'&&c.can_approve||status==='approved'&&c.approved||status==='blocked'&&!c.approved&&!c.can_approve));
  }
  function renderCandidates(){
    const candidates=$('ds-candidates');candidates.replaceChildren();const visible=visibleCandidates(),all=current?.candidates||[];
    $('ds-approval-summary').textContent=all.length?'显示 '+visible.length+' / '+all.length+' 个组件规则版本 · 已审批 '+all.filter(c=>c.approved).length+' · 可审批 '+all.filter(c=>c.can_approve).length:'当前没有候选规则；已有审批组件会直接复用。';
    for(const c of visible){const row=element('div',undefined,'device-candidate'),title=element('label'),input=element('input');input.type='checkbox';input.value=c.type;input.checked=reviewSelection.get(c.type)===c.review_sha256;input.disabled=blocked()||!c.can_approve;input.setAttribute('aria-label','选择审批 '+c.type);
      input.onchange=()=>{if(input.checked)reviewSelection.set(c.type,c.review_sha256);else reviewSelection.delete(c.type);controls();};
      const caption=element('span');caption.append(element('strong',c.type+' · '+c.version),element('small',c.approved?'已人工审批':!c.review_current?'检查已过期，需复查':c.validation_status==='passed'?'组件检查通过 · 待人工审批':c.geometry_ready?'几何检查通过 · 存在未解决问题':'组件检查未通过'));title.append(input,caption);
      const button=element('button','查看详情');button.type='button';button.onclick=()=>openReview(c.type);row.append(title,button);candidates.append(row);}
    if(!visible.length)candidates.append(element('p',all.length?'没有匹配的组件。':'暂无候选组件。','device-help'));
    controls();
  }
  $('ds-review-search').oninput=renderCandidates;$('ds-review-filter').onchange=renderCandidates;
  $('ds-select-visible').onclick=()=>{if(blocked())return;for(const c of visibleCandidates())if(c.can_approve)reviewSelection.set(c.type,c.review_sha256);renderCandidates();};
  $('ds-clear-reviews').onclick=()=>{reviewSelection.clear();renderCandidates();};
  $('ds-batch-review').onclick=()=>openReview();
  function appendReview(host,c){
    host.append(element('p',c.approved?'此版本已人工审批':c.can_approve?'检查通过，等待你核对并审批':'此版本尚不能审批，请处理失败或过期的检查','device-help'));
    if(c.last_revalidation?.status==='failed')host.append(element('p','本次复查未通过，已保留原审批记录；修复并重新检查后才能冻结。','device-error'));
    if(c.has_preview){const img=document.createElement('img');img.src=fileUrl('preview.png',c.type)+'&v='+encodeURIComponent(c.review_sha256);img.alt=c.type+' 组件预览';host.append(img);}else host.append(element('p','结构容器没有独立实体几何，请核对结构规则。'));
    host.append(element('h3','参数适用范围'));table(host,['参数','最小值','最大值'],Object.entries(c.rule.parameter_bounds).map(([k,v])=>[k,...v]));
    const ids=new Set((current.device_ir?.components||[]).filter(item=>item.type===c.type).map(item=>item.id));
    const points=(current.device_ir?.io||[]).filter(point=>ids.has(point.component_id));
    host.append(element('h3','驱动与信号绑定'),element('p','行为内核：'+(c.rule.model.behavior||'none')));
    if(points.length){const bindings=element('div',undefined,'device-io-bindings');bindings.tabIndex=0;bindings.setAttribute('role','region');bindings.setAttribute('aria-label','PLC 信号绑定，可横向滚动');table(bindings,['PLC 变量','方向','类型','地址','绑定量','写入者'],points.map(p=>[p.name,sceneText.direction(p.dir),p.type,p.address,p.quantity,sceneText.writer(p.writer)]));host.append(bindings);}
    for(const [heading,items] of [['建模近似',c.rule.assumptions],['未解决问题（阻断 PLC 验证）',c.rule.unresolved],['简化与适用范围（不阻断当前验证）',c.rule.provenance?.limitations||[]]]){
      host.append(element('h3',heading));const list=element('ul');
      for(const item of items.length?items:['无']){const description=sceneText.describe(item),row=element('li',description.text);
        if(description.original){const source=element('details',undefined,'device-description-source');source.append(element('summary',description.translated?'查看英文原文':'查看原文（待补充中文译文）'),element('p',description.original));row.append(source);}
        list.append(row);
      }
      host.append(list);
    }
    host.append(element('h3','组件检查'),element('p',(c.validation?.checks||[]).filter(r=>r.passed).length+'/'+(c.validation?.checks||[]).length+' 项通过'));
    const checks=element('details');checks.append(element('summary','查看检查证据'),element('pre',JSON.stringify(c.validation,null,2)));host.append(checks);
    const rule=element('details');rule.append(element('summary','查看建模规则和当前审批校验值'),element('pre',JSON.stringify(c.rule,null,2)),element('pre',c.review_sha256));host.append(rule);
    const files=element('div',undefined,'device-downloads');link(files,'建模规则','rule.json',c.type);link(files,'检查报告','validation.json',c.type);if(c.rule.blender_script)link(files,'Blender 源文件','geometry/component.blend',c.type);
    for(const body of c.rule.model.blender_bodies||[])if(body.mesh)link(files,body.body+' 刚体 OBJ',body.mesh,c.type);
    host.append(files);
  }
  function openReview(kind){
    if(working)return;
    const items=kind?current.candidates.filter(c=>c.type===kind):current.candidates.filter(c=>reviewSelection.get(c.type)===c.review_sha256&&c.can_approve);
    if(!items.length)return;
    const host=$('ds-review-content');host.replaceChildren();
    reviewContext={task:current.id,revision:current.revision,items:items.map(c=>({component_type:c.type,review_sha256:c.review_sha256}))};
    $('ds-review-title').textContent=kind?items[0].type+' · '+items[0].version:'批量审批 · '+items.length+' 个组件版本';
    if(current.model_requirements){const scope=element('details');scope.append(element('summary',current.model_requirements.purpose));const list=element('ul');for(const text of current.model_requirements.required)list.append(element('li',text));scope.append(list,element('p',current.model_requirements.simplification));host.append(scope);}
    if(kind)appendReview(host,items[0]);else{
      host.append(element('p','展开核对所选组件，确认后批量提交；审批说明选填，每个版本分别保存审批记录。','device-help'));
      for(const c of items){const detail=element('details');detail.append(element('summary',c.type+' · '+c.version));appendReview(detail,c);host.append(detail);}
    }
    $('ds-approve').textContent=kind?'批准当前组件版本':'批准所选 '+items.length+' 个组件版本';
    $('ds-approve-form').hidden=items.every(c=>c.approved);$('ds-review-confirmed').checked=false;$('ds-review-note').value='';$('ds-review-error').hidden=true;
    $('ds-review-confirmed').parentElement.lastChild.textContent=kind?'我已核对该版本的参数、建模近似、预览与检查报告':'我已逐项核对以上所有版本的参数、建模近似、预览与检查报告';
    $('ds-reviewer').value=localStorage.getItem('device-scene-reviewer')||'';controls();$('ds-review-dialog').showModal();
  }
  $('ds-review-close').onclick=()=>$('ds-review-dialog').close();
  $('ds-review-dialog').addEventListener('cancel',event=>{if(working)event.preventDefault();});
  for(const id of ['ds-reviewer','ds-review-note','ds-review-confirmed'])$(id).addEventListener('input',controls);
  $('ds-approve-form').onsubmit=async event=>{
    event.preventDefault();if($('ds-approve').disabled)return;const context=reviewContext,completed=[],reviewer=$('ds-reviewer').value.trim(),note=$('ds-review-note').value.trim();working=true;controls();
    try{
      let state=await api('/api/device-scene/state?'+qs({task:context.task}));
      if(state.revision!==context.revision||!context.items.every(item=>state.candidates.some(c=>c.type===item.component_type&&c.review_sha256===item.review_sha256&&c.can_approve)))throw Error('审批版本已更新，请关闭后重新核对所选组件。');
      // Keep the existing single-version server gate and audit record for every
      // decision. Each response provides the revision for the next request.
      for(const item of context.items){
        $('ds-approve').textContent='正在审批 '+(completed.length+1)+' / '+context.items.length;
        state=await api('/api/device-scene/approve',{task:context.task,revision:state.revision,...item,reviewer,note,confirmed:true});completed.push(item.component_type);
      }
      localStorage.setItem('device-scene-reviewer',reviewer);$('ds-review-dialog').close();render(state);
    }
    catch(exc){reviewContext=null;$('ds-approve').textContent='请关闭后重新核对';showError('ds-review-error',exc.message,completed.length?'已保存 '+completed.length+' 项（'+completed.join('、')+'），其余项请刷新后核对；':'');try{render(await api('/api/device-scene/state?'+qs({task:context.task})));}catch(_){} }
    finally{working=false;controls();}
  };
  async function useAml(file){
    if(!file)return false;if(blocked()){error('设备场景任务正在处理，请先完成或停止当前任务后再替换 AML');return false;}
    if(!file.name.toLowerCase().endsWith('.aml')||!file.size||file.size>2*1024*1024){error('请选择非空且不超过2 MiB的 AML 文件');return false;}
    const version=++sourceVersion,request=current&&$('ds-request').value===current.request?defaultRequest:$('ds-request').value.trim()||defaultRequest;
    clearTimeout(timer);playing=false;replay=null;chosen.clear();render(null);publishSource(null,file.name);
    $('ds-aml-name').textContent=file.name;$('ds-aml-status').textContent='正在重新解析';working=true;controls();error();
    try{
      const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
      const record=await api('/api/device-scene/import-package',{aml:{name:file.name,data:btoa(binary)},request});
      if(version!==sourceVersion)return false;render(record);$('ds-settings').close();await loadListing();return true;
    }catch(exc){if(version===sourceVersion){error('新 AML 解析失败：'+exc.message);$('ds-aml-name').textContent=file.name;$('ds-aml-status').textContent='解析失败';publishSource(null,file.name,exc.message);}return false;}
    finally{working=false;controls();}
  }
  $('ds-aml').onchange=()=>useAml($('ds-aml').files[0]);
  $('ds-aml-choose').onclick=()=>{if(!blocked())$('ds-aml').click();};
  $('ds-aml-reset').onclick=()=>{if(blocked())return;$('ds-aml').value='';renderAmlSource();};
  $('ds-create-form').onsubmit=async event=>{
    event.preventDefault();if(blocked())return;const file=$('ds-aml').files[0],request=$('ds-request').value.trim();
    if(!request){error('请输入设备场景需求');return;}const payload={request,method:'agent'};
    if(file){if(!file.name.toLowerCase().endsWith('.aml')||!file.size||file.size>2*1024*1024){error('请选择非空且不超过2 MiB的 AML 文件');return;}
      const bytes=new Uint8Array(await file.arrayBuffer());let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));payload.aml={name:file.name,data:btoa(binary)};
    }else if(current)payload.source_task=current.id;else{error('请先选择 AML 文件');return;}
    chosen.clear();await post(current?.resource_package||file?'import-package':'create',payload);
  };
  $('ds-package-import').onclick=async()=>{
    const path=$('ds-package-path').value.trim();if(!path){error('请输入后端主机上的 AML 完整路径');return;}
    await post('import-package',{path});
  };
  $('ds-new').onclick=()=>{sourceVersion++;clearTimeout(timer);playing=false;replay=null;chosen.clear();$('ds-create-form').reset();localStorage.removeItem('device-scene-task');render(null);publishSource(null);$('ds-tasks').value='';$('ds-settings').showModal();};
  $('ds-tasks').onchange=()=>{if($('ds-tasks').value)selectTask($('ds-tasks').value).catch(exc=>error(exc.message));};
  for(const action of ['build','check','freeze','revalidate'])$('ds-'+action).onclick=()=>{playing=false;replay=null;return post('operate',{action});};
  $('ds-tests-all').onclick=()=>{testSelection=new Set((current?.test_modules||[]).filter(m=>m.available).map(m=>m.id));for(const input of $('ds-test-modules').querySelectorAll('input'))input.checked=testSelection.has(input.value);controls();};
  $('ds-tests-clear').onclick=()=>{testSelection.clear();for(const input of $('ds-test-modules').querySelectorAll('input'))input.checked=false;controls();};
  $('ds-tests-run').onclick=()=>{playing=false;replay=null;return post('tests',{test_ids:[...testSelection]});};
  $('ds-plc-run').onclick=()=>post('plc-test',{host:$('ds-plc-host').value.trim(),port:Number($('ds-plc-port').value)});
  $('ds-tests-view').onclick=async()=>{const module=current?.test_modules?.find(m=>testSelection.has(m.id)&&m.replay);if(!module)return;$('ds-results-dialog').close();$('ds-test-replay').value=module.id;await $('ds-test-replay').onchange();$('ds-settings').showModal();$('ds-playback-card').scrollIntoView({block:'nearest',behavior:'smooth'});};
  $('ds-history-open').onclick=async()=>{try{await loadListing();historySelection.clear();$('ds-history-search').value='';showError('ds-history-error');renderHistory();$('ds-history-dialog').showModal();controls();}catch(exc){error(exc.message);}};
  $('ds-history-close').onclick=()=>$('ds-history-dialog').close();
  $('ds-history-search').oninput=()=>{renderHistory();controls();};
  $('ds-history-all').onclick=()=>{for(const r of visibleHistory())historySelection.add(r.id);renderHistory();controls();};
  $('ds-history-none').onclick=()=>{historySelection.clear();renderHistory();controls();};
  $('ds-history-delete').onclick=async()=>{
    if(blocked()||!historySelection.size)return;working=true;controls();showError('ds-history-error');
    const ids=[...historySelection],revisions=Object.fromEntries(historyRecords.filter(r=>historySelection.has(r.id)).map(r=>[r.id,r.revision]));
    try{const result=await api('/api/device-scene/delete-history',{task_ids:ids,revisions});retireHistory(result.retired_tasks);historySelection.clear();listingKey='';await loadListing();renderHistory();
      if(result.cache_status==='pending')showError('ds-history-error','记录已移除，部分缓存暂未删除：'+result.cache_error);
      else $('ds-history-selection').textContent='已删除 '+result.retired_tasks.length+' 条场景记录及缓存';
    }catch(exc){showError('ds-history-error',exc.message);await loadListing();}finally{working=false;controls();}
  };
  window.addEventListener?.('storage',event=>{if(event.key==='device-scene-retired-event'&&event.newValue){try{retireHistory(JSON.parse(event.newValue).task_ids,true);loadListing().catch(()=>{});}catch{}}});
  $('ds-test-replay').onchange=async()=>{playing=false;replay=null;testReplayVersion++;$('ds-replay-time').value=0;$('ds-play').textContent='播放测试回放';try{await loadReplay();applyFrame(0);}catch(exc){error(exc.message);}};
  $('ds-stop').onclick=()=>post('stop');
  $('ds-edit-request').oninput=controls;
  $('ds-clear-selection').onclick=()=>{if(blocked())return;chosen.clear();syncSelection();};
  for(const button of document.querySelectorAll('[data-ds-visibility]'))button.onclick=()=>changeVisibility(button.dataset.dsVisibility);
  $('ds-component-search').oninput=filterComponents;
  $('ds-linked-visibility').onchange=()=>{syncVisibility();fitSelection();};
  $('ds-fit-view').onclick=fitSelection;
  $('ds-fullscreen').onclick=()=>{const stage=$('ds-edit-card').parentElement;if(document.fullscreenElement)document.exitFullscreen();else stage.requestFullscreen().catch(e=>error(e.message));};
  $('ds-components').onkeydown=event=>{if(!tree)return;const item=event.target.closest('[role=treeitem]'),id=item?.dataset.nodeId;if(!id)return;
    const visible=[...$('ds-components').querySelectorAll('input:not(:disabled),.device-tree-class,.device-tree-reference:not(:disabled)')].filter(n=>n.getClientRects().length),index=visible.indexOf(event.target);let target=null;
    if(event.key==='ArrowDown')target=visible[index+1];else if(event.key==='ArrowUp')target=visible[index-1];else if(event.key==='Home')target=visible[0];else if(event.key==='End')target=visible.at(-1);
    else if(event.key==='ArrowRight'){if(collapsed.has(id))setExpanded(id,true);else target=nodeControl(tree.children.get(id)?.[0]);}
    else if(event.key==='ArrowLeft'){if(tree.children.get(id)?.length&&!collapsed.has(id))setExpanded(id,false);else target=nodeControl(tree.parents.get(id));}else return;
    event.preventDefault();target?.focus();};
  $('ds-interface-close').onclick=()=>$('ds-interface-dialog').close();
  $('ds-interface-form').onsubmit=async event=>{event.preventDefault();if(blocked()||!editingInterface)return;const e=editingInterface;
    if(current.id!==e.taskId||current.device_ir.facts_sha256!==e.base){$('ds-interface-error').textContent='设备版本已改变，请关闭后重新打开接口。';$('ds-interface-error').hidden=false;return;}
    const position=['x','y','z'].map(a=>Number($('ds-interface-'+a).value)),angles=['x','y','z'].map(a=>Number($('ds-interface-r'+a).value)*Math.PI/180);if([...position,...angles].some(v=>!Number.isFinite(v)))return;
    const op={op:e.adding?'add_interface':'set_interface_pose',component_id:e.componentId,interface_id:e.interfaceId,position_m:position};if(e.adding)op.name=$('ds-interface-name').value.trim();if(e.rotatable)op.rpy_rad=angles;
    const scope=[...chosen],patch={base_sha256:e.base,editable_ids:scope,reason:'用户在组件属性中'+(e.adding?'新增机械接口':'设置接口位置'),operations:[op]};
    $('ds-interface-save').disabled=true;try{if(await post('edit',{editable_ids:scope,request:patch.reason,patch}))$('ds-interface-dialog').close();}finally{$('ds-interface-save').disabled=false;}
  };
  $('ds-edit-form').onsubmit=async event=>{
    event.preventDefault();if($('ds-edit').disabled)return;
    const task=current.id,version=viewer.jointPreviewVersion;
    agentSubmitting=true;controls();error();playing=false;replay=null;
    try{
      const result=await api('/api/device-scene/agent-command',{task,version,client:jointPanel.client,request:$('ds-edit-request').value.trim()});
      if(current?.id!==task)return;
      chosen=new Set(result.components);syncSelection(result.components[0]);
      jointPanel.renderRows(result.frame.joints,result.frame.positions);jointPanel.apply(result.frame);jointPanel.schedule();
    }catch(exc){error(exc.message);}
    finally{agentSubmitting=false;controls();}
  };
  $('ds-pose-close').onclick=()=>$('ds-pose-dialog').close();
  $('ds-pose-form').onsubmit=async event=>{event.preventDefault();const c=current.device_ir.components.find(c=>chosen.has(c.id));const position=['x','y','z'].map(a=>Number($('ds-pose-'+a).value)),angles=['x','y','z'].map(a=>Number($('ds-pose-r'+a).value)*Math.PI/180);if([...position,...angles].some(v=>!Number.isFinite(v)))return;
    const patch={base_sha256:current.device_ir.facts_sha256,editable_ids:[c.id],reason:'用户在前端调整组件局部位置',operations:[{op:'set_pose',component_id:c.id,position_m:position,rpy_rad:angles}]};
    $('ds-pose-submit').disabled=true;try{if(await post('edit',{editable_ids:[c.id],request:patch.reason,patch}))$('ds-pose-dialog').close();}finally{$('ds-pose-submit').disabled=false;}
  };
  function applyFrame(time){
    if(!replay?.frames?.length||!viewer)return;let frame=replay.frames[0];for(const item of replay.frames){if(item.time_s>time)break;frame=item;}
    viewer.previous=null;viewer.current={transforms:frame.transforms,body_transforms:frame.body_transforms,collisions:frame.collisions,colors:frame.colors,time:performance.now()};viewer.active=false;
    const module=current.test_modules?.find(m=>m.id===$('ds-test-replay').value);
    $('ds-replay-note').textContent=frame.collisions?.length?'检测到部件穿透，回放停在首次干涉位置；请在检查结果中查看证据。':module?.ruleset&&module.steps.some(s=>s.op==='observe')?'反馈状态注入 · 验证实测编码与阈值，不作为驱动测试':module?.kind==='mechanical_joint'?'关节空间连续运动学采样 · 驱动响应见 IO 动态测试':'MuJoCo 实际采样 · 独立设备测试';
    $('ds-test-signals').textContent=Object.entries(frame.signals||{}).map(([name,value])=>name+' = '+value).join('  ·  ');
    $('ds-replay-time').value=time;$('ds-replay-clock').textContent=time.toFixed(2)+' s';
  }
  async function loadReplay(){
    await jointPanel.suspend();
    if(replay)return;const id=current.id,version=testReplayVersion,testId=$('ds-test-replay').value,module=current.test_modules?.find(m=>m.id===testId);
    const path=testId?module?.replay:'behavior_replay.json';if(!path)throw Error('此模块尚无测试回放，请先运行');
    const url=fileUrl(current.scene_kind+'/'+path),response=await fetch(url);if(!response.ok)throw Error('测试回放尚未生成');const data=await response.json();if(current?.id!==id||version!==testReplayVersion)return;
    if(data.model_xml_sha256!==current.model_xml_sha256)throw Error('测试回放与当前模型版本不一致，请重新检查设备行为');
    if(testId&&(data.test_id!==testId||data.device_ir_sha256!==current.device_ir_sha256))throw Error('模块回放与当前 AML 版本不一致');
    if(testId&&(data.plan_sha256!==current.test_plan_sha256||data.definition_sha256!==current.scene_definition_sha256))throw Error('测试脚本或模型绑定已改变，请重新运行此模块');
    replay=data;if(!replay.frames.length)throw Error('此设备尚无完整行为测试回放');$('ds-replay-time').max=replay.frames.at(-1).time_s;
  }
  $('ds-play').onclick=async()=>{try{await loadReplay();if(!replay)return;playing=!playing;if(playing)$('ds-settings').close();replayOffset=Number($('ds-replay-time').value);if(replayOffset>=replay.frames.at(-1).time_s)replayOffset=0;replayStart=performance.now();$('ds-play').textContent=playing?'暂停回放':'播放测试回放';}catch(exc){error(exc.message);}};
  $('ds-replay-time').oninput=async()=>{playing=false;$('ds-play').textContent='播放测试回放';try{await loadReplay();applyFrame(Number($('ds-replay-time').value));}catch(exc){error(exc.message);}};
  $('ds-replay-speed').onchange=()=>{replayOffset=Number($('ds-replay-time').value);replayStart=performance.now();};
  $('ds-show-collision').onchange=()=>{if(viewer)viewer.showCollision=$('ds-show-collision').checked;};
  $('ds-show-mechanism').onchange=()=>{if(viewer)viewer.showMechanism=$('ds-show-mechanism').checked;};
  function animate(now){requestAnimationFrame(animate);if(!playing||!replay||$('workflow-panel-3').hidden)return;const time=replayOffset+(now-replayStart)/1000*Number($('ds-replay-speed').value),end=replay.frames.at(-1).time_s;applyFrame(Math.min(time,end));if(time>=end){playing=false;$('ds-play').textContent='重新播放测试';}}
  requestAnimationFrame(animate);
  window.DeviceScenes={configure(value){config=value;clearRetiredSources(value.retired_device_scene_tasks);},useAml,isBusy:blocked,async activate(){try{await loadListing();if(!current){const id=new URLSearchParams(location.search).get('scene_task')||localStorage.getItem('device-scene-task');if(id){try{await selectTask(id);}catch(exc){localStorage.removeItem('device-scene-task');error(exc.message);}}}else await poll();}catch(exc){error('无法连接设备场景服务：'+exc.message);}}};
  controls();
})();
