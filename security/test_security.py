"""
test_security.py -- verifies the demonstration table in README.md is
actually true of the code, rather than just asserted in prose.

Run with:  python3 -m pytest security/test_security.py -v
       or:  python3 -m security.test_security      (runs a plain summary)
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .attacks import FalseDataInjectionAttack, SensorSpoofingAttack
from .auth import AuthError, OperatorAuth
from .approval import ApprovalRecord, append_approval, load_latest_approval, sha256_file, verify_still_valid
from .channel import ChannelRejected, SensorChannel
from .crypto import DecryptionError, SensorCrypto
from . import detection


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


# ---------------------------------------------------------------------------
# attacks.py -- magnitude modes (1a) and key-compromise variant (1b)
# ---------------------------------------------------------------------------

def test_stealthy_mode_scales_true_value_not_a_constant():
    channel = _fresh_channel(encryption_enabled=False)
    channel.add_interceptor(FalseDataInjectionAttack(mode="stealthy", stealth_factor=1.3))
    event = channel.send_and_receive({"road": "North", "vehicles": 10})
    assert event.accepted is True
    assert event.plaintext["vehicles"] == 13, "10 * 1.3 = 13"


def test_stealthy_mode_has_minimum_effect_on_small_values():
    channel = _fresh_channel(encryption_enabled=False)
    channel.add_interceptor(FalseDataInjectionAttack(mode="stealthy", stealth_factor=1.3))
    event = channel.send_and_receive({"road": "North", "vehicles": 0})
    assert event.accepted is True
    assert event.plaintext["vehicles"] == 1, "0 * 1.3 rounds to 0; must still change by >=1"


def test_crude_mode_still_substitutes_fixed_constant():
    channel = _fresh_channel(encryption_enabled=False)
    channel.add_interceptor(FalseDataInjectionAttack(mode="crude", fake_vehicle_count=999))
    event = channel.send_and_receive({"road": "North", "vehicles": 10})
    assert event.plaintext["vehicles"] == 999


def test_stealthy_mode_still_rejected_without_key_when_encryption_on():
    channel = _fresh_channel(encryption_enabled=True)
    channel.add_interceptor(FalseDataInjectionAttack(mode="stealthy"))
    try:
        channel.send_and_receive({"road": "North", "vehicles": 10})
        assert False, "stealthy magnitude does not help without the key"
    except ChannelRejected:
        pass


def test_false_data_injection_with_stolen_key_passes_encryption():
    crypto = SensorCrypto()
    channel = SensorChannel(crypto=crypto, encryption_enabled=True)
    channel.add_interceptor(FalseDataInjectionAttack(mode="stealthy", crypto=crypto))
    event = channel.send_and_receive({"road": "North", "vehicles": 10})
    assert event.accepted is True, "attacker with the real key must pass AEAD"
    assert event.plaintext["vehicles"] == 13


def test_false_data_injection_with_wrong_key_still_rejected():
    real_crypto = SensorCrypto()
    attacker_crypto = SensorCrypto()  # different key - not actually stolen
    channel = SensorChannel(crypto=real_crypto, encryption_enabled=True)
    channel.add_interceptor(FalseDataInjectionAttack(mode="stealthy", crypto=attacker_crypto))
    try:
        channel.send_and_receive({"road": "North", "vehicles": 10})
        assert False, "the wrong key must not pass AEAD verification"
    except ChannelRejected:
        pass


def test_sensor_spoofing_with_stolen_key_passes_encryption():
    crypto = SensorCrypto()
    channel = SensorChannel(crypto=crypto, encryption_enabled=True)
    channel.add_interceptor(SensorSpoofingAttack(crypto=crypto))
    event = channel.send_and_receive({"road": "South", "vehicles": 4})
    assert event.accepted is True
    assert event.plaintext["road"] == "North"
    assert event.plaintext["vehicles"] == 250


# ---------------------------------------------------------------------------
# detection.py -- pure classification functions (1c)
# ---------------------------------------------------------------------------

# Mirrors traffic_sim.py's SIM_SATURATION_HEADWAY_S = MIN_GAP / (MAX_SPEED
# * 60) = 48 / (2.2 * 60). test_security.py stays import-free of
# traffic_sim.py (matching detection.py's own design), so this is an
# independently-computed constant, not an import - see Phase A's ADR-027
# for why compute_channel_signals takes this as a real physics parameter
# rather than a module-level literal in detection.py.
_SIM_SATURATION_HEADWAY_S = 48 / (2.2 * 60)  # 0.3636...


def _channel_signals(**kwargs):
    defaults = dict(
        accepted=True, encryption_enabled=True,
        reported_vehicles=5, true_vehicles=5, green_seconds_window=20.0,
        sim_saturation_headway_s=_SIM_SATURATION_HEADWAY_S,
    )
    defaults.update(kwargs)
    return detection.compute_channel_signals(**defaults)


def test_detection_s1_fires_only_when_rejected_and_encrypted():
    rejected_encrypted = _channel_signals(accepted=False, encryption_enabled=True)
    assert rejected_encrypted.s1_integrity_fail is True

    rejected_unencrypted = _channel_signals(accepted=False, encryption_enabled=False)
    assert rejected_unencrypted.s1_integrity_fail is False, \
        "S1 requires encryption to have been enabled - see detection.py docstring"


def test_detection_s2_implausible_uses_saturation_headway():
    # Physical bound = green_seconds / sim_saturation_headway_s (0.3636...,
    # NOT the HCM 1.9s - see Phase A / ADR-027). At green=20s, bound = 55.0
    # exactly (20 / (48/132) = 2640/48 = 55).
    just_over = _channel_signals(reported_vehicles=56, green_seconds_window=20.0)
    just_under = _channel_signals(reported_vehicles=55, green_seconds_window=20.0)
    assert just_over.s2_implausible is True
    assert just_under.s2_implausible is False, "55 is not > the bound of exactly 55.0"


def test_detection_s2_does_not_fire_below_min_green_guard():
    # Sub-second green window: bound < 1, so even a single genuine
    # discharge (carried over from a previous, misaligned green phase)
    # would trip S2 without the S2_MIN_GREEN_S guard. Regression test for
    # the Phase 1 false-positive fix.
    assert detection.S2_MIN_GREEN_S == 6.0
    below_guard = _channel_signals(reported_vehicles=1, true_vehicles=1,
                                    green_seconds_window=0.65)
    assert below_guard.s2_implausible is False, \
        "below S2_MIN_GREEN_S, S2 must not evaluate at all - even a genuine reading would trip it"

    # At the guard boundary (green == 6.0 exactly), S2 DOES evaluate:
    # bound = 6.0 / 0.3636... = 16.5, so 16 is plausible and 17 is not.
    at_guard_plausible = _channel_signals(reported_vehicles=16, true_vehicles=16,
                                           green_seconds_window=6.0)
    at_guard_implausible = _channel_signals(reported_vehicles=17, true_vehicles=17,
                                             green_seconds_window=6.0)
    assert at_guard_plausible.s2_implausible is False
    assert at_guard_implausible.s2_implausible is True, \
        "at the guard boundary S2 must still evaluate and can still fire"


def test_detection_s3_divergence_any_nonzero_difference():
    same = _channel_signals(reported_vehicles=5, true_vehicles=5)
    different = _channel_signals(reported_vehicles=6, true_vehicles=5)
    assert same.s3_divergence is False
    assert different.s3_divergence is True


def test_detection_s3_and_s2_do_not_fire_on_rejected_messages():
    rejected = _channel_signals(accepted=False, encryption_enabled=True,
                                 reported_vehicles=None, true_vehicles=5)
    assert rejected.s2_implausible is False
    assert rejected.s3_divergence is False


def test_detection_simultaneity_requires_three_or_more_arms():
    two_flagged = {
        "North": _channel_signals(reported_vehicles=999, green_seconds_window=20.0),
        "South": _channel_signals(reported_vehicles=999, green_seconds_window=20.0),
        "East": _channel_signals(reported_vehicles=5, true_vehicles=5),
        "West": _channel_signals(reported_vehicles=5, true_vehicles=5),
    }
    assert detection.simultaneity_flag(two_flagged) is False

    three_flagged = dict(two_flagged)
    three_flagged["East"] = _channel_signals(reported_vehicles=999, green_seconds_window=20.0)
    assert detection.simultaneity_flag(three_flagged) is True


def test_detection_classify_priority_s1_beats_everything():
    channel = _channel_signals(accepted=False, encryption_enabled=True)
    result = detection.classify(channel=channel, physical_anomaly=True,
                                 simultaneity=True, accident_active=False)
    assert result.classification == detection.CLASSIFICATION_CYBER_CONFIRMED
    assert "S1_INTEGRITY_FAIL" in result.signals


def test_detection_classify_crude_attack_is_implausible():
    channel = _channel_signals(reported_vehicles=999, true_vehicles=5, green_seconds_window=20.0)
    result = detection.classify(channel=channel, physical_anomaly=False,
                                 simultaneity=False, accident_active=False)
    assert result.classification == detection.CLASSIFICATION_CYBER_LIKELY
    assert "S2_IMPLAUSIBLE" in result.signals


def test_detection_classify_stealthy_attack_is_divergence_not_implausible():
    # Plausible count (within HCM bound) but differs from the true value.
    channel = _channel_signals(reported_vehicles=7, true_vehicles=5, green_seconds_window=20.0)
    result = detection.classify(channel=channel, physical_anomaly=False,
                                 simultaneity=False, accident_active=False)
    assert result.classification == detection.CLASSIFICATION_CYBER_LIKELY
    assert "S3_DIVERGENCE" in result.signals
    assert "S2_IMPLAUSIBLE" not in result.signals


def test_detection_classify_real_accident_is_physical_not_cyber():
    channel = _channel_signals(reported_vehicles=5, true_vehicles=5)  # clean channel
    result = detection.classify(channel=channel, physical_anomaly=True,
                                 simultaneity=False, accident_active=True)
    assert result.classification == detection.CLASSIFICATION_PHYSICAL_INCIDENT
    assert result.action == detection.ACTION_NOTIFY_TRAFFIC_OPS


def test_detection_classify_ambiguous_when_physical_and_divergence_both_present():
    channel = _channel_signals(reported_vehicles=7, true_vehicles=5, green_seconds_window=20.0)
    result = detection.classify(channel=channel, physical_anomaly=True,
                                 simultaneity=False, accident_active=True)
    assert result.classification == detection.CLASSIFICATION_AMBIGUOUS
    assert result.action == detection.ACTION_VERIFY_CAMERA


def test_detection_classify_normal_when_nothing_fires():
    channel = _channel_signals(reported_vehicles=5, true_vehicles=5)
    result = detection.classify(channel=channel, physical_anomaly=False,
                                 simultaneity=False, accident_active=False)
    assert result.classification == detection.CLASSIFICATION_NORMAL
    assert result.signals == []


def test_detection_classify_simultaneity_ignored_during_accident():
    channel = _channel_signals(reported_vehicles=5, true_vehicles=5)  # this arm's own reading is clean
    result = detection.classify(channel=channel, physical_anomaly=False,
                                 simultaneity=True, accident_active=True)
    assert result.classification == detection.CLASSIFICATION_NORMAL, \
        "S4 must not fire while a real accident is active (S4 and not accident)"


# ---------------------------------------------------------------------------
# approval.py -- schedule approval hash binding and append-only log
# ---------------------------------------------------------------------------

def test_approval_hash_stable_across_repeated_calls():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.csv"
        path.write_text("hour,road,predicted_count,green_seconds\n0,North,62.09,46\n")
        h1 = sha256_file(path)
        h2 = sha256_file(path)
        assert h1 == h2
        assert len(h1) == 64, "sha256 hex digest must be 64 characters"


def test_approval_hash_changes_on_one_byte_modification():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.csv"
        path.write_text("hour,road,predicted_count,green_seconds\n0,North,62.09,46\n")
        before = sha256_file(path)
        path.write_text("hour,road,predicted_count,green_seconds\n0,North,62.19,46\n")  # one digit changed
        after = sha256_file(path)
        assert before != after


def test_approval_verify_still_valid_true_before_false_after_modification():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "plan.csv"
        path.write_text("hour,road,predicted_count,green_seconds\n0,North,62.09,46\n")
        record = ApprovalRecord(
            timestamp="2026-08-18T12:00:00Z", username="op1",
            schedule_path=str(path), sha256=sha256_file(path))
        assert verify_still_valid(path, record) is True

        path.write_text("hour,road,predicted_count,green_seconds\n0,North,999,46\n")
        assert verify_still_valid(path, record) is False


def test_approval_append_then_load_latest_round_trips():
    with tempfile.TemporaryDirectory() as tmp:
        schedule_path = str(Path(tmp) / "plan.csv")
        log_path = Path(tmp) / "approvals.jsonl"
        record = ApprovalRecord(
            timestamp="2026-08-18T12:00:00Z", username="op1",
            schedule_path=schedule_path, sha256="a" * 64)
        append_approval(record, log_path=log_path)

        loaded = load_latest_approval(schedule_path, log_path=log_path)
        assert loaded == record

        # a second, later approval for the same path must become "latest"
        record2 = ApprovalRecord(
            timestamp="2026-08-18T13:00:00Z", username="op2",
            schedule_path=schedule_path, sha256="b" * 64)
        append_approval(record2, log_path=log_path)
        assert load_latest_approval(schedule_path, log_path=log_path) == record2

        # the log itself has both lines, append-only
        lines = log_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2


def test_approval_load_latest_returns_none_for_unknown_path():
    with tempfile.TemporaryDirectory() as tmp:
        log_path = Path(tmp) / "approvals.jsonl"
        append_approval(
            ApprovalRecord(timestamp="2026-08-18T12:00:00Z", username="op1",
                            schedule_path=str(Path(tmp) / "known.csv"), sha256="a" * 64),
            log_path=log_path)
        assert load_latest_approval(str(Path(tmp) / "unknown.csv"), log_path=log_path) is None

        # no log file at all is also "no record", not an error
        assert load_latest_approval(str(Path(tmp) / "known.csv"),
                                     log_path=Path(tmp) / "does_not_exist.jsonl") is None


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
    test_stealthy_mode_scales_true_value_not_a_constant,
    test_stealthy_mode_has_minimum_effect_on_small_values,
    test_crude_mode_still_substitutes_fixed_constant,
    test_stealthy_mode_still_rejected_without_key_when_encryption_on,
    test_false_data_injection_with_stolen_key_passes_encryption,
    test_false_data_injection_with_wrong_key_still_rejected,
    test_sensor_spoofing_with_stolen_key_passes_encryption,
    test_detection_s1_fires_only_when_rejected_and_encrypted,
    test_detection_s2_implausible_uses_saturation_headway,
    test_detection_s2_does_not_fire_below_min_green_guard,
    test_detection_s3_divergence_any_nonzero_difference,
    test_detection_s3_and_s2_do_not_fire_on_rejected_messages,
    test_detection_simultaneity_requires_three_or_more_arms,
    test_detection_classify_priority_s1_beats_everything,
    test_detection_classify_crude_attack_is_implausible,
    test_detection_classify_stealthy_attack_is_divergence_not_implausible,
    test_detection_classify_real_accident_is_physical_not_cyber,
    test_detection_classify_ambiguous_when_physical_and_divergence_both_present,
    test_detection_classify_normal_when_nothing_fires,
    test_detection_classify_simultaneity_ignored_during_accident,
    test_approval_hash_stable_across_repeated_calls,
    test_approval_hash_changes_on_one_byte_modification,
    test_approval_verify_still_valid_true_before_false_after_modification,
    test_approval_append_then_load_latest_round_trips,
    test_approval_load_latest_returns_none_for_unknown_path,
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
