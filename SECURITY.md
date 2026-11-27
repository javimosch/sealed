# Security

`sealed` is experimental and has had **no independent security audit**. Do not use
it for production secrets.

## Reporting

Please report vulnerabilities privately through GitHub: **Security → Report a
vulnerability**. Do not include real secrets in a report.

## Already known

These are limits of the design, described in the README, and not new findings:

- a token holder can obtain any in-scope value (`exec`, `render`, the daemon API);
- a process running as the same user, or as root, is not kept out;
- someone who can write to the database can delete rows or restore old ones.

A way to get a value **outside** a token's scope, to `get --reveal` without the
passphrase, to forge a token or approval, or to read values from the database
without the passphrase **is** a vulnerability.
