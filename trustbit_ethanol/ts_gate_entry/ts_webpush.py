# Copyright (c) 2026, Trustbit Software and contributors
# Web Push transport for the BBPL Approvals executive PWA (v2.32.0).
#
# RFC 8291 (Message Encryption for Web Push, aes128gcm) + RFC 8292 (VAPID).
#
# WHY THIS IS HAND-WRITTEN INSTEAD OF `pip install pywebpush`:
# pywebpush 2.x requires cryptography>=47 and drags in the whole aiohttp
# stack. Production runs cryptography 46.0.6 and demo 44.0.3, so installing it
# would upgrade the crypto library underneath a live ERP (ERPNext uses it for
# password decryption and TLS) and add 9 transitive packages — a bench-wide
# change for a notifications convenience. Verified on BOTH servers that every
# primitive these RFCs need already works with the installed cryptography.
#
# We implement NO cryptographic primitive here: key agreement, HKDF and AES-GCM
# are all library calls. What this module adds is the framing and the HKDF
# info-strings — and RFC 8291 §5 publishes a byte-exact known-answer vector for
# exactly that, which tests/test_push_vectors.py asserts against. That is a
# stronger correctness guarantee than "we imported a popular library".
#
# SSRF: we POST to a URL supplied by the client's browser. Every send is gated
# by validate_endpoint() — exact-set host allowlist, https only, no redirects.
#
# ⚠ Rejections raise frappe.ValidationError, NEVER a bare ValueError. A bare
# ValueError has no http_status_code, so Frappe answers 500 and
# log_error_snapshot() writes a traceback WITH EVERY FRAME'S LOCALS into
# tabError Log — which would put the push endpoint (a capability token), the
# p256dh and the auth secret in cleartext in front of any System Manager,
# voiding the permlevel-1 protection those fields are given. Frappe's redactor
# only masks names containing password/secret/token/key.

import base64
import json
import os
import struct
import time
from urllib.parse import urlparse

import frappe
import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# ── SSRF allowlist ────────────────────────────────────────────────────────────
# EXACT set membership, never endswith(): "evilfcm.googleapis.com".endswith(
# "fcm.googleapis.com") is True, which is how allowlists get bypassed.
_ALLOWED_PUSH_HOSTS = frozenset({
    "fcm.googleapis.com",                 # Chrome / Android  → the CEO
    "web.push.apple.com",                 # Safari / iOS 16.4+ → the MD
    "updates.push.services.mozilla.com",  # Firefox — desktop testing only
})
_MAX_ENDPOINT_LEN = 500

# Record size: one record, so it only has to exceed the payload. 4096 is the
# conventional value and keeps us inside every push service's body limit.
_RECORD_SIZE = 4096
# aes128gcm overhead per record: 16-byte tag + 1-byte padding delimiter.
MAX_PAYLOAD_BYTES = _RECORD_SIZE - 16 - 1 - 86  # header is 86 bytes


def b64url_encode(raw: bytes) -> str:
    """Unpadded base64url — RFC 8292 requires the padding stripped."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(data) -> bytes:
    """Accepts padded or unpadded base64url (browsers emit unpadded)."""
    if isinstance(data, bytes):
        data = data.decode("ascii")
    data = (data or "").strip()
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


# ── RFC 8291 — message encryption ─────────────────────────────────────────────

def _hkdf_extract_expand(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def encrypt_payload(payload: bytes, ua_public: bytes, ua_auth: bytes,
                    as_private=None, salt: bytes = None) -> bytes:
    """Return the aes128gcm body for `payload` addressed to one subscription.

    ua_public : the subscription's p256dh, raw uncompressed point (65 bytes)
    ua_auth   : the subscription's auth secret (16 bytes)
    as_private/salt: injectable ONLY so the RFC 8291 §5 known-answer test can
                     pin them; production always generates both randomly.
    """
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise ValueError("push payload too large: {0} > {1}".format(
            len(payload), MAX_PAYLOAD_BYTES))

    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    if as_private is None:
        as_private = ec.generate_private_key(ec.SECP256R1())
    if salt is None:
        salt = os.urandom(16)

    as_public = as_private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    shared = as_private.exchange(ec.ECDH(), ua_key)

    # RFC 8291 §3.4 — the auth secret is the HKDF *salt* for the IKM step, and
    # the info string binds both public keys so a captured record cannot be
    # replayed against a different subscription.
    key_info = b"WebPush: info\x00" + ua_public + as_public
    ikm = _hkdf_extract_expand(ua_auth, shared, key_info, 32)

    # RFC 8188 §2.2 — content encryption key and nonce, salted with the record
    # salt that also travels in the header.
    cek = _hkdf_extract_expand(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf_extract_expand(salt, ikm, b"Content-Encoding: nonce\x00", 12)

    # 0x02 = "last record" padding delimiter (0x01 would mean more follow).
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)

    # Header: salt(16) || record_size(uint32 BE) || idlen(1) || as_public(65)
    header = salt + struct.pack("!IB", _RECORD_SIZE, len(as_public)) + as_public
    return header + ciphertext


# ── RFC 8292 — VAPID ──────────────────────────────────────────────────────────

def _der_to_raw_signature(der: bytes) -> bytes:
    """ES256 wants r‖s as 64 raw bytes; `cryptography` emits DER.

    Both halves MUST be left-padded to 32 bytes. A short r or s occurs roughly
    1 in 256 signatures, so an implementation that just concatenates works
    ~99.6% of the time and then mints an invalid JWT — the classic flaky-push
    bug. tests/test_push_vectors.py hammers this deliberately.
    """
    r, s = decode_dss_signature(der)
    return r.to_bytes(32, "big") + s.to_bytes(32, "big")


def _load_vapid_private(private_b64url: str):
    raw = b64url_decode(private_b64url)
    if len(raw) != 32:
        # frappe.ValidationError, not ValueError, ON PURPOSE: a bare ValueError
        # has no http_status_code, so Frappe returns 500 and log_error_snapshot
        # writes a traceback WITH LOCALS into tabError Log — and the local here
        # is the VAPID PRIVATE KEY. Frappe's redactor only masks names matching
        # password/secret/token/key, which `private_b64url` does not.
        raise frappe.ValidationError("VAPID private key is malformed")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def build_vapid_header(endpoint: str, private_b64url: str, public_b64url: str,
                       subject: str, now: int = None) -> str:
    """Authorization header value: `vapid t=<jwt>, k=<public key>`."""
    parsed = urlparse(endpoint)
    # `aud` is scheme+host with NO path — Apple rejects the token otherwise.
    audience = "{0}://{1}".format(parsed.scheme, parsed.netloc)
    now = int(time.time()) if now is None else now

    header = {"typ": "JWT", "alg": "ES256"}
    claims = {"aud": audience, "exp": now + 12 * 3600, "sub": subject}
    signing_input = (
        b64url_encode(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64url_encode(json.dumps(claims, separators=(",", ":")).encode())
    ).encode("ascii")

    signature = _load_vapid_private(private_b64url).sign(
        signing_input, ec.ECDSA(hashes.SHA256()))
    jwt = signing_input.decode("ascii") + "." + b64url_encode(
        _der_to_raw_signature(signature))
    return "vapid t={0}, k={1}".format(jwt, public_b64url)


def generate_vapid_keys():
    """Console helper. Print once, paste into site_config.json, never store the
    private half in the database (a Password field would be an L309 landmine:
    a blank-field save silently wipes it and push dies with no error)."""
    private = ec.generate_private_key(ec.SECP256R1())
    private_raw = private.private_numbers().private_value.to_bytes(32, "big")
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )
    return {
        "ts_vapid_private_key": b64url_encode(private_raw),
        "ts_vapid_public_key": b64url_encode(public_raw),
    }


# ── SSRF-validated sender ─────────────────────────────────────────────────────

def validate_endpoint(endpoint: str) -> str:
    """Raise unless `endpoint` is a push service we are willing to POST to.

    Runs at BOTH boundaries (subscribe and send) because a stored row can
    predate a policy change.
    """
    if not endpoint or not isinstance(endpoint, str):
        raise frappe.ValidationError("endpoint required")
    if len(endpoint) > _MAX_ENDPOINT_LEN:
        raise frappe.ValidationError("endpoint too long")
    if any(c in endpoint for c in " \t\r\n\0\"'<>\\^`{|}") or "@" in endpoint or "#" in endpoint:
        raise frappe.ValidationError("endpoint contains illegal characters")

    p = urlparse(endpoint)
    if p.scheme != "https":
        raise frappe.ValidationError("endpoint must be https")
    if p.username or p.password:
        raise frappe.ValidationError("endpoint must not carry userinfo")
    if p.port not in (None, 443):
        raise frappe.ValidationError("endpoint must use port 443")
    if p.hostname not in _ALLOWED_PUSH_HOSTS:  # exact membership, never endswith
        raise frappe.ValidationError("endpoint host not allowed")
    return endpoint


def vapid_config():
    """(private, public, subject) from site_config — never the database."""
    return (
        frappe.conf.get("ts_vapid_private_key"),
        frappe.conf.get("ts_vapid_public_key"),
        frappe.conf.get("ts_vapid_subject") or "mailto:it@betulbiofuel.com",
    )


def push_capable():
    """O(1), no DB: are both VAPID halves configured on this site?"""
    private, public, _ = vapid_config()
    return bool(private and public)


def send(endpoint: str, p256dh: str, auth: str, payload: dict, ttl: int = 3600):
    """Deliver one push. Returns (status_code, reason).

    NEVER raises for transport problems — the caller (an enqueued job) maps the
    status to an outcome: 404/410 prune, 429 back off, 5xx retry, 401/403 revoke.
    """
    validate_endpoint(endpoint)
    private, public, subject = vapid_config()
    if not (private and public):
        # Same reasoning as _load_vapid_private: `private` is in scope here.
        raise frappe.ValidationError("VAPID keys are not configured on this site")

    body = encrypt_payload(
        json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        b64url_decode(p256dh),
        b64url_decode(auth),
    )
    headers = {
        "Authorization": build_vapid_header(endpoint, private, public, subject),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(int(ttl)),
        # Apple and FCM both honour this; "normal" wakes the device.
        "Urgency": "normal",
    }
    resp = requests.post(
        endpoint,
        data=body,
        headers=headers,
        timeout=(5, 10),
        # The single most important control after the allowlist: an allowlisted
        # host must never be able to bounce us at 169.254.169.254.
        allow_redirects=False,
    )
    return resp.status_code, (resp.text or "")[:200]
