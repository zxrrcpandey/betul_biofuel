"""Web Push correctness gate — RFC 8291 §5 known-answer + VAPID + SSRF.

Run standalone (no site needed for the crypto/SSRF halves):
    cd /home/frappe/frappe-bench
    env/bin/python apps/trustbit_ethanol/trustbit_ethanol/ts_gate_entry/tests/test_push_vectors.py

The first test is the one that matters: RFC 8291 §5 publishes a complete
worked example — receiver keys, sender keys, salt, plaintext and the exact
encrypted body. If our framing or HKDF info-strings are off by a single byte,
the output differs and the test fails. That is a stronger guarantee than
trusting a third-party library, and it is why this module is hand-written.

Exit code: 0 = all passed, 1 = failures (BLOCK the build).
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402

from trustbit_ethanol.ts_gate_entry.ts_webpush import (  # noqa: E402
    _der_to_raw_signature,
    b64url_decode,
    b64url_encode,
    build_vapid_header,
    encrypt_payload,
    generate_vapid_keys,
    validate_endpoint,
)

results = {"passed": 0, "failed": 0, "errors": []}


def check(name, fn):
    try:
        fn()
        results["passed"] += 1
        sys.stdout.write(".")
    except Exception as e:  # noqa: BLE001
        results["failed"] += 1
        results["errors"].append({"test": name, "error": "{0}: {1}".format(type(e).__name__, e)})
        sys.stdout.write("F")
    sys.stdout.flush()


# ── RFC 8291 §5 — the published worked example ────────────────────────────────
V_PLAINTEXT = b"When I grow up, I want to be a watermelon"
V_UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
V_UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
V_AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
V_AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
V_SALT = "DGv6ra1nlYgDCS1FRnbzlw"
V_EXPECTED_BODY = (
    "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLoc"
    "InmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLV"
    "WGNWQexSgSxsj_Qulcy4a-fN"
)


def _as_private_key():
    return ec.derive_private_key(
        int.from_bytes(b64url_decode(V_AS_PRIVATE), "big"), ec.SECP256R1())


def test_rfc8291_known_answer():
    body = encrypt_payload(
        V_PLAINTEXT,
        b64url_decode(V_UA_PUBLIC),
        b64url_decode(V_AUTH),
        as_private=_as_private_key(),
        salt=b64url_decode(V_SALT),
    )
    got = b64url_encode(body)
    assert got == V_EXPECTED_BODY, (
        "RFC 8291 §5 mismatch.\n expected {0}\n      got {1}".format(V_EXPECTED_BODY, got))


def test_header_framing():
    body = encrypt_payload(V_PLAINTEXT, b64url_decode(V_UA_PUBLIC), b64url_decode(V_AUTH),
                           as_private=_as_private_key(), salt=b64url_decode(V_SALT))
    assert body[:16] == b64url_decode(V_SALT), "salt must be the first 16 bytes"
    assert int.from_bytes(body[16:20], "big") == 4096, "record size must be uint32 BE 4096"
    assert body[20] == 65, "key id length byte must be 65"
    assert body[21] == 0x04, "sender key must be an uncompressed point (0x04)"


def test_roundtrip_decrypts():
    """Encrypt to a freshly generated subscription and decrypt it back — proves
    the CEK/nonce derivation, not just that we match one frozen vector."""
    import struct

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    ua_priv = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua_priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint)
    auth = os.urandom(16)
    plaintext = b'{"t":"Approval needed","d":"Purchase Order BBPL-PO-OT-2026-00595"}'

    body = encrypt_payload(plaintext, ua_pub, auth)

    salt, rs, idlen = body[:16], struct.unpack("!I", body[16:20])[0], body[20]
    as_pub = body[21:21 + idlen]
    ciphertext = body[21 + idlen:]
    assert rs == 4096

    shared = ua_priv.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), as_pub))
    ikm = HKDF(algorithm=hashes.SHA256(), length=32, salt=auth,
               info=b"WebPush: info\x00" + ua_pub + as_pub).derive(shared)
    cek = HKDF(algorithm=hashes.SHA256(), length=16, salt=salt,
               info=b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(algorithm=hashes.SHA256(), length=12, salt=salt,
                 info=b"Content-Encoding: nonce\x00").derive(ikm)

    decrypted = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert decrypted[-1:] == b"\x02", "last-record padding delimiter must be 0x02"
    assert decrypted[:-1] == plaintext, "roundtrip payload mismatch"


def test_unicode_payload():
    """₹ and Devanagari must survive — subjects carry both."""
    ua_priv = ec.generate_private_key(ec.SECP256R1())
    from cryptography.hazmat.primitives import serialization
    ua_pub = ua_priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint)
    encrypt_payload("अनुमोदन ₹4.64 Cr".encode("utf-8"), ua_pub, os.urandom(16))


def test_oversize_payload_refused():
    from cryptography.hazmat.primitives import serialization
    ua_priv = ec.generate_private_key(ec.SECP256R1())
    ua_pub = ua_priv.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint)
    try:
        encrypt_payload(b"x" * 5000, ua_pub, os.urandom(16))
    except ValueError:
        return
    raise AssertionError("oversize payload must raise")


def test_malformed_subscription_keys_rejected():
    bad = [
        (b"\x04" + b"\x00" * 10, os.urandom(16)),   # p256dh too short
        (os.urandom(65), os.urandom(16)),            # not a valid curve point
    ]
    for pub, auth in bad:
        try:
            encrypt_payload(b"x", pub, auth)
        except Exception:
            continue
        raise AssertionError("malformed p256dh must be rejected")


# ── RFC 8292 — VAPID ──────────────────────────────────────────────────────────

def test_vapid_signature_padding():
    """r and s must ALWAYS be 32 bytes each. A short half appears ~1/256 of the
    time; concatenating without left-padding mints an invalid JWT that fails
    only occasionally. 400 iterations makes a short half near-certain."""
    from cryptography.hazmat.primitives import hashes
    key = ec.generate_private_key(ec.SECP256R1())
    for i in range(400):
        der = key.sign(b"msg-%d" % i, ec.ECDSA(hashes.SHA256()))
        raw = _der_to_raw_signature(der)
        assert len(raw) == 64, "signature must be exactly 64 bytes, got %d" % len(raw)


def test_vapid_header_shape_and_verifies():
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

    keys = generate_vapid_keys()
    endpoint = "https://fcm.googleapis.com/fcm/send/abc123"
    header = build_vapid_header(endpoint, keys["ts_vapid_private_key"],
                                keys["ts_vapid_public_key"], "mailto:it@betulbiofuel.com")
    assert header.startswith("vapid t="), "scheme must be `vapid t=`"
    assert ", k=" in header

    import json as _json
    jwt = header.split("vapid t=")[1].split(", k=")[0]
    h_b64, c_b64, s_b64 = jwt.split(".")
    claims = _json.loads(b64url_decode(c_b64))
    # aud must be scheme+host with NO path — Apple is strict about this.
    assert claims["aud"] == "https://fcm.googleapis.com", claims["aud"]
    assert claims["sub"].startswith("mailto:")
    assert 0 < claims["exp"] - int(__import__("time").time()) <= 12 * 3600

    # The signature must verify against the advertised public key.
    raw = b64url_decode(s_b64)
    pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), b64url_decode(keys["ts_vapid_public_key"]))
    pub.verify(
        encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
        (h_b64 + "." + c_b64).encode("ascii"),
        ec.ECDSA(hashes.SHA256()),
    )


def test_vapid_keys_are_wellformed():
    keys = generate_vapid_keys()
    assert len(b64url_decode(keys["ts_vapid_private_key"])) == 32
    pub = b64url_decode(keys["ts_vapid_public_key"])
    assert len(pub) == 65 and pub[0] == 0x04
    assert "=" not in keys["ts_vapid_public_key"], "base64url must be unpadded"


# ── SSRF ──────────────────────────────────────────────────────────────────────

_REJECT = [
    "http://fcm.googleapis.com/x",                     # not https
    "https://evilfcm.googleapis.com/x",                # endswith() bypass
    "https://fcm.googleapis.com.evil.tld/x",           # suffix-domain bypass
    "https://fcm.googleapis.com:8080/x",               # non-443 port
    "https://fcm.googleapis.com:22/x",
    "https://user:pass@fcm.googleapis.com/x",          # userinfo
    "https://169.254.169.254/latest/meta-data/",       # cloud metadata
    "https://127.0.0.1/x",
    "https://10.0.0.5/x",
    "https://[::1]/x",
    "https://localhost/x",
    "file:///etc/passwd",
    "gopher://fcm.googleapis.com/x",
    "//fcm.googleapis.com/x",                          # scheme-relative
    "https://fcm.googleapis.com./x",                   # trailing dot
    "https://fcm.googleapis.com/x\nX-Injected: 1",     # header injection
    "https://fcm.googleapis.com/x\r\n",
    "https://fcm.googleapis.com/x#@evil.com",
    "https://fcm.googleapis.com/" + "a" * 600,         # over length cap
    "",
    None,
]

_ACCEPT = [
    "https://fcm.googleapis.com/fcm/send/abc123",
    "https://web.push.apple.com/QAlongtokenvalue",
    "https://updates.push.services.mozilla.com/wpush/v2/gAAA",
    "https://fcm.googleapis.com:443/fcm/send/abc",     # explicit 443 is fine
]


def test_ssrf_rejects():
    for ep in _REJECT:
        try:
            validate_endpoint(ep)
        except Exception:
            continue
        raise AssertionError("SSRF: endpoint should have been rejected: %r" % (ep,))


def test_ssrf_accepts_real_services():
    for ep in _ACCEPT:
        validate_endpoint(ep)


def main():
    print("\n=== WEB PUSH CORRECTNESS GATE (RFC 8291 / RFC 8292 / SSRF) ===")
    print("\n[1/3] RFC 8291 encryption")
    check("rfc8291_known_answer", test_rfc8291_known_answer)
    check("header_framing", test_header_framing)
    check("roundtrip_decrypts", test_roundtrip_decrypts)
    check("unicode_payload", test_unicode_payload)
    check("oversize_payload_refused", test_oversize_payload_refused)
    check("malformed_keys_rejected", test_malformed_subscription_keys_rejected)

    print("\n[2/3] RFC 8292 VAPID")
    check("vapid_signature_padding", test_vapid_signature_padding)
    check("vapid_header_shape_and_verifies", test_vapid_header_shape_and_verifies)
    check("vapid_keys_wellformed", test_vapid_keys_are_wellformed)

    print("\n[3/3] SSRF endpoint validation ({0} reject + {1} accept)".format(
        len(_REJECT), len(_ACCEPT)))
    check("ssrf_rejects", test_ssrf_rejects)
    check("ssrf_accepts", test_ssrf_accepts_real_services)

    print("\n\n" + "=" * 60)
    print("PUSH VECTOR RESULTS")
    print("Total: {0} | Passed: {1} | Failed: {2}".format(
        results["passed"] + results["failed"], results["passed"], results["failed"]))
    if results["errors"]:
        print("\nFAILURES:")
        for e in results["errors"]:
            print("  ✗ {0}\n    {1}".format(e["test"], e["error"]))
    print("=" * 60)
    return 1 if results["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
