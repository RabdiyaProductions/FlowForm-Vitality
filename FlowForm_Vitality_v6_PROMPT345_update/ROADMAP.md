# ROADMAP

Next 10 deliverables to move from stubbed APIs to production-ready CCE-flat app.

1. **Unify app entrypoint strategy**
   - Decide canonical Flask module (`app.py` vs `app_server.py`) and deprecate duplicate route stacks.

2. **Formalize FIRST_CHECK config contract**
   - Ensure `FIRST_CHECK` is always initialized inside app config to avoid runtime key errors.

3. **Define persistent project schema**
   - Add SQLite tables for projects, timelines, approvals, imports, and export jobs.

4. **Implement `/api/projects/<code>` real lookup**
   - Replace echo payload with DB-backed retrieval + 404 behavior.

5. **Implement timeline mutation persistence**
   - `update/regenerate/apply_global` should read/write actual project timeline data.

6. **Implement critic + approve state machine**
   - Add explicit draft/review/approved states with validation and idempotency.

7. **Implement real import/export pipeline**
   - `/api/import` ingests payload bundle; `/api/export` returns generated archive + manifest.

8. **Expand diagnostics with dependency checks**
   - Include provider config, DB migration level, and write/read probes in diagnostics output.

9. **Add migration/versioning tooling**
   - Introduce schema migrations and app version compatibility checks.

10. **CI hardening for structure + smoke + API contract tests**
   - Run `check_structure`, smoke tests, and API contract assertions on every PR.
