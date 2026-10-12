# Refresh

Run the same command again with the same `--output-dir` (or a shared `--state-dir`).

- **Stable keys.** Each claim has a semantic key (`leadership_workplaces.role.DAGL.<name>`,
  `financials.revenue.SELSKAP.2025-12-31`, `hiring_activity.job_posting.nav.<uuid>` …) and an id derived from
  `(organisation number, key, value)`. Identical sources produce identical ids, so a re-run is idempotent and reports
  zero changes.
- **Observation times.** `first_observed_at` is inherited from the previous run for unchanged claims; `last_observed_at`
  is the current run.
- **Typed diffs.** Added / changed / removed claim keys become changes with `change_type` (`new_role`, `removed_role`,
  `new_location`, `closed_location`, `new_job`, `closed_job`, `new_filing`, `new_activity`, `new_profile`,
  `changed_value`, …), a `material` flag, both values, the previous evidence records and the new evidence ids.
- **History is preserved.** Before the latest-state file is replaced, the previous envelope state is archived to
  `state/history/<org>/<previous run id>.json` (never overwritten). Raw snapshots are content-addressed and never deleted.
- **Failed refreshes never erase evidence.** If a module fails or is blocked, the previous supported claims of that
  module are carried forward with a note, their `last_observed_at` stays at the old value, and no "removed" change is
  emitted for them.
- **Volatility.** Registry and accounts are cheap and re-read every run; jobs and company news are naturally the most
  volatile and produce most changes.
