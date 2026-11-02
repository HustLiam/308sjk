'use strict';
// Component ownership and joint references are distinct: constraints never own their endpoints.
globalThis.DeviceTree={
  build(ir){
    const joints=new Map((ir.joints||[]).map(j=>[j.id,j]));
    const source=new Map(ir.components.map(c=>[c.id,c]));
    const nodes=new Map(ir.components.filter(c=>!joints.has(c.id)).map(c=>[c.id,{id:c.id,kind:'component',component:c,joint:null,name:c.name}]));
    const frames=new Map((ir.interfaces||[]).map(f=>[f.id,f]));
    const parents=new Map(),issues=[];
    for(const [id,n] of nodes){let parent=n.component.parent_id||null;const seen=new Set([id]);
      // Legacy AML may nest a component in a joint declaration. Retain its containing assembly.
      while(parent&&joints.has(parent)){if(seen.has(parent)){issues.push({kind:'cycle',id});parent=null;break;}seen.add(parent);parent=source.get(parent)?.parent_id||null;}
      parents.set(id,parent);
    }
    for(const frame of frames.values())if(['JointAttachment','MechanicalMount'].includes(frame.type)&&nodes.has(frame.component_id)){
      nodes.set(frame.id,{id:frame.id,kind:'interface',name:frame.name+' · 接口',frame,targetId:frame.component_id,component:null,joint:null});parents.set(frame.id,frame.component_id);
    }
    const reserved=new Set([...source.keys(),...joints.keys(),...(ir.interfaces||[]).map(f=>f.id)]);
    const virtualId=prefix=>{let id=prefix;while(reserved.has(id))id+=':';reserved.add(id);return id;};
    const jointClassId=virtualId('@joint-class');
    nodes.set(jointClassId,{id:jointClassId,kind:'joint_class',name:'运动副类（Joint）',component:null,joint:null});parents.set(jointClassId,null);
    for(const joint of joints.values()){
      nodes.set(joint.id,{id:joint.id,kind:'joint',name:joint.name||source.get(joint.id)?.name||joint.id,component:source.get(joint.id)||null,joint});parents.set(joint.id,jointClassId);
      if(joint.parent_body===joint.child_body)issues.push({kind:'joint_self_reference',id:joint.id});
      for(const side of ['parent','child']){
        const id=virtualId('@joint-reference:'+joint.id+':'+side),targetId=joint[side+'_body'];
        const target=nodes.get(targetId),valid=target?.kind==='component';
        const port=[...frames.values()].find(f=>f.component_id===joint.id&&f.name===side+'_body');
        const link=(ir.connections||[]).find(c=>c.a===port?.id||c.b===port?.id);
        const endpoint=link?frames.get(link.a===port?.id?link.b:link.a):null;
        if(!valid)issues.push({kind:'missing_component_reference',id:joint.id,side,target_id:targetId});
        nodes.set(id,{id,kind:'reference',name:(side==='parent'?'父接口':'子接口')+' → '+(source.get(targetId)?.name||targetId||'未指定')+(endpoint?' / '+endpoint.name:''),
          targetId,interfaceId:endpoint?.id,side,valid,component:null,joint:null});parents.set(id,joint.id);
      }
    }
    for(const id of nodes.keys()){
      const trail=new Set();let cursor=id;
      while(cursor){
        if(!nodes.has(cursor)){issues.push({kind:'missing_parent',id});parents.set(id,null);break;}
        if(trail.has(cursor)){issues.push({kind:'cycle',id:cursor});parents.set(cursor,null);break;}
        trail.add(cursor);cursor=parents.get(cursor);
      }
    }
    const children=new Map([...nodes.keys()].map(id=>[id,[]])),roots=[];
    for(const id of nodes.keys()){const parent=parents.get(id);if(parent&&children.has(parent))children.get(parent).push(id);else roots.push(id);}
    return {nodes,parents,children,roots,issues,jointClassId,joints};
  }
};
