'use strict';
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
  }
  mesh(type,size){
    const vertices=[];const push=(p,n)=>vertices.push(...p,...n);
    const quad=(a,b,c,d,n)=>{for(const p of [a,b,c,a,c,d])push(p,n);};
    if(type===6 || type===0){
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
    return {buffer,count:vertices.length/6};
  }
  resize(){
    const bounds=this.canvas.getBoundingClientRect(),dpr=Math.min(devicePixelRatio||1,2);
    const scale=Math.min(dpr,2560/Math.max(1,bounds.width),1440/Math.max(1,bounds.height));
    const w=Math.max(1,Math.round(bounds.width*scale)),h=Math.max(1,Math.round(bounds.height*scale));
    if(this.canvas.width!==w || this.canvas.height!==h){this.canvas.width=w;this.canvas.height=h;}
  }
  async load(source,live){
    if(source===this.source)return;
    const version=++this.version;this.source=source;this.live=live;this.status.textContent='加载中…';
    try{
      const response=await fetch(source);const scene=await response.json();if(!response.ok)throw Error(scene.error||'场景加载失败');
      if(version!==this.version)return;
      for(const mesh of this.meshes)this.gl.deleteBuffer(mesh.buffer);
      this.meshes=scene.geoms.map(g=>({...this.mesh(g.type,g.size),color:g.color}));
      this.center=scene.center;this.span=scene.span;this.previous=this.current={transforms:scene.transforms,time:performance.now()};
      this.ink=[];this.strokes=[];this.inkVertices=0;this.lastInk=-1;this.run=null;this.status.textContent='';this.lastPoll=0;this.resize();
    }catch(exc){if(version===this.version)this.status.textContent=exc.message;}
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
    canvas.onpointerdown=e=>{if(![0,1,2].includes(e.button))return;e.preventDefault();canvas.focus({preventScroll:true});canvas.setPointerCapture(e.pointerId);this.drag={id:e.pointerId,x:e.clientX,y:e.clientY,pan:e.button!==0||e.shiftKey};canvas.classList.add('dragging');};
    canvas.onpointermove=e=>{if(this.drag?.id!==e.pointerId)return;const dx=e.clientX-this.drag.x,dy=e.clientY-this.drag.y;Object.assign(this.drag,{x:e.clientX,y:e.clientY});
      if(this.drag.pan){this.camera[3]=clamp(this.camera[3]+dx*this.camera[2]/500,-4,4);this.camera[4]=clamp(this.camera[4]-dy*this.camera[2]/500,-4,4);}
      else{this.camera[0]=(this.camera[0]-dx*.35+360)%360;this.camera[1]=clamp(this.camera[1]-dy*.25,-89,-5);}};
    const release=e=>{if(this.drag?.id!==e.pointerId)return;this.drag=null;canvas.classList.remove('dragging');if(canvas.hasPointerCapture(e.pointerId))canvas.releasePointerCapture(e.pointerId);};
    canvas.onpointerup=release;canvas.onpointercancel=release;canvas.oncontextmenu=e=>e.preventDefault();
    canvas.addEventListener('wheel',e=>{e.preventDefault();this.camera[2]=clamp(this.camera[2]*Math.exp(clamp(e.deltaY,-500,500)*.001),.3,8);},{passive:false});
    canvas.onkeydown=e=>{const c=this.camera;if(e.key==='ArrowLeft')c[0]-=10;else if(e.key==='ArrowRight')c[0]+=10;else if(e.key==='ArrowUp')c[1]=clamp(c[1]-5,-89,-5);else if(e.key==='ArrowDown')c[1]=clamp(c[1]+5,-89,-5);else if(['+','='].includes(e.key))c[2]=clamp(c[2]*.9,.3,8);else if(e.key==='-')c[2]=clamp(c[2]/.9,.3,8);else if(e.key==='Home')this.camera=[135,-45,1.9,0,0];else return;e.preventDefault();};
  }
  tick(now){
    this.animation=requestAnimationFrame(this.tick);
    if(document.hidden || this.host.offsetParent===null)return;
    if(!this.meshes.length)return;
    if(this.active!==false || now-this.lastPoll>400)this.poll(now);
    const period=1000/60;
    if(now-this.lastDraw<period)return;
    this.lastDraw=now-(now-this.lastDraw)%period;
    this.draw(now);
    this.frames++;
    if(now-this.lastStats>=1000){this.fps=this.frames*1000/(now-this.lastStats);this.frames=0;this.lastStats=now;}
  }
  draw(now){
    const gl=this.gl;this.resize();gl.viewport(0,0,this.canvas.width,this.canvas.height);gl.clear(gl.COLOR_BUFFER_BIT|gl.DEPTH_BUFFER_BIT);gl.useProgram(this.program);
    const [az,el,d,px,py]=this.camera,a=az*Math.PI/180,e=el*Math.PI/180,s=this.span;
    const center=[this.center[0]+s*(-px*Math.sin(a)+py*Math.cos(a)*Math.sin(e)),this.center[1]+s*(px*Math.cos(a)+py*Math.sin(a)*Math.sin(e)),this.center[2]+s*py*Math.cos(e)];
    const eye=[center[0]+d*s*1.3*Math.cos(e)*Math.cos(a),center[1]+d*s*1.3*Math.cos(e)*Math.sin(a),center[2]-d*s*1.3*Math.sin(e)];
    gl.uniformMatrix4fv(this.uniform.vp,false,SceneViewer.multiply(SceneViewer.perspective(.8,this.canvas.width/this.canvas.height,.005,s*50),SceneViewer.lookAt(eye,center)));
    const old=this.previous,current=this.current,interval=old?Math.min(120,Math.max(20,current.time-old.time)):40;
    const t=old?Math.max(0,Math.min(1,(now-current.time)/interval)):1;
    this.meshes.forEach((mesh,i)=>{
      const b=current.transforms[i],a=old?.transforms[i]||b;
      const values=b.map((v,j)=>j<3?a[j]+(v-a[j])*t:v);
      gl.uniformMatrix4fv(this.uniform.model,false,SceneViewer.transform(values));gl.uniform4fv(this.uniform.color,mesh.color);gl.uniform1f(this.uniform.unlit,0);this.buffer(mesh.buffer);gl.drawArrays(gl.TRIANGLES,0,mesh.count);
    });
    if(this.inkVertices){gl.uniformMatrix4fv(this.uniform.model,false,new Float32Array([1,0,0,0,0,1,0,0,0,0,1,0,0,0,0,1]));gl.uniform4fv(this.uniform.color,[.04,.12,.42,1]);gl.uniform1f(this.uniform.unlit,1);this.buffer(this.inkBuffer);gl.drawArrays(gl.LINES,0,this.inkVertices);}
  }
  buffer(buffer){const gl=this.gl;gl.bindBuffer(gl.ARRAY_BUFFER,buffer);this.attr.forEach((a,i)=>{gl.enableVertexAttribArray(a);gl.vertexAttribPointer(a,3,gl.FLOAT,false,24,i*12);});}
  static transform(v){return new Float32Array([v[3],v[6],v[9],0,v[4],v[7],v[10],0,v[5],v[8],v[11],0,v[0],v[1],v[2],1]);}
  static perspective(fov,aspect,near,far){const f=1/Math.tan(fov/2);return [f/aspect,0,0,0,0,f,0,0,0,0,(far+near)/(near-far),-1,0,0,2*far*near/(near-far),0];}
  static lookAt(eye,target){const norm=v=>{const l=Math.hypot(...v);return v.map(x=>x/l);},cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]],dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);const z=norm(eye.map((v,i)=>v-target[i])),x=norm(cross([0,0,1],z)),y=cross(z,x);return [x[0],y[0],z[0],0,x[1],y[1],z[1],0,x[2],y[2],z[2],0,-dot(x,eye),-dot(y,eye),-dot(z,eye),1];}
  static multiply(a,b){return new Float32Array(Array.from({length:16},(_,i)=>{const r=i%4,c=Math.floor(i/4);return a[r]*b[c*4]+a[r+4]*b[c*4+1]+a[r+8]*b[c*4+2]+a[r+12]*b[c*4+3];}));}
}
