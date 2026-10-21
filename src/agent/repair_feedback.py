"""Bounded, inspectable knowledge feedback; no extra model calls."""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .memory import MemoryStore, normalize_errors


class GenerationStalled(RuntimeError):
    """Repeated evidence without a useful code change must not spend another call."""


def source_signature(xml):
    """Ignore formatting/comments, retain declarations, instructions and literal values."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return hashlib.sha256(xml.encode()).hexdigest()
    def node(element):
        def normalize(text):
            # Strings may contain URLs/comment markers; recognize them before comments.
            parts = re.findall(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|\(\*[\s\S]*?\*\)|//[^\n]*|[^'\"/\s(]+|.", text or '')
            return ''.join(p if p.startswith(("'", '"')) else re.sub(r'\s+', '', p)
                           for p in parts if not p.startswith(('(*', '//')))
        text = normalize(element.text)
        attrs = sorted((k, v) for k, v in element.attrib.items()
                       if k not in ('creationDateTime', 'modificationDateTime'))
        return [element.tag, attrs, text, [[node(child), normalize(child.tail)] for child in element]]
    return hashlib.sha256(json.dumps(node(root), ensure_ascii=False).encode()).hexdigest()


def knowledge_feedback(errors, gate='generate', memory=None):
    memory = memory or MemoryStore()
    pitfalls = memory.match_pitfalls(errors)
    fixes = memory.match_fixes(errors)
    lessons = memory.match_lessons(errors, gate=gate)
    lines = ['已知坑 %s：%s' % (p['id'], p['fix']) for p in pitfalls]
    lines += ['历史成功修复：' + str(f.get('action', '')) for f in fixes]
    for lesson in lessons:
        lines.append('历史相似修复：' + lesson.get('fix_summary', ''))
        lines += lesson.get('diff_hunks', [])[:2]
    return '\n'.join(lines), {'pitfalls': [p['id'] for p in pitfalls],
                             'fixes': len(fixes), 'lessons': len(lessons)}


def static_feedback(generator, errors, xml):
    advice, hits = knowledge_feedback(errors, memory=getattr(generator, 'memory', None))
    signature = (source_signature(xml or ''), normalize_errors(errors))
    seen = getattr(generator, '_failed_candidates', set())
    repeated = signature in seen
    seen.add(signature)
    generator._failed_candidates = seen
    record = dict(hits, errors=list(errors), repeated=repeated)
    if generator.evidence_dir:
        path = Path(generator.evidence_dir) / ('call_%03d.feedback.json' % generator.call_count)
        path.parent.mkdir(parents=True, exist_ok=True)
        record.update(source_signature=signature[0], advice=advice)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    generator.report('修复知识检索：故障库 %s；历史修复 %d 条；经验 %d 条。' %
                     (','.join(hits['pitfalls']) or '未命中', hits['fixes'], hits['lessons']))
    if repeated:
        generator.stop_reason = 'no_progress'
        raise GenerationStalled('相同程序重复出现相同静态错误，停止自动调用；详见本轮 feedback.json')
    # Keep at least one example of every error family instead of hiding later families.
    groups = {}
    for error in errors:
        key = re.match(r'(?:\[[^]]+\]\s*)*(?:R\d+:)?', error)[0]
        groups.setdefault(key, []).append(error)
    bounded = []
    while groups and len(bounded) < 18:
        for key in list(groups):
            bounded.append(groups[key].pop(0))
            if not groups[key]:
                del groups[key]
            if len(bounded) == 18:
                break
    return '\n'.join('- ' + error for error in bounded) + ('\n' + advice if advice else '')
