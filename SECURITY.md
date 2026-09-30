# Security Policy

Concord is a student/research project and is not certified for production use.
Do not send real credentials, payment data, medical records or other sensitive
personal data to the public demo or synthetic environments.

Report a suspected vulnerability privately through GitHub Security Advisories.
Do not open a public issue containing exploit details, credentials or private
data. Maintainers will acknowledge a report when available and will document
the affected versions after a fix is ready.

The repository must not contain runtime `.env` files or anything under the
local `docs/` directory. Real-write adapters are disabled by default; current
state-changing examples operate only on resettable synthetic Case state.
