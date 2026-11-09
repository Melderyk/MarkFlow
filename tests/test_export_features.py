import json
from pathlib import Path
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

import pytest
from PIL import Image

from markflow.core import Converter,Options,Cancelled
from markflow.image_host import HostConfig,ImageUploader,UploadError,upload_images,protect_token,unprotect_token,PRESETS
from markflow.markdown_tools import document_index,preview_sections,change_language,export_chapters
from tests.samples import create_samples


@pytest.fixture
def server():
    state={'requests':[],'status':200,'body':{'data':{'url':'https://images.example.test/photo.png'}}}
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body=self.rfile.read(int(self.headers['Content-Length']))
            state['requests'].append((dict(self.headers),body))
            status=state['status']
            if state.get('once'):
                state['status']=200
                state['once']=False
            self.send_response(status)
            if status==302: self.send_header('Location','http://127.0.0.1:9/steal')
            self.send_header('Content-Type','application/json')
            self.end_headers()
            self.wfile.write(json.dumps(state['body']).encode())
        def log_message(self,*args): pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True)
    thread.start()
    state['endpoint']=f'http://127.0.0.1:{http.server_port}/upload'
    yield state
    http.shutdown()
    http.server_close()
    thread.join()


@pytest.fixture
def picture(tmp_path):
    path=tmp_path/'图片.png'
    Image.new('RGB',(24,24),'blue').save(path)
    return path


def config(server,**kwargs):
    return HostConfig(provider='自定义 HTTP 接口',endpoint=server['endpoint'],url_path='data.url',**kwargs)


def test_multipart_auth_dedup_and_no_credential_record(server,picture,tmp_path):
    cfg=config(server,token='TOP-SECRET',auth_prefix='Bearer ',extra_fields={'strategy_id':'2'})
    cache=tmp_path/'cache.json'
    uploader=ImageUploader(cfg,cache)
    assert uploader.upload(picture)==('https://images.example.test/photo.png',False)
    assert ImageUploader(cfg,cache).upload(picture)[1]
    assert len(server['requests'])==1
    headers,body=server['requests'][0]
    assert headers['Authorization']=='Bearer TOP-SECRET'
    assert b'name="file"' in body and b'image/png' in body and b'name="strategy_id"' in body
    assert picture.read_bytes() in body
    assert 'TOP-SECRET' not in cache.read_text() and 'TOP-SECRET' not in repr(cfg)


def test_picgo_json_upload(server,picture):
    server['body']={'success':True,'result':['https://example.test/a.png']}
    cfg=HostConfig(endpoint=server['endpoint'])
    assert ImageUploader(cfg).upload(picture)[0]=='https://example.test/a.png'
    headers,body=server['requests'][0]
    assert headers['Content-Type']=='application/json'
    assert json.loads(body)['list']==[str(picture.resolve())]


@pytest.mark.parametrize('provider,response,field,header',[
    ('Lsky Pro / 兰空',{'status':True,'data':{'links':{'url':'https://example.test/lsky.png'}}},'file','Bearer PRIVATE'),
    ('EasyImages / 简单图床',{'result':'success','code':200,'url':'https://example.test/easy.png'},'image',None),
    ('S.EE',{'code':200,'data':{'url':'https://example.test/see.png'}},'smfile','PRIVATE'),
    ('SM.MS（兼容接口）',{'success':True,'data':{'url':'https://example.test/smms.png'}},'smfile','PRIVATE'),
])
def test_preset_multipart_contracts(server,picture,provider,response,field,header):
    values=dict(PRESETS[provider])
    values['endpoint']=server['endpoint']
    server['body']=response
    cfg=HostConfig(provider=provider,token='PRIVATE',**values)
    assert ImageUploader(cfg).upload(picture)[0].startswith('https://example.test/')
    headers,body=server['requests'][0]
    assert f'name="{field}"'.encode() in body
    assert headers.get('Authorization')==header
    if provider.startswith('EasyImages'):
        assert b'name="token"' in body and b'PRIVATE' in body


def test_repeated_failures_keep_remaining_images_local(server,picture,tmp_path):
    server['status']=401
    images=[]
    for i in range(8):
        path=tmp_path/f'{i}.png'
        Image.new('RGB',(10,10),(i,0,0)).save(path)
        images.append(f'![图]({i}.png)')
    text='\n'.join(images)
    updated,mapping,warnings=upload_images(text,tmp_path,ImageUploader(config(server)))
    assert updated==text and not mapping
    assert len(server['requests'])==3 and len(warnings)==4


def test_retry_transient_but_not_auth_errors(server,picture):
    server['status']=503
    server['once']=True
    assert ImageUploader(config(server)).upload(picture)[0].startswith('https:')
    assert len(server['requests'])==2
    server['requests'].clear()
    server['status']=401
    with pytest.raises(UploadError,match='401'):
        ImageUploader(config(server,token='PRIVATE')).upload(picture)
    assert len(server['requests'])==1


@pytest.mark.parametrize('body',[{'success':False,'message':'SECRET-DATA'}, {'data':{'url':'javascript:bad'}}, {'data':{}}])
def test_invalid_responses_never_expose_server_content(server,picture,body):
    server['body']=body
    with pytest.raises(UploadError) as error:
        ImageUploader(config(server)).upload(picture)
    assert 'SECRET-DATA' not in str(error.value)


def test_redirect_does_not_forward_auth(server,picture):
    server['status']=302
    with pytest.raises(UploadError,match='302'):
        ImageUploader(config(server,token='PRIVATE')).upload(picture)
    assert len(server['requests'])==1


def test_upload_rewrites_only_actual_images_and_keeps_failed_links(server,picture,tmp_path):
    text=f'![图]({picture.name})\n\n```md\n![example]({picture.name})\n```\n\n![missing](missing.png)\n'
    updated,mapping,warnings=upload_images(text,tmp_path,ImageUploader(config(server)))
    assert '![图](https://images.example.test/photo.png)' in updated
    assert f'![example]({picture.name})' in updated
    assert '![missing](missing.png)' in updated
    assert len(mapping)==1 and len(warnings)==1 and picture.exists()


def test_host_conversion_preserves_backup_and_records(server,tmp_path):
    source=create_samples(tmp_path/'samples')[0]
    result=Converter().convert(source,Options(tmp_path/'out',image_mode='host',host=config(server,token='PRIVATE')))
    assert result.remote_images and 'https://images.example.test/photo.png' in result.markdown
    assert result.assets_dir and list(result.assets_dir.glob('*.png'))
    record=result.output.with_suffix('.images.json').read_text(encoding='utf8')
    assert 'PRIVATE' not in record
    assert result.output.read_text(encoding='utf8')==result.markdown


def test_failed_host_preserves_local_document(server,tmp_path):
    server['status']=401
    source=create_samples(tmp_path/'samples')[0]
    result=Converter().convert(source,Options(tmp_path/'out',image_mode='host',host=config(server)))
    assert result.output.is_file() and result.assets_dir.exists()
    assert result.warnings and not result.remote_images
    assert '.assets/' in unquote(result.markdown)


def test_cancellation_before_upload_does_not_send(server,picture):
    event=threading.Event()
    event.set()
    with pytest.raises(Cancelled): ImageUploader(config(server)).upload(picture,event)
    assert not server['requests']


@pytest.mark.parametrize('endpoint',['ftp://example.test/x','http://example.test/upload','https://user:pass@example.test/upload'])
def test_invalid_host_endpoint(endpoint):
    with pytest.raises(ValueError): HostConfig(endpoint=endpoint).validate()


def test_token_encrypted_for_current_windows_user():
    import os
    if os.name!='nt': pytest.skip('Windows DPAPI')
    encrypted=protect_token('PRIVATE-密钥')
    assert 'PRIVATE' not in encrypted
    assert unprotect_token(encrypted)=='PRIVATE-密钥'


def test_fenced_headings_diagnostics_and_manual_language():
    text='# Main\n\n```text\n#include <stdio.h>\n# fake heading\n```\n\n### Jump\n![missing](no.png)\n~~~c\nint x;\n~~~\n'
    index=document_index(text,Path.cwd(),['第 2 页 OCR 代码需核对'])
    assert [h.title for h in index.headings]==['Main','Jump']
    assert len(index.codes)==2
    assert any('语言待确认' in i.message for i in index.issues)
    assert any(i.page==2 for i in index.issues)
    changed=change_language(text,index.codes[0],'c')
    assert changed.replace('```c','```text',1)==text
    assert document_index('```c\nint x;').issues[0].message=='代码围栏未闭合'


def test_preview_sections_preserve_fences_and_all_text():
    text=''.join(f'# Chapter {i}\n\n```c\n'+('int x;\n'*40)+'```\n\n' for i in range(30))
    sections=preview_sections(text,target=1200)
    assert len(sections)>1
    assert ''.join(s[2] for s in sections)==text
    assert all(not any('未闭合' in i.message for i in document_index(s[2]).issues) for s in sections)


def test_large_table_preview_pages_keep_headers_and_every_source_row():
    header='| Name | Value |\n| :--- | ---: |\n'
    rows=[f'| row-{i} | {i} |\n' for i in range(12000)]
    text='# Data\n\n'+header+''.join(rows)+'\n# End\n'
    sections=preview_sections(text)
    assert len(sections)>10
    source_lines=text.splitlines(keepends=True)
    restored=[]
    for start,end,content in sections:
        original=''.join(source_lines[start:end])
        assert content in (original,header+original)
        assert len(content)<14500
        restored.append(original)
        if original.startswith('| row-'):
            assert content.startswith(header)
    assert ''.join(restored)==text
    assert sum(s[2].count('| row-') for s in sections)==len(rows)


def test_table_like_code_remains_one_intact_preview_block():
    text='```text\n| Header | Value |\n| --- | --- |\n'+('| code | example |\n'*1000)+'```\n'
    assert preview_sections(text,target=1000)==[(0,len(text.splitlines()),text)]


def test_chapter_exports_have_valid_assets_links_and_all_blocks(tmp_path):
    assets=tmp_path/'book.assets'
    assets.mkdir()
    Image.new('RGB',(2,2)).save(assets/'p.png')
    text=''.join(f'# 第{i}章\n\n![图](book.assets/p.png)\n\n```c\nint value_{i};\n```\n\n'+('正文内容。\n\n'*3000) for i in range(3))
    destination=tmp_path/'book.md'
    destination.write_text(text,encoding='utf8')
    folder=export_chapters(text,destination)
    chapters=sorted(folder.glob('[0-9]*.md'))
    assert len(chapters)==3 and (folder/'目录.md').is_file()
    for i,path in enumerate(chapters):
        content=path.read_text(encoding='utf8')
        assert f'int value_{i};' in content
        assert (folder/'../book.assets/p.png').is_file()
        assert '![图](../book.assets/p.png)' in content
    assert destination.read_text(encoding='utf8')==text
