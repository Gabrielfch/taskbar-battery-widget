# Security policy

## Supported versions

Only the latest release receives fixes. Please update before reporting.

## Reporting a vulnerability

Use GitHub private vulnerability reporting: open the repository's **Security** tab and choose
**Report a vulnerability**. Please do not open a public issue for security problems.

Include the app version, Windows version, the device involved (if any) and steps to reproduce. Do not
attach files containing personal data.

## What is relevant

- Any command sent to a device that is not a read/query command from the reader whitelist.
- Personal data in logs or stored files (MAC addresses, serial numbers, user names).
- Unexpected network access (the app is designed to use no network at all).
- Problems in how the installer or the portable executable is built or distributed.
