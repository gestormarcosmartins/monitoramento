import * as Notifications from 'expo-notifications';
import Constants from 'expo-constants';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

type Request = (path: string, options?: RequestInit, tenantHeader?: string, retry?: boolean) => Promise<any>;
const DEVICE_KEY = 'nexora.push.device';
const INSTALL_KEY = 'nexora.push.installation';

export type PushOpenTarget = { notificationId: string; tenantId: string; actionType: string; actionRef: string | null };

// Primeiro plano: mostra o aviso, sem som. A abertura sempre passa pela autorização do backend.
Notifications.setNotificationHandler({
  handleNotification: async () => ({ shouldShowBanner: true, shouldShowList: true, shouldPlaySound: false, shouldSetBadge: false }),
});

async function installationId() {
  let id = await SecureStore.getItemAsync(INSTALL_KEY);
  if (!id) {
    id = 'inst-' + Array.from({ length: 24 }, () => Math.floor(Math.random() * 16).toString(16)).join('');
    await SecureStore.setItemAsync(INSTALL_KEY, id);
  }
  return id;
}

/** Pede permissão, obtém o ExpoPushToken e registra o aparelho. Nunca lança: o app funciona sem push. */
export async function registerForPush(request: Request): Promise<'registered' | 'denied' | 'unavailable'> {
  try {
    if (Platform.OS === 'android') {
      await Notifications.setNotificationChannelAsync('alerts', {
        name: 'Alertas Nexora',
        importance: Notifications.AndroidImportance.MAX,
        vibrationPattern: [0, 250, 250, 250],
        lockscreenVisibility: Notifications.AndroidNotificationVisibility.PRIVATE,
      });
    }
    const projectId = (Constants.expoConfig?.extra as any)?.eas?.projectId ?? (Constants as any).easConfig?.projectId;
    if (!projectId) return 'unavailable'; // projeto EAS ainda não configurado (eas init)
    let { status } = await Notifications.getPermissionsAsync();
    if (status !== 'granted') status = (await Notifications.requestPermissionsAsync()).status;
    if (status !== 'granted') return 'denied';
    const { data: expoPushToken } = await Notifications.getExpoPushTokenAsync({ projectId });
    const device = await request('/api/v1/notificacoes/dispositivos-push', {
      method: 'POST',
      body: JSON.stringify({
        expoPushToken, platform: Platform.OS === 'ios' ? 'ios' : 'android', installationId: await installationId(),
        expoProjectId: projectId, appVersion: Constants.expoConfig?.version, permissionStatus: status,
      }),
    }, '');
    if (device?.id) await SecureStore.setItemAsync(DEVICE_KEY, device.id);
    return 'registered';
  } catch {
    return 'unavailable';
  }
}

/** Logout: revoga o vínculo deste aparelho para não receber mais push da sessão antiga. */
export async function unregisterPush(request: Request) {
  try {
    const id = await SecureStore.getItemAsync(DEVICE_KEY);
    if (id) await request(`/api/v1/notificacoes/dispositivos-push/${id}`, { method: 'DELETE' }, '', false);
  } catch {}
  await SecureStore.deleteItemAsync(DEVICE_KEY);
}

export function parsePushData(data: any): PushOpenTarget | null {
  if (!data || typeof data.notificationId !== 'string' || typeof data.tenantId !== 'string') return null;
  return { notificationId: data.notificationId, tenantId: data.tenantId, actionType: String(data.actionType ?? data.type ?? ''), actionRef: typeof data.actionRef === 'string' ? data.actionRef : null };
}

/** Toque na notificação: o destino é confirmado pelo backend (/abrir), nunca pelo conteúdo do push. */
export function onPushTap(handler: (data: PushOpenTarget) => void) {
  const open = (response: Notifications.NotificationResponse | null) => {
    const parsed = parsePushData(response?.notification.request.content.data);
    if (parsed) handler(parsed);
  };
  Notifications.getLastNotificationResponseAsync().then(open).catch(() => {});
  const subscription = Notifications.addNotificationResponseReceivedListener(open);
  return () => subscription.remove();
}
