"""Chinese presentation retains unsupported behavior and the original evidence."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

REPO=Path(__file__).resolve().parents[1]


def render_text(body):
    node=shutil.which('node')
    if not node:pytest.skip('Node is needed for scene presentation checks')
    script="require('./src/agent/web_ui/device_scene_i18n.js');require('./src/agent/web_ui/joint_preview.js');const t=globalThis.DeviceSceneText;"+body
    result=subprocess.run([node,'-e',script],cwd=REPO,capture_output=True,text=True,encoding='utf-8',check=True)
    return json.loads(result.stdout)


def test_chinese_translation_preserves_unimplemented_behavior_and_identifiers():
    source='Panel signal wiring to PLC IO retained as-is; no behavior kernel is claimed.'
    result=render_text('console.log(JSON.stringify(t.describe('+json.dumps(source)+')));')
    assert result['original']==source and result['translated']
    assert '保持原样' in result['text'] and '未声明已实现' in result['text']
    source='PLC angle encoding is signed_INT 0.01 deg/count; the simulation-side conversion and feedback (measured, never copied command) require runtime implementation not present in AML.'
    result=render_text('console.log(JSON.stringify(t.describe('+json.dumps(source)+')));')
    assert '0.01°' in result['text'] and '不能复制指令' in result['text'] and '需要运行时实现' in result['text']


def test_unknown_translation_never_fabricates_a_pass_and_retains_original():
    result=render_text('console.log(JSON.stringify(t.describe("Novel drive fault: axis 8 overheated")));')
    assert not result['translated'] and result['original']=='Novel drive fault: axis 8 overheated'
    assert '尚无中文译文' in result['text']


def test_existing_chinese_and_check_ids_remain_intact():
    result=render_text('console.log(JSON.stringify({description:t.describe("传感器 sensor_low 尚未标定"),check:t.checkName("joint_range:joint-123"),dir:t.direction("input"),writer:t.writer("simulation")}));')
    assert result['description']['text']=='传感器 sensor_low 尚未标定'
    assert result['check']=='关节运动范围 · joint-123'
    assert result['dir']=='PLC 输入' and result['writer']=='仿真反馈'


def test_rule_version_error_translates_inside_chinese_and_authoring_prefixes():
    source='Approved rule versions are immutable; increment the version'
    result=render_text('console.log(JSON.stringify(['+
        ','.join('t.errorMessage('+json.dumps(value)+')' for value in
                 (source,'组件模型：'+source,'候选生成失败：Candidate authoring failed: '+source))+']));')
    assert all('已审批的组件规则版本不能修改' in text and '新版本号' in text for text in result)
    assert result[1].startswith('组件模型：')
    assert '候选组件规则生成失败：' in result[2]
    assert all('immutable' not in text for text in result)


def test_error_translation_preserves_identifiers_and_existing_chinese():
    result=render_text('console.log(JSON.stringify(['
        't.errorMessage("Missing approved component rules: OperatorPanel, Robot6R"),'
        't.errorMessage("Review is stale: candidate, assets or checks changed"),'
        't.errorMessage("审批版本已更新，请重新核对 OperatorPanel 0.1.0。"),'
        't.errorMessage(""),t.errorMessage("Reviewer and decision note required")]));')
    assert result[0]=='缺少可用的已审批组件规则：OperatorPanel, Robot6R'
    assert '重新核对后审批' in result[1]
    assert result[2]=='审批版本已更新，请重新核对 OperatorPanel 0.1.0。'
    assert result[3]==''
    assert result[4]=='请填写审批人。'


def test_moving_mass_error_explains_the_failed_component():
    result=render_text('console.log(JSON.stringify(t.errorMessage("组件模型：Moving body mass must be explicit in candidate rule: pusher_plate")));')
    assert '运动组件缺少明确的质量或密度' in result and result.endswith('pusher_plate')


def test_frontend_error_keeps_raw_evidence_without_changing_backend_record():
    result=render_text(r'''
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{
  value:'',textContent:'',title:'',hidden:false,dataset:{},classList:{toggle(){}},
  addEventListener(){},querySelectorAll(){return [];}
});return nodes.get(id);};
globalThis.window=globalThis;
globalThis.document={getElementById:node,querySelectorAll:()=>[]};
globalThis.MutationObserver=class{observe(){}};
globalThis.requestAnimationFrame=()=>{};
globalThis.localStorage={getItem:()=>null};
require('./src/agent/web_ui/device_scene.js');
DeviceScenes.configure({token:'isolated-test'});
const raw='Approved rule versions are immutable; increment the version';
const backend={error:raw},before=JSON.stringify(backend);
globalThis.fetch=async()=>({ok:false,json:async()=>backend});
(async()=>{
  await DeviceScenes.activate();
  const known={...nodes.get('ds-error')},afterKnown=JSON.stringify(backend);
  backend.error='Novel drive fault: axis 8 overheated';
  globalThis.fetch=async()=>{throw Error(backend.error);};
  await DeviceScenes.activate();
  const unknown={...nodes.get('ds-error')};
  console.log(JSON.stringify({known,unknown,before,afterKnown}));
})().catch(error=>{process.stderr.write(error.stack);process.exitCode=1;});
''')
    assert '已审批的组件规则版本不能修改' in result['known']['textContent']
    assert result['known']['title'].endswith('Approved rule versions are immutable; increment the version')
    assert not result['known']['hidden']
    assert '查看原始错误' in result['unknown']['textContent']
    assert result['unknown']['title'].endswith('Novel drive fault: axis 8 overheated')
    assert result['before']==result['afterKnown']


def test_retired_sorter_browser_pointers_do_not_clear_other_device_or_plc():
    result=render_text(r'''
const nodes=new Map();
const node=id=>{if(!nodes.has(id))nodes.set(id,{
  value:'',textContent:'',title:'',hidden:false,dataset:{},classList:{toggle(){}},
  addEventListener(){},querySelectorAll(){return [];}
});return nodes.get(id);};
globalThis.window=globalThis;
globalThis.document={getElementById:node,querySelectorAll:()=>[]};
globalThis.MutationObserver=class{observe(){}};
globalThis.requestAnimationFrame=()=>{};
const saved=new Map([
  ['device-scene-task','retired-sorter'],['studio-device-scene','retired-sorter'],
  ['device-scene-selection',JSON.stringify({task:'retired-sorter'})],
  ['device-scene-last-frozen','kept-robot'],['studio-frozen-scene','kept-robot'],
  ['svg-workbench-session','kept-plc-session']
]);
globalThis.localStorage={getItem:key=>saved.get(key)||null,removeItem:key=>saved.delete(key)};
globalThis.location={href:'http://localhost/?view=scenes&scene_task=retired-sorter'};
let url;globalThis.history={replaceState:(_state,_title,value)=>{url=String(value);}};
require('./src/agent/web_ui/device_scene.js');
DeviceScenes.configure({retired_device_scene_tasks:['retired-sorter']});
console.log(JSON.stringify({saved:Object.fromEntries(saved),url}));
''')
    assert result['saved']=={'device-scene-last-frozen':'kept-robot','studio-frozen-scene':'kept-robot',
                             'svg-workbench-session':'kept-plc-session'}
    assert result['url']=='http://localhost/?view=scenes'
