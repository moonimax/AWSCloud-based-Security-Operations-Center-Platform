"""Portable Apache APR1, compatible with the prior OpenSSL output."""
import hashlib
import secrets

def apr1(password, salt=None):
    salt=(salt or secrets.token_hex(4)).encode('ascii')[:8]
    key=password.encode('utf-8');magic=b'$apr1$'
    alternate=hashlib.md5(key+salt+key).digest()
    ctx=key+magic+salt+alternate*(len(key)//16)+alternate[:len(key)%16]
    size=len(key)
    while size:
        ctx+=b'\0' if size & 1 else key[:1]
        size>>=1
    digest=hashlib.md5(ctx).digest()
    for index in range(1000):
        value=key if index & 1 else digest
        if index % 3:value+=salt
        if index % 7:value+=key
        value+=digest if index & 1 else key
        digest=hashlib.md5(value).digest()
    alphabet='./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    result=''
    for a,b,c in [(0,6,12),(1,7,13),(2,8,14),(3,9,15),(4,10,5)]:
        value=(digest[a]<<16)|(digest[b]<<8)|digest[c]
        for _ in range(4):result+=alphabet[value & 63];value>>=6
    value=digest[11]
    for _ in range(2):result+=alphabet[value & 63];value>>=6
    return '$apr1$'+salt.decode()+'$'+result
