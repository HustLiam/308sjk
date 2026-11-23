"""物理 SVG 验收不能被完成灯、路径终点或跳过中间点欺骗。"""
import copy
from agent.svg_verification import verify_svg


def setup():
    spec={'axes':[{'axis':a+'_axis','poswin':.1,'io':{'fb':a+'_fb'}} for a in 'xyz']}
    trajectory={'shape':'svg','pen_z':{'down':0,'up':10},'steps':[
        {'no':1,'to':[0,0,10]},{'no':2,'to':[0,0,0]},
        {'no':3,'to':[1,1,0],'waypoints':[[1,0,0],[1,1,0]]},
        {'no':4,'to':[1,1,10]}]}
    coords=[[0,0,10],[0,0,0],[.5,0,0],[1,0,0],[1,.5,0],[1,1,0],[1,1,10]]
    trace=[dict(zip(['x_fb','y_fb','z_fb'],p),t=i,pen_contact=p[2]==0,pen_down=True) for i,p in enumerate(coords)]
    return trace,trajectory,spec


def test_complete_physical_path_passes_without_changing_thresholds():
    trace,trajectory,spec=setup();original=copy.deepcopy(spec)
    results=verify_svg(trace,trajectory,spec)
    assert all(r['pass'] for r in results) and spec==original


def test_skipping_an_intermediate_vertex_fails_even_with_all_endpoints():
    trace,trajectory,spec=setup()
    del trace[2:5]
    results=verify_svg(trace,trajectory,spec)
    assert not next(r for r in results if r['id']=='SVG_PATH')['pass']


def test_fake_pen_signal_or_off_path_contact_cannot_pass():
    trace,trajectory,spec=setup()
    for row in trace:row['pen_contact']=False
    assert not any(r['pass'] for r in verify_svg(trace,trajectory,spec))
    trace,trajectory,spec=setup()
    trace.insert(3,{'x_fb':.5,'y_fb':.5,'z_fb':0,'t':2.5,'pen_contact':True})
    assert not next(r for r in verify_svg(trace,trajectory,spec) if r['id']=='SVG_GEOMETRY')['pass']
