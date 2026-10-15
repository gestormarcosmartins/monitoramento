// Regras puras de sessão/empresa e do sino, testáveis sem React Native.
export const ALL_TENANTS = 'all';
export type CompanyRef = { id: string };

/** Última empresa lembrada só vale se o usuário ainda tem acesso a ela (ou ao consolidado, com 2+ empresas). */
export function validSavedTenant(saved: string | null | undefined, tenants: CompanyRef[]): string | null {
  if (!saved) return null;
  if (tenants.some((item) => item.id === saved)) return saved;
  if (saved === ALL_TENANTS && tenants.length > 1) return saved;
  return null;
}

/** Sem empresa lembrada: 1 empresa entra direto; 2+ exige a escolha excepcional pós-login. */
export function needsCompanyChoice(saved: string | null | undefined, tenants: CompanyRef[]): boolean {
  return validSavedTenant(saved, tenants) === null && tenants.length > 1;
}

export function unreadBadgeText(count: number): string {
  return count > 99 ? '99+' : String(Math.max(0, count));
}
