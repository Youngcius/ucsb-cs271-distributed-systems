# # coding=utf-8
# from Crypto import Random
# from Crypto.PublicKey import RSA
 
 
# random_generator = Random.new().read
# rsa = RSA.generate(2048, random_generator)
# # 生成私钥
# private_key = rsa.exportKey()
# print(private_key.decode('utf-8'))
# print("-" * 30 + "分割线" + "-" * 30)
# # 生成公钥
# public_key = rsa.publickey().exportKey()
# print(public_key.decode('utf-8'))


import cryptography
from cryptography.hazmat.primitives import serialization, asymmetric

def generate_key_pair():
    private_key = asymmetric.rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )
    return private_key, private_key.public_key()


pri, pub = generate_key_pair()

print(pri)
print(type(pri))
print()
print(pub)
print(type(pub))
print(pri.private_bytes(encoding=serialization.Encoding.PEM, format=serialization.PrivateFormat.PKCS8, encryption_algorithm=serialization.NoEncryption()))
print(pub.public_bytes(encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo).decode())

