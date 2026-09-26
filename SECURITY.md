# Security policy

This is a learning and research backend, not a hosted service or a supported product. There are no releases with security support windows. Fixes, if any, land on `main`.

## Reporting a vulnerability

Report vulnerabilities privately through GitHub Security Advisories: open the repository's **Security** tab and choose **Report a vulnerability** ([direct link](https://github.com/soralvy/fintech-agent/security/advisories/new)).

Do not open a public issue, pull request, or discussion for a vulnerability.

Include what you can of:

- the affected file, endpoint, or component;
- the steps to reproduce, and what you expected instead;
- the impact you believe it has.

## Do not share secrets or sensitive data

In a report, an issue, or anywhere public:

- never paste an API key (OpenAI, Alpha Vantage, or any other), a database password, or a connection string that contains one;
- never attach a `.env` file, server logs that may contain secrets, or provider responses;
- never attach private or confidential financial documents. Use a public or synthetic document to reproduce a problem.

If you have exposed a key, revoke it with its provider first. Deleting a message or a commit does not undo the exposure.

## Scope

The trust boundaries the project claims are listed in the README's "Security and trust boundaries" section. The application has no authentication and is meant to run locally. Running it on a public network is outside what the project claims to protect.
