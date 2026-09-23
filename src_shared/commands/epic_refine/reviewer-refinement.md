## Focus: refinement review

You review the epic's approved `acceptance-criteria.md` and its `plan.md`
before any code is written. Read `details.md`, the durable architecture docs
the plan references, and the code it will change.

Answer four questions:

1. **Does the plan satisfy every acceptance criterion?** Each criterion has
   stories that deliver it and tests that prove it. Nothing approved is
   silently dropped.
2. **Is it feasible and consistent with the current architecture docs?** The
   named components, contracts, and data exist as the plan assumes, or the
   plan creates them. It contradicts no ADR or spec without saying so.
3. **Is it proportional?** Flag speculative generality, scope beyond the
   criteria or into the "Not building" list, an implausible size estimate,
   stories above complexity 7 without an acceptable exception, and planned
   modules above the size limits. Say what could be removed.
4. **Are the doc obligations complete?** Every durable doc the epic changes
   has a target file and an owner story; ADRs for lasting decisions; specs for
   new or changed contracts.

Do not ask the plan to pre-specify what the implementer can decide. Do not
review the criteria's product choices; the user approved them. Raise a
`product_decision` finding only when the plan cannot proceed without one.
