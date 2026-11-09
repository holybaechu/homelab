import hashlib
import json
import os
import re
import secrets
import sys


def verifier(password, salt):
    # Encoding from 3proxy 1.0.3 src/3proxy_crypt.c:mycrypt; not a custom KDF.
    digest = hashlib.blake2b(password.encode('ascii') + b'\0' + salt.encode('ascii'), digest_size=16).digest()
    alphabet = './0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'
    encoded = ''
    for indices in ((0, 6, 12), (1, 7, 13), (2, 8, 14), (3, 9, 15), (4, 10, 5), (11,)):
        value = int.from_bytes(bytes(digest[i] for i in indices), 'big')
        for _ in range(2 if len(indices) == 1 else 4):
            encoded += alphabet[value & 63]
            value >>= 6
    return 'CR:$3$' + salt + '$' + encoded


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError()
        result[key] = value
    return result


def initialize(source, target):
    os.umask(0o027)
    with open(source, 'rb') as stream:
        raw = stream.read(4097)
    if len(raw) > 4096:
        raise ValueError()
    data = json.loads(raw, object_pairs_hook=unique_object)
    if not isinstance(data, dict) or set(data) != {'host', 'port', 'protocol', 'username', 'password'}:
        raise ValueError()
    if data['host'] != 'proxy.hchu.me' or type(data['port']) is not int or data['port'] != 443 or data['protocol'] != 'https':
        raise ValueError()
    if not isinstance(data['username'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', data['username'], flags=re.ASCII):
        raise ValueError()
    # Shape is fail-closed input policy, NOT proof of CSPRNG origin/entropy.
    if not isinstance(data['password'], str) or not re.fullmatch(r'[A-Za-z0-9_-]{64}', data['password'], flags=re.ASCII):
        raise ValueError()
    line = '"' + data['username'] + ':' + verifier(data['password'], secrets.token_hex(16)) + '"\n'
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    try:
        with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
            stream.write(line)
            os.fchmod(stream.fileno(), 0o640)
    except Exception:
        os.unlink(target)
        raise


if __name__ == '__main__':
    try:
        initialize(*sys.argv[1:])
    except Exception:
        sys.exit(1)
