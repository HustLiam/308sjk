"""Language drawing input for the workbench, sharing the existing deterministic planners."""
import re

from .clarification import prepare_request, extract_explicit_geometry
from .trajectory import goal_text


def drawing_description(trajectory):
    p=trajectory['params'];cx,cy=p['center']
    if trajectory['shape']=='square':
        return '画一个正方形，边长%g，中心(%g,%g)'%(p['size'],cx,cy)
    return '画一个圆，半径%g，中心(%g,%g)，段数%d'%(p['radius'],cx,cy,p['segments'])


def prepare_language_drawing(text, device_model, previous=None, request=None):
    if not isinstance(text,str) or not text.strip() or len(text)>8000:
        raise ValueError('请输入 1..8000 字的绘图要求，例如“画一个正方形，边长40”。')
    # Unknown replacement shapes must not silently retain the previous geometry.
    geometry_text=re.sub(r'(边长|半径|直径|中心|圆心|段数)\s*(?:为|是)\s*',r'\1',text)
    local=extract_explicit_geometry(geometry_text)
    if re.search(r'三角形|矩形|长方形|椭圆|多边形|五角星|triangle|rectangle|ellipse|polygon|star\b',text,re.I):
        raise ValueError('语言绘图目前支持正方形和圆；其他轮廓请使用 SVG 导入。')
    prior=(previous or {}).get('trajectory') or {}
    info={'shape':prior.get('shape','unknown'),'params':prior.get('params',{})}
    # A new shape gets its own dimensions; center may be retained for an edit.
    if local['shape']!='unknown' and local['shape']!=info['shape']:
        info['params']={k:v for k,v in info['params'].items() if k=='center'}
    result=prepare_request(geometry_text,device_model,shape_info=info,accept_defaults=True)
    if not result.get('trajectory'):
        raise ValueError('请先描述要画的图形，例如“画一个正方形”或“画一个圆，半径20”。')
    result['summary']=drawing_description(result['trajectory']).removeprefix('画一个')+'（设备坐标）'
    result['request_text']=(request or text)+'\n当前语言绘图轨迹（以步表为准）：'+goal_text(result['trajectory'])
    return result
