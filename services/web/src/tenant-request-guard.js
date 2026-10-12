export function isTenantResponseCurrent(activeTenantId, requestedTenantId) {
  return Boolean(requestedTenantId) && activeTenantId === requestedTenantId;
}
