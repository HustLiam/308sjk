"""SVG 几何、离散、抬落笔、归档及生成器接线；不依赖模型或在线 PLC。"""
import argparse
import json
import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.svg_input import SVGError, add_svg_arguments, parse_svg, plan_svg, prepare_svg_request, refine_svg_request
from agent.trajectory import goal_text, summarize_for_prompt
from agent.visualization import trajectory_segments, write_preview


def svg_file(tmp_path, body, attributes='viewBox="0 0 100 100"'):
    path = tmp_path / "input.svg"
    path.write_text('<svg xmlns="http://www.w3.org/2000/svg" '+attributes+'>'+body+'</svg>', encoding="utf-8")
    return path


def strokes(trajectory):
    return [s for s in trajectory["steps"] if s["action"] == "polyline_sweep"]


def points(trajectory):
    return [p for s in strokes(trajectory) for p in s["waypoints"]]


def test_relative_and_repeated_commands_close_rectangle(tmp_path):
    path = svg_file(tmp_path, '<path d="m10 10 40 0 v20 h-40 z"/>')
    trajectory = plan_svg(path, width=40, height=40, flip_y=False)
    assert trajectory["params"]["path_count"] == 1
    assert strokes(trajectory)[0]["waypoints"] == [[70,40,0],[70,60,0],[30,60,0],[30,40,0]]


def test_independent_subpaths_have_pen_up_travel(tmp_path):
    path = svg_file(tmp_path, '<path d="M0 0 L10 0 M20 10 L30 10"/>')
    trajectory = plan_svg(path)
    assert trajectory["params"]["path_count"] == 2
    assert [s["action"] for s in trajectory["steps"]] == [
        "pen_up_move","pen_down_move","polyline_sweep","pen_up_move",
        "pen_up_move","pen_down_move","polyline_sweep","pen_up_move","pen_up_move"]
    drawn = [s for s in trajectory_segments(trajectory) if s["draw"]]
    assert len(drawn) == 2
    assert drawn[0]["end"] != drawn[1]["start"]


def test_open_path_is_not_closed(tmp_path):
    trajectory = plan_svg(svg_file(tmp_path, '<path d="M0 0 L10 0 L10 10"/>'))
    assert len(strokes(trajectory)[0]["waypoints"]) == 2
    assert strokes(trajectory)[0]["to"] != trajectory["steps"][1]["to"]


def test_nested_transform_order_and_rotation(tmp_path):
    path = svg_file(tmp_path, '<g transform="translate(10 20) scale(2 3)"><path d="M0 0 L10 10" transform="rotate(90)"/></g>')
    trajectory = plan_svg(path, width=60, height=60, flip_y=False)
    assert trajectory["source"]["bounds"] == pytest.approx([-10,20,10,50])
    assert strokes(trajectory)[0]["to"] == [30,80,0]


@pytest.mark.parametrize("transform", ['matrix(1 0 0 1 10 20)', 'translate(10,20)', 'rotate(0 5 5)', 'skewX(10)', 'skewY(-10)'])
def test_affine_transforms_are_accepted(tmp_path, transform):
    assert plan_svg(svg_file(tmp_path, '<path transform="%s" d="M0 0 L10 10"/>' % transform))["steps"]


@pytest.mark.parametrize("data", [
    'M0 0 C0 100 100 100 100 0 S200 -100 200 0',
    'M0 0 Q50 100 100 0 T200 0',
    'm0 0 c0 100 100 100 100 0 s100 -100 100 0',
    'm0 0 q50 100 100 0 t100 0',
    'M0 0 A50 30 25 0 1 100 0',
    'M0 0 a50 30 25 1 0 100 0',
    'M0 0 A50 50 0 01100 0',  # adjacent flags are legal SVG syntax
])
def test_curves_arcs_and_smooth_commands_flatten(tmp_path, data):
    trajectory = plan_svg(svg_file(tmp_path, '<path d="%s"/>' % data))
    assert trajectory["params"]["point_count"] > 3
    assert all(isinstance(v, int) for p in points(trajectory) for v in p)
    assert all(0 <= p[0] <= 100 and 0 <= p[1] <= 100 for p in points(trajectory))


def test_curve_extrema_not_control_hull_determine_fit(tmp_path):
    trajectory = plan_svg(svg_file(tmp_path, '<path d="M0 0 Q50 100 100 0"/>'))
    assert trajectory["source"]["bounds"] == pytest.approx([0,0,100,50])


def test_quarter_arc_bounds_do_not_include_unused_ellipse(tmp_path):
    trajectory = plan_svg(svg_file(tmp_path, '<path d="M10 0 A10 10 0 0 1 0 10"/>'))
    assert trajectory["source"]["bounds"] == pytest.approx([0,0,10,10])


def test_cubic_overshoot_is_kept_not_flattened_away(tmp_path):
    trajectory = plan_svg(svg_file(tmp_path, '<path d="M0 0 C100 0 -100 0 1 0"/>'))
    p = points(trajectory)
    assert min(v[0] for v in p) < 30 and max(v[0] for v in p) > 70


@pytest.mark.parametrize("body", [
    '<line x1="0" y1="0" x2="20" y2="30"/>',
    '<polyline points="0,0 10,0 20,20"/>',
    '<polygon points="0,0 10,0 20,20"/>',
    '<rect x="0" y="0" width="20" height="30"/>',
    '<rect width="20" height="30" rx="3"/>',
    '<circle cx="20" cy="20" r="10"/>',
    '<ellipse cx="20" cy="20" rx="10" ry="20"/>',
])
def test_basic_shapes(tmp_path, body):
    assert plan_svg(svg_file(tmp_path, body))["params"]["point_count"] >= 2


def test_absolute_svg_lengths_use_css_unit_ratios(tmp_path):
    trajectory = plan_svg(svg_file(tmp_path, '<line x1="0" y1="0" x2="1in" y2="25.4mm"/>'))
    assert trajectory["source"]["bounds"] == pytest.approx([0,0,96,96])


def test_preserve_aspect_ratio_none_is_applied(tmp_path):
    trajectory = plan_svg(svg_file(tmp_path, '<path d="M0 0 L10 10"/>',
        'viewBox="0 0 10 10" width="200" height="100" preserveAspectRatio="none"'))
    assert trajectory["source"]["bounds"] == pytest.approx([0,0,200,100])


def test_default_y_flip_preserves_visual_orientation(tmp_path):
    source = svg_file(tmp_path, '<path d="M0 0 L10 10"/>')
    assert strokes(plan_svg(source))[0]["to"] == [80,20,0]
    assert strokes(plan_svg(source, flip_y=False))[0]["to"] == [80,80,0]


def test_hidden_paths_are_skipped_and_defs_not_drawn(tmp_path):
    source = svg_file(tmp_path, '<defs><path d="M0 0 L1000 1000"/></defs>'
        '<g display="none"><text>ignored</text></g><path d="M0 0 L10 10"/>')
    assert plan_svg(source)["source"]["bounds"] == [0,0,10,10]


def test_child_can_override_visibility(tmp_path):
    source = svg_file(tmp_path, '<g visibility="hidden"><path visibility="visible" d="M0 0 L10 10"/></g>')
    assert plan_svg(source)["params"]["path_count"] == 1


@pytest.mark.parametrize("body", ['<text>abc</text>', '<use href="#x"/>', '<image href="file.png"/>',
    '<path clip-path="url(#x)" d="M0 0 L10 10"/>', '<style>path{display:none}</style>',
    '<svg viewBox="0 0 10 10"><path d="M0 0 L10 10"/></svg>'])
def test_unsupported_visible_content_is_rejected_not_silently_dropped(tmp_path, body):
    with pytest.raises(SVGError):
        plan_svg(svg_file(tmp_path, body))


@pytest.mark.parametrize("data", ['L10 10', 'M0 0 X10 20', 'M0 0 C1 2',
    'M0 0 A10 10 0 2 0 20 20', 'M0 0 L1e309 0', 'M0 0 Z 10 10'])
def test_invalid_path_data_is_rejected(tmp_path, data):
    with pytest.raises(SVGError):
        plan_svg(svg_file(tmp_path, '<path d="%s"/>' % data))


def test_doctype_entities_are_rejected():
    with pytest.raises(SVGError, match="DTD"):
        parse_svg('<!DOCTYPE svg [<!ENTITY x "M0 0 L10 10">]><svg><path d="&x;"/></svg>')


def test_drawing_outside_aml_stroke_is_rejected(tmp_path):
    model = {"kinematics": {"axes": [{"axis":"x_axis","stroke":[0,40],"unit":"%"}]}}
    source = svg_file(tmp_path, '<path d="M0 0 L10 10"/>')
    with pytest.raises(SVGError, match="行程"):
        plan_svg(source, model, center=[35,50], width=40, height=40)


def test_point_budget_refuses_complexity_instead_of_truncating(tmp_path):
    source = svg_file(tmp_path, '<circle r="50"/>')
    with pytest.raises(SVGError, match="预算"):
        plan_svg(source, tolerance=.01, max_points=8)


def test_tiny_independent_path_is_not_silently_dropped(tmp_path):
    source = svg_file(tmp_path, '<path d="M0 0 L100 0 M50 0 L50.01 0"/>')
    with pytest.raises(SVGError, match="退化"):
        plan_svg(source)


def test_source_provenance_preview_and_generation_prompt(tmp_path):
    from agent.chat import save_preparation
    parser = argparse.ArgumentParser()
    add_svg_arguments(parser)
    args = parser.parse_args(['--svg', str(REPO/'examples/svg/house.svg')])
    prepared = prepare_svg_request(args, '照图描画')
    path = save_preparation(prepared, tmp_path/'run')
    assert path.is_file() and (tmp_path/'run/input.svg').read_bytes() == Path(args.svg).read_bytes()
    assert len(prepared['trajectory']['source']['sha256']) == 64
    text = summarize_for_prompt(prepared['trajectory'])
    assert 'polyline_sweep' in text and '不得省略' in text and 'SVG' in goal_text(prepared['trajectory'])
    assert 'INT 量化' in path.read_text(encoding='utf-8')


def test_source_changed_after_planning_is_detected(tmp_path):
    from agent.chat import save_preparation
    source = svg_file(tmp_path, '<path d="M0 0 L10 10"/>')
    trajectory = plan_svg(source)
    source.write_text('<svg/>',encoding='utf-8')
    with pytest.raises(ValueError, match="发生变化"):
        save_preparation({'trajectory':trajectory},tmp_path/'out')


def test_refinement_updates_svg_geometry_and_not_shape(tmp_path):
    parser=argparse.ArgumentParser();add_svg_arguments(parser)
    args=parser.parse_args(['--svg', str(svg_file(tmp_path, '<path d="M0 0 L10 10"/>'))])
    original=prepare_svg_request(args,'照图描画')
    revised=refine_svg_request(args,original,'宽度30，高度40，中心(40,45)')
    assert revised['trajectory']['params']['width']==30
    assert revised['trajectory']['params']['center']==[40,45]
    with pytest.raises(SVGError,match='设备坐标'):
        refine_svg_request(args,original,'宽度30mm')


def test_svg_cli_preview_is_offline_and_archives_source(tmp_path, monkeypatch):
    from agent import chat
    monkeypatch.setattr(chat,'get_api_key',lambda:'')
    monkeypatch.setattr('builtins.input',lambda *a:pytest.fail('SVG 文件预览不应等待输入'))
    result=chat.main(['--svg',str(REPO/'examples/svg/house.svg'),'--preview-only','--confirm',
                      '--runs-root',str(tmp_path),'--task-id','svg_test'])
    assert result==0
    trajectory=json.loads((tmp_path/'svg_test/trajectory.json').read_text(encoding='utf-8'))
    assert trajectory['shape']=='svg' and trajectory['params']['path_count']==4
    assert (tmp_path/'svg_test/input.svg').is_file()
    assert not (tmp_path/'svg_test/iter_001').exists()


def test_generator_receives_every_svg_waypoint():
    from agent.pipeline import PLCGenerator
    trajectory = plan_svg(REPO/'examples/svg/house.svg')
    spec = json.loads((REPO/'examples/specs/plotter3axis.spec.json').read_text(encoding='utf-8'))
    messages = PLCGenerator(client=None, no_cards=True).build_messages(spec, trajectory=trajectory)
    prompt = messages[-1]['content']
    for stroke in strokes(trajectory):
        for point in stroke['waypoints']:
            assert '(%g,%g,%g)' % tuple(point) in prompt
    assert '不是一个标准 FB' in prompt


def test_orchestrator_svg_entry_passes_trajectory_and_archives(tmp_path, monkeypatch):
    from agent import orchestrator
    captured = {}
    def solve(self, spec, generator, **options):
        captured.update(spec=spec, **options)
        return {'status':'final', 'iter':1, 'run_dir':str(tmp_path/'svg_orch')}
    monkeypatch.setattr(orchestrator.Orchestrator, 'solve', solve)
    monkeypatch.setattr(orchestrator, 'get_api_key', lambda:'test-not-used')
    monkeypatch.setattr(sys, 'argv', ['orchestrator', str(REPO/'examples/specs/plotter3axis.spec.json'),
        '--svg', str(REPO/'examples/svg/house.svg'), '--runs-root', str(tmp_path),
        '--task-id', 'svg_orch', '--no-scene', '--no-attribution'])
    assert orchestrator.main() == 0
    assert captured['trajectory']['shape'] == 'svg'
    assert captured['acceptance'] is None
    assert 'SVG' in captured['spec']['task_goal']
    assert (tmp_path/'svg_orch/input.svg').is_file()


def test_unrepresentable_pen_lift_and_coordinates_are_rejected(tmp_path):
    source = svg_file(tmp_path, '<path d="M0 0 L10 10"/>')
    with pytest.raises(SVGError, match='Z 轴'):
        plan_svg(source, {'kinematics':{'axes':[{'axis':'z', 'stroke':[0,.5]}]}})
    model = {'kinematics':{'axes':[{'axis':'x', 'stroke':[0,100000]}]}}
    with pytest.raises(SVGError, match='INT'):
        plan_svg(source, model, center=[50000,50])


@pytest.mark.parametrize('value', [None, 'abc', True])
def test_invalid_tolerance_has_user_facing_error(tmp_path, value):
    with pytest.raises(SVGError):
        plan_svg(svg_file(tmp_path, '<path d="M0 0 L10 10"/>'), tolerance=value)


def test_extreme_arc_does_not_escape_with_arithmetic_exception(tmp_path):
    with pytest.raises(SVGError):
        plan_svg(svg_file(tmp_path, '<path d="M0 0 A1e-300 1e-300 0 0 1 10 10"/>'))
