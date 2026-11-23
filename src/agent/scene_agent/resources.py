"""Local AML package resources with explicit provenance and no network fallback."""
import hashlib
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

from .common import SceneError


def tag(node):
    return node.tag.rsplit('}', 1)[-1]


def children(node, name):
    return [n for n in node if tag(n) == name]


def child(node, name):
    return next(iter(children(node, name)), None)


def text(node, name, default=''):
    found = child(node, name)
    return (found.text or '').strip() if found is not None else default


def attributes(node):
    return {a.get('Name'): {'value': text(a, 'Value'), 'unit': a.get('Unit'),
                           'children': attributes(a), 'xml_attributes': dict(a.attrib)}
            for a in children(node, 'Attribute')}


class PackageResources:
    def __init__(self, root, aliases=None):
        self.root = Path(root).resolve()
        self.aliases = aliases or {}
        if not isinstance(self.aliases,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in self.aliases.items()):
            raise SceneError('资源路径修正必须是字符串映射', 'resource_import')
        self.documents = {}
        self.records = {}
        self.references = []

    def relative(self, path):
        path = Path(path).resolve()
        if not path.is_relative_to(self.root):
            raise SceneError('资源路径超出 AML 包目录：' + str(path), 'resource_import')
        return path.relative_to(self.root).as_posix()

    def resolve(self, owner, uri):
        owner = Path(owner).resolve()
        self.relative(owner)
        parsed = urlsplit(uri.replace('\\', '/'))
        if parsed.scheme not in ('', 'file') or parsed.netloc or parsed.query:
            raise SceneError('只接受包内资源引用：' + uri, 'resource_import')
        raw = unquote(parsed.path)
        # The public AML example uses file:///LogicXML/... for package-relative paths.
        if parsed.scheme == 'file':
            if re.match(r'^/?[A-Za-z]:', raw):
                raise SceneError('不接受绝对文件 URI：' + uri, 'resource_import')
            candidate = self.root / raw.lstrip('/')
        else:
            if raw.startswith('/') or re.match(r'^[A-Za-z]:', raw):
                raise SceneError('不接受绝对资源路径：' + uri, 'resource_import')
            candidate = owner.parent / raw if raw else owner
        relative = self.relative(candidate)
        alias = self.aliases.get(relative)
        if alias:
            candidate = self.root / alias
            relative = self.relative(candidate)
        record = {'owner': self.relative(owner), 'uri': uri, 'path': relative,
                  'fragment': unquote(parsed.fragment), 'alias': alias,
                  'status': 'resolved' if candidate.is_file() else 'missing'}
        if record not in self.references:
            self.references.append(record)
        if not candidate.is_file():
            raise SceneError('资源不存在：' + relative, 'resource_import', record)
        return candidate.resolve(), record['fragment']

    def document(self, path):
        path = Path(path).resolve()
        relative = self.relative(path)
        if relative not in self.documents:
            data = path.read_bytes()
            if len(data) > 64 * 1024 * 1024:
                raise SceneError('单个 XML 资源超过 64 MiB', 'resource_import')
            if b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper():
                raise SceneError('资源 XML 不允许 DTD/entity', 'resource_import')
            try:
                class ResourceTree(ET.TreeBuilder):
                    def doctype(self, *args):
                        raise SceneError('资源 XML 不允许 DTD/entity', 'resource_import')
                root = ET.fromstring(data, parser=ET.XMLParser(target=ResourceTree()))
            except ET.ParseError as exc:
                raise SceneError('资源 XML 无法解析：' + relative, 'resource_import') from exc
            ids = {}
            for n in root.iter():
                ident = n.get('id') or n.get('ID') or n.get('globalId')
                if ident:
                    if ident in ids:
                        raise SceneError('资源 ID 重复：' + relative + '#' + ident, 'resource_import')
                    ids[ident] = n
            self.documents[relative] = (root, ids)
            self.records[relative] = {'path': relative, 'sha256': hashlib.sha256(data).hexdigest(),
                                      'bytes': len(data), 'root': tag(root), 'version': root.get('version')}
        return self.documents[relative]

    def blob(self, path):
        """Snapshot non-XML geometry/definition with the same confinement and hashes."""
        path = Path(path).resolve(); relative = self.relative(path)
        data = path.read_bytes()
        if len(data) > 64*1024*1024:
            raise SceneError('单个资源超过 64 MiB', 'resource_import')
        self.records[relative] = {'path': relative, 'sha256': hashlib.sha256(data).hexdigest(),
                                  'bytes': len(data), 'root': path.suffix, 'version': None}
        return data

    def element(self, owner, uri):
        path, fragment = self.resolve(owner, uri)
        root, ids = self.document(path)
        if not fragment:
            return path, root
        if fragment not in ids:
            raise SceneError('资源 fragment 不存在：' + uri, 'resource_import')
        return path, ids[fragment]

    def verify(self):
        for name, record in self.records.items():
            if hashlib.sha256((self.root/name).read_bytes()).hexdigest() != record['sha256']:
                raise SceneError('导入期间源资源发生变化：' + name, 'resource_import')
