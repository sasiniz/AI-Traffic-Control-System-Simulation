# security/

Cybersecurity module for the Secure AI Driven Smart Traffic Control System.
This folder is intentionally kept flat, not a branch, so the security work
is visible on `main` and directly pointable-at for marking (ADR-023).

## Files and what each one demonstrates

| File | Job | Primary ISO/IEC 27001:2022 Annex A control(s) |
|---|---|---|
| `crypto.py` | AES-256-GCM encrypt/decrypt of sensor readings in transit, and of `sensor_log.csv` at rest | A.8.24 Use of cryptography |
| `auth.py` | bcrypt-hashed operator credentials for the manual-override dashboard | A.5.17 Authentication information; A.8.5 Secure authentication |
| `channel.py` | The `send() -> [encryption] -> [attacker] -> receive()` abstraction the encryption toggle and attacks hang off | A.8.20 Networks security (modelled at application layer, in-process); A.8.24 Use of cryptography |
| `attacks.py` | False data injection and sensor spoofing, as channel interceptors | Maps to the *threats* the other controls mitigate, not a control itself — cited under A.5.7 Threat intelligence and A.8.16 Monitoring activities in the risk assessment |

Two controls this project claims are demonstrated empirically, not just
described: A.8.24 (the GCM tag rejection you can watch happen live by
pressing `E`) and A.5.17/A.8.5 (a wrong operator password is rejected by
`auth.py`, correct one is accepted).

## Design decisions (see ADR-023 in `DECISIONS.md` for the full record)

**AES-256-GCM and bcrypt are not interchangeable and are not both "sensor
channel protection".** AES-256-GCM is an AEAD cipher: one operation gives
both confidentiality and a tamper-evident authentication tag, which is what
lets the demo show a corrupted or forged reading being *rejected*, not just
logged after the fact. bcrypt is a slow, salted password hash meant for
authenticating a human once per login, not a stream of sensor messages —
using it to authenticate messages would make replay attacks trivial and
would not provide freshness. `crypto.py` owns the first job; `auth.py` owns
the second. They are not substitutes for each other.

**In-process channel, not real sockets (Option B).** `channel.py` passes
Python bytes through interceptor callables rather than opening real UDP
sockets. Scapy and packet-level network attacks are therefore out of scope.
This was chosen to fit the remaining timeline on one laptop; the ethics form
should be updated to match (no real network traffic is generated or
intercepted).

**Two attacks only. DoS/DDoS is explicitly excluded.** A credible
denial-of-service demonstration needs real network infrastructure and load
generation that is not available for this project. Verified absent from the
codebase with `Select-String -Path *.py -Pattern "dos|ddos|flood|denial"`.
Do not add a DoS attack to this folder without first raising it with the
supervisor and revising the ethics form and proposal, both of which
currently describe two attacks.

**Encryption is a runtime toggle, not a deletable code path.** The `E` key
in the dashboard should flip `channel.encryption_enabled`, not remove or
bypass `crypto.py`. This is what makes it possible to demonstrate the same
attack (same interceptor instance, same channel) succeeding with encryption
off and failing with encryption on, in one run, with one keypress.

## Dashboard keys (to be wired into `traffic_sim.py`'s existing key handler)

| Key | Action | Effect |
|---|---|---|
| `E` | Toggle encryption | Flips `channel.encryption_enabled` |
| `1` | False data injection | `channel.add_interceptor(FalseDataInjectionAttack())` |
| `2` | Sensor spoofing | `channel.add_interceptor(SensorSpoofingAttack())` |
| `3` | Clear attacks | `channel.clear_interceptors()` |

This project does not have `traffic_sim.py`'s current contents available in
this session, so the exact insertion point into its event loop was not
written as a merged file — see the separate integration snippet delivered
alongside this folder, and treat it as something to insert, not a
replacement file.

## Expected demonstration (measured, see `test_security.py`)

| | Encryption off | Encryption on |
|---|---|---|
| False data injection | Dashboard shows the fake count | GCM tag fails to verify, reading rejected |
| Sensor spoofing | Forged reading accepted as real | No valid key to forge a verifying ciphertext, rejected |

## Known limitation to disclose in the dissertation

The in-process channel model means "the attacker" and "the sensor" run in
the same Python process and trust boundary as the defender. This is
sufficient to demonstrate the cryptographic properties (AEAD tamper
detection, bcrypt authentication) correctly, but it is not a claim that the
system has been tested against a network-positioned adversary. State this
explicitly rather than letting the demo imply more than it shows.
