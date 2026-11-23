'use strict';
// The backend owns qpos and forward kinematics; the browser only displays frames.
class JointPreviewPanel {
  constructor(host, api, beforeMove) {
    this.host=host;this.api=api;this.beforeMove=beforeMove;
    this.client=crypto.randomUUID().replaceAll('-','');this.context=null;this.key='';
    this.epoch=0;this.active=false;this.pending=0;this.inflight=false;this.lastPoll=0;
    this.sequence=-1;this.rows=[];this.commands=Promise.resolve();
    this.timer=null;this.plcAvailable=false;this.plcActive=false;
    this.labels=document.getElementById('ds-joint-labels');this.lines=document.getElementById('ds-joint-lines');
    this.frame=null;this.annotationsActive=false;
  }
  node(tag,text,className){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(className)n.className=className;return n;}
  placePanel(rect){
    const parent=this.host.parentElement,w=parent.clientWidth,h=parent.clientHeight;
    if(!w||!h)return;
    const margin=8,width=Math.min(Math.max(340,rect.width),w-margin*2),height=Math.min(Math.max(220,rect.height),h-margin*2);
    const left=Math.max(margin,Math.min(w-width-margin,rect.left)),top=Math.max(margin,Math.min(h-height-margin,rect.top));
    this.panelRect={left,top,width,height};
    Object.assign(this.host.style,{left:left+'px',top:top+'px',width:width+'px',height:height+'px',right:'auto',bottom:'auto',margin:'0',maxWidth:'none'});
    this.host.classList.add('is-positioned');
  }
  bindPanel(heading,handle){
    const start=(event,resize)=>{
      if(event.button!==0)return;
      event.preventDefault();event.stopPropagation();
      const box=this.host.getBoundingClientRect(),parent=this.host.parentElement.getBoundingClientRect();
      this.panelDrag={id:event.pointerId,x:event.clientX,y:event.clientY,resize,rect:{left:box.left-parent.left,top:box.top-parent.top,width:box.width,height:box.height}};
      this.host.setPointerCapture(event.pointerId);this.host.classList.add('is-dragging');
    };
    heading.onpointerdown=event=>start(event,false);handle.onpointerdown=event=>start(event,true);
    this.host.onpointermove=event=>{
      const drag=this.panelDrag;if(drag?.id!==event.pointerId)return;
      const dx=event.clientX-drag.x,dy=event.clientY-drag.y,rect={...drag.rect};
      if(drag.resize){rect.width=Math.min(rect.width+dx,this.host.parentElement.clientWidth-rect.left-8);rect.height=Math.min(rect.height+dy,this.host.parentElement.clientHeight-rect.top-8);}
      else{rect.left+=dx;rect.top+=dy;}
      this.placePanel(rect);
    };
    const end=event=>{if(this.panelDrag?.id!==event.pointerId)return;this.panelDrag=null;this.host.classList.remove('is-dragging');if(this.host.hasPointerCapture(event.pointerId))this.host.releasePointerCapture(event.pointerId);};
    this.host.onpointerup=this.host.onpointercancel=this.host.onlostpointercapture=end;
    const keyboard=(event,resize)=>{
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key))return;
      event.preventDefault();event.stopPropagation();
      const box=this.host.getBoundingClientRect(),parent=this.host.parentElement.getBoundingClientRect(),step=event.shiftKey?30:10;
      const rect={left:box.left-parent.left,top:box.top-parent.top,width:box.width,height:box.height};
      const x=(event.key==='ArrowRight'?step:event.key==='ArrowLeft'?-step:0),y=(event.key==='ArrowDown'?step:event.key==='ArrowUp'?-step:0);
      if(resize){rect.width+=x;rect.height+=y;}else{rect.left+=x;rect.top+=y;}this.placePanel(rect);
    };
    heading.onkeydown=event=>keyboard(event,false);handle.onkeydown=event=>keyboard(event,true);
    if(!this.panelObserver){this.panelObserver=new ResizeObserver(()=>{if(this.panelRect&&!this.host.hidden)this.placePanel(this.panelRect);});this.panelObserver.observe(this.host.parentElement);}
    if(this.panelRect)this.placePanel(this.panelRect);
  }
  enqueue(context,action,extra={}) {
    const run=()=>this.api('/api/device-scene/joint-command',{...context,client:this.client,action,...extra});
    const result=this.commands.then(run,run);this.commands=result.catch(()=>{});return result;
  }
  async suspend(){
    this.epoch++;this.active=false;clearTimeout(this.timer);
    if(this.context)await this.enqueue(this.context,'stop');
    this.annotationsActive=false;
    if(this.message)this.message.hidden=true;
    this.buttons();
  }
  sync(record, chosen, viewer) {
    const components=[...chosen].filter(id=>record?.device_ir?.components.some(c=>c.id===id)).sort();
    const ready=record?.scene_ready&&!record.busy&&!record.occupied&&viewer?.jointPreviewVersion;
    const context=ready&&components.length?{task:record.id,version:viewer.jointPreviewVersion,components}:null;
    const key=JSON.stringify(context);
    if(key===this.key)return;
    if(this.context&&(this.active||this.pending))this.enqueue(this.context,'stop').catch(()=>{});
    this.key=key;this.context=context;this.viewer=viewer;this.epoch++;this.active=false;this.sequence=-1;this.rows=[];this.plcAvailable=false;this.plcActive=false;
    this.frame=null;this.annotationsActive=false;this.labels.replaceChildren();this.lines.replaceChildren();
    clearTimeout(this.timer);
    this.host.hidden=!context;this.host.replaceChildren();if(!context)return;
    viewer.frameRate=30;
    const heading=this.node('div',undefined,'device-joint-heading');heading.append(this.node('h4','关节位置'));heading.tabIndex=0;heading.setAttribute('aria-label','移动关节控制面板');this.host.append(heading);
    this.message=this.node('p','','device-help');this.message.hidden=true;this.message.setAttribute('role','status');this.host.append(this.message);
    this.list=this.node('div',undefined,'device-joint-list');this.host.append(this.list);
    const actions=this.node('div',undefined,'device-joint-actions');
    const label=this.node('label','运动时长（秒）');this.duration=this.node('input');
    Object.assign(this.duration,{type:'number',min:'.1',max:'120',step:'.1',value:'2'});label.append(this.duration);actions.append(label);
    this.move=this.node('button','执行所选关节','primary');this.plc=this.node('button','PLC控制');this.stop=this.node('button','停止');this.reset=this.node('button','恢复初始姿态');
    for(const [button,action] of [[this.move,'move'],[this.plc,'plc'],[this.stop,'stop'],[this.reset,'reset']]){button.type='button';button.disabled=true;button.onclick=()=>this.command(action);actions.append(button);}
    this.host.append(actions);
    this.reload=this.node('button','重新加载关节');this.reload.type='button';this.reload.hidden=true;
    this.reload.onclick=async()=>{
      const epoch=this.epoch;this.reload.disabled=true;
      try{const source=viewer.source;viewer.source=null;await viewer.load(source);if(epoch===this.epoch){this.key='';this.sync(record,components,viewer);}}
      catch(e){this.fail(e);}finally{this.reload.disabled=false;}
    };
    this.host.append(this.reload);
    const handle=this.node('button',undefined,'device-joint-resize');handle.type='button';handle.setAttribute('aria-label','调整关节控制面板大小');this.host.append(handle);this.bindPanel(heading,handle);
    this.fetchFrame(true,this.epoch);
  }
  async fetchFrame(metadata,epoch) {
    const context=this.context;if(!context)return;
    const query=new URLSearchParams({task:context.task,client:this.client,version:context.version,metadata:metadata?'1':'0'});
    for(const id of context.components)query.append('component',id);
    this.inflight=true;
    try{
      const frame=await this.api('/api/device-scene/joint-state?'+query);
      if(epoch!==this.epoch)return;
      if(metadata)this.renderRows(frame.joints,frame.positions);
      this.apply(frame);
      if(metadata)this.viewer.fitVisible({top:110,bottom:this.host.offsetHeight+50});
    }catch(e){if(epoch===this.epoch)this.fail(e);}
    finally{this.inflight=false;this.schedule();}
  }
  renderRows(joints,positions) {
    this.list.replaceChildren();this.labels.replaceChildren();this.lines.replaceChildren();this.rows=[];
    for(const joint of joints){
      const card=this.node('div',undefined,'device-joint-row');card.dataset.jointName=joint.joint_name;
      const title=this.node('label',undefined,'device-joint-title'),check=this.node('input');check.type='checkbox';check.checked=joint.controllable;check.disabled=!joint.controllable;
      check.onchange=()=>this.buttons();
      title.append(check,document.createTextNode('ID '+joint.joint_id));
      title.title=`${joint.component_name} / ${joint.name}\n${joint.joint_name}\njnt_qposadr: ${joint.jnt_qposadr}`;
      const identity=this.node('div',undefined,'device-joint-identity');identity.append(title);
      const index=this.node('code',`joint_id: ${joint.joint_id} · jnt_qposadr: ${joint.jnt_qposadr}`,'sr-only');identity.append(index);
      const value=this.node('span',undefined,'device-joint-current');identity.append(value);card.append(identity);
      const input=this.node('input');input.type='number';input.step='any';input.disabled=!joint.controllable;
      input.setAttribute('aria-label',joint.name+' 目标位置（'+joint.unit+'）');
      input.value=String(Number((positions[joint.joint_name][0]*joint.display_factor).toFixed(6)));
      const unit=joint.unit==='deg'?'°':joint.unit,label=this.node('label',undefined,'device-joint-target');label.append(input,this.node('span',unit));card.append(label);
      const limits=this.node('small',undefined,'device-joint-limits');limits.title='限位范围';
      if(joint.range){const bounds=joint.range.map(v=>Number((v*joint.display_factor).toFixed(3)));input.min=String(bounds[0]);input.max=String(bounds[1]);limits.textContent=`${bounds[0]} ～ ${bounds[1]} ${unit}`;}
      else limits.textContent=joint.controllable?'未声明限位':'仅显示状态';card.append(limits);
      const marker=this.node('div',undefined,'device-joint-marker'),markerValue=this.node('span');marker.append(this.node('b','ID '+joint.joint_id),markerValue);marker.title=title.title;this.labels.append(marker);
      const line=document.createElementNS('http://www.w3.org/2000/svg','path'),dot=document.createElementNS('http://www.w3.org/2000/svg','circle');dot.setAttribute('r','3');this.lines.append(line,dot);
      this.list.append(card);this.rows.push({joint,check,input,value,marker,markerValue,line,dot});
    }
    this.buttons();
  }
  buttons(){if(!this.move)return;const enabled=this.rows.some(r=>r.joint.controllable),checked=this.rows.some(r=>r.joint.controllable&&r.check.checked);this.move.disabled=this.reset.disabled=!enabled||this.pending>0||this.plcActive;this.plc.disabled=!this.plcAvailable||!checked||this.pending>0||this.plcActive;this.plc.title=this.plcAvailable?'通过 OpenPLC 控制所选关节':'当前组件未完成 IO 绑定';this.stop.disabled=!this.active&&this.pending===0;}
  apply(frame) {
    if(frame.version!==this.context?.version||frame.sequence<this.sequence)return;
    this.sequence=frame.sequence;this.active=frame.active;
    this.plcAvailable=!!frame.plc?.available;this.plcActive=!!frame.plc?.active;
    this.frame=frame;this.annotationsActive=true;
    for(const row of this.rows){const values=frame.positions[row.joint.joint_name];if(values){const text=values.map(v=>(v*row.joint.display_factor).toFixed(3)).join(', ')+' '+(row.joint.unit==='deg'?'°':row.joint.unit);row.value.textContent=text;row.markerValue.textContent=text;}}
    this.viewer.previous=null;
    this.viewer.current={transforms:frame.transforms,body_transforms:frame.body_transforms,time:performance.now()};
    this.message.textContent=this.rows.length?'':'所选组件及其子组件没有可动关节。';this.message.hidden=!!this.rows.length;
    this.message.className='device-help';if(frame.plc?.error){this.message.textContent=frame.plc.error;this.message.hidden=false;this.message.className='device-error';}this.buttons();this.onChange?.();
  }
  drawLabels(vp){
    const enabled=!!(this.context&&this.annotationsActive&&this.frame&&this.viewer);
    this.labels.hidden=this.lines.hidden=!enabled;if(!enabled)return;
    const w=this.viewer.host.clientWidth,h=this.viewer.host.clientHeight,placed=[];
    const stage=this.viewer.host.getBoundingClientRect(),dock=this.host.getBoundingClientRect();
    const leaders=document.getElementById('ds-joint-leaders').checked;
    this.rows.forEach((row,i)=>{
      const p=this.frame.anchors?.[row.joint.joint_name],show=p&&this.viewer.isComponentVisible(row.joint.component_id);
      row.marker.hidden=!show;row.line.style.display=row.dot.style.display=show&&leaders?'':'none';if(!show)return;
      const clip=[0,1,2,3].map(k=>vp[k]*p[0]+vp[k+4]*p[1]+vp[k+8]*p[2]+vp[k+12]);
      const x=(clip[0]/clip[3]+1)*w/2,y=(1-clip[1]/clip[3])*h/2;
      if(clip[3]<=0||x<0||x>w||y<0||y>h){row.marker.hidden=true;row.line.style.display=row.dot.style.display='none';return;}
      const width=126,height=28,left=Math.max(6,Math.min(w-width-6,x+(i%2?45:-width-45)));
      let top=Math.max(70,Math.min(h-height-10,y-20));
      const ceiling=h-height-10;
      for(let n=0;n<30;n++){
        const candidate=Math.max(70,Math.min(ceiling,top+(n?Math.ceil(n/2)*(height+5)*(n%2?1:-1):0)));
        const overPanel=left<dock.right-stage.left+5&&left+width>dock.left-stage.left-5&&candidate<dock.bottom-stage.top+5&&candidate+height>dock.top-stage.top-5;
        if(!overPanel&&!placed.some(r=>left<r[0]+width+5&&left+width+5>r[0]&&candidate<r[1]+height+4&&candidate+height+4>r[1])){top=candidate;break;}
      }
      placed.push([left,top]);row.marker.style.left=left+'px';row.marker.style.top=top+'px';
      const endX=left>x?left:left+width,endY=top+height/2;
      row.line.setAttribute('d',`M ${x} ${y} L ${endX+(endX>x?-10:10)} ${endY} L ${endX} ${endY}`);row.dot.setAttribute('cx',x);row.dot.setAttribute('cy',y);
    });
  }
  fail(error){this.active=false;this.message.textContent=error.message;this.message.hidden=false;this.message.className='device-error';this.reload.hidden=false;this.buttons();}
  async command(action) {
    const context=this.context,epoch=this.epoch;if(!context)return;
    try{
      const duration=Number(this.duration.value);
      if(action!=='stop'&&(!this.duration.value.trim()||!Number.isFinite(duration)||duration<.1||duration>120))throw Error('运动时长须为 0.1 到 120 秒');
      const targets=[];
      if(action==='move'||action==='plc'){
        if(action==='plc'&&!this.plcAvailable)throw Error('当前组件未完成 IO 绑定');
        for(const {joint,check,input} of this.rows){if(!check.checked||!joint.controllable)continue;
          const v=Number(input.value),position=v/joint.display_factor;
          if(!input.value.trim()||!Number.isFinite(position))throw Error(joint.name+' 的目标位置必须是有限数值');
          if(joint.range&&(position<joint.range[0]-1e-9||position>joint.range[1]+1e-9))throw Error(joint.name+' 的目标位置超出关节限位');
          targets.push({joint_name:joint.joint_name,position});
        }
        if(!targets.length)throw Error('请选择至少一个关节');
      }
      this.pending++;this.buttons();this.beforeMove();
      const frame=await this.enqueue(context,action,{targets,duration});
      if(epoch===this.epoch)this.apply(frame);
    }catch(e){if(epoch===this.epoch){this.message.textContent=e.message;this.message.hidden=false;this.message.className='device-error';if(e.message.includes('版本'))this.fail(e);}}
    finally{this.pending=Math.max(0,this.pending-1);if(epoch===this.epoch){this.buttons();this.schedule();}}
  }
  schedule(){
    clearTimeout(this.timer);
    if(!this.active||this.inflight||this.pending)return;
    this.timer=setTimeout(()=>{
      if(document.hidden||this.host.offsetParent===null){this.lastPoll=performance.now()+200;this.schedule();return;}
      this.lastPoll=performance.now();this.fetchFrame(false,this.epoch);
    },Math.max(0,this.lastPoll+1000/30-performance.now()));
  }
}
globalThis.JointPreviewPanel=JointPreviewPanel;
