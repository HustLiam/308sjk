"""Local IO qualifications tied to exact mapping, model and adapter settings."""
import hashlib
from pathlib import Path

from .common import digest, read_json, write_json
from .robotcell_io import load_config, component_id

ROOT = Path(__file__).resolve().parents[3]


def fingerprint(config):
    folder = Path(config['adapter']['scene_dir'])
    files = {name: hashlib.sha256((folder/name).read_bytes()).hexdigest()
             for name in ('scene.xml', 'body_mapping.json', 'assembly_checks.json')}
    return digest({'config': config, 'model': files})


def receipt_path(profile):
    return Path(profile).with_name(Path(profile).name.removesuffix('.io.json') + '.verified.json')


def certify(profile, config, report_path):
    report_path = Path(report_path).resolve()
    report = read_json(report_path)
    if report.get('status') != 'passed' or not report.get('unbound_qpos_unchanged') or not report.get('events'):
        raise ValueError('IO 双向检验未通过')
    expected = {p['name'] for p in config['io_list'] if p['role'] == 'target'}
    if not expected.issubset(report.get('channels', {})):
        raise ValueError('IO 检验通道不完整')
    for event in report['events']:
        if not event.get('feedback') or event['feedback'] != event.get('plc_observed_inputs'):
            raise ValueError('PLC 未确认接收模型反馈')
    write_json(receipt_path(profile), {'status': 'passed', 'component_id': component_id(config),
        'fingerprint': fingerprint(config), 'report': str(report_path),
        'report_sha256': hashlib.sha256(report_path.read_bytes()).hexdigest()})


def qualified(profile, config):
    try:
        receipt = read_json(receipt_path(profile))
        return (load_config(profile) == config and receipt['status'] == 'passed' and receipt['component_id'] == component_id(config)
                and receipt['fingerprint'] == fingerprint(config)
                and receipt['report_sha256'] == hashlib.sha256(Path(receipt['report']).read_bytes()).hexdigest())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def profiles(scene_dir, roots=None):
    scene_dir = Path(scene_dir).resolve()
    roots = roots if roots is not None else (ROOT/'examples', scene_dir.parent/'io')
    found = {}
    duplicates = set()
    for root in roots:
        for path in sorted(Path(root).rglob('*.io.json')):
            try:
                config = load_config(path)
                if Path(config['adapter']['scene_dir']).resolve() != scene_dir:
                    continue
                cid = component_id(config)
                if cid in found:
                    duplicates.add(cid)
                found[cid] = (path, config)
            except (OSError, ValueError, KeyError, TypeError):
                continue
    return {cid: entry for cid, entry in found.items() if cid not in duplicates and qualified(*entry)}
