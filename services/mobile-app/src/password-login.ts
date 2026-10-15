// Login por usuário e senha (direct grant no client `nexora-mobile`). Lógica pura, testável sem React Native.
export const MOBILE_CLIENT = 'nexora-mobile';
export const BROWSER_CLIENT = 'central-monitor';

export type TokenResult = { accessToken: string; refreshToken: string };
export type LoginFailure = { message: string; needsOtp: boolean };

export function tokenEndpoint(server: string): string {
  return `${server.replace(/\/+$/, '')}/auth/realms/central-monitor/protocol/openid-connect/token`;
}

/** Corpo form-urlencoded do grant de senha. O código OTP só é enviado quando preenchido (somente dígitos). */
export function passwordGrantBody(username: string, password: string, otp: string): string {
  const params = new URLSearchParams({ grant_type: 'password', client_id: MOBILE_CLIENT, scope: 'openid profile email', username: username.trim(), password });
  const code = otp.replace(/\s+/g, '');
  if (code) params.set('totp', code);
  return params.toString();
}

/** Valida o formulário antes de chamar o servidor. Devolve a mensagem de erro ou null. */
export function validateLoginForm(username: string, password: string, otp: string, otpVisible: boolean): string | null {
  if (!username.trim()) return 'Informe seu usuário.';
  if (!password) return 'Informe sua senha.';
  if (otpVisible && !/^\d{6,8}$/.test(otp.replace(/\s+/g, ''))) return 'Informe o código de 6 dígitos do aplicativo autenticador.';
  return null;
}

/** Traduz a resposta de erro do Keycloak em mensagem amigável, sem expor detalhes técnicos nem dizer qual dado falhou. */
export function describeLoginFailure(status: number, body: any, otpSent: boolean): LoginFailure {
  const code = String(body?.error || '');
  const detail = String(body?.error_description || '').toLowerCase();
  if (status === 0) return { message: 'Sem conexão com o servidor. Verifique sua internet e tente novamente.', needsOtp: false };
  if (status >= 500) return { message: 'O serviço de login está indisponível no momento. Tente novamente em instantes.', needsOtp: false };
  if (status === 429 || detail.includes('temporarily') || detail.includes('too many')) return { message: 'Muitas tentativas. Aguarde alguns minutos e tente novamente.', needsOtp: false };
  if (detail.includes('disabled')) return { message: 'Conta desativada ou bloqueada temporariamente. Aguarde alguns minutos ou fale com o administrador.', needsOtp: false };
  if (detail.includes('not fully set up') || detail.includes('required action') || detail.includes('resolve_required_actions')) {
    return { message: 'Sua conta precisa de uma configuração pendente (por exemplo, o autenticador). Entre uma vez pelo navegador seguro para concluir.', needsOtp: false };
  }
  if (code === 'invalid_grant' || code === 'invalid_request' || status === 401 || status === 400) {
    return { message: otpSent ? 'Usuário, senha ou código inválidos.' : 'Usuário ou senha inválidos. Se sua conta usa verificação em duas etapas, informe também o código do autenticador.', needsOtp: !otpSent };
  }
  return { message: 'Não foi possível entrar. Tente novamente.', needsOtp: false };
}

/** Qual client emitiu a sessão guardada: sessões antigas (navegador) não têm marca e usam `central-monitor`. */
export function clientForSession(saved: string | null | undefined): string {
  return saved === MOBILE_CLIENT ? MOBILE_CLIENT : BROWSER_CLIENT;
}
