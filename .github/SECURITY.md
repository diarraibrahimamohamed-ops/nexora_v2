# Security policy

## Supported version

Security fixes target Nexora v2's default branch and active maintenance branch.

## Reporting a vulnerability

Do not publish exploit details, secrets, tokens or sensitive biological data in public issues. Use GitHub's private vulnerability reporting for this repository if enabled; otherwise contact the maintainer privately through GitHub.

Include the affected component, impact, reproducible steps that do not expose real user data, and a proposed mitigation if available.

## Security expectations

- Never commit secrets or provider keys.
- JWT verification must use an explicit algorithm allowlist and required claims.
- XML/Entrez inputs, archives, uploaded structures and external provider responses are untrusted input.
- Do not disable TLS verification to work around provider problems.
- Include affected package/version and reproduction details when reporting dependency vulnerabilities.
