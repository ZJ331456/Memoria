"""Temporary engineering file bytes; no uploaded personal documents or model calls."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from memoria.api.app import _create_tenant_app
from memoria.memory.document_parser import DocumentParseError, parse_document
from tests.test_memory_kernel import config_at


def docx(body, **parts):
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + body + '</w:body></w:document>')
        for path, text in parts.items(): archive.writestr(path, text)
    return data.getvalue()


@pytest.mark.parametrize('encoding', ['utf-8-sig', 'utf-16', 'gb18030'])
def test_text_encoding_and_character_anchors_are_exact(encoding):
    raw = '第一行。\r\n第二行：保留条件。\r\n'
    parsed = parse_document('C:\\private\\notes.txt', raw.encode(encoding))
    assert parsed.filename == 'notes.txt' and parsed.content == raw
    assert parsed.content[parsed.manifest['anchors'][1]['start']:parsed.manifest['anchors'][1]['end']] == '第二行：保留条件。\r\n'


@pytest.mark.parametrize(('name', 'text'), [('x.md', '# Heading\nOriginal text'),
    ('x.csv', 'name,value\n工程,原话\n'), ('x.json', '{"topic":"来源","enabled":true}'),
    ('x.jsonl', '{"a":1}\n{"a":2}\n')])
def test_plain_formats_are_validated_but_not_reformatted(name, text):
    assert parse_document(name, text.encode()).content == text


def test_docx_text_table_header_and_image_warning():
    raw = docx('<w:p><w:r><w:t>工程段落</w:t></w:r></w:p><w:tbl><w:tr>'
        '<w:tc><w:p><w:r><w:t>名称</w:t></w:r></w:p></w:tc>'
        '<w:tc><w:p><w:r><w:t>检索条件</w:t></w:r></w:p></w:tc></w:tr></w:tbl>',
        **{'word/header1.xml':'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>页眉</w:t></w:r></w:p></w:hdr>',
           'word/media/image1.png':'not-a-real-image-not-read'})
    parsed = parse_document('工程.docx', raw)
    assert parsed.content == '工程段落\n名称\t检索条件\n页眉'
    assert '/table:1/row:1/cell:2' in parsed.manifest['anchors'][2]['locator']
    assert 'images_not_recognized' in parsed.manifest['warnings']


@pytest.mark.parametrize(('name', 'raw'), [('x.pdf', b'%PDF'), ('x.doc', b'binary-Word'),
    ('x.png', b'png'), ('x.txt', b'\x00binary'), ('x.json', b'{broken'), ('x.docx', b'not-zip'),
    ('x.txt', b'x' * 120001), ('x.txt', b'x' * 1048577), ('x.txt', b'')],
    ids=['pdf','legacy-doc','image','binary','broken-json','broken-docx','text-limit','byte-limit','empty'])
def test_unsupported_or_invalid_files_fail_without_partial_import(name, raw):
    with pytest.raises(DocumentParseError): parse_document(name, raw)


def test_docx_rejects_unaccepted_changes_dtd_and_macro():
    for raw in (docx('<w:p><w:ins><w:r><w:t>未确认修改</w:t></w:r></w:ins></w:p>'),
                docx('<w:p/>', **{'word/vbaProject.bin':'macro'})):
        with pytest.raises(DocumentParseError): parse_document('x.docx', raw)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w') as archive:
        archive.writestr('[Content_Types].xml', '<Types/>')
        archive.writestr('word/document.xml', '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "x">]><x>&e;</x>')
    with pytest.raises(DocumentParseError, match='实体'): parse_document('x.docx', raw.getvalue())


def test_file_api_preserves_original_and_invalidates_it_after_revision_or_retraction(tmp_path):
    app = _create_tenant_app(config_at(tmp_path))
    try:
        client = TestClient(app)
        raw = '工程文档：检索前核对来源。\n'.encode('utf-8-sig')
        response = client.post('/api/library/import', params={'filename':'原始资料.txt','authorship':'external'},
            content=raw, headers={'Content-Type':'application/octet-stream'})
        assert response.status_code == 201, response.text
        source = response.json(); sid, rid = source['id'], source['revision']['id']
        assert source['file']['file_bytes'] == len(raw)
        assert client.get('/api/library/sources/'+sid+'/file').content == raw
        hit = client.post('/api/library/search', json={'query':'核对来源'}).json()[0]
        assert hit['file_location']['locations'] == ['line:1']
        assert app.state.store.sessions() == [] and app.state.store.memories() == []
        changed = client.post('/api/library/sources/'+sid+'/revisions', json={
            'expected_revision_id':rid,'title':'编辑文本版本','content':'工程修订内容'}).json()
        assert changed['file'] is None  # Edited text cannot claim the old original file SHA.
        assert client.get('/api/library/sources/'+sid+'/file').status_code == 404
        assert client.get('/api/library/sources/'+sid+'/file', params={'revision_id':rid}).content == raw
        client.post('/api/library/sources/'+sid+'/retract')
        assert client.get('/api/library/sources/'+sid+'/file', params={'revision_id':rid}).status_code == 404
    finally: app.state.store.close()


def test_binary_import_rejects_fake_authority_and_rolls_back_invalid_file(tmp_path):
    app = _create_tenant_app(config_at(tmp_path))
    try:
        client = TestClient(app)
        bad = client.post('/api/memory-kernel/import', params={'filename':'bad.docx'}, content=b'bad')
        assert bad.status_code == 422 and app.state.service.library.list() == []
        # Unknown query parameters cannot choose caller identity.
        ok = client.post('/api/memory-kernel/import', params={'filename':'x.txt','user_id':'other'}, content=b'Engineering source')
        assert ok.status_code == 422
        ok = client.post('/api/memory-kernel/import', params={'filename':'x.txt'}, content=b'Engineering source')
        assert ok.status_code == 201
        assert len(app.state.service.library.list()) == 1
        assert app.state.store.db.execute('SELECT count(*) FROM library_files').fetchone()[0] == 1
        schema = _create_tenant_app(config_at(tmp_path/'schema'),schema_only=True).openapi()
        assert '/api/library/import' in schema['paths']
        assert schema['paths']['/api/library/import']['post']['requestBody']['content']['application/octet-stream']['schema']['format']=='binary'
    finally: app.state.store.close()
