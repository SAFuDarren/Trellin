# Security policy

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Report it privately through GitHub: open the repository's **Security** tab and choose **Report a vulnerability**. Include what you found, how to reproduce it, the affected version, and any suggested fix. If that option is unavailable, contact the maintainer, [@SAFuDarren](https://github.com/SAFuDarren), using the details on that GitHub profile, and ask for a private channel.

You will receive an acknowledgement, and we will agree on a fix and a disclosure date with you. Confirmed issues are fixed before they are disclosed publicly.

## Scope

Especially relevant reports concern:

- the local workbench server: session token, Host and Origin checks, content security policy;
- database roles, row-level security and migrations;
- credential handling and anything that could expose connection strings or research content;
- verifier integrity: ways to make an unverified proof or certificate be recorded as verified;
- model and plugin calls that could send workspace content without authorization.

## Supported versions

Security fixes go into the latest release. Version 1.0.0 is the first public release.
