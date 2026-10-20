"""Bounded local text extraction; no Office process, network fetch, PDF or OCR."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 1_048_576
MAX_TEXT_CHARS = 120_000
MAX_XML_BYTES = 8_388_608
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
WS = '{http://purl.oclc.org/ooxml/wordprocessingml/main}'


class DocumentParseError(ValueError):
    pass


class _BoundedTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__(); self.nodes = self.depth = 0

    def start(self, tag, attrs):
        self.nodes += 1; self.depth += 1
        if self.nodes > 50000 or self.depth > 128:
            raise DocumentParseError('Word XML节点数量或嵌套深度超出解析上限')
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)


def _xml(xml):
    scan=xml.replace(b'\x00',b'').upper()
    if b'<!DOCTYPE' in scan or b'<!ENTITY' in scan:
        raise DocumentParseError('拒绝带DTD或实体声明的XML')
    return ET.fromstring(xml,parser=ET.XMLParser(target=_BoundedTree()))


@dataclass(frozen=True)
class ParsedDocument:
    filename: str
    content: str
    manifest: dict


def safe_filename(name):
    name = name.replace('\\', '/').rsplit('/', 1)[-1]
    if not name or len(name) > 160 or any(ord(c) < 32 for c in name):
        raise DocumentParseError('文件名为空、过长或包含控制字符')
    return name


def _decode(data, encoding):
    if encoding not in {'auto', 'utf-8', 'utf-16', 'gb18030'}:
        raise DocumentParseError('编码须为auto、utf-8、utf-16或gb18030')
    if encoding == 'auto':
        encoding = 'utf-16' if data.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            encoding = 'gb18030'
    try:
        return data.decode('utf-8-sig' if encoding == 'utf-8' else encoding), encoding
    except (UnicodeError, LookupError) as exc:
        raise DocumentParseError('无法按所选编码解码，请检查文件或明确指定编码') from exc


def parse_document(filename: str, data: bytes, encoding='auto') -> ParsedDocument:
    filename = safe_filename(filename)
    extension = PurePosixPath(filename).suffix.lower()
    if extension not in {'.txt', '.text', '.md', '.markdown', '.csv', '.json', '.jsonl', '.docx'}:
        raise DocumentParseError('支持TXT/Markdown/CSV/JSON/JSONL/DOCX；旧DOC请先在Word中另存为DOCX。暂不接入PDF、图片或OCR')
    if not data or len(data) > MAX_FILE_BYTES:
        raise DocumentParseError('上传文件不能为空，单文件上限1 MiB')
    if extension == '.docx':
        text, anchors, warnings = _docx(data)
        encoding = 'ooxml'
    else:
        text, encoding = _decode(data, encoding)
        if len(text) > MAX_TEXT_CHARS:
            raise DocumentParseError('解析正文超过120000字符，未截断或部分导入')
        if any(ord(c) < 32 and c not in '\t\r\n' for c in text):
            raise DocumentParseError('文件包含二进制控制字符，未按文本导入')
        if extension in {'.json', '.jsonl'}:
            try:
                if extension == '.json':
                    json.loads(text)
                else:
                    for line in text.splitlines():
                        if line.strip(): json.loads(line)
            except (ValueError, RecursionError) as exc:
                raise DocumentParseError('JSON/JSONL格式不合法；不会静默改写原始结构') from exc
        if extension == '.csv':
            try:
                for _ in csv.reader(io.StringIO(text), strict=True): pass
            except csv.Error as exc:
                raise DocumentParseError('CSV格式不合法') from exc
        anchors, position = [], 0
        for number, line in enumerate(text.splitlines(keepends=True), 1):
            if number > 20000:
                raise DocumentParseError('文本行数超过20000，请拆分文件后导入')
            anchors.append({'start': position, 'end': position + len(line), 'locator': f'line:{number}'})
            position += len(line)
        warnings = ['gb18030_auto_fallback'] if encoding == 'gb18030' else []
    if not text.strip():
        raise DocumentParseError('文件没有可读取的文字；不会对图片执行OCR')
    if len(text) > MAX_TEXT_CHARS:
        raise DocumentParseError('解析正文超过120000字符，请分成有来源的文件；未截断或部分导入')
    return ParsedDocument(filename, text, {'parser': 'memoria-text-v1', 'format': extension[1:],
        'encoding': encoding, 'file_sha256': hashlib.sha256(data).hexdigest(), 'file_bytes': len(data),
        'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'anchors': anchors, 'warnings': warnings})


def _docx(data):
    pieces, anchors, warnings, length = [], [], [], 0

    def append(text, locator, separator='\n', keep_empty=False, style=''):
        nonlocal length
        if not text and not keep_empty: return
        if pieces:
            pieces.append(separator); length += len(separator)
        start = length
        pieces.append(text); length += len(text)
        if length > MAX_TEXT_CHARS:
            raise DocumentParseError('Word正文超过120000字符，未截断或部分导入')
        anchors.append({'start': start, 'end': length, 'locator': locator,'style':style})

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = [m.filename for m in members]
            if len(members) > 2000 or len(set(names)) != len(names):
                raise DocumentParseError('DOCX包成员数量异常或存在重复成员')
            if any(m.flag_bits & 1 or m.file_size > MAX_XML_BYTES or
                   m.file_size / max(m.compress_size, 1) > 200 for m in members):
                raise DocumentParseError('不支持加密或异常压缩的Word文件')
            if sum(m.file_size for m in members) > MAX_XML_BYTES:
                raise DocumentParseError('DOCX展开体积超过8 MiB')
            if any('..' in PurePosixPath(n.replace('\\', '/')).parts or n.startswith(('/', '\\')) for n in names):
                raise DocumentParseError('DOCX包含异常成员路径')
            if 'word/document.xml' not in names or '[Content_Types].xml' not in names:
                raise DocumentParseError('文件不是有效的DOCX文档包')
            if any('vbaproject' in n.lower() for n in names):
                raise DocumentParseError('不接入包含宏代码的Word文件')
            styles={}
            if 'word/styles.xml' in names:
                definitions=_xml(archive.read('word/styles.xml'))
                ns=WS if definitions.tag.startswith(WS) else W
                for style in definitions.iter(ns+'style'):
                    label=style.find(ns+'name'); outline=style.find(ns+'pPr/'+ns+'outlineLvl')
                    value=label.get(ns+'val','') if label is not None else ''
                    if outline is not None and outline.get(ns+'val')=='0':value='Heading1'
                    styles[style.get(ns+'styleId','')]=value
            parts = ['word/document.xml'] + sorted(n for n in names if re.fullmatch(
                r'word/(?:header\d+|footer\d+|footnotes|endnotes)\.xml', n))
            if any(n.startswith('word/media/') for n in names): warnings.append('images_not_recognized')
            if 'word/comments.xml' in names: warnings.append('review_comments_excluded')
            for part in parts:
                xml = archive.read(part)
                root = _xml(xml)
                namespaces = {node.tag.split('}')[0] + '}' for node in root.iter() if '}' in node.tag}
                ns = WS if WS in namespaces else W
                if any(node.tag in {ns+'ins', ns+'del', ns+'moveFrom', ns+'moveTo'} for node in root.iter()):
                    raise DocumentParseError('Word中存在未确认修订，请在Word中接受或拒绝修订后再导入')
                if any(node.tag == ns+'altChunk' for node in root.iter()):
                    raise DocumentParseError('Word包含外嵌文档块，请先转为普通正文后导入')
                if any(node.tag == ns+'instrText' for node in root.iter()): warnings.append('field_instructions_not_executed')
                if any(node.tag.endswith(('}oMath','}oMathPara')) for node in root.iter()):warnings.append('math_not_linearized')
                number = 0
                parents = {child: parent for parent in root.iter() for child in parent}
                tables = list(root.iter(ns+'tbl'))
                previous_cell = None
                # Paragraph iteration follows OOXML document order, including table-cell paragraphs.
                for paragraph in root.iter(ns+'p'):
                    number += 1
                    def inline(node):
                        if node is not paragraph and node.tag == ns+'p': return ''
                        if node.tag == ns+'t': return node.text or ''
                        if node.tag == ns+'tab': return '\t'
                        if node.tag in {ns+'br', ns+'cr'}: return '\n'
                        return ''.join(inline(child) for child in node)
                    text = inline(paragraph)
                    ancestors, current = [], paragraph
                    while current in parents:
                        current = parents[current]; ancestors.append(current)
                    table = next((a for a in ancestors if a.tag == ns+'tbl'), None)
                    locator = f'{part}#paragraph:{number}'
                    cell_key = None
                    if table is not None:
                        row = next((a for a in ancestors if a.tag == ns+'tr'), None)
                        cell = next((a for a in ancestors if a.tag == ns+'tc'), None)
                        if row is None or cell is None: raise DocumentParseError('Word表格结构不合法')
                        rows, cells = [n for n in table if n.tag == ns+'tr'], [n for n in row if n.tag == ns+'tc']
                        if row in rows and cell in cells:
                            locator += f'/table:{tables.index(table)+1}/row:{rows.index(row)+1}/cell:{cells.index(cell)+1}'
                            cell_key = (table,row,cell)
                    separator = '\t' if previous_cell and cell_key and previous_cell[:2]==cell_key[:2] and previous_cell[2] is not cell_key[2] else '\n'
                    style_node=paragraph.find(ns+'pPr/'+ns+'pStyle')
                    style=style_node.get(ns+'val','') if style_node is not None else ''
                    style=styles.get(style,style)
                    append(text, locator, separator,keep_empty=cell_key is not None,style=style)
                    if text or cell_key is not None:previous_cell=cell_key
    except DocumentParseError:
        raise
    except (zipfile.BadZipFile, ET.ParseError, KeyError, RuntimeError, NotImplementedError, ValueError, OSError) as exc:
        raise DocumentParseError('DOCX损坏、格式不受支持或无法安全解析') from exc
    return ''.join(pieces), anchors, sorted(set(warnings))
