"""Curated, UNAPPROVED FactoryForge modeling candidates for reproducible review.

These are example rules, not LLM output and not pre-approved library entries.
Physical approximations absent from AML are listed for the human reviewer.
"""
from src.agent.scene_agent.device_ir import values
from src.agent.scene_agent.rules import RULE_FORMAT
from src.agent.scene_agent.requirements import MODEL_PROFILE


def make_reference_candidates(ir):
    excluded={j['id'] for j in ir['joints']};types={c['type'] for c in ir['components'] if c['id'] not in excluded}
    rules=[]
    def geom(name,size,pos=None,**extra):
        return {'name':name,'type':'box','size':size,'pos':pos or [0,0,0],**extra}
    for kind in sorted(types):
        samples=[values(c) for c in ir['components'] if c['type']==kind]
        numeric={k for p in samples for k,v in p.items() if type(v) in (int,float) and not k.startswith(('position_','rotation_'))}
        bounds={k:[min(p[k] for p in samples if k in p),max(p[k] for p in samples if k in p)] for k in numeric}
        model={'geoms':[],'behavior':'none','interfaces':[],'physical_parameters':{}}
        assumptions=[];limitations=[]
        if kind=='BeltConveyor':
            model['behavior']='conveyor'
            model['geoms']=[geom('belt',['=length_m/2','=width_m/2','=thickness_m/2'],
                friction=.01,priority=1,rgba=[.18,.22,.27,1])]
            assumptions=['输送表面采用实际接触触发的切向力近似，上限为 AML 的 mu*m*g，速度响应为 20 ms；碰撞体 friction=0.01 用于数值正则化，AML 摩擦系数用于切向力上限。']
            limitations=['用于输送、停机、接触和传感逻辑验证，不覆盖皮带弹性或电机转矩精度。']
        elif kind=='DiffusePhotoelectricSensor':
            model['behavior']='ray'
            model['geoms']=[geom('post',[.015,.015,'=beam_origin_local_z_m/2'],[0,0,'=beam_origin_local_z_m/2'],collision=False,rgba=[1,.7,.05,1])]
            assumptions=['立柱仅为示意，检测射线的原点、方向和量程使用 AML 参数。']
            limitations=['不模拟传感器外壳细节；传感输出仍由实际射线检测计算。']
        elif kind=='SingleCommandPusher':model['behavior']='binary_prismatic'
        elif kind in ('FixedBody','KinematicBody'):
            model['geoms']=[geom('shape',['=size_x_m/2','=size_y_m/2','=size_z_m/2'],
                ['$geometry_center_x_m','$geometry_center_y_m','$geometry_center_z_m'] if kind=='FixedBody' else [0,0,0],
                collision='$collision_enabled',rgba=[.18,.4,.75,1])]
            if kind=='KinematicBody':
                model['physical_parameters']={'mass_kg':1,'position_kp':50000,'damping':400}
                assumptions=['推板使用 1 kg 等效质量、position_kp=50000、damping=400 近似运动响应；故障通过临时关节等式保持实际位置，到位反馈读取实际关节 q。']
                limitations=['不验证气压、耗气量或实测气动受力精度。']
        elif kind=='GravityChute':
            model['geoms']=[geom('ramp',['=ramp_width_m/2','=ramp_length_m/2','=ramp_thickness_m/2'],
                [0,'=high_lip_local_y_m-cos(incline_deg)*ramp_length_m/2+sin(incline_deg)*ramp_thickness_m/2',
                 '=high_lip_local_z_m-sin(incline_deg)*ramp_length_m/2-cos(incline_deg)*ramp_thickness_m/2'],
                rpy_rad=['$incline_deg',0,0],friction='$surface_friction',rgba=[.5,.55,.6,1])]
        elif kind=='SimulationSource':
            model['behavior']='source';model['geoms']=[geom('marker',[.025,.15,.025],collision=False,rgba=[.1,.7,.35,1])]
            assumptions=['通过有限工件池实现生成与回收；容量不足时保留一个待处理触发沿，料源标记仅为示意。']
        elif kind=='SimulationSink':
            model['behavior']='sink';model['geoms']=[geom('zone',['=zone_size_x_m/2','=zone_size_y_m/2','=zone_size_z_m/2'],collision=False,rgba=[.2,.7,.4,.13])]
            assumptions=['出口进入判定依据实际变换后的工件碰撞盒与出口区 AABB 重叠。']
            limitations=['当前范围为示例箱体，不提供任意旋转、任意形状工件的精确相交判定。']
        elif kind=='RigidCarton':
            model['behavior']='rigid';model['geoms']=[geom('carton',['=size_x_m/2','=size_y_m/2','=size_z_m/2'],mass='$mass_kg',friction='$friction',rgba=[.65,.43,.22,1])]
        elif kind=='StackIndicator':
            model['behavior']='indicator';model['geoms']=[{'name':'lamp','type':'sphere','size':[.05],'pos':[0,0,.2],'collision':False,'rgba':[.1,.8,.2,1]}]
            assumptions=['指示灯外形尺寸为显示近似，不参与碰撞或检测。']
        elif kind=='OperatorPanel':
            model['behavior']='panel'
            assumptions=['按 AML 映射操作员输入触点与设定值；PLC 工艺逻辑由独立需求定义，模型不内置分拣策略。']
            limitations=['未提供的面板外壳不建模；本次不验证按钮内部接线或急停电气回路。']
        elif kind not in ('HeightSortingLine','PLCInterface','WorkpieceCatalog'):
            raise ValueError('No curated reference recipe for '+kind)
        rules.append({'format':RULE_FORMAT,'component_type':kind,'version':'0.2.0','parameter_bounds':bounds,
                      'model':model,'assumptions':assumptions,'unresolved':[],
                      'provenance':{'kind':'curated_reference_candidate','validation_profile':MODEL_PROFILE,'limitations':limitations,
                                    'source_commit':'57273ccde7064c818c507c638f7e92526bc5c436'}})
    return rules
