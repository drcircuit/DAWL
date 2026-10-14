"""Current-user Windows DPAPI secret storage. No plaintext keys in logs or files."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path


class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]


def transform(value, protect):
    if os.name!='nt':raise RuntimeError('Use OPENAI_API_KEY on non-Windows hosts')
    buffer=ctypes.create_string_buffer(value)
    source=Blob(len(value),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)));target=Blob()
    crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
    call=crypt.CryptProtectData if protect else crypt.CryptUnprotectData
    ok=call(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    if not ok:raise OSError(ctypes.get_last_error(),'Local credential protection failed')
    try:return ctypes.string_at(target.data,target.size)
    finally:kernel.LocalFree(target.data)


def save_key(path, value):
    if not value.startswith('sk-') or not 20<len(value)<512:raise ValueError('Invalid API key format')
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(transform(value.encode(),True))


def load_key(path):
    value=os.environ.get('OPENAI_API_KEY','').strip()
    if value:return value
    if os.name=='nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,'Environment') as k:
                value=winreg.QueryValueEx(k,'OPENAI_API_KEY')[0].strip()
                if value:return value
        except FileNotFoundError:pass
    path=Path(path)
    return transform(path.read_bytes(),False).decode() if path.exists() else ''
