"""iap_service.decode_apple_jws 根证书锚定回归测试（B1）。

漏洞：x5c 证书链完全来自请求方，旧实现只验证链内自洽 + 根 subject 字符串
含 "Apple Root CA"——攻击者自签同名私有 CA 即可伪造 App Store 通知。
修复：根证书与 Apple 官方 Apple Root CA - G3 字节级钉扎。
"""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from backend.core.errors import AppException, ErrorCode
from backend.services.iap_service import (
    _APPLE_ROOT_CA_G3_B64,
    _b64url_decode,
    decode_apple_jws,
)

# Apple 官方 Apple Root CA - G3 的 SHA-256 指纹
# https://www.apple.com/certificateauthority/AppleRootCA-G3.cer
OFFICIAL_G3_SHA256 = "63343abfb89a6a03ebb57e9b3f5fa7be7c4f5c756f3017b3a8c488c3653e9179"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_cert(*, subject_cn: str, key, issuer_cn: str, issuer_key, is_ca: bool):
    """生成自签/他签测试证书。"""
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject_cn)])
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_cn)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(
            x509.BasicConstraints(ca=is_ca, path_length=None), critical=True
        )
    )
    return builder.sign(issuer_key, hashes.SHA256())


def _forge_jws(*, leaf_key, chain_ders: list[bytes], payload: dict) -> str:
    """用给定链与叶子私钥伪造/构造一个 ES256 JWS。"""
    header = {"alg": "ES256", "x5c": [base64.b64encode(d).decode() for d in chain_ders]}
    signing_input = f"{_b64url(json.dumps(header).encode())}.{_b64url(json.dumps(payload).encode())}"
    signature = leaf_key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256()))
    return f"{signing_input}.{_b64url(signature)}"


def _der(cert) -> bytes:
    return cert.public_bytes(serialization.Encoding.DER)


def test_embedded_root_matches_official_g3_fingerprint():
    """内嵌锚定根必须是 Apple 官方 Apple Root CA - G3（防内嵌值被篡改）。"""
    der = base64.b64decode(_APPLE_ROOT_CA_G3_B64)
    assert hashlib.sha256(der).hexdigest() == OFFICIAL_G3_SHA256


def test_forged_apple_root_ca_chain_rejected():
    """B1 核心回归：subject 为 "Apple Root CA" 的私有 CA 链必须被拒。"""
    fake_root_key = ec.generate_private_key(ec.SECP256R1())
    fake_leaf_key = ec.generate_private_key(ec.SECP256R1())
    fake_root = _make_cert(
        subject_cn="Apple Root CA", key=fake_root_key,
        issuer_cn="Apple Root CA", issuer_key=fake_root_key, is_ca=True,
    )
    fake_leaf = _make_cert(
        subject_cn="attacker-leaf", key=fake_leaf_key,
        issuer_cn="Apple Root CA", issuer_key=fake_root_key, is_ca=False,
    )
    token = _forge_jws(
        leaf_key=fake_leaf_key,
        chain_ders=[_der(fake_leaf), _der(fake_root)],
        payload={"notificationType": "DID_RENEW"},
    )
    with pytest.raises(AppException) as exc_info:
        decode_apple_jws(token)
    assert exc_info.value.code == ErrorCode.IAP_VERIFY_FAILED


def test_valid_chain_with_trusted_root_passes(monkeypatch):
    """链内自洽 + 根在可信锚集合内 → 验签通过并返回 payload。"""
    root_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    root = _make_cert(
        subject_cn="Test Root", key=root_key,
        issuer_cn="Test Root", issuer_key=root_key, is_ca=True,
    )
    leaf = _make_cert(
        subject_cn="test-leaf", key=leaf_key,
        issuer_cn="Test Root", issuer_key=root_key, is_ca=False,
    )
    # 测试根注入可信锚集合（模拟 Apple 官方根位置）
    monkeypatch.setattr(
        "backend.services.iap_service._TRUSTED_APPLE_ROOTS_DER", {_der(root)}
    )
    payload = {"notificationType": "DID_RENEW", "subtype": "BILLING"}
    token = _forge_jws(
        leaf_key=leaf_key, chain_ders=[_der(leaf), _der(root)], payload=payload
    )
    assert decode_apple_jws(token) == payload


def test_tampered_payload_rejected(monkeypatch):
    """签名后篡改 payload 必须验签失败。"""
    root_key = ec.generate_private_key(ec.SECP256R1())
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    root = _make_cert(
        subject_cn="Test Root", key=root_key,
        issuer_cn="Test Root", issuer_key=root_key, is_ca=True,
    )
    leaf = _make_cert(
        subject_cn="test-leaf", key=leaf_key,
        issuer_cn="Test Root", issuer_key=root_key, is_ca=False,
    )
    monkeypatch.setattr(
        "backend.services.iap_service._TRUSTED_APPLE_ROOTS_DER", {_der(root)}
    )
    token = _forge_jws(
        leaf_key=leaf_key,
        chain_ders=[_der(leaf), _der(root)],
        payload={"notificationType": "DID_RENEW"},
    )
    head, body, sig = token.split(".")
    tampered = f"{head}.{_b64url(json.dumps({'notificationType': 'GRACE'}).encode())}.{sig}"
    # 确认 payload 确实被改动了（排除误改同值）
    assert _b64url_decode(body) != _b64url_decode(tampered.split(".")[1])
    with pytest.raises(AppException) as exc_info:
        decode_apple_jws(tampered)
    assert exc_info.value.code == ErrorCode.IAP_VERIFY_FAILED
