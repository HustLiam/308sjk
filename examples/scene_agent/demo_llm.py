"""Real opt-in model/Blender authoring probe; never approves components."""
import argparse
from pathlib import Path

from src.agent.client import BigModelClient
from src.agent.config import get_api_key
from src.agent.scene_agent.author import SceneGenerationAgent
from src.agent.scene_agent.common import SceneError, write_json
from src.agent.scene_agent.rules import ComponentLibrary
from src.agent.scene_agent.validation import validate_candidate


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',required=True)
    args=parser.parse_args();output=Path(args.out)
    if not get_api_key():raise SceneError('No configured model key')
    params={'width_m':.3,'depth_m':.15,'height_m':.2,'wall_m':.01}
    component={'id':'demo-hollow-support','name':'HollowSupport','type':'HollowSupport',
        'role':'static_fixture','parameters':{k:{'value':v,'source':{'kind':'explicit_demo_input'}} for k,v in params.items()},
        'pose':{'position_m':[0,0,.2],'rpy_rad':[0,0,0]}}
    ir={'components':[component],'joints':[],'interfaces':[],'io':[]}
    write_json(output/'authoring_input.json',ir)
    request=('Generate a static rectangular hollow support with a through-channel along Y. '
        'Use a Blender Boolean difference, four primitive box collision proxies and a central '
        'clearance probe. Exact outside full dimensions are width=.3 m, depth=.15 m, height=.2 m; '
        'wall=.01 m. All bounds may be pinned to this sample. Name the Blender body root; its origin '
        'is the bounding box center. Visual OBJ must be geometry/bodies/root.obj, collision=false. '
        'No mass or drive required for this static support. Treat absence of strength analysis as '
        'an explicit modeling approximation, not a missing device fact. Include a central mount frame.')
    library=ComponentLibrary(output/'library')
    try:
        rules=SceneGenerationAgent(BigModelClient(get_api_key(),timeout=180)).draft_rules(
            ir,request,{'HollowSupport'},output/'model_calls')
        rule=rules[0];author=SceneGenerationAgent(BigModelClient(get_api_key(),timeout=180))
        for attempt in range(1,4):
            candidate=library.stage(rule,origin='real_llm_authoring_probe')
            try:report,token=validate_candidate(library,candidate,params)
            except (SceneError,ValueError,KeyError) as exc:
                report={'status':'failed','checks':[{'name':'build','passed':False,'detail':str(exc)}],
                        'error_details':getattr(exc,'details',{})}
                token=library.submit_validation(candidate,report)
            write_json(output/('validation_round_%03d.json'%attempt),report)
            if report['status']=='passed' or attempt==3:break
            rule=author.repair_candidate(ir,request,rule,[report],output/'model_calls'/('build_repair_%03d'%attempt))
        result={'status':report['status'],'candidate':str(candidate),'review_sha256':token,
                'approval':'pending','plc_acceptance':'not_run','checks':report['checks']}
    except Exception as exc:
        result={'status':'failed','layer':getattr(exc,'layer','component_model'),'error':str(exc),'plc_acceptance':'not_run'}
        write_json(output/'result.json',result)
        raise
    write_json(output/'result.json',result)
    print(result['status'],result['candidate'])


if __name__=='__main__':main()
