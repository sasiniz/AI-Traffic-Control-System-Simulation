# results/runs/

Index of every file in this directory. `results/` is append-only (ADR-035),
so files are never deleted or moved here even when they turn out not to be
usable evidence — this table records which ones are, and why.

Two ways a file can fail to be evidence: **zero rows** (nothing ran long
enough to write data), or **pre-ADR-037 schema** (the file predates the
per-reading `readings_sent_window` / `readings_rejected_window` /
`signals_fired_window` columns, so a header claiming `attacks_fired` cannot
be checked against anything in the body — the header's claim is simply
unverifiable from the file itself).

| Filename | rows_written | Schema | attacks_fired | encryption_at_end | Verdict |
|---|---|---|---|---|---|
| `run_20260819T125729_attack-false_data_enc-on_dens-1x.csv` | 0 | 14-col (pre-ADR-037) | none | on | **NOT EVIDENCE** — zero-row smoke-test artefact from the ADR-035 verification pass |
| `run_20260819T125730_attack-true_data_enc-on_dens-10x.csv` | 0 | 14-col (pre-ADR-037) | sensor_spoofing | on | **NOT EVIDENCE** — zero-row smoke-test artefact from the ADR-035 verification pass |
| `run_20260819T125732_attack-true_data_enc-off_dens-50x.csv` | 0 | 14-col (pre-ADR-037) | false_data_injection, sensor_spoofing | off | **NOT EVIDENCE** — zero-row smoke-test artefact from the ADR-035 verification pass |
| `run_20260819T173925_attack-true_data_enc-on_dens-1x.csv` | 232 | 14-col (pre-ADR-037) | false_data_injection, sensor_spoofing | on | **NOT EVIDENCE** for security comparison — no `readings_sent`/`readings_rejected`/`signals_fired` columns, so `attacks_fired` cannot be checked against the body |
| `run_20260820T045141_attack-false_data_enc-on_dens-1x.csv` | 788 | 14-col (pre-ADR-037) | none | on | **NOT EVIDENCE** for security comparison — same schema gap |
| `run_20260820T045342_attack-true_data_enc-off_dens-1x.csv` | 80 | 14-col (pre-ADR-037) | false_data_injection | off | **NOT EVIDENCE** for security comparison — same schema gap |
| `run_20260820T045439_attack-true_data_enc-on_dens-1x.csv` | 108 | 14-col (pre-ADR-037) | false_data_injection | on | **NOT EVIDENCE** for security comparison — same schema gap |
| `run_20260820T052206_attack-false_data_enc-on_dens-50x.csv` | 100 | 17-col (post-ADR-037) | none | on | **EVIDENCE** — baseline condition, 50x density (RESULTS_LOG row 11) |
| `run_20260820T052330_attack-true_data_enc-off_dens-50x.csv` | 108 | 17-col (post-ADR-037) | false_data_injection | off | **EVIDENCE** — attack condition, encryption OFF, 50x density (RESULTS_LOG row 12) |
| `run_20260820T052443_attack-true_data_enc-on_dens-50x.csv` | 100 | 17-col (post-ADR-037) | false_data_injection | on | **EVIDENCE** — attack condition, encryption ON, 50x density (RESULTS_LOG row 13) |

10 files total.

The three-condition comparison in `results/RESULTS_LOG.md` (row 14) and the
per-file findings in rows 11-13 draw only from the three EVIDENCE files
above, all at commit `8a25cc8`. Nothing in this repository cites any NOT
EVIDENCE file as a result.
