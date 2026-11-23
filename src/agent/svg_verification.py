"""SVG 仿真验收：完整路点与实际接触轨迹，容差取冻结轴参数。"""
import copy


def verify_svg(trace, trajectory, spec):
    from src.pipeline.scenario_mujoco import verify_trajectory
    import numpy as np
    if trajectory.get('shape')!='svg':
        return []
    expanded=copy.deepcopy(trajectory)
    expanded['steps']=[]
    segments=[]
    current=None
    for step in trajectory['steps']:
        points=step.get('waypoints') or [step['to']]
        for point in points:
            expanded['steps'].append({'no':len(expanded['steps'])+1,'to':point})
            if current is not None and current[2]==point[2]==trajectory['pen_z']['down'] and current[:2]!=point[:2]:
                segments.append([current[:2],point[:2]])
            current=point
    actual=[dict(row,pen_down=row.get('pen_contact') is True) for row in trace]
    path=verify_trajectory(actual,expanded,spec)
    path.update(id='SVG_PATH',desc='SVG 全部路点与实际落笔')
    axes={a['axis'].removesuffix('_axis'):a for a in spec['axes']}
    contact=[row for row in actual if row['pen_down']]
    geometry={'id':'SVG_GEOMETRY','desc':'实际笔迹与 SVG 轮廓','pass':False,
              'evidence':'缺少实际接触笔迹或参考线段'}
    if contact and segments:
        scale=np.array([axes[a]['poswin'] for a in 'xy'],dtype=float)
        if not np.all(np.isfinite(scale)) or np.any(scale<=0):
            geometry['evidence']='冻结轴到位容差无效'
            return [path,geometry]
        lines=np.array(segments,dtype=float)/scale
        starts=lines[:,0];delta=lines[:,1]-starts
        norm=(delta*delta).sum(axis=1)
        pts=np.array([[row[axes[a]['io']['fb']] for a in 'xy'] for row in contact])/scale
        if not np.all(np.isfinite(pts)):
            geometry['evidence']='实际物理采样包含非有限位置'
            return [path,geometry]
        worst=0.0
        for start in range(0,len(pts),128):
            diff=pts[start:start+128,None,:]-starts
            ratio=np.clip((diff*delta).sum(axis=2)/np.maximum(norm,1e-20),0,1)
            errors=np.linalg.norm(diff-ratio[:,:,None]*delta,axis=2).min(axis=1)
            worst=max(worst,float(errors.max()))
        geometry.update({'pass':worst<=1+1e-6,'evidence':
            '实际接触采样%d点；最大偏离为轴到位容差的%.4f倍（上限1，未放宽容差）' % (len(contact),worst)})
    return [path,geometry]
