# Security and privacy

Please do not post authentication files, API keys, bearer tokens, raw Codex logs, account identifiers, or screenshots with personal information in public issues.

For a security concern, open an issue containing only a brief, non-sensitive description and ask for a private reporting channel. Do not include exploit details or secrets in that public issue.

QuotaDock uses the local Codex runtime's normal authentication. It does not read authentication files directly or request API keys. Its own persistent storage contains UI preferences only. The runtime itself can maintain logs and settings according to its configuration.

Before publishing this repository, the release files were reviewed for personal paths, machine/account identifiers, credentials, local configuration, logs and screenshot data. Only an allowlisted set of source, documentation, tests and installation scripts was included. No local authentication, usage snapshots or original working-directory history is distributed. Pattern scanning and source review reduce risk but cannot prove the absence of every possible secret.
