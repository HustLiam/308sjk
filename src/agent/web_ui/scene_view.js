'use strict';
// Display-only materials and directional shadows. No simulation state is written.
class SceneAppearance {
  constructor(viewer){
    this.view=viewer;this.gl=viewer.gl;this.components=new Map();this.materials=new Map();this.shadowUpdates=0;
    const gl=this.gl;
    const vertex=`#version 300 es
      layout(location=0) in vec3 position;layout(location=1) in vec3 normal;
      uniform mat4 vp,model,lightVP;out vec3 world,n;out vec4 lightPosition;
      void main(){vec4 p=model*vec4(position,1.);world=p.xyz;n=mat3(model)*normal;lightPosition=lightVP*p;gl_Position=vp*p;}`;
    this.surface=this.program(vertex,`#version 300 es
      precision highp float;in vec3 world,n;in vec4 lightPosition;out vec4 pixel;
      uniform vec3 eye,baseColor,background;uniform float roughness,metalness,selected,ground,shadowEnabled;
      uniform vec4 floorArea;uniform sampler2D shadowMap;
      float visibility(vec3 N,vec3 L){
        if(shadowEnabled<.5)return 1.;
        vec3 p=lightPosition.xyz/lightPosition.w*.5+.5;
        if(any(lessThan(p,vec3(0.)))||any(greaterThan(p,vec3(1.))))return 1.;
        float bias=max(.00025,.0012*(1.-max(dot(N,L),0.))),sum=0.;
        vec2 texel=1./vec2(textureSize(shadowMap,0));
        for(int x=-1;x<=1;x++)for(int y=-1;y<=1;y++)sum+=p.z-bias<=texture(shadowMap,p.xy+vec2(x,y)*texel).r?1.:0.;
        return sum/9.;
      }
      void main(){
        vec3 N=normalize(n);if(!gl_FrontFacing)N=-N;
        vec3 V=normalize(eye-world),L=normalize(vec3(.45,-.6,1.)),H=normalize(L+V);
        float diffuse=max(dot(N,L),0.),shade=visibility(N,L),facing=max(dot(N,V),0.);
        vec3 ambient=mix(vec3(.20,.23,.27),vec3(.55,.60,.67),N.z*.5+.5);
        float fill=max(dot(N,normalize(vec3(-.7,.3,.5))),0.);
        float spec=pow(max(dot(N,H),0.),mix(160.,12.,roughness))*(1.-roughness*.65);
        vec3 specColor=mix(vec3(.25),baseColor*.8,metalness);
        vec3 rgb=baseColor*(ambient+vec3(1.,.94,.85)*diffuse*shade*.65+vec3(.18,.23,.30)*fill)
          +specColor*spec*shade+vec3(.13,.18,.22)*pow(1.-facing,3.)*metalness;
        if(ground>.5){
          vec2 cell=world.xy/floorArea.w;
          vec2 grid=abs(fract(cell-.5)-.5)/max(fwidth(cell),vec2(.0001));
          float line=1.-min(min(grid.x,grid.y),1.);
          rgb=baseColor*(.96-.24*(1.-shade))-line*.025;
          float fade=smoothstep(.48,1.,length((world.xy-floorArea.xy)/floorArea.z));
          rgb=mix(rgb,background,fade);
        }else{
          float edge=pow(1.-facing,2.5)*selected;
          rgb=mix(rgb,vec3(.08,.72,.77),edge*.65);
          rgb+=vec3(.03,.055,.05)*selected;
        }
        pixel=vec4(clamp(rgb,0.,1.),1.);
      }`);
    this.depth=this.program(`#version 300 es
      layout(location=0) in vec3 position;uniform mat4 vp,model;
      void main(){gl_Position=vp*model*vec4(position,1.);}`,`#version 300 es
      precision highp float;void main(){}`);
    this.floor=viewer.mesh(0,[1,1,0]);
    this.shadow={frame:gl.createFramebuffer(),texture:gl.createTexture(),size:Math.min(2048,gl.getParameter(gl.MAX_TEXTURE_SIZE))};
    const target=this.shadow,previous=gl.getParameter(gl.FRAMEBUFFER_BINDING);
    gl.bindTexture(gl.TEXTURE_2D,target.texture);
    gl.texImage2D(gl.TEXTURE_2D,0,gl.DEPTH_COMPONENT24,target.size,target.size,0,gl.DEPTH_COMPONENT,gl.UNSIGNED_INT,null);
    for(const key of [gl.TEXTURE_MIN_FILTER,gl.TEXTURE_MAG_FILTER])gl.texParameteri(gl.TEXTURE_2D,key,gl.NEAREST);
    for(const key of [gl.TEXTURE_WRAP_S,gl.TEXTURE_WRAP_T])gl.texParameteri(gl.TEXTURE_2D,key,gl.CLAMP_TO_EDGE);
    gl.bindFramebuffer(gl.FRAMEBUFFER,target.frame);gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.TEXTURE_2D,target.texture,0);
    gl.drawBuffers([gl.NONE]);gl.readBuffer(gl.NONE);
    target.ready=gl.checkFramebufferStatus(gl.FRAMEBUFFER)===gl.FRAMEBUFFER_COMPLETE;
    gl.bindFramebuffer(gl.FRAMEBUFFER,previous);this.invalidate();
  }
  program(vertex,fragment){
    const gl=this.gl,p=gl.createProgram();
    for(const [type,source] of [[gl.VERTEX_SHADER,vertex],[gl.FRAGMENT_SHADER,fragment]]){
      const s=gl.createShader(type);gl.shaderSource(s,source);gl.compileShader(s);
      if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));
      gl.attachShader(p,s);gl.deleteShader(s);
    }
    gl.linkProgram(p);if(!gl.getProgramParameter(p,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(p));
    const u={};for(const key of ['vp','model','lightVP','eye','baseColor','background','roughness','metalness','selected','ground','floorArea','shadowMap','shadowEnabled'])u[key]=gl.getUniformLocation(p,key);
    return {p,u};
  }
  invalidate(){this.lastMatrices=null;this.groundZ=null;this.floorSelection=null;}
  setComponents(components,automatic){
    this.components=new Map(components.map(c=>[c.id,c]));this.automatic=automatic;this.materials.clear();
  }
  material(mesh){
    if(this.materials.has(mesh))return this.materials.get(mesh);
    const c=this.components.get(mesh.componentId)||{},kind=String(c.type||''),name=String(c.name||'');
    let color=mesh.color?.slice(0,3)||[.55,.61,.65],roughness=.52,metalness=.12;
    if(this.automatic){
      if(/robot/i.test(kind)){color=[.95,.39,.075];roughness=.32;metalness=.22;}
      else if(/clamp|gripper/i.test(kind)){color=[.50,.59,.65];roughness=.26;metalness=.8;}
      else if(/machine/i.test(kind)){color=[.12,.46,.49];roughness=.4;metalness=.35;}
      else if(/zaun|fence|guard/i.test(name)){color=[.30,.37,.41];roughness=.6;metalness=.45;}
      else if(/schrank|cabinet/i.test(name)){color=[.69,.75,.77];roughness=.48;metalness=.3;}
      else{color=[.46,.55,.61];roughness=.44;metalness=.5;}
    }
    const result={color,roughness,metalness};this.materials.set(mesh,result);return result;
  }
  static bounds(mesh,t){
    const b=mesh.bounds||{center:[0,0,0],half:mesh.size||[.05,.05,.05]};
    const center=[0,1,2].map(k=>t[k]+t[3+k*3]*b.center[0]+t[4+k*3]*b.center[1]+t[5+k*3]*b.center[2]);
    const half=[0,1,2].map(k=>Math.abs(t[3+k*3])*b.half[0]+Math.abs(t[4+k*3])*b.half[1]+Math.abs(t[5+k*3])*b.half[2]);
    return {low:center.map((v,k)=>v-half[k]),high:center.map((v,k)=>v+half[k])};
  }
  bind(mesh,normals){
    const gl=this.gl;gl.bindBuffer(gl.ARRAY_BUFFER,mesh.buffer);gl.enableVertexAttribArray(0);gl.vertexAttribPointer(0,3,gl.FLOAT,false,24,0);
    if(normals){gl.enableVertexAttribArray(1);gl.vertexAttribPointer(1,3,gl.FLOAT,false,24,12);}else gl.disableVertexAttribArray(1);
  }
  draw(vp,eye,blend,collisions){
    const v=this.view,gl=this.gl,items=[],low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity],matrices=[];
    v.meshes.forEach((mesh,i)=>{
      if(!v.isComponentVisible(mesh.componentId)||mesh.collisionProxy||!v.assemblyVisible(mesh))return;
      const b=v.current.transforms[i],a=v.previous?.transforms[i]||b;
      const pose=b.map((value,k)=>k<3?a[k]+(value-a[k])*blend:value),t=v.assemblyTransform(mesh,pose);
      items.push({mesh,index:i,matrix:SceneViewer.transform(t)});matrices.push(i,...t);
      if(mesh.geomType!==0){const bounds=SceneAppearance.bounds(mesh,pose);for(let k=0;k<3;k++){low[k]=Math.min(low[k],bounds.low[k]);high[k]=Math.max(high[k],bounds.high[k]);}}
    });
    if(!items.length)return;
    if(!Number.isFinite(low[0])){const b=SceneAppearance.bounds(items[0].mesh,v.current.transforms[items[0].index]);low.splice(0,3,...b.low);high.splice(0,3,...b.high);}
    const center=low.map((value,k)=>(value+high[k])/2),radius=Math.max(.2,Math.hypot(...high.map((value,k)=>value-low[k]))/2);
    const selection=items.map(item=>item.index).join(',');
    if(this.groundZ===null||selection!==this.floorSelection){this.groundZ=low[2]-radius*.005;this.floorSelection=selection;}
    const lightEye=center.map((x,k)=>x+[.36,-.48,.8][k]*radius*3);
    const extent=radius*1.25,near=radius*.1,far=radius*6;
    const ortho=[1/extent,0,0,0,0,1/extent,0,0,0,0,-2/(far-near),0,0,0,-(far+near)/(far-near),1];
    const lightVP=SceneViewer.multiply(ortho,SceneViewer.lookAt(lightEye,center));
    const changed=!this.lastMatrices||matrices.length!==this.lastMatrices.length||matrices.some((x,i)=>x!==this.lastMatrices[i]);
    if(changed&&this.shadow.ready){
      const previous=gl.getParameter(gl.FRAMEBUFFER_BINDING),d=this.depth;
      gl.bindFramebuffer(gl.FRAMEBUFFER,this.shadow.frame);gl.viewport(0,0,this.shadow.size,this.shadow.size);gl.clear(gl.DEPTH_BUFFER_BIT);
      gl.useProgram(d.p);gl.uniformMatrix4fv(d.u.vp,false,lightVP);gl.enable(gl.POLYGON_OFFSET_FILL);gl.polygonOffset(1.5,2.);
      for(const item of items){if(item.mesh.geomType===0)continue;gl.uniformMatrix4fv(d.u.model,false,item.matrix);this.bind(item.mesh,false);gl.drawArrays(gl.TRIANGLES,0,item.mesh.count);}
      gl.disable(gl.POLYGON_OFFSET_FILL);gl.bindFramebuffer(gl.FRAMEBUFFER,previous);this.shadowUpdates++;this.lastMatrices=matrices;
    }
    gl.viewport(0,0,v.canvas.width,v.canvas.height);const {p,u}=this.surface;gl.useProgram(p);
    gl.uniformMatrix4fv(u.vp,false,vp);gl.uniformMatrix4fv(u.lightVP,false,lightVP);gl.uniform3fv(u.eye,eye);
    gl.activeTexture(gl.TEXTURE0);gl.bindTexture(gl.TEXTURE_2D,this.shadow.texture);gl.uniform1i(u.shadowMap,0);gl.uniform1f(u.shadowEnabled,this.shadow.ready?1:0);
    gl.uniform3fv(u.background,(v.background||[.937,.953,.968]).slice(0,3));
    if(!items.some(item=>item.mesh.geomType===0)){
      const r=radius*3,z=this.groundZ;
      gl.uniformMatrix4fv(u.model,false,new Float32Array([r/4,0,0,0,0,r/4,0,0,0,0,1,0,center[0],center[1],z-.002,1]));
      gl.uniform4fv(u.floorArea,[center[0],center[1],r,Math.pow(10,Math.floor(Math.log10(radius)))*.5]);
      gl.uniform3fv(u.baseColor,[.90,.925,.942]);gl.uniform1f(u.ground,1);gl.uniform1f(u.selected,0);
      this.bind(this.floor,true);gl.drawArrays(gl.TRIANGLES,0,this.floor.count);
    }
    gl.uniform1f(u.ground,0);
    for(const {mesh,index,matrix} of items){
      const material=this.material(mesh),color=collisions.has(index)?[.95,.12,.12]:(v.current.colors?.[index]?.slice(0,3)||material.color);
      gl.uniformMatrix4fv(u.model,false,matrix);gl.uniform3fv(u.baseColor,color);gl.uniform1f(u.roughness,material.roughness);gl.uniform1f(u.metalness,material.metalness);
      gl.uniform1f(u.selected,v.selectedIds.has(mesh.componentId)?1:0);this.bind(mesh,true);gl.drawArrays(gl.TRIANGLES,0,mesh.count);
    }
  }
}
// Draw MuJoCo geometry locally. Physics and ink samples always come from the server.
class SceneViewer {
  constructor(host) {
    this.host=host;this.canvas=document.createElement('canvas');this.canvas.tabIndex=0;
    this.canvas.setAttribute('aria-label','三维场景：拖动旋转，右键拖动平移，滚轮缩放');
    this.status=document.createElement('small');this.status.className='view-status';
    host.replaceChildren(this.canvas,this.status);this.camera=[135,-45,1.9,0,0];
    this.gl=this.canvas.getContext('webgl2',{antialias:true,alpha:false,powerPreference:'high-performance'});
    if(!this.gl)throw new Error('浏览器未启用 WebGL2');
    this.source=null;this.meshes=[];this.ink=[];this.strokes=[];this.previous=null;this.current=null;
    this.selectedIds=new Set();this.onSelect=null;this.selectionEnabled=true;this.pickTarget=null;
    this.visibleComponents=null;this.componentChildren={};this.onDraw=null;this.litSelection=false;
    this.showMechanism=true;this.mechanism=null;this.mechanismLabels=[];this.mechanismLeaders=[];
    this.labelLayer=document.createElement('div');this.labelLayer.className='view-mechanism-labels';host.append(this.labelLayer);
    this.frames=0;this.fps=0;this.lastStats=performance.now();this.lastDraw=0;this.lastPoll=0;this.loading=false;this.version=0;
    this.setup();this.bind();this.observer=new ResizeObserver(()=>this.resize());this.observer.observe(host);
    this.tick=this.tick.bind(this);this.animation=requestAnimationFrame(this.tick);
  }
  setup(){
    const gl=this.gl;
    const compile=(kind,src)=>{const s=gl.createShader(kind);gl.shaderSource(s,src);gl.compileShader(s);if(!gl.getShaderParameter(s,gl.COMPILE_STATUS))throw Error(gl.getShaderInfoLog(s));return s;};
    this.program=gl.createProgram();
    gl.attachShader(this.program,compile(gl.VERTEX_SHADER,`#version 300 es
      in vec3 position; in vec3 normal; uniform mat4 vp; uniform mat4 model;
      out vec3 n; void main(){gl_Position=vp*model*vec4(position,1.);n=mat3(model)*normal;}`));
    gl.attachShader(this.program,compile(gl.FRAGMENT_SHADER,`#version 300 es
      precision highp float; in vec3 n;uniform vec4 color;uniform float unlit;out vec4 pixel;
      void main(){float light=mix(.62+.38*max(0.,dot(normalize(n),normalize(vec3(.4,-.3,.9)))),1.,unlit);pixel=vec4(color.rgb*light,color.a);}`));
    gl.linkProgram(this.program);if(!gl.getProgramParameter(this.program,gl.LINK_STATUS))throw Error(gl.getProgramInfoLog(this.program));
    this.uniform={};for(const key of ['vp','model','color','unlit'])this.uniform[key]=gl.getUniformLocation(this.program,key);
    this.attr=[gl.getAttribLocation(this.program,'position'),gl.getAttribLocation(this.program,'normal')];
    gl.enable(gl.DEPTH_TEST);gl.clearColor(.885,.891,.881,1);
    this.inkBuffer=gl.createBuffer();this.inkVertices=0;
    this.mechanismBuffer=gl.createBuffer();
  }
  mesh(type,size,triangles){
    const vertices=[];const push=(p,n)=>vertices.push(...p,...n);
    const quad=(a,b,c,d,n)=>{for(const p of [a,b,c,a,c,d])push(p,n);};
    if(type===7 && triangles){
      for(let i=0;i<triangles.length;i+=9){
        const a=triangles.slice(i,i+3),b=triangles.slice(i+3,i+6),c=triangles.slice(i+6,i+9);
        const u=b.map((v,k)=>v-a[k]),v=c.map((v,k)=>v-a[k]);
        const n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]],length=Math.hypot(...n)||1;
        for(const p of [a,b,c])push(p,n.map(x=>x/length));
      }
    }else if(type===6 || type===0){
      const [x,y,z]=type===0?[Math.max(size[0],4),Math.max(size[1],4),.002]:size;
      quad([x,-y,-z],[x,y,-z],[x,y,z],[x,-y,z],[1,0,0]);quad([-x,y,-z],[-x,-y,-z],[-x,-y,z],[-x,y,z],[-1,0,0]);
      quad([x,y,-z],[-x,y,-z],[-x,y,z],[x,y,z],[0,1,0]);quad([-x,-y,-z],[x,-y,-z],[x,-y,z],[-x,-y,z],[0,-1,0]);
      quad([-x,-y,z],[x,-y,z],[x,y,z],[-x,y,z],[0,0,1]);quad([-x,y,-z],[x,y,-z],[x,-y,-z],[-x,-y,-z],[0,0,-1]);
    }else if([2,3,4,5].includes(type)){
      const slices=32,rings=type===5?2:24;
      const sample=(i,j)=>{
        const t=i/rings*Math.PI,a=j/slices*2*Math.PI;
        if(type===5)return {p:[size[0]*Math.cos(a),size[0]*Math.sin(a),(i?1:-1)*size[1]],n:[Math.cos(a),Math.sin(a),0]};
        const n=[Math.sin(t)*Math.cos(a),Math.sin(t)*Math.sin(a),Math.cos(t)];
        const scales=type===4?size:[size[0],size[0],size[0]];
        return {p:n.map((v,k)=>v*scales[k]+(type===3 && k===2?(Math.cos(t)>=0?1:-1)*size[1]:0)),n};
      };
      for(let i=0;i<rings;i++)for(let j=0;j<slices;j++){
        const points=[sample(i,j),sample(i+1,j),sample(i+1,j+1),sample(i,j+1)];
        for(const k of [0,1,2,0,2,3])push(points[k].p,points[k].n);
      }
      if(type===5)for(const sign of [-1,1])for(let j=0;j<slices;j++){
        const a=j/slices*2*Math.PI,b=(j+1)/slices*2*Math.PI,n=[0,0,sign];
        for(const p of [[0,0,sign*size[1]],[size[0]*Math.cos(a),size[0]*Math.sin(a),sign*size[1]],[size[0]*Math.cos(b),size[0]*Math.sin(b),sign*size[1]]])push(p,n);
      }
    }else throw Error('场景包含尚未支持的几何类型 '+type);
    const gl=this.gl,buffer=gl.createBuffer();gl.bindBuffer(gl.ARRAY_BUFFER,buffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.STATIC_DRAW);
    return {buffer,count:vertices.length/6,bounds:SceneViewer.vertexBounds(vertices)};
  }
  resize(){
    const bounds=this.canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);
    const scale=Math.min(dpr,2560/Math.max(1,bounds.width),1440/Math.max(1,bounds.height));
    const w=Math.max(1,Math.round(bounds.width*scale)),h=Math.max(1,Math.round(bounds.height*scale));
    if(this.canvas.width!==w || this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
  }
  reset(){
    this.loadController?.abort();
    this.releaseAssemblyBuffers();this.assembly=null;this.assemblyPromise=null;this.assemblyScope=null;
    this.host.classList.remove('assembly-wait');this.host.dataset.assembling='false';
    this.host.dataset.loaded='false';
    this.version++;this.source=null;this.live=null;this.active=false;
    this.jointPreviewVersion=null;this.frameRate=60;
    this.visibleComponents=null;this.componentChildren={};
    for(const mesh of this.meshes)this.gl.deleteBuffer(mesh.buffer);
    this.meshes=[];this.previous=null;this.current=null;this.ink=[];this.strokes=[];this.inkVertices=0;this.run=null;
    this.selectedIds.clear();this.status.textContent='';
    this.mechanism=null;this.labelLayer.replaceChildren();this.mechanismLabels=[];this.mechanismLeaders=[];
  }
  async load(source,live){
    if(this.assemblyEnabled)return this.loadAssembly(source);
    if(source===this.source)return;
    this.loadController?.abort();const controller=new AbortController();this.loadController=controller;
    const buffers=new Map();let adopted=false;
    const version=++this.version;this.source=source;this.live=live;this.status.textContent='加载中…';this.host.dataset.loaded='false';
    this.jointPreviewVersion=null;
    try{
      const response=await fetch(source,{signal:controller.signal});const scene=await response.json();if(!response.ok)throw Error(scene.error||'场景加载失败');
      if(version!==this.version)return;
      const entries=Object.entries(scene.mesh_assets||{}),queue=entries.values();
      // Bound concurrent downloads and release each CPU buffer after uploading.
      // Shared mesh instances still reference one GPU buffer, with every face intact.
      await Promise.all(Array.from({length:Math.min(4,entries.length)},async()=>{
        for(const [key,asset] of queue){
          let bytes;
          if(asset.url){
            const response=await fetch(asset.url,{signal:controller.signal});
            if(!response.ok){const error=await response.json();throw Error(error.error||'网格加载失败');}
            bytes=new Uint8Array(await response.arrayBuffer());
          }else bytes=Uint8Array.from(atob(asset.data),c=>c.charCodeAt(0));
          if(version!==this.version||controller.signal.aborted)return;
          if(bytes.byteLength!==asset.vertices*24)throw Error('预览网格数据长度不匹配');
          const buffer=this.gl.createBuffer();this.gl.bindBuffer(this.gl.ARRAY_BUFFER,buffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,bytes,this.gl.STATIC_DRAW);
          buffers.set(key,{buffer,count:asset.vertices,bounds:SceneViewer.vertexBounds(new Float32Array(bytes.buffer))});
        }
      }));
      if(version!==this.version)return;
      const geometry=g=>{
        if(g.mesh_asset===undefined)return this.mesh(g.type,g.size,g.vertices);
        return buffers.get(g.mesh_asset);
      };
      for(const mesh of this.meshes)this.gl.deleteBuffer(mesh.buffer);
      this.meshes=scene.geoms.map(g=>({...geometry(g),color:g.color,size:g.size,geomType:g.type,collisionProxy:g.collision_proxy,visualMesh:g.visual_mesh,componentId:g.component_id||null}));
      adopted=true;this.previewQuality=scene.preview_quality||null;
      this.center=scene.center;this.span=scene.span;this.previous=this.current={transforms:scene.transforms,body_transforms:scene.body_transforms,time:performance.now()};
      this.mechanism=scene.mechanism;this.labelLayer.replaceChildren();this.mechanismLeaders=[];
      this.mechanismLabels=(this.mechanism?.joints||[]).map(j=>{const leader=document.createElement('i');leader.className='view-mechanism-leader';this.mechanismLeaders.push(leader);const label=document.createElement('span');label.className='view-mechanism-label';label.textContent=j.name+' · '+(j.type==='hinge'?'转动副':'运动副');label.title='两端接口随实际刚体运动，橙线表示运动轴';this.labelLayer.append(leader,label);return label;});
      this.ink=[];this.strokes=[];this.inkVertices=0;this.lastInk=-1;this.run=null;this.status.textContent='';this.lastPoll=0;this.resize();this.host.dataset.loaded='true';
      this.jointPreviewVersion=scene.joint_preview_version||null;
      this.componentChildren=scene.component_children||{};
      if(this.appearance)this.appearance.invalidate();
    }catch(exc){controller.abort();if(version===this.version){this.source=null;this.status.textContent=exc.message;}}
    finally{if(!adopted)for(const mesh of buffers.values())this.gl.deleteBuffer(mesh.buffer);}
  }
  releaseAssemblyBuffers(keep=new Set()){
    for(const [key,mesh] of this.assemblyBuffers||[])if(!keep.has(key)){this.gl.deleteBuffer(mesh.buffer);this.assemblyBuffers.delete(key);}
  }
  assemblyVisible(mesh){
    if(!this.assembly)return true;
    const step=this.assembly.steps.get(mesh.componentId);
    return !!mesh.count&&!!step&&step.start!==null&&performance.now()>=step.start;
  }
  assemblyTransform(mesh,values){
    const step=this.assembly?.steps.get(mesh.componentId);
    if(!step||step.start===null)return values;
    const p=Math.max(0,Math.min(1,(performance.now()-step.start)/650));
    if(p===1)return values;
    // Display-only approach offset. Never mutate MuJoCo frames or joint targets.
    const result=values.slice();result[2]+=step.lift*Math.pow(1-p,3);return result;
  }
  stageAssembly(){
    if(!this.assembly||this.assembly.failed)return;
    const state=this.assembly,now=performance.now();let ready=0;
    for(const [id,step] of state.steps){
      if(step.start===null){
        if(step.indices.some(i=>!this.meshes[i]?.count))break;
        step.start=Math.max(now,state.nextStart);state.nextStart=step.start+state.gap;
      }
      if(now>=step.start+650)ready++;
    }
    this.host.dataset.assemblyDone=String(ready);this.host.dataset.assemblyTotal=String(state.steps.size);
    const moving=ready<state.steps.size||!state.downloaded;
    this.host.dataset.assembling=String(moving);
    if(!state.failed)this.status.textContent=moving?'正在装配 '+ready+' / '+state.steps.size:state.streaming?'正在装配…':'';
    if(!moving&&!state.streaming){this.assembly=null;this.onAssemblyComplete?.();}
  }
  loadAssembly(source){
    if(source===this.source)return this.assemblyPromise||Promise.resolve();
    // A polling snapshot must not abort an unfinished batch of real meshes.
    if(source.includes('/assembly-view')&&this.assemblyPromise)return this.assemblyPromise;
    this.loadController?.abort();const controller=new AbortController();this.loadController=controller;
    const version=++this.version;this.source=source;this.live=null;this.active=false;this.jointPreviewVersion=null;
    this.host.dataset.loaded='false';this.host.classList.add('assembly-wait');this.status.textContent='正在装配…';
    const promise=this.fetchAssembly(source,version,controller);
    this.assemblyPromise=promise;
    promise.finally(()=>{if(this.assemblyPromise===promise)this.assemblyPromise=null;});return promise;
  }
  async fetchAssembly(source,version,controller){
    try{
      const response=await fetch(source,{signal:controller.signal}),scene=await response.json();
      if(!response.ok)throw Error(scene.error||'场景加载失败');
      if(version!==this.version)return;
      const streaming=!!scene.assembly_token,scope=scene.assembly_token||scene.joint_preview_version||source;
      const retain=scope===this.assemblyScope||(!streaming&&this.assemblyStreaming);
      const oldSteps=retain?this.assembly?.steps:new Map();
      this.assemblyScope=scope;this.assemblyStreaming=streaming;
      this.assemblyBuffers??=new Map();
      const assets=scene.mesh_assets||{},used=new Set(Object.entries(assets).map(([key,a])=>a.url||scope+':'+key));
      for(const mesh of this.meshes)if(mesh.assemblyPrimitive)this.gl.deleteBuffer(mesh.buffer);
      this.meshes=scene.geoms.map(g=>{
        const asset=assets[g.mesh_asset],key=asset?(asset.url||scope+':'+g.mesh_asset):null;
        const geometry=asset?(this.assemblyBuffers.get(key)||{count:0}):this.mesh(g.type,g.size,g.vertices);
        return {...geometry,assetKey:key,assemblyPrimitive:!asset,color:g.color,size:g.size,geomType:g.type,
          collisionProxy:g.collision_proxy,visualMesh:g.visual_mesh,componentId:g.component_id||null};
      });
      this.releaseAssemblyBuffers(used);
      this.center=scene.center;this.span=scene.span;this.previous=null;
      this.current={transforms:scene.transforms,body_transforms:scene.body_transforms,time:performance.now()};
      this.componentChildren=scene.component_children||{};this.mechanism=scene.mechanism;
      this.labelLayer.replaceChildren();this.mechanismLabels=[];this.mechanismLeaders=[];
      this.mechanismLabels=(this.mechanism?.joints||[]).map(j=>{const leader=document.createElement('i');leader.className='view-mechanism-leader';this.mechanismLeaders.push(leader);const label=document.createElement('span');label.className='view-mechanism-label';label.textContent=j.name+' · '+(j.type==='hinge'?'转动副':'运动副');this.labelLayer.append(leader,label);return label;});
      this.ink=[];this.strokes=[];this.inkVertices=0;
      const components=scene.components||[...(this.appearance?.components.values()||[])];
      if(scene.components)this.appearance?.setComponents(components,true);
      const parents=new Map(components.map(c=>[c.id,c.parent_id]));
      const depth=id=>{let n=0,seen=new Set();while(parents.get(id)&&!seen.has(id)){seen.add(id);id=parents.get(id);n++;}return n;};
      const groups=new Map();this.meshes.forEach((mesh,i)=>{if(!groups.has(mesh.componentId))groups.set(mesh.componentId,[]);groups.get(mesh.componentId).push(i);});
      const steps=new Map([...groups].sort((a,b)=>depth(a[0])-depth(b[0])).map(([id,indices])=>[id,
        {indices,start:oldSteps?.get(id)?.start??null,lift:oldSteps?.get(id)?.lift??Math.max(.05,scene.span*.08)}]));
      this.assembly={steps,nextStart:performance.now(),gap:Math.min(150,6000/Math.max(1,steps.size)),streaming,downloaded:false};
      this.appearance?.invalidate();this.resize();this.stageAssembly();
      const entries=Object.entries(assets).filter(([key,a])=>!this.assemblyBuffers.has(a.url||scope+':'+key));
      const order=new Map();for(const step of steps.values())for(const i of step.indices)if(!order.has(this.meshes[i].assetKey))order.set(this.meshes[i].assetKey,order.size);
      entries.sort((a,b)=>(order.get(a[1].url||scope+':'+a[0])??Infinity)-(order.get(b[1].url||scope+':'+b[0])??Infinity));
      const queue=entries.values();
      await Promise.all(Array.from({length:Math.min(4,entries.length)},async()=>{
        for(const [key,asset] of queue){
          let bytes;if(asset.url){const r=await fetch(asset.url,{signal:controller.signal});if(!r.ok)throw Error('网格加载失败');bytes=new Uint8Array(await r.arrayBuffer());}
          else bytes=Uint8Array.from(atob(asset.data),c=>c.charCodeAt(0));
          if(version!==this.version||controller.signal.aborted)return;
          if(bytes.byteLength!==asset.vertices*24)throw Error('预览网格数据长度不匹配');
          const buffer=this.gl.createBuffer();this.gl.bindBuffer(this.gl.ARRAY_BUFFER,buffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,bytes,this.gl.STATIC_DRAW);
          const item={buffer,count:asset.vertices,bounds:SceneViewer.vertexBounds(new Float32Array(bytes.buffer))},assetKey=asset.url||scope+':'+key;
          this.assemblyBuffers.set(assetKey,item);for(const mesh of this.meshes)if(mesh.assetKey===assetKey)Object.assign(mesh,item);
          this.stageAssembly();
          // Let the browser paint between uploads, including cached responses.
          await new Promise(resolve=>setTimeout(resolve,0));
        }
      }));
      if(version!==this.version)return;
      this.assembly.downloaded=true;this.stageAssembly();this.releaseAssemblyBuffers(used);
      this.host.classList.toggle('assembly-wait',streaming);
      if(!streaming){
        // Keep controls disabled until the visual assembly has reached its final poses.
        while(this.assembly&&version===this.version){await new Promise(resolve=>setTimeout(resolve,35));this.stageAssembly();}
        if(version!==this.version)return;
        this.jointPreviewVersion=scene.joint_preview_version||null;this.previewQuality=scene.preview_quality||null;
        this.host.dataset.loaded='true';
      }
    }catch(exc){
      if(version===this.version){controller.abort();if(this.assembly)this.assembly.failed=true;
        this.source=null;this.status.textContent=exc.message;this.host.classList.remove('assembly-wait');this.host.dataset.assembling='false';}
    }
  }
  async poll(now){
    if(this.loading || !this.live || now-this.lastPoll<40)return;
    this.lastPoll=now;this.loading=true;const version=this.version;
    try{
      const response=await fetch(this.live+'&after='+Math.max(0,this.lastInk||0)+'&run='+encodeURIComponent(this.run||''));const frame=await response.json();if(!response.ok)throw Error(frame.error||'读取运动失败');
      if(version!==this.version)return;
      if(frame.transforms?.length){
        if(frame.run!==this.run){this.run=frame.run;this.previous=null;this.ink=[];this.strokes=[];this.lastInk=0;}
        this.previous=this.current;this.current={transforms:frame.transforms,time:performance.now()};
        if(frame.ink_reset){this.ink=[];this.strokes=[];}
        this.ink.push(...(frame.ink||[]));this.strokes.push(...(frame.strokes||[]));
        this.lastInk=frame.ink_total;
        if(frame.ink?.length || frame.ink_reset)this.updateInk();
      }
      this.active=frame.active;this.status.textContent='';
    }catch(exc){if(version===this.version)this.status.textContent=exc.message;}
    finally{this.loading=false;}
  }
  updateInk(){
    const points=[];
    for(let i=1;i<this.ink.length;i++)if(this.strokes[i]===this.strokes[i-1]){
      for(const p of [this.ink[i-1],this.ink[i]])points.push(...p,0,0,1);
    }
    this.gl.bindBuffer(this.gl.ARRAY_BUFFER,this.inkBuffer);this.gl.bufferData(this.gl.ARRAY_BUFFER,new Float32Array(points),this.gl.DYNAMIC_DRAW);this.inkVertices=points.length/6;
  }
  bind(){
    const canvas=this.canvas,clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
    canvas.onpointerdown=e=>{if(![0,1,2].includes(e.button))return;e.preventDefault();canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);this.drag={id:e.pointerId,x:e.clientX,y:e.clientY,startX:e.clientX,startY:e.clientY,moved:false,pan:e.button!==0||e.shiftKey};canvas.classList.add('dragging');};
    canvas.onpointermove=e=>{if(this.drag?.id!==e.pointerId)return;const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;Object.assign(this.drag,{x:e.clientX,y:e.clientY});
      if(Math.hypot(e.clientX-this.drag.startX,e.clientY-this.drag.startY)>5)this.drag.moved=true;
      if(this.drag.pan)this.panBy(dx,dy);
      else{this.camera[0]=(this.camera[0]-dx*.35+360)%360;this.camera[1]=clamp(this.camera[1]-dy*.25,-89,-5);}};
    const release=e=>{if(this.drag?.id!==e.pointerId)return;const drag=this.drag;this.drag=null;canvas.classList.remove('dragging');if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);
      if(e.type==='pointerup'&&!drag.pan&&!drag.moved&&this.onSelect&&this.selectionEnabled)this.onSelect(this.pick(e.clientX,e.clientY),{additive:e.ctrlKey||e.metaKey});};
    canvas.onpointerup=release;canvas.onpointercancel=release;canvas.oncontextmenu=e=>e.preventDefault();
    canvas.onlostpointercapture=()=>{this.drag=null;canvas.classList.remove('dragging');};
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.camera[2]=clamp(this.camera[2]*Math.exp(clamp(e.deltaY,-500,500)*.001),.3,8);},{passive:false});
    canvas.onkeydown=e=>{const c=this.camera;if(e.key==='Escape'&&this.onSelect&&this.selectionEnabled)this.onSelect(null,{additive:false});else if(e.key==='ArrowLeft')c[0]-=10;else if(e.key==='ArrowRight')c[0]+=10;else if(e.key==='ArrowUp')c[1]=clamp(c[1]-5,-89,-5);else if(e.key==='ArrowDown')c[1]=clamp(c[1]+5,-89,-5);else if(['+','='].includes(e.key))c[2]=clamp(c[2]*.9,.3,8);else if(e.key==='-')c[2]=clamp(c[2]/.9,.3,8);else if(e.key==='Home')this.camera=[135,-45,1.9,0,0];else return;e.preventDefault();};
  }
  panBy(dx,dy){
    // Move the projected model with the pointer, independent of orbit angle or zoom.
    const units=2*1.3*this.camera[2]*Math.tan(.4)/Math.max(1,this.host.clientHeight);
    this.camera[3]-=dx*units;this.camera[4]+=dy*units;
  }
  setSelection(ids){this.selectedIds=new Set(ids);}
  setVisibleComponents(ids){this.visibleComponents=ids===null?null:new Set(ids);}
  isComponentVisible(id){return this.visibleComponents===null||this.visibleComponents.has(id);}
  fitVisible({top=100,bottom=40}={}){
    if(!this.current||!this.meshes.length)return;
    const low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];let found=false;
    this.meshes.forEach((mesh,i)=>{if(!this.isComponentVisible(mesh.componentId)||mesh.collisionProxy||mesh.geomType===0)return;
      const t=this.current.transforms[i];if(!t)return;found=true;
      for(let k=0;k<3;k++){const size=mesh.size||[.05,.05,.05],extent=Math.max(.025,Math.abs(t[3+k*3])*size[0]+Math.abs(t[4+k*3])*size[1]+Math.abs(t[5+k*3])*size[2]);low[k]=Math.min(low[k],t[k]-extent);high[k]=Math.max(high[k],t[k]+extent);}
    });
    if(!found)return;
    this.center=low.map((v,k)=>(v+high[k])/2);this.span=Math.max(.4,...high.map((v,k)=>v-low[k]))*1.2;
    const h=this.host.clientHeight,w=this.host.clientWidth,usable=Math.max(160,h-top-bottom);
    const distance=Math.max(1.1,1.04*h/usable,.65*h/Math.max(160,w-80));
    const pixels=h/(2*1.3*distance*Math.tan(.4)),py=((top+h-bottom)/2-h/2)/pixels;
    this.camera=[135,-27,distance,0,py];
  }
  pick(clientX,clientY){
    // Re-render visible geometry as IDs with depth testing, so occluded parts cannot be picked.
    if(!this.meshes.length||!this.current)return null;
    const gl=this.gl;this.resize();const bounds=this.canvas.getBoundingClientRect();
    if(!bounds.width||!bounds.height||clientX<bounds.left||clientX>=bounds.right||clientY<bounds.top||clientY>=bounds.bottom)return null;
    const previous=gl.getParameter(gl.FRAMEBUFFER_BINDING),dither=gl.isEnabled(gl.DITHER);
    try{
      if(!this.pickTarget)this.pickTarget={frame:gl.createFramebuffer(),color:gl.createTexture(),depth:gl.createRenderbuffer()};
      const target=this.pickTarget;gl.bindFramebuffer(gl.FRAMEBUFFER,target.frame);
      if(target.width!==this.canvas.width||target.height!==this.canvas.height){
        target.width=this.canvas.width;target.height=this.canvas.height;
        gl.bindTexture(gl.TEXTURE_2D,target.color);gl.texImage2D(gl.TEXTURE_2D,0,gl.RGBA,target.width,target.height,0,gl.RGBA,gl.UNSIGNED_BYTE,null);
        gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MIN_FILTER,gl.NEAREST);gl.texParameteri(gl.TEXTURE_2D,gl.TEXTURE_MAG_FILTER,gl.NEAREST);
        gl.framebufferTexture2D(gl.FRAMEBUFFER,gl.COLOR_ATTACHMENT0,gl.TEXTURE_2D,target.color,0);
        gl.bindRenderbuffer(gl.RENDERBUFFER,target.depth);gl.renderbufferStorage(gl.RENDERBUFFER,gl.DEPTH_COMPONENT24,target.width,target.height);
        gl.framebufferRenderbuffer(gl.FRAMEBUFFER,gl.DEPTH_ATTACHMENT,gl.RENDERBUFFER,target.depth);
      }
      if(gl.checkFramebufferStatus(gl.FRAMEBUFFER)!==gl.FRAMEBUFFER_COMPLETE)throw Error('无法创建组件选择缓冲区');
      gl.disable(gl.DITHER);this.draw(performance.now(),true);
      const pixel=new Uint8Array(4),x=Math.floor((clientX-bounds.left)/bounds.width*this.canvas.width),y=this.canvas.height-1-Math.floor((clientY-bounds.top)/bounds.height*this.canvas.height);
      gl.readPixels(x,y,1,1,gl.RGBA,gl.UNSIGNED_BYTE,pixel);
      return this.meshes[(pixel[0]|pixel[1]<<8|pixel[2]<<16)-1]?.componentId||null;
    }finally{
      gl.bindFramebuffer(gl.FRAMEBUFFER,previous);if(dither)gl.enable(gl.DITHER);
      this.draw(performance.now());
    }
  }
  tick(now){
    this.animation=requestAnimationFrame(this.tick);
    if(document.hidden || this.host.offsetParent===null)return;
    if(!this.meshes.length){this.resize();this.gl.clearColor(...(this.background||[.937,.953,.968,1]));this.gl.clear(this.gl.COLOR_BUFFER_BIT);return;}
    if(this.assembly)this.stageAssembly();
    if(this.active!==false || now-this.lastPoll>400)this.poll(now);
    const period=1000/(this.frameRate||60);
    if(now-this.lastDraw<period)return;
    this.lastDraw=now-(now-this.lastDraw)%period;
    this.draw(now);
    this.frames++;
    if(now-this.lastStats>=1000){this.fps=this.frames*1000/(now-this.lastStats);this.frames=0;this.lastStats=now;}
  }
  draw(now,picking=false){
    const gl=this.gl;this.resize();gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clearColor(...(picking?[0,0,0,1]:(this.background||[.885,.891,.881,1])));gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);
    const [az,el,d,px,py]=this.camera,a=az*Math.PI/180,e=el*Math.PI/180,s=this.span;
    const center=[this.center[0]+s*(-px*Math.sin(a)+py*Math.cos(a)*Math.sin(e)),this.center[1]+s*(px*Math.cos(a)+py*Math.sin(a)*Math.sin(e)),this.center[2]+s*py*Math.cos(e)];
    const eye=[center[0]+d*s*1.3*Math.cos(e)*Math.cos(a),center[1]+d*s*1.3*Math.cos(e)*Math.sin(a),center[2]-d*s*1.3*Math.sin(e)];
    const vp=SceneViewer.multiply(SceneViewer.perspective(.8,this.canvas.width/this.canvas.height,.005,s*50),SceneViewer.lookAt(eye,center));gl.uniformMatrix4fv(this.uniform.vp,false,vp);
    const old=this.previous,current=this.current,interval=old?Math.min(120,Math.max(20,current.time-old.time)):40;
    const t=old?Math.max(0,Math.min(1,(now-current.time)/interval)):1;
    const collisions=new Set((current.collisions||[]).flatMap(c=>c.geom_ids));
    if(this.appearance&&!picking&&!this.showCollision){
      this.appearance.draw(vp,eye,t,collisions);
      gl.useProgram(this.program);gl.uniformMatrix4fv(this.uniform.vp,false,vp);
    }else{
    this.meshes.forEach((mesh,i)=>{
      if(!this.isComponentVisible(mesh.componentId)||!this.assemblyVisible(mesh))return;
      if((mesh.collisionProxy && !this.showCollision) || (mesh.visualMesh && this.showCollision))return;
      const b=current.transforms[i],a=old?.transforms[i]||b;
      const values=this.assemblyTransform(mesh,b.map((v,j)=>j<3?a[j]+(v-a[j])*t:v));
      const selected=this.selectedIds.has(mesh.componentId),id=mesh.componentId?i+1:0;
      const color=picking?[(id&255)/255,((id>>8)&255)/255,((id>>16)&255)/255,1]:collisions.has(i)?[.95,.12,.12,1]:selected?(this.litSelection?[.89,.65,.34,1]:[1,.67,.1,1]):(current.colors?.[i]||mesh.color);
      gl.uniformMatrix4fv(this.uniform.model,false,SceneViewer.transform(values));gl.uniform4fv(this.uniform.color,color);gl.uniform1f(this.uniform.unlit,picking||(selected&&!this.litSelection)?1:0);this.buffer(mesh.buffer);gl.drawArrays(gl.TRIANGLES,0,mesh.count);
    });
    }
    if(this.inkVertices&&!picking){gl.uniformMatrix4fv(this.uniform.model,false,new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]));gl.uniform4fv(this.uniform.color,[.04,.12,.42,1]);gl.uniform1f(this.uniform.unlit,1);this.buffer(this.inkBuffer);gl.drawArrays(gl.LINES,0,this.inkVertices);}
    if(!picking&&!this.assembly)this.drawMechanism(vp,current.body_transforms);
    if(!picking&&!this.assembly&&this.onDraw)this.onDraw(vp);
  }
  drawMechanism(vp,bodies){
    const visible=!!(this.showMechanism&&this.mechanism&&bodies);this.labelLayer.hidden=!visible;if(!visible)return;
    const point=(body,p)=>{const t=bodies[body];return t?[t[0]+t[3]*p[0]+t[4]*p[1]+t[5]*p[2],t[1]+t[6]*p[0]+t[7]*p[1]+t[8]*p[2],t[2]+t[9]*p[0]+t[10]*p[1]+t[11]*p[2]]:null;};
    const groups=[[],[],[],[]],line=(group,a,b)=>{if(a&&b)groups[group].push(...a,0,0,1,...b,0,0,1);},length=Math.min(this.span*.08,.06);
    for(const frame of this.mechanism.frames){
      const origin=point(frame.body,frame.position_m),r=frame.rpy_rad,[rx,ry,rz]=r,[cx,sx,cy,sy,cz,sz]=[Math.cos(rx),Math.sin(rx),Math.cos(ry),Math.sin(ry),Math.cos(rz),Math.sin(rz)];
      const axes=[[cz*cy,sz*cy,-sy],[cz*sy*sx-sz*cx,sz*sy*sx+cz*cx,cy*sx],[cz*sy*cx+sz*sx,sz*sy*cx-cz*sx,cy*cx]];
      for(let k=0;k<3;k++)line(k,origin,point(frame.body,frame.position_m.map((v,i)=>v+axes[k][i]*length)));
    }
    const placed=[],width=this.host.clientWidth,height=this.host.clientHeight;
    this.mechanism.joints.forEach((joint,index)=>{
      const a=point(joint.parent_body,joint.parent_anchor_m),b=point(joint.child_body,joint.child_anchor_m);
      line(3,point(joint.parent_body,[0,0,0]),a);line(3,a,b);line(3,b,point(joint.child_body,[0,0,0]));
      line(3,point(joint.child_body,joint.child_anchor_m.map((v,i)=>v-joint.axis[i]*length)),point(joint.child_body,joint.child_anchor_m.map((v,i)=>v+joint.axis[i]*length*1.8)));
      const label=this.mechanismLabels[index],leader=this.mechanismLeaders[index];if(!b){label.hidden=leader.hidden=true;return;}
      const clip=Array.from({length:4},(_,i)=>vp[i]*b[0]+vp[i+4]*b[1]+vp[i+8]*b[2]+vp[i+12]),x=clip[0]/clip[3],y=clip[1]/clip[3];
      label.hidden=leader.hidden=clip[3]<=0||Math.abs(x)>1||Math.abs(y)>1;if(label.hidden)return;
      const anchorX=(x+1)*width/2,anchorY=(1-y)*height/2,w=label.offsetWidth||84,h=label.offsetHeight||20;
      const left=Math.max(2,Math.min(width-w-2,anchorX+9)),base=anchorY-h-4;let top=base;
      for(let offset=0;offset<30;offset++){
        const shift=offset===0?0:Math.ceil(offset/2)*(h+3)*(offset%2?-1:1);top=Math.max(2,Math.min(height-h-2,base+shift));
        if(!placed.some(r=>left<r.right+3&&left+w+3>r.left&&top<r.bottom+3&&top+h+3>r.top))break;
      }
      placed.push({left,top,right:left+w,bottom:top+h});label.style.left=left+'px';label.style.top=top+'px';
      const dx=left-anchorX,dy=top+h/2-anchorY;leader.style.left=anchorX+'px';leader.style.top=anchorY+'px';leader.style.width=Math.hypot(dx,dy)+'px';leader.style.transform='rotate('+Math.atan2(dy,dx)+'rad)';
    });
    const gl=this.gl;gl.disable(gl.DEPTH_TEST);gl.uniformMatrix4fv(this.uniform.model,false,SceneViewer.transform([0,0,0,1,0,0,0,1,0,0,0,1]));gl.uniform1f(this.uniform.unlit,1);
    groups.forEach((vertices,index)=>{if(!vertices.length)return;gl.bindBuffer(gl.ARRAY_BUFFER,this.mechanismBuffer);gl.bufferData(gl.ARRAY_BUFFER,new Float32Array(vertices),gl.DYNAMIC_DRAW);this.buffer(this.mechanismBuffer);gl.uniform4fv(this.uniform.color,[[.95,.15,.15,1],[.1,.65,.25,1],[.15,.35,.95,1],[1,.45,.02,1]][index]);gl.drawArrays(gl.LINES,0,vertices.length/6);});
    gl.enable(gl.DEPTH_TEST);
  }
  buffer(buffer){const gl=this.gl;gl.bindBuffer(gl.ARRAY_BUFFER,buffer);this.attr.forEach((a,i)=>{gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,3,gl.FLOAT,false,24,i*12);});}
  static vertexBounds(vertices){
    const low=[Infinity,Infinity,Infinity],high=[-Infinity,-Infinity,-Infinity];
    for(let i=0;i<vertices.length;i+=6)for(let k=0;k<3;k++){low[k]=Math.min(low[k],vertices[i+k]);high[k]=Math.max(high[k],vertices[i+k]);}
    return {center:low.map((v,k)=>(v+high[k])/2),half:low.map((v,k)=>(high[k]-v)/2)};
  }
  static transform(v){return new Float32Array([v[3],v[6],v[9],0,v[4],v[7],v[10],0,v[5],v[8],v[11],0,v[0],v[1],v[2],1]);}
  static perspective(fov,aspect,near,far){const f=1/Math.tan(fov/2);return [f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0];}
  static lookAt(eye,target){const norm=v=>{const l=Math.hypot(...v);return v.map(x=>x/l);},cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);const z=norm(eye.map((v,i)=>v-target[i])),x=norm(cross([0,0,1],z)),y=cross(z,x);return [x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1];}
  static multiply(a,b){return new Float32Array(Array.from({length:16},(_,i)=>{const r=i%4,c=Math.floor(i/4);return a[r]*b[c*4]+a[r+4]*b[c*4+1]+a[r+8]*b[c*4+2]+a[r+12]*b[c*4+3];}));}
}
