"""Explicit image uploads, local backups and credential-free upload records."""
from dataclasses import dataclass, asdict, field
from pathlib import Path
import base64
import hashlib
import ipaddress
import json
import mimetypes
import os
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit, unquote
import uuid

from .markdown_tools import rewrite_images


PRESETS={
    'PicGo（本机）': dict(endpoint='http://127.0.0.1:36677/upload',field_name='file',url_path='result.0',auth_prefix='Bearer '),
    'PicList（本机兼容）': dict(endpoint='http://127.0.0.1:36677/upload',field_name='file',url_path='result.0',auth_prefix='Bearer '),
    'Lsky Pro / 兰空': dict(endpoint='',field_name='file',url_path='data.links.url',auth_prefix='Bearer '),
    'EasyImages / 简单图床': dict(endpoint='',field_name='image',url_path='url',auth_prefix='',token_field='token'),
    'S.EE': dict(endpoint='https://s.ee/api/v1/file/upload',field_name='smfile',url_path='data.url',auth_prefix=''),
    'SM.MS（兼容接口）': dict(endpoint='https://sm.ms/api/v2/upload',field_name='smfile',url_path='data.url',auth_prefix=''),
    '自定义 HTTP 接口': dict(endpoint='',field_name='file',url_path='data.url',auth_prefix='Bearer '),
}


@dataclass
class HostConfig:
    provider: str = 'PicGo（本机）'
    endpoint: str = 'http://127.0.0.1:36677/upload'
    token: str = field(default='',repr=False)
    field_name: str = 'file'
    url_path: str = 'result.0'
    auth_header: str = 'Authorization'
    auth_prefix: str = 'Bearer '
    token_field: str = ''
    extra_fields: dict = field(default_factory=dict)
    timeout: int = 20
    retries: int = 2

    def validate(self):
        parsed=urlsplit(self.endpoint)
        if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('请输入完整的 HTTP(S) 上传地址，不要在网址中填写密钥。')
        local=parsed.hostname.lower()=='localhost'
        try:
            local=local or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            pass
        if parsed.scheme=='http' and not local:
            raise ValueError('远程图床请使用 HTTPS；本机 PicGo 可使用 HTTP。')
        if not self.field_name or not self.url_path:
            raise ValueError('请填写文件字段和返回链接路径。')
        if self.token and not self.token_field and not self.auth_header:
            raise ValueError('填写密钥后需要设置鉴权请求头或 token 表单字段。')
        for value in [self.field_name,self.auth_header,self.auth_prefix,self.token,self.token_field,*self.extra_fields.keys()]:
            if '\r' in value or '\n' in value or '"' in value:
                raise ValueError('接口字段或密钥含无效字符。')
        if not 1<=self.timeout<=120 or not 0<=self.retries<=3:
            raise ValueError('上传超时或重试次数超出范围。')

    def public(self):
        data=asdict(self)
        data.pop('token')
        return data


class UploadError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        # Do not forward credentials to a different upload destination.
        return None


def dig(value,path):
    for key in path.split('.'):
        value=value[int(key)] if isinstance(value,list) else value[key]
    return value


def valid_image_url(value):
    if not isinstance(value,str) or any(c in value for c in '\r\n<>'):
        raise UploadError('图床未返回有效的图片网址。')
    parsed=urlsplit(value)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password:
        raise UploadError('图床返回的网址需要以 http:// 或 https:// 开头。')
    return value.replace(' ','%20').replace('(','%28').replace(')','%29')


class ImageUploader:
    def __init__(self,config,cache_path=None):
        self.config=config
        config.validate()
        self.cache_path=Path(cache_path) if cache_path else None
        self.cache={}
        if self.cache_path:
            try:
                self.cache=json.loads(self.cache_path.read_text(encoding='utf8'))
                if not isinstance(self.cache,dict): self.cache={}
            except (OSError,ValueError):
                pass
        self.opener=urllib.request.build_opener(NoRedirect())

    def _request(self,path):
        cfg=self.config
        headers={'Accept':'application/json','User-Agent':'MarkFlow/0.2'}
        if cfg.token and not cfg.token_field:
            headers[cfg.auth_header]=cfg.auth_prefix+cfg.token
        if cfg.provider in {'PicGo（本机）','PicList（本机兼容）'}:
            headers['Content-Type']='application/json'
            data=json.dumps({'list':[str(path.resolve())]},ensure_ascii=False).encode('utf8')
        else:
            boundary='MarkFlow'+uuid.uuid4().hex
            chunks=[]
            fields=dict(cfg.extra_fields)
            if cfg.token_field and cfg.token:
                fields[cfg.token_field]=cfg.token
            for name,value in fields.items():
                chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode('utf8'))
            mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
            # ASCII filename avoids servers that mishandle multipart UTF-8 names.
            name=hashlib.sha256(path.read_bytes()).hexdigest()[:16]+path.suffix.lower()
            chunks.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{cfg.field_name}"; filename="{name}"\r\nContent-Type: {mime}\r\n\r\n'.encode())
            chunks.extend([path.read_bytes(),f'\r\n--{boundary}--\r\n'.encode()])
            data=b''.join(chunks)
            headers['Content-Type']='multipart/form-data; boundary='+boundary
        return urllib.request.Request(cfg.endpoint,data=data,headers=headers,method='POST')

    def upload(self,path,cancel=None,use_cache=True):
        from .core import Cancelled, atomic_write
        path=Path(path)
        identity=json.dumps(self.config.public(),sort_keys=True,ensure_ascii=False)+self.config.token
        key=hashlib.sha256(identity.encode()+path.read_bytes()).hexdigest()
        if use_cache and key in self.cache:
            try: return valid_image_url(self.cache[key]),True
            except UploadError: self.cache.pop(key,None)
        for attempt in range(self.config.retries+1):
            if cancel and cancel.is_set(): raise Cancelled('已停止图片上传；本地文件保留')
            try:
                with self.opener.open(self._request(path),timeout=self.config.timeout) as response:
                    raw=response.read(2*1024*1024+1)
                    if len(raw)>2*1024*1024: raise UploadError('图床返回数据过大。')
                    data=json.loads(raw)
                if not isinstance(data,dict): raise UploadError('图床返回数据格式无效。')
                if data.get('success') is False or data.get('status') is False:
                    # SM.MS may return the previous URL for a duplicate image.
                    if data.get('code')=='image_repeated' and data.get('images'):
                        url=valid_image_url(data['images'])
                    else:
                        raise UploadError('图床拒绝上传，请检查密钥、配额和接口配置。')
                else:
                    try: url=valid_image_url(dig(data,self.config.url_path))
                    except (KeyError,IndexError,TypeError,ValueError):
                        raise UploadError('返回结果没有指定的图片网址，请检查“返回链接路径”。') from None
                if cancel and cancel.is_set(): raise Cancelled('已停止图片上传；本地文件保留')
                self.cache[key]=url
                if self.cache_path:
                    try: atomic_write(self.cache_path,json.dumps(self.cache,ensure_ascii=False))
                    except OSError: pass
                return url,False
            except urllib.error.HTTPError as exc:
                retry=exc.code in {408,429,500,502,503,504}
                message=f'图床 HTTP {exc.code}；请检查服务状态、密钥与上传限制。'
            except (urllib.error.URLError,TimeoutError,OSError):
                retry=True
                message='无法连接图床或上传超时，请检查网络和接口地址。'
            except (json.JSONDecodeError,UnicodeDecodeError):
                raise UploadError('图床没有返回 JSON，请检查是否填写了上传 API 地址。') from None
            if not retry or attempt>=self.config.retries:
                raise UploadError(message) from None
            if cancel and cancel.wait(min(attempt+1,3)):
                raise Cancelled('已停止图片上传；本地文件保留')
        raise UploadError('上传失败')


def upload_images(markdown,base,uploader,progress=lambda _:None,cancel=None):
    from .core import Cancelled
    base=Path(base).resolve()
    mapping={}
    warnings=[]
    failed=set()
    failures=0
    def rewrite(match):
        nonlocal failures
        target=match[2].strip('<>')
        if urlsplit(target).scheme or target.startswith('#'):
            return match[0]
        if cancel and cancel.is_set(): raise Cancelled('已停止图片上传；本地文件保留')
        if target in failed: return match[0]
        if failures>=3: return match[0]
        if target not in mapping:
            path=(base/unquote(target)).resolve()
            if not path.is_relative_to(base) or not path.is_file():
                warnings.append('图片不存在或位于导出目录之外，保留本地链接。')
                failed.add(target)
                return match[0]
            progress(f'图床图片 {len(mapping)+len(failed)+1} · {path.name}')
            try:
                url,cached=uploader.upload(path,cancel)
                mapping[target]=url
            except UploadError as exc:
                failures+=1
                failed.add(target)
                warnings.append(f'图片 {path.name} 上传失败，保留本地链接：{exc}')
                if failures>=3:
                    warnings.append('图片连续上传失败，已停止后续上传；其余图片保留本地链接，可修正配置后重试。')
                return match[0]
            failures=0
        return f'![{match[1]}]({mapping[target]})'
    return rewrite_images(markdown,rewrite),mapping,warnings


def protect_token(token):
    """Windows DPAPI encrypts saved credentials for the current user."""
    return _dpapi(token.encode('utf8'),False) if token else ''


def unprotect_token(value):
    return _dpapi(value,True).decode('utf8') if value else ''


def _dpapi(value,decrypt):
    import ctypes
    from ctypes import wintypes
    if os.name!='nt': raise OSError('此平台不支持 Windows 密钥存储')
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    data=base64.b64decode(value) if decrypt else value
    buffer=ctypes.create_string_buffer(data)
    source=Blob(len(data),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    output=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True)
    function=crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    function.restype=wintypes.BOOL
    if not function(ctypes.byref(source),None,None,None,None,1,ctypes.byref(output)):
        raise OSError(ctypes.get_last_error(),'无法读取或保存本机加密密钥，请重新填写')
    try: result=ctypes.string_at(output.data,output.size)
    finally:
        free=ctypes.windll.kernel32.LocalFree
        free.argtypes=[ctypes.c_void_p]
        free.restype=ctypes.c_void_p
        free(output.data)
    return result if decrypt else base64.b64encode(result).decode('ascii')
