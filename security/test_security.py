"""
test_security.py -- verifies the demonstration table in README.md is
actually true of the code, rather than just asserted in prose.

Run with:  python3 -m pytest security/test_security.py -v
       or:  python3 -m security.test_security      (runs a plain summary)
"""

from __future__ import annotations

from .attacks import FalseDataInjectionAttack, SensorSpoofingAttack
from .auth import AuthError, OperatorAuth
from .channel import ChannelRejected, SensorChannel
from .crypto import DecryptionError, SensorCrypto


def _fresh_channel(encryption_enabled: bool) -> SensorChannel:
    return SensorChannel(crypto=SensorCrypto(), encryption_enabled=encryption_enabled)


# ---------------------------------------------------------------------------
# crypto.py
# ---------------------------------------------------------------------------

def test_crypto_round_trip():
    sc = SensorCrypto()
    msg = sc.encrypt_json({"road": "North", "vehicles": 41})
    assert sc.decrypt_json(msg) == {"road": "North", "vehicles": 41}


def test_crypto_rejects_tampered_ciphertext():
    sc = SensorCrypto()
    msg = sc.encrypt_json({"road": "North", "vehicles": 41})
    tampered_ct = bytes([msg.ciphertext[0] ^ 0xFF]) + msg.ciphertext[1:]
    tampered = type(msg)(nonce=msg.nonce, ciphertext=tampered_ct)
    try:
        sc.decrypt_json(tampered)
        assert False, "tampered ciphertext must not decrypt"
    except DecryptionError:
        pass


def test_crypto_rejects_wrong_key():
    sc1, sc2 = SensorCrypto(), SensorCrypto()
    msg = sc1.encrypt_json({"road": "North", "vehicles": 41})
    try:
        sc2.decrypt_json(msg)
        assert False, "decrypting under the wrong key must fail"
    except DecryptionError:
        pass


def test_log_line_round_trip_at_rest():
    sc = SensorCrypto()
    line = "2026-08-18T09:00:00,North,41"
    enc = sc.encrypt_log_line(line)
    assert enc != line
    assert sc.decrypt_log_line(enc) == line


# ---------------------------------------------------------------------------
# auth.py
# ---------------------------------------------------------------------------

def test_auth_correct_and_wrong_password():
    auth = OperatorAuth()
    auth.register("operator1", "correct horse battery staple")
    assert auth.verify("operator1", "correct horse battery staple") is True
    assert auth.verify("operator1", "wrong password") is False
    assert auth.verify("nobody", "anything") is False


def test_auth_duplicate_registration_rejected():
    auth = OperatorAuth()
    auth.register("operator1", "password one")
    try:
        auth.register("operator1", "password two")
        assert False, "duplicate registration without overwrite=True must raise"
    except AuthError:
        pass


# ---------------------------------------------------------------------------
# channel.py + attacks.py -- the table in README.md, measured
# ---------------------------------------------------------------------------

def test_false_data_injection_succeeds_when_encryption_off():
    channel = _fresh_channel(encryption_enabled=False)
    channel.add_interceptor(FalseDataInjectionAttack(fake_vehicle_count=999))
    event = channel.send_and_receive({"road": "North", "vehicles": 41})
    assert event.accepted is True
    assert event.plaintext["vehicles"] == 999, "attack should have overwritten the real count"


def test_false_data_injection_rejected_when_encryption_on():
    channel = _fresh_channel(encryption_enabled=True)
    channel.add_interceptor(FalseDataInjectionAttack(fake_vehicle_count=999))
    try:
        channel.send_and_receive({"road": "North", "vehicles": 41})
        assert False, "corrupted ciphertext must be rejected, not silently accepted"
    except ChannelRejected:
        pass


def test_sensor_spoofing_succeeds_when_encryption_off():
    channel = _fresh_channel(encryption_enabled=False)
    channel.add_interceptor(SensorSpoofingAttack(spoofed_road="North", spoofed_vehicle_count=250))
    event = channel.send_and_receive({"road": "South", "vehicles": 12})
    assert event.accepted is True
    assert event.plaintext["road"] == "North"
    assert event.plaintext["vehicles"] == 250
    assert event.plaintext.get("spoofed") is True


def test_sensor_spoofing_rejected_when_encryption_on():
    channel = _fresh_channel(encryption_enabled=True)
    channel.add_interceptor(SensorSpoofingAttack(spoofed_road="North", spoofed_vehicle_count=250))
    try:
        channel.send_and_receive({"road": "South", "vehicles": 12})
        assert False, "forged plaintext must fail AEAD verification, not be accepted"
    except ChannelRejected:
        pass


def test_toggle_same_attack_same_channel_object():
    """The specific claim in the handoff: the SAME interceptor and channel
    demonstrate both outcomes with only encryption_enabled flipped, no
    restart, no new attack object."""
    channel = _fresh_channel(encryption_enabled=False)
    attack = FalseDataInjectionAttack(fake_vehicle_count=777)
    channel.add_interceptor(attack)

    off_event = channel.send_and_receive({"road": "East", "vehicles": 10})
    assert off_event.accepted and off_event.plaintext["vehicles"] == 777

    channel.encryption_enabled = True
    try:
        channel.send_and_receive({"road": "East", "vehicles": 10})
        assert False, "same attack must be rejected once encryption is toggled on"
    except ChannelRejected:
        pass


def test_clear_interceptors_restores_clean_traffic():
    channel = _fresh_channel(encryption_enabled=True)
    channel.add_interceptor(FalseDataInjectionAttack(fake_vehicle_count=999))
    channel.clear_interceptors()
    event = channel.send_and_receive({"road": "West", "vehicles": 7})
    assert event.accepted is True
    assert event.plaintext["vehicles"] == 7


ALL_TESTS = [
    test_crypto_round_trip,
    test_crypto_rejects_tampered_ciphertext,
    test_crypto_rejects_wrong_key,
    test_log_line_round_trip_at_rest,
    test_auth_correct_and_wrong_password,
    test_auth_duplicate_registration_rejected,
    test_false_data_injection_succeeds_when_encryption_off,
    test_false_data_injection_rejected_when_encryption_on,
    test_sensor_spoofing_succeeds_when_encryption_off,
    test_sensor_spoofing_rejected_when_encryption_on,
    test_toggle_same_attack_same_channel_object,
    test_clear_interceptors_restores_clean_traffic,
]


if __name__ == "__main__":
    passed, failed = 0, 0
    for t in ALL_TESTS:
        try:
            t()
            print(f"PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed out of {len(ALL_TESTS)}")
