import uuid
import hashlib
import base64
from cryptography.hazmat.primitives import serialization, asymmetric, hashes


def generate_key_pair(output='rsa'):
    private_key = asymmetric.rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    public_key = private_key.public_key()
    if output == 'rsa':
        return {'private': private_key, 'public': public_key}
    if output == 'bytes':
        return {'private': key_to_bytes(private_key, 'private'), 'public': key_to_bytes(public_key, 'public')}
    if output == 'str':
        return {'private': key_to_str(private_key, 'private'), 'public': key_to_str(public_key, 'public')}


def key_to_bytes(key, type='public'):
    if type == 'public':
        assert isinstance(key, asymmetric.rsa.RSAPublicKey)
        return key.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo)
    if type == 'private':
        assert isinstance(key, asymmetric.rsa.RSAPrivateKey)
        return key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption())


def key_to_str(key, type='public'):
    if type == 'public':
        assert isinstance(key, asymmetric.rsa.RSAPublicKey)
        return key.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    if type == 'private':
        assert isinstance(key, asymmetric.rsa.RSAPrivateKey)
        return key.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()).decode()


def str_to_key(key, type='public'):
    if type == 'public':
        assert isinstance(key, str)
        return serialization.load_pem_public_key(key.encode())
    if type == 'private':
        assert isinstance(key, str)
        return serialization.load_pem_private_key(key.encode(), password=None)


def bytes_to_key(key, type='public'):
    if type == 'public':
        assert isinstance(key, bytes)
        return serialization.load_pem_public_key(key)
    if type == 'private':
        assert isinstance(key, bytes)
        return serialization.load_pem_private_key(key, password=None)


def encrypt_with_public_key(data: bytes, pub: asymmetric.rsa.RSAPublicKey) -> bytes:
    """
    Encrypt data with public key by segmenting raw data
    """
    plain_segment_size = 128
    cipher = b''
    for i in range(0, len(data), plain_segment_size):
        cipher += pub.encrypt(data[i:i+plain_segment_size], asymmetric.padding.OAEP(mgf=asymmetric.padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return cipher


def decrypt_with_private_key(data: bytes, priv: asymmetric.rsa.RSAPrivateKey) -> bytes:
    """
    Decrypt data with private key by segmenting encrypted data
    """
    cipher_segment_size = priv.key_size // 8
    plain = b''
    for i in range(0, len(data), cipher_segment_size):
        plain += priv.decrypt(data[i:i+cipher_segment_size], asymmetric.padding.OAEP(mgf=asymmetric.padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    return plain


def encrypt_with_public_key_str(data: str, pub: str) -> str:
    """All-string version of encrypt_with_public_key, based on base64 encoding/decoding"""
    return base64.b64encode(encrypt_with_public_key(data.encode(), str_to_key(pub, 'public'))).decode()


def decrypt_with_private_key_str(data: str, priv: str) -> str:
    """All-string version of decrypt_with_private_key, based on base64 encoding/decoding"""
    return decrypt_with_private_key(base64.b64decode(data.encode()), str_to_key(priv, 'private')).decode()


def hash256(data) -> str:
    return hashlib.sha256(str(data).encode()).hexdigest()


def generate_dict_id():
    return str(uuid.uuid4()).split('-')[0]
