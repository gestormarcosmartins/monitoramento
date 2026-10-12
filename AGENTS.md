# Nexora project instructions

## Mandatory development blueprint

All product and architecture work must follow `docs/NEXORA_DEVELOPMENT_BLUEPRINT.md`.
Execution order, dependencies and traceability must follow `docs/NEXORA_DEVELOPMENT_SLICES.md`.

- Treat the blueprint as the implementation bridge between the functional specification and the current codebase.
- Preserve existing IDs and working integrations; evolve them through incremental migrations instead of rebuilding from names in the target model.
- A feature is not complete without backend authorization, real states, automated tests, observability and failure handling.
- Consolidated multi-company context is read-only unless an endpoint explicitly supports it. Every mutation requires one concrete authorized Empresa.
- GLPI is the authority for confirmed ITSM records. Never report a remote mutation as successful before confirmation or reconciliation.
- Do not add menu items, buttons or screens for unavailable capabilities.
- Every story must identify its CD development slice and inherited NX, RN, T and AT references.
- A development slice is complete only when its release gate, automated tests, evidence and operational requirements are satisfied.

## Mandatory frontend standard

All frontend work under `services/web` must follow `docs/NEXORA_FRONTEND_STANDARD.md`.

- Treat that document as a product requirement, not optional visual guidance.
- Reuse the shared shell, page header, metrics, filters, grids, status badges, form sections and state components.
- New and Edit flows must use dedicated in-app pages. Do not use a modal, side panel or a form beside the listing grid.
- Listing pages must follow: page identification, relevant metrics, filters, full-width grid, pagination.
- Preserve the global Empresa context and real backend authorization.
- Validate desktop and mobile behavior down to 320 CSS pixels, keyboard focus and 44 px touch targets.
- Do not copy sample people, values, counts or unsupported features from mockups.
- A deviation requires explicit product approval and documentation of desktop and mobile impact.

