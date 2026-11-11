# Contributing

## Running the tests
```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
```
(`pip install -r requirements-dev.txt` and `python -m pytest -q` work the same in any activated venv.)

## New device readers
Follow `docs/reader-contract.md`. In short:
- Send only read/query commands from a module-level whitelist; building anything else must raise.
- Never set configuration and never send firmware/DFU commands.
- Add unit tests that use fake handles (no hardware needed), including the whitelist and bad-reply cases.
- Do not copy GPL code or tables. Protocol facts from public documentation are fine; write an
  independent implementation and say so in the module docstring.
- Unverified readers must be experimental (off by default) until confirmed on real hardware.

## Reporting a device
Open a "Device report" issue and fill in the form (model, connection, VID:PID, result). Do not paste
personal data; the app logs contain no MAC addresses or serial numbers, but check any snippet first.

## Pull requests
Keep changes focused, make sure the tests pass and fill in the PR template.
