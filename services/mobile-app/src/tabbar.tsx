import React from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import { Icon, IconName } from './icons';
import { C } from './theme';

export type TabKey = 'home' | 'tickets' | 'new' | 'monitoring' | 'noc' | 'settings';
export type MonitoringBadge = { count: number; critical?: number; level: 'none' | 'attention' | 'critical' };
const TABS: Array<{ key: TabKey; label: string; icon: IconName }> = [
  { key: 'home', label: 'Início', icon: 'inicio' },
  { key: 'tickets', label: 'Chamados', icon: 'chamados' },
  { key: 'new', label: 'Novo', icon: 'novo' },
  { key: 'monitoring', label: 'Monitoramento', icon: 'monitoramento' },
  { key: 'noc', label: 'NOC', icon: 'noc' },
  { key: 'settings', label: 'Mais', icon: 'mais' },
];

// Estado do selo: sem problemas = sem selo (icone azul neutro); atencao = amarelo; critico = vermelho.
// O numero (apenas alertas abertos visiveis ao usuario) e o rotulo acessivel garantem que a cor nunca e o unico sinal.
export function TabBar({ active, onChange, monitoringBadge }: { active: TabKey; onChange: (tab: TabKey) => void; monitoringBadge?: MonitoringBadge }) {
  return (
    <View style={s.bar} accessibilityRole="tablist">
      {TABS.map((tab) => {
        const on = tab.key === active;
        const badge = tab.key === 'monitoring' && monitoringBadge && monitoringBadge.level !== 'none' ? monitoringBadge : null;
        const label = badge ? `${tab.label}, ${badge.count} alerta(s) aberto(s)${badge.level === 'critical' ? `, ${badge.critical} crítico(s)` : ''}` : tab.label;
        return (
          <Pressable key={tab.key} accessibilityRole="tab" accessibilityState={{ selected: on }} accessibilityLabel={label} onPress={() => onChange(tab.key)} style={[s.tab, on && s.tabOn]}>
            <View>
              <Icon name={tab.icon} size={26} color={on ? C.blue : C.muted} />
              {badge ? <View style={[s.badge, badge.level === 'critical' ? s.badgeCritical : s.badgeAttention]}><Text style={[s.badgeText, badge.level === 'attention' && s.badgeTextDark]}>{badge.count > 99 ? '99+' : badge.count}</Text></View> : null}
            </View>
            <Text numberOfLines={1} adjustsFontSizeToFit style={[s.label, on && s.labelOn]}>{tab.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const s = StyleSheet.create({
  bar: { flexDirection: 'row', backgroundColor: 'white', borderTopWidth: 1, borderTopColor: C.border, paddingHorizontal: 4, paddingTop: 6, paddingBottom: Platform.OS === 'ios' ? 16 : 8, gap: 2 },
  tab: { flex: 1, minHeight: 56, borderRadius: 16, alignItems: 'center', justifyContent: 'center', gap: 2, paddingHorizontal: 2 }, tabOn: { backgroundColor: C.blueSoft },
  label: { color: C.muted, fontSize: 11, fontWeight: '600' }, labelOn: { color: C.blue, fontWeight: '800' },
  badge: { position: 'absolute', top: -5, right: -10, minWidth: 18, height: 18, borderRadius: 9, paddingHorizontal: 4, alignItems: 'center', justifyContent: 'center', borderWidth: 1.5, borderColor: 'white' },
  badgeCritical: { backgroundColor: '#DC2626' }, badgeAttention: { backgroundColor: '#F59E0B' }, badgeText: { color: 'white', fontSize: 10, fontWeight: '800' }, badgeTextDark: { color: '#1F2937' },
});
