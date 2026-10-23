# Overseen epic

A `docs/oversight/<name>.md` file in the main checkout lists this epic: a
program overseer (`/overseer`) holds it to the program's decided items and
interfaces between epics. The overseer flags; the user decides.

- **Drift rule.** Never change a decided item the oversight file lists, move
  scope between epics, or alter an interface between epics. When one looks
  necessary, ask the user.
- **Notes.** Before each planner or implementer job, read the epic's note,
  `docs/oversight/<name>/notes/<epic-id>.md` in the main checkout. Carry every
  entry the epic's files do not yet reflect into the job's task, quoted, and
  ask the user any decision an entry raises. Act on a user decision only as
  the note quotes it.
- **Checkpoints.** Tell the user when an overseer's check is due: Gate 1
  approved, the plan ready, and the audit settled (before `/wrap_epic`).
- **Gate 2.** In `/wrap_epic`, add to the Gate 2 summary the overseer's
  latest pre-merge check (`docs/oversight/<name>/checks/*-merge.md`) and any
  note entry the branch does not reflect, or say that no pre-merge check has
  run.
