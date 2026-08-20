# security/

Cybersecurity module for the Secure AI Driven Smart Traffic Control System.
This folder is intentionally kept flat, not a branch, so the security work
is visible on `main` and directly pointable-at for marking (ADR-023).

## Files and what each one demonstrates

| File | Job | Primary ISO/IEC 27001:2022 Annex A control(s) |
|---|---|---|
| `crypto.py` | AES-256-GCM encrypt/decrypt of sensor readings **in transit only**. `encrypt_log_line`/`decrypt_log_line` also exist and pass their own unit tests, but have zero call sites outside `test_security.py` — `sensor_log.csv` is written as plaintext. See ADR-029. | A.8.24 Use of cryptography |
| `auth.py` | bcrypt-hashed operator credentials for the approval gate | A.5.17 Authentication information; A.8.5 Secure authentication |
| `channel.py` | The `send() -> [encryption] -> [attacker] -> receive()` abstraction the encryption toggle and attacks hang off | A.8.20 Networks security (modelled at application layer, in-process); A.8.24 Use of cryptography |
| `attacks.py` | False data injection and sensor spoofing, as channel interceptors | Maps to the *threats* the other controls mitigate, not a control itself — cited under A.5.7 Threat intelligence and A.8.16 Monitoring activities in the risk assessment |
| `approval.py` | SHA-256 file hash-binding and an append-only approval log, so an operator's accept is bound to the exact bytes of the schedule file, not just "someone clicked accept" | A.5.33 Protection of records; A.8.24 Use of cryptography |

Three controls this project claims are demonstrated empirically, not just
described: A.8.24 (the GCM tag rejection you can watch happen live by
pressing `E`), A.5.17/A.8.5 (a wrong operator password is rejected by
`auth.py`, correct one is accepted), and A.5.33/A.8.24 (the approval gate's
scroll-to-end + hash-bind, below).

**At-rest encryption is NOT implemented — do not claim otherwise.**
ADR-023 originally stated that AES-256-GCM protects `sensor_log.csv` "at
rest" as well as in transit. ADR-029 (2026-08-19) records that this was
false of the running system: `crypto.py`'s `encrypt_log_line`/
`decrypt_log_line` exist, are unit-tested, and are never called from
`traffic_sim.py`'s write path. The first two lines of `sensor_log.csv` on
disk are a plaintext CSV header followed by plaintext data. Every document
in this project, including this one, describes encryption here as
protecting sensor readings **in transit only**. See ADR-029 for the full
record, including how the gap was found and why it was left unfixed for
submission (residual-risk statement: the log holds only simulated traffic
counts, no personal data).

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
in the dashboard flips `channel.encryption_enabled`; it does not remove or
bypass `crypto.py`. This is what makes it possible to demonstrate the same
attack (same interceptor instance, same channel) succeeding with encryption
off and failing with encryption on, in one run, with one keypress.

## Dashboard keys (implemented — `traffic_sim.py:2564-2696`)

| Key | Action | Effect |
|---|---|---|
| `E` | Toggle encryption | Flips `channel.encryption_enabled` (`traffic_sim.py:2609-2611, 2664-2665`) |
| `F` | False data injection | `channel.add_interceptor(FalseDataInjectionAttack(...))` (`:2564-2572, 2666-2667`) |
| `G` | Sensor spoofing | `channel.add_interceptor(SensorSpoofingAttack(...))` (`:2574-2578, 2668-2669`) |
| `H` | Clear attacks | `channel.clear_interceptors()` (`:2617, 2670-2671`) |
| `S` | Toggle stealthy attack mode | `sim.attack_stealthy` (`:2618, 2672-2673`) |
| `K` | Toggle key-compromise mode | `sim.attack_key_compromise` (`:2619-2620, 2674-2675`) |

Keyboard and the on-screen `SEC_BUTTONS` mouse controls share the same
`sec_actions` dict (`traffic_sim.py:2613-2635`), so both trigger the exact
same action — one source of truth, same reasoning as the approval gate's
single submit path below.

## The approval gate (implemented — `traffic_sim.py:2375-2536`, ADR-028/031/033)

`_run_approval_gate()` runs to completion before `Simulation()` is
constructed, so it has no way to influence schedule content or phase
advancement — those objects don't exist yet while it runs. The target file
(`APPROVAL_TARGET_PATH`) is read exactly once, as raw bytes; the SHA-256
hash, the plan-summary line, and the review pane's pivoted rows are all
derived from that same read, never a second independent `open()`.

**Scroll gate:** the ACCEPT control only enables once `scrolled_to_end` is
true, which requires the operator to have scrolled or paged to the last
pivoted row, or used `END` / the "jump to end" button to get there in one
step. Both `ENTER` and a mouse click on the accept button route through the
same `_attempt_submit()` function, so there is exactly one path that can
approve a schedule, and both are blocked identically by the scroll check.

**The ratchet is one-way:** pressing `HOME` after reaching `END` scrolls
the view back to the top but does **not** reopen the gate —
`scrolled_to_end` is only ever set `True`, never reset. This is deliberate:
the control's claim is "the operator was shown the artefact's full extent",
not "the operator is currently looking at the last row". Covered by
`test_approval_gate_home_after_end_keeps_scroll_ratchet` in
`test_security.py`.

Approving writes one line to `security/approvals.jsonl` (gitignored,
append-only): timestamp, username, the exact `schedule_path`, and the
SHA-256 that was bound to that read. `verify_still_valid()` lets anyone
re-check later whether the file on disk still matches what was approved.

## Known limitation to disclose in the dissertation

The in-process channel model means "the attacker" and "the sensor" run in
the same Python process and trust boundary as the defender. This is
sufficient to demonstrate the cryptographic properties (AEAD tamper
detection, bcrypt authentication) correctly, but it is not a claim that the
system has been tested against a network-positioned adversary. State this
explicitly rather than letting the demo imply more than it shows.
