# Installation

Replace these three files in the repository:

- `monitor.py`
- `tests/test_monitor.py`
- `state.json`

Commit them to `master`.

Then manually run the workflow with:

- `send_test`: `false`
- `notify_on_first_run`: `true`

The first run creates a clean baseline containing only IMAX screenings. It treats
the old noisy state as a migration and therefore does not send a false alert that
every existing IMAX screening is new.
