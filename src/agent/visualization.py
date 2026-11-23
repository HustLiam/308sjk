"""离线轨迹预览与故障回放页面；纯 SVG/JS，不依赖浏览器服务或外部资源。"""

import json
import math
from pathlib import Path


def trajectory_segments(trajectory):
    """按权威步表展开线段；抬笔与落笔分别展示，不推测开场位置。"""
    trajectory = trajectory or {}
    previous, segments = None, []
    down = trajectory.get("pen_z", {}).get("down", 0)
    for step in trajectory.get("steps", []):
        points = list(step.get("waypoints", []))
        target = step.get("to")
        if target is not None and (not points or points[-1] != target):
            points.append(target)
        for point in points:
            if len(point) != 3 or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in point):
                raise ValueError("轨迹路点必须包含三个有限坐标")
            if previous is not None:
                segments.append({"start": previous, "end": point,
                                 "draw": previous[2] == down and point[2] == down,
                                 "step": step["no"], "action": step["action"],
                                 "note": step.get("note", "")})
            previous = point
    return segments


def write_view(path, data):
    """所有用户内容作为 JSON 数据嵌入；禁止结束 script 标签及 HTML 注入。"""
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = '<h1 id="title"></h1>'
    notice = ''
    if data.get("kind") != "preview":
        header = '<small>308SJK / GC AGENT</small>' + header + '<p id="subtitle"></p>'
        notice = '<div id="notice"></div>'
    page = _PAGE.replace("__VIEW_HEADER__", header).replace("__VIEW_NOTICE__", notice)
    page = page.replace("__VIEW_INFO__", "" if data.get("kind")=="preview" else _INFO_CARD)
    page = page.replace("__VIEW_CLASS__", "preview" if data.get("kind")=="preview" else "replay")
    path.write_text(page.replace("__AGENT_DATA__", payload), encoding="utf-8")
    return path


def write_preview(trajectory, path, unit="设备坐标", sources=None):
    segments = trajectory_segments(trajectory)
    xy_distance = sum(math.dist(s["start"][:2], s["end"][:2]) for s in segments)
    z_distance = sum(abs(s["start"][2] - s["end"][2]) for s in segments)
    data = {"kind": "preview", "title": "轨迹预览", "trajectory": trajectory,
            "segments": segments, "unit": unit, "sources": sources or {},
            "xy_distance": round(xy_distance, 3), "z_distance": round(z_distance, 3), "iterations": [],
            "notes": ["这是规划轨迹预览，未执行 PLC，也不代表已通过验收。",
                      "实线为落笔路径，虚线为抬笔或落笔移动；起始点前的未知运动不作推测。"] + trajectory.get("notes", [])}
    if trajectory.get("sampling"):
        sampling = trajectory["sampling"]
        data["notes"].append("曲线展开容差 %g；INT 量化的最大路点偏移 %g（均为设备坐标）。" % (
            sampling["flatten_tolerance"], sampling["max_quantization_error"]))
    return write_view(path, data)


_INFO_CARD = '''<div class="card"><h2 id="info-title">规划参数</h2><div id="parameters"></div><p id="summary" class="muted"></p><table id="steps"><thead><tr><th>步骤</th><th>动作</th><th>目标 XYZ</th></tr></thead><tbody></tbody></table></div>'''


_PAGE = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>308sjk · 轨迹与故障回放</title>
<style>
:root{color-scheme:light;--ink:#172a36;--muted:#587080;--edge:#dbe5e8;--blue:#087c8a;--orange:#d35b23}
*{box-sizing:border-box}body{margin:0;background:#f3f7f8;color:var(--ink);font:15px/1.65 system-ui,"Microsoft YaHei",sans-serif}
header{background:#152e3b;color:#fff;padding:26px max(24px,calc((100vw - 1240px)/2));border-bottom:4px solid #49c6bc}
header small{letter-spacing:2px;color:#9fd7d6}h1{font-size:27px;margin:4px 0}h2{font-size:18px;margin:0 0 14px}p{margin:5px 0}
main{max-width:1288px;margin:auto;padding:24px}.grid{display:grid;grid-template-columns:minmax(0,1.55fr) minmax(300px,1fr);gap:18px}.preview .grid{grid-template-columns:1fr}.preview aside{display:none}.preview #path{max-height:420px}
.card{background:white;border:1px solid var(--edge);border-radius:12px;padding:20px;min-width:0;margin-bottom:18px}.muted,small{color:var(--muted)}
.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:16px}button,select{font:inherit;border:1px solid var(--edge);border-radius:6px;background:white;color:var(--ink);padding:7px 12px}button{cursor:pointer}button.primary{background:var(--blue);color:white}button:disabled{opacity:.5;cursor:default}
input[type=range]{accent-color:var(--blue);flex:1;min-width:150px}svg{display:block;width:100%;background:#fbfdfd;border-radius:8px}#path{aspect-ratio:680/460}.badge{display:inline-block;padding:3px 9px;border-radius:5px;background:#e7f4f3;color:#086572}.fail{background:#fff0e8;color:#a5431a}.good{color:#15723e}.legend{display:flex;gap:20px;font-size:13px;margin-top:8px}.legend span:before{content:"";display:inline-block;width:20px;border-top:3px solid var(--blue);margin-right:6px;vertical-align:middle}.legend span.actual:before{border-color:var(--orange)}.legend span.travel:before{border-top-style:dashed;border-color:#8298a3}
table{border-collapse:collapse;width:100%;font-size:13px}td,th{border-bottom:1px solid var(--edge);padding:7px;text-align:left;vertical-align:top}th{color:var(--muted);font-weight:500}pre{font:12px/1.7 Consolas,monospace;white-space:pre-wrap;overflow-wrap:anywhere;margin:0;max-height:420px;overflow:auto}#notice{margin-bottom:18px;border-left:3px solid #d39b38;padding-left:12px}.checks{display:flex;gap:12px;flex-wrap:wrap;font-size:13px;margin-bottom:10px}details{border-top:1px solid var(--edge);padding-top:10px;margin-top:12px}summary{cursor:pointer}.selected{background:#eaf5f5}a{color:var(--blue)}@media(max-width:850px){.grid{grid-template-columns:1fr}main{padding:14px}.card{padding:14px}header{padding:20px}}
</style></head><body class="__VIEW_CLASS__">
<header>__VIEW_HEADER__</header>
<main>__VIEW_NOTICE__<div class="controls" id="iteration-controls"><label>运行轮次 <select id="iteration"></select></label><span id="status" class="badge"></span></div>
<div class="grid"><section><div class="card"><h2>XY 轨迹</h2><svg id="path" viewBox="0 0 680 460" role="img" aria-label="规划与实际 XY 轨迹"></svg><div class="legend"><span>规划落笔</span><span class="travel">规划抬落笔</span><span class="actual">实际采样</span></div><p id="path-note" class="muted"></p></div>
<div class="card"><h2 id="timeline-title">时间线</h2><div class="controls"><button id="play" class="primary">播放</button><input id="cursor" type="range" min="0" max="0" value="0" aria-label="回放进度"><select id="speed" aria-label="回放速度"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select><span id="time"></span></div><div class="checks" id="signals"></div><svg id="chart" viewBox="0 0 680 230" aria-label="变量采样曲线"></svg><table><thead><tr><th>变量</th><th>当前值</th></tr></thead><tbody id="values"></tbody></table></div></section>
<aside>__VIEW_INFO__
<div class="card" id="evidence"><h2>失败证据与诊断</h2><p id="sample-note" class="muted"></p><pre id="errors"></pre><details open><summary>逐条判定</summary><div id="verdict"></div></details><details><summary>归因与修复建议</summary><pre id="diagnosis"></pre></details><details><summary>完整验收日志</summary><pre id="logs"></pre></details><details><summary>与上一轮的 ST 差异</summary><pre id="diff"></pre></details></div></aside></div></main>
<script id="agent-data" type="application/json">__AGENT_DATA__</script>
<script>
'use strict';
const data=JSON.parse(document.getElementById('agent-data').textContent), $=id=>document.getElementById(id), ns='http://www.w3.org/2000/svg';
const preview=data.kind==='preview', segments=data.segments||[], trajectory=data.trajectory||{}, steps=trajectory.steps||[];
let current=null, rows=[], active=new Set(), timer=null, delay=150;
function text(id,value){$(id).textContent=value==null?'':String(value)}
function node(tag,attrs={},label){const e=document.createElementNS(ns,tag);for(const [k,v]of Object.entries(attrs))e.setAttribute(k,v);if(label!=null)e.textContent=label;return e}
function number(v){return typeof v==='number'&&Number.isFinite(v)}
function row(table,cells){const tr=document.createElement('tr');cells.forEach(v=>{const td=document.createElement('td');td.textContent=v==null?'—':typeof v==='object'?JSON.stringify(v):String(v);tr.append(td)});table.append(tr);return tr}
function stop(){if(timer)clearTimeout(timer);timer=null;text('play','播放')}
text('title',data.title);
if(!preview){text('subtitle','离线查看已有运行证据；此页面不会连接、部署或控制 PLC。');text('notice',(data.notes||[]).join(' '));}
if(!preview){const params=$('parameters');for(const [k,v]of Object.entries(trajectory.params||{})){const p=document.createElement('p');p.textContent=k+': '+JSON.stringify(v)+' '+(data.sources?.[k]==='default'?'（采用默认值）':'');params.append(p)}text('summary','规划参数取自该任务已保存的 trajectory.json；缺失时不推测预期轨迹。');}
const tbody=$('steps')?.querySelector('tbody')||document.createElement('tbody');for(const s of steps)row(tbody,[s.no,s.note||s.action,s.to]);
function selectedIndex(){return Number($('cursor').value)}
function drawPath(){const svg=$('path');svg.replaceChildren();const points=segments.flatMap(s=>[s.start,s.end]);if(!preview)points.push(...rows.filter(r=>number(r.x_fb)&&number(r.y_fb)).map(r=>[r.x_fb,r.y_fb]));
if(!points.length){svg.append(node('text',{x:30,y:55,fill:'#587080'},'没有规划或实际 XY 数据'));return}
let xmin=Math.min(...points.map(p=>p[0])),xmax=Math.max(...points.map(p=>p[0])),ymin=Math.min(...points.map(p=>p[1])),ymax=Math.max(...points.map(p=>p[1]));
const span=Math.max(xmax-xmin,ymax-ymin,1)*1.18,cx=(xmin+xmax)/2,cy=(ymin+ymax)/2,scale=360/span;
const xy=p=>[340+(p[0]-cx)*scale,220-(p[1]-cy)*scale];
for(let i=0;i<=4;i++){const x=160+i*90,y=40+i*90;svg.append(node('line',{x1:x,x2:x,y1:40,y2:400,stroke:'#e0e9ec'}),node('line',{x1:160,x2:520,y1:y,y2:y,stroke:'#e0e9ec'}));svg.append(node('text',{x:x,y:425,'text-anchor':'middle',fill:'#587080','font-size':11},(cx+(x-340)/scale).toFixed(1)),node('text',{x:150,y:y+4,'text-anchor':'end',fill:'#587080','font-size':11},(cy-(y-220)/scale).toFixed(1)))}
svg.append(node('text',{x:550,y:425,fill:'#587080'},'X'),node('text',{x:140,y:23,fill:'#587080'},'Y'));
segments.forEach((s,i)=>{const a=xy(s.start),b=xy(s.end);svg.append(node('line',{x1:a[0],y1:a[1],x2:b[0],y2:b[1],stroke:s.draw?'#087c8a':'#8298a3','stroke-width':s.draw?2.5:1.5,'stroke-dasharray':s.draw?'none':'6 5',opacity:preview&&i>selectedIndex()?.3:.85}));if(s.draw){svg.append(node('circle',{cx:b[0],cy:b[1],r:2,fill:'#087c8a'}))}});
if(preview&&segments.length){const s=segments[selectedIndex()]||segments[0],p=xy(s.end);svg.append(node('circle',{cx:p[0],cy:p[1],r:6,fill:'#d35b23'}));text('path-note',`步骤 ${s.step} · ${s.note||s.action} · (${s.end.join(', ')})`)}
else{const upto=rows.slice(0,selectedIndex()+1);let prev=null;for(const s of upto){if(!number(s.x_fb)||!number(s.y_fb)){prev=null;continue}const p=xy([s.x_fb,s.y_fb]);if(prev)svg.append(node('line',{x1:prev.p[0],y1:prev.p[1],x2:p[0],y2:p[1],stroke:'#d35b23','stroke-width':2.5,'stroke-dasharray':prev.s.pen_down===false||s.pen_down===false?'4 4':'none'}));prev={p,s}}
if(prev)svg.append(node('circle',{cx:prev.p[0],cy:prev.p[1],r:6,fill:'#d35b23'}));text('path-note',current?.sample_note||'实际曲线来自已保存的反馈采样；不代表样本之间已被连续验证。')}
}
function drawChart(){const svg=$('chart');svg.replaceChildren();if(preview||!rows.length){svg.style.display='none';return}svg.style.display='block';const colors=['#087c8a','#d35b23','#5b65bc','#48824e','#af459b','#8b731d'];const timeBased=rows.every(r=>number(r.t)),tx=(r,i)=>timeBased?r.t:i;
const xs=rows.map(tx),lo=Math.min(...xs),hi=Math.max(...xs),x=v=>50+(v-lo)/(hi-lo||1)*590;let color=0;
for(const key of active){const values=rows.map(r=>typeof r[key]==='boolean'?Number(r[key]):r[key]).filter(number);if(!values.length)continue;const min=Math.min(...values),max=Math.max(...values),y=v=>185-(Number(v)-min)/(max-min||1)*145;let d='';rows.forEach((r,i)=>{const v=r[key];if(!number(v)&&typeof v!=='boolean'){d+=' ';return}const cmd=i===0||(!number(rows[i-1][key])&&typeof rows[i-1][key]!=='boolean')?'M':'L';d+=`${cmd}${x(tx(r,i))},${y(v)} `});svg.append(node('path',{d,fill:'none',stroke:colors[color++%colors.length],'stroke-width':2}));svg.append(node('text',{x:55,y:20+color*17,fill:colors[(color-1)%colors.length],'font-size':12},`${key}: ${min}..${max}`))}
const r=rows[selectedIndex()];svg.append(node('line',{x1:x(tx(r,selectedIndex())),x2:x(tx(r,selectedIndex())),y1:25,y2:190,stroke:'#192f3b','stroke-dasharray':'4 3'}));svg.append(node('text',{x:50,y:218,fill:'#587080','font-size':12},`${timeBased?'时间 / 秒':'样本序号'}；各变量独立归一化，真实值见右侧范围及下表`))}
function update(){const index=selectedIndex(),r=preview?null:rows[index];text('time',preview?`${index+1} / ${segments.length} 路段`:r?`${number(r.t)?r.t.toFixed(3)+' s':'样本 '+index} · ${index+1}/${rows.length}`:'没有时序采样');$('values').replaceChildren();if(r)for(const key of active)row($('values'),[key,r[key]]);drawPath();drawChart();[...tbody.children].forEach(tr=>tr.classList.toggle('selected',preview&&segments[index]&&Number(tr.firstChild.textContent)===segments[index].step))}
function loadIteration(index){stop();current=(data.iterations||[])[index]||{};rows=current.samples||[];text('status',current.status||current.gate||'未记录结果');$('status').classList.toggle('fail',current.ok===false);text('errors',(current.errors||[]).join('\n')||'没有保存失败报错');text('diagnosis',current.diagnosis||'未保存归因');text('logs',current.logs||'未保存完整日志');text('diff',current.diff||'没有上一轮可比较的 ST');text('sample-note',current.sample_note||'');
const verdict=$('verdict');verdict.replaceChildren();for(const r of current.verdict||[]){const p=document.createElement('p');p.className=r.pass===false||r.ok===false?'fail':'muted';p.textContent=`${r.id||''} ${r.desc||''}：${r.pass===false||r.ok===false?'失败':r.pass===true||r.ok===true?'通过':'未判定'} ${typeof r.evidence==='string'?r.evidence:JSON.stringify(r.evidence||'')}`;verdict.append(p)}
if(!verdict.children.length)verdict.textContent='未保存结构化逐条判定。';
active=new Set();const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))].filter(k=>k!=='t'&&rows.some(r=>number(r[k])||typeof r[k]==='boolean'));const checks=$('signals');checks.replaceChildren();keys.forEach((key,i)=>{const label=document.createElement('label'),input=document.createElement('input');input.type='checkbox';input.checked=i<4;if(input.checked)active.add(key);input.onchange=()=>{input.checked?active.add(key):active.delete(key);update()};label.append(input,document.createTextNode(key));checks.append(label)});$('cursor').max=Math.max(0,rows.length-1);$('cursor').value=0;$('cursor').disabled=!rows.length;$('play').disabled=rows.length<2;update()}
$('cursor').oninput=()=>{stop();update()};$('iteration').onchange=()=>loadIteration(Number($('iteration').value));
$('play').onclick=()=>{if(timer){stop();return}if(selectedIndex()>=Number($('cursor').max))$('cursor').value=0;text('play','暂停');function tick(){if(selectedIndex()>=Number($('cursor').max)){stop();return}const i=selectedIndex();$('cursor').value=i+1;update();delay=preview?450:rows[i+1]&&number(rows[i+1].t)&&number(rows[i].t)?Math.max(10,(rows[i+1].t-rows[i].t)*1000):150;timer=setTimeout(tick,delay/Number($('speed').value))}timer=setTimeout(tick,20)};
if(preview){$('iteration-controls').style.display='none';$('evidence').style.display='none';$('signals').style.display='none';$('values').parentElement.style.display='none';document.querySelector('.legend .actual').textContent='当前规划点';text('timeline-title','按工艺顺序预览');$('cursor').max=Math.max(0,segments.length-1);$('play').disabled=segments.length<2;update()}
else{const select=$('iteration');(data.iterations||[]).forEach((it,i)=>{const o=document.createElement('option');o.value=i;o.textContent=`第 ${it.number} 轮 · ${it.status||it.gate}`;select.append(o)});let initial=(data.iterations||[]).findIndex(it=>it.number===data.selected_iteration);if(initial<0)initial=(data.iterations||[]).findIndex(it=>it.ok===false);if(initial<0)initial=0;select.value=initial;loadIteration(initial)}
</script></body></html>'''
