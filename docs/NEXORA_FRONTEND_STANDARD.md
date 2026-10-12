# Nexora frontend standard

This is the mandatory implementation contract for existing and future Nexora screens. It consolidates the approved specification dated 11 October 2026. When a mockup conflicts with these written rules, these rules prevail.

## Design tokens

- Font: Inter, then `system-ui` and Arial.
- Midnight: `#0B1F3B` for navigation and dark brand surfaces.
- Electric Blue: `#2563FF` for primary actions, selection, links and focus.
- Cyan: `#06B6D4` for brand accents and the active-navigation accent.
- Violet: `#8B5CF6` only for specialized automation information.
- Primary and secondary text: `#0F172A` and `#64748B`.
- Border and content background: `#E2E8F0` and `#F8FAFC`.
- Success, warning and danger: `#22C55E`, `#F59E0B` and `#EF4444`; always pair color with text or an icon.
- Use the 8 px spacing scale. Cards use a 12 px radius; fields and buttons use 8 px; borders use 1 px and shadows remain subtle.

## Global shell and navigation

- Desktop sidebar: 256 px. Collapsed sidebar: 72 px. Desktop topbar: 72 px; mobile topbar: at least 64 px.
- Sidebar order: official Nexora logo and signature, central navigation, user profile and exit at the bottom.
- Sidebar uses a navy background with the approved blue technological visual. Maintain contrast with a dark overlay.
- Only the current item receives the main blue highlight and cyan accent. Groups may be expanded with a quieter treatment.
- Use one coherent SVG icon family. Do not use emoji, raster screenshots or icons with inconsistent meaning.
- Content padding: 24 px on desktop and 16 px on mobile.
- The topbar and page header must identify the route and the selected Empresa at all widths.
- Use “Empresa”, never “Cliente”, for the multi-company context.

## Listing page contract

Every consultation/listing page uses this order:

1. Page identification: breadcrumb, module icon, one H1, brief contextual description and primary action.
2. Relevant metric cards based on real data.
3. Shared filter bar.
4. Full-width data grid.
5. Pagination immediately below the grid.

Cards, filters, grid and pagination share the same lateral alignment and query scope. Never reserve a side column for a form or future panel.

### Metrics

- Use the shared MetricGroup and MetricCard patterns.
- Do not invent metrics to fill a row or copy sample numbers from mockups.
- Loading uses skeletons; confirmed zero uses `0`; unavailable uses `—`; errors show a recovery action.
- Clickable metrics require keyboard focus, a visible selected state and a useful filtering action.

### Filters

- Search comes first and applies after about 300 ms; Enter may apply immediately.
- Selects apply on completed selection. Valid filter changes return pagination to page 1.
- Show active-filter chips with individual removal and a Clear action.
- Preserve filters, sorting, page and reading position when returning from Edit when possible.
- On mobile, keep search visible and expand remaining filters using “Filtros (N)” within the page.

### Grids and actions

- The grid occupies the entire useful width. Horizontal scrolling, when essential, is limited to the grid container.
- Rows have a reference minimum height of 64 px and may grow. Never clip valid content to force a fixed height.
- Sort only allowed columns and always use a deterministic tie-breaker. Actions are never sortable.
- Standard actions: plus for New, pencil for Edit, pause/play for Inactivate/Reactivate, trash for Delete and eye for View when implemented.
- Icon actions need an accessible name and tooltip. Desktop target is at least 36 px; touch target is at least 44 px.
- Pagination supports 10, 25 and 50 items with 10 as default and reports filtered total and displayed range.

## New and Edit pages

- New and Edit always navigate to a dedicated, addressable page in the same application tab.
- Keep the app shell and topbar. Breadcrumb examples: `Administração › Equipes › Editar equipe`.
- Desktop forms use a 12-column layout and the full useful width. Related sections may form two columns.
- Mobile forms stack into one column. Vertical scrolling is expected.
- Never use the primary form in a modal, side drawer or beside the listing grid.
- Back and Cancel return to the source list. Confirm before discarding unsaved changes.
- Disable duplicate submissions, show progress, keep field values after errors and focus the first invalid field.
- Form actions use consistent Cancel and Save/Create positions. Sticky actions may not cover content or keyboard focus.

## Empresa context and authorization

- A specific Empresa limits all page data to that company and is inherited, identified and read-only during creation.
- “Todas as empresas” means only companies authorized to the current user. Multi-company grids identify each record’s Empresa.
- Changing Empresa invalidates incompatible queries, selections and stale responses.
- Frontend visibility never replaces backend authorization.
- Adding a technician to a team must not grant Empresa access automatically.

## Responsive and accessibility acceptance

- Validate at 360 × 800, 390 × 844 and down to 320 CSS px, plus 768, 1024 and 1280 px breakpoints.
- Users must be able to consult, filter, sort, paginate, create, edit and execute authorized actions by touch.
- Never scale down a fixed desktop layout or hide capabilities to appear responsive.
- Use visible labels, logical keyboard order, visible focus and accessible button names.
- Target WCAG 2.2 AA: 4.5:1 normal text, 3:1 large text and applicable non-text controls.
- Do not rely on hover or color alone. Return focus predictably after internal navigation and dialogs.

## Shared component responsibilities

- `AppShell`: sidebar, topbar, global Empresa context and breakpoints.
- `PageHeader`: breadcrumb, icon, title, description, Back and actions.
- `ListPage`: header, metrics, filters, grid and pagination in the required order.
- `MetricGroup`: data scope, loading/error/selected states and responsive layout.
- `FilterBar`: uniform fields, automatic application, active filters and mobile expansion.
- `DataGrid`: columns, sorting, responsive records, actions, empty/error/loading states and pagination.
- `FormPage`, `FormSection`, `FormActions`: dedicated page, desktop columns, mobile stacking and safe submission.
- `Status`, `Loading`, `Empty`, `Error`, `Forbidden`: consistent semantic states without fabricated data.

## Change control

If a need is not covered, document the reason, proposed flow and desktop/mobile impact and request product approval before implementing an exception. Fix accessibility and layout defects in shared components rather than adding per-screen overrides.
# Ícones obrigatórios em grids

- Toda ação de grid deve usar SVG vetorial consistente: lápis para editar, olho para visualizar/detalhes, pausa para inativar/ignorar, play para ativar/reativar e lixeira vermelha para excluir/remover.
- A implementação deve passar pelo componente compartilhado `DataGrid`; não duplicar iconografia tela a tela.
- Botões de ação usam área mínima de 36 x 36 px no desktop e 44 x 44 px em dispositivos de toque.
- O texto visual pode ser ocultado na coluna compacta, mas `title` e `aria-label` descritivos são obrigatórios.
- A primeira coluna identificadora (`name`, `display_name` ou `title`) deve exibir um ícone contextual dentro de um bloco de 38 x 38 px quando houver espaço.

