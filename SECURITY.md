# Security Policy

## Supported Versions

Only the latest minor release line of **K.H.A.V.I.S.** receives security updates:

| Version | Supported          |
| ------- | ------------------ |
| 0.5.x   | :white_check_mark: |
| < 0.5.0 | :x:                |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, report sensitive findings privately via email:

- **Security Contact:** `security@kiddhsu.taipei`
- **PGP Public Key:** Served at `/pgp.txt` on the official status endpoint (`deploy/pgp.txt` in this repository).

### What to Include

1. A clear description of the vulnerability and affected component (`core`, `providers`, `bot`, `server`, `web`, or `agents`).
2. Step-by-step reproduction instructions or a minimal proof-of-concept (PoC).
3. Potential impact (e.g., credential exposure, SSRF, auth bypass, or sandbox escape).

### Response SLA

- **Acknowledgement:** Within **48 hours** of receipt.
- **Triage & Assessment:** Within **5 business days**.
- **Remediation & Coordinated Disclosure:** Critical patches are prioritized for immediate release; credit will be given in `CHANGELOG.md` unless anonymity is requested.
