import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { AlertsScreen, useAlerts } from './alerts';
import { EnvironmentItem, EnvironmentsScreen, ServiceItem, ServicesScreen, useInventory } from './inventory';
import { MonitoringScreen, useDashboard } from './monitoring';
import { AlertFilter, InventoryFilter } from './monitoring-logic';
import { C } from './theme';

export type MonitoringSection = 'overview' | 'alerts' | 'environments' | 'services';
export type AssetFilter = { id: string; name: string } | null;
const SECTIONS: Array<{ key: MonitoringSection; label: string }> = [
  { key: 'overview', label: 'Visão geral' }, { key: 'alerts', label: 'Alertas' }, { key: 'environments', label: 'Ambientes' }, { key: 'services', label: 'Serviços' },
];

export function MonitoringHub({ section, setSection, dashboard, alerts, environments, services, serviceAsset, setServiceAsset, contextLabel, consolidated, openTicket }: {
  section: MonitoringSection; setSection: (section: MonitoringSection) => void; dashboard: ReturnType<typeof useDashboard>; alerts: ReturnType<typeof useAlerts>;
  environments: ReturnType<typeof useInventory<EnvironmentItem>>; services: ReturnType<typeof useInventory<ServiceItem>>;
  serviceAsset: AssetFilter; setServiceAsset: (asset: AssetFilter) => void; contextLabel: string; consolidated: boolean; openTicket: (id: string, tenantId?: string) => void;
}) {
  // Os indicadores e atalhos da Visao geral abrem a lista correspondente ja filtrada.
  const openAlerts = (filter: AlertFilter) => { alerts.setFilter(filter); setSection('alerts'); };
  const openServices = (filter: InventoryFilter, asset: AssetFilter = null) => { services.setFilter(filter); setServiceAsset(asset); setSection('services'); };
  const openEnvironments = () => { environments.setFilter('problems'); setSection('environments'); };
  return (
    <View style={{ flex: 1 }}>
      <View style={s.segment} accessibilityRole="tablist">
        {SECTIONS.map((item) => {
          const on = item.key === section;
          return <Pressable key={item.key} accessibilityRole="tab" accessibilityState={{ selected: on }} onPress={() => setSection(item.key)} style={[s.segmentItem, on && s.segmentOn]}><Text numberOfLines={1} adjustsFontSizeToFit style={[s.segmentText, on && s.segmentTextOn]}>{item.label}</Text></Pressable>;
        })}
      </View>
      {section === 'overview' ? <MonitoringScreen state={dashboard} contextLabel={contextLabel} consolidated={consolidated} onOpenAlerts={openAlerts} onOpenServices={(filter) => openServices(filter)} onOpenEnvironments={openEnvironments} />
        : section === 'alerts' ? <AlertsScreen state={alerts} contextLabel={contextLabel} consolidated={consolidated} openTicket={openTicket} />
        : section === 'environments' ? <EnvironmentsScreen state={environments} contextLabel={contextLabel} consolidated={consolidated} openServices={(asset) => openServices('all', asset)} />
        : <ServicesScreen state={services} contextLabel={contextLabel} consolidated={consolidated} asset={serviceAsset} clearAsset={() => setServiceAsset(null)} />}
    </View>
  );
}

const s = StyleSheet.create({
  segment: { flexDirection: 'row', margin: 12, marginBottom: 0, padding: 4, borderRadius: 14, backgroundColor: '#E2E8F0', gap: 4 },
  segmentItem: { flex: 1, minHeight: 44, borderRadius: 10, alignItems: 'center', justifyContent: 'center', paddingHorizontal: 2 }, segmentOn: { backgroundColor: 'white' },
  segmentText: { color: C.muted, fontWeight: '700', fontSize: 13 }, segmentTextOn: { color: C.blue, fontWeight: '800' },
});
