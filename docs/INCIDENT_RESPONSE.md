# Incident response

1. **Triage:** record UTC time, affected routes/jobs, latest deploy and data-source state.
2. **Contain:** disable affected recommendations or stop the specific timer. Do not delete data.
3. **Preserve:** save journals, source payload hashes, model run IDs and the latest verified dump.
4. **Recover:** use the documented service recovery or restore into a new database.
5. **Verify:** run health, authentication, forecast, timer, database and backup smoke checks.
6. **Learn:** create a data-quality/drift incident and a test or monitor for the failure mode.

Credential exposure is a blocking incident: revoke the credential at its provider, replace it in
the root-owned environment file, restart dependent services and verify that the old value fails.
