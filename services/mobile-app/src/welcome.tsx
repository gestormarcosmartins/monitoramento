import React from 'react';
import { Pressable, SafeAreaView, ScrollView, StyleSheet, Text, View } from 'react-native';
import { StatusBar } from 'expo-status-bar';
import { Brand } from './brand';
import { Icon } from './icons';
import { C } from './theme';

export type ServerStatus = { state: 'checking' | 'ok' | 'fail'; at?: Date };
type Tenant = { id: string; name: string; role: string };

const hhmm = (date?: Date) => (date ? date.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }) : '—');

export function Welcome({ name, server, status, tenants, tenantId, onSelect, onEnter, onLogout, onRecheck, version }: {
  name: string; server: string; status: ServerStatus; tenants: Tenant[]; tenantId: string;
  onSelect: (id: string) => void; onEnter: () => void; onLogout: () => void; onRecheck: () => void; version: string;
}) {
  const ok = status.state === 'ok'; const fail = status.state === 'fail';
  const options: Tenant[] = tenants.length > 1 ? [{ id: 'all', name: 'Todas as empresas', role: `${tenants.length} empresas · leitura consolidada` }, ...tenants] : tenants;
  return (
    <SafeAreaView style={s.app}>
      <StatusBar style="light" />
      <ScrollView contentContainerStyle={s.scroll} bounces={false}>
        <View style={s.hero}>
          <Brand />
          <Text style={s.eyebrow}>BEM-VINDO</Text>
          <Text style={s.name} numberOfLines={1}>{name}</Text>
          <Text style={s.sub}>Selecione a empresa para continuar</Text>
        </View>

        <View style={s.cards}>
          <View style={s.card}>
            <View style={s.row}>
              <View style={s.iconBox}><Icon name="servidor" size={30} /></View>
              <View style={s.grow}>
                <Text style={s.kicker}>SERVIDOR</Text>
                <Text style={s.title}>Oracle Cloud</Text>
                <Text style={s.muted} numberOfLines={1}>{server}</Text>
              </View>
            </View>
            <View style={s.pill}><View style={s.dot} /><Text style={s.pillText}>Conexão fixa</Text></View>
            <View style={s.divider} />
            <Pressable onPress={onRecheck} accessibilityRole="button" accessibilityLabel="Verificar servidor novamente" style={s.row}>
              <Icon name="nuvem" size={30} color={fail ? C.danger : C.blue} />
              <View style={s.sep} />
              <View style={s.grow}>
                <Text style={[s.status, fail && { color: C.danger }]}>{status.state === 'checking' ? 'Verificando servidor…' : ok ? 'Servidor conectado e operacional' : 'Servidor indisponível'}</Text>
                <Text style={s.muted}>{status.state === 'checking' ? 'Aguarde' : `Última verificação: ${hhmm(status.at)} · toque para verificar`}</Text>
              </View>
            </Pressable>
          </View>

          <View style={s.card}>
            <View style={s.row}>
              <View style={s.iconBox}><Icon name="empresa" size={30} /></View>
              <View style={s.grow}>
                <Text style={s.kicker}>EMPRESA ATIVA</Text>
                <Text style={s.title}>Selecione a empresa</Text>
                <Text style={s.muted}>Escolha a empresa que deseja acessar</Text>
              </View>
            </View>
            <View style={s.list} accessibilityRole="radiogroup">
              {options.map((t) => {
                const on = t.id === tenantId;
                return (
                  <Pressable key={t.id} accessibilityRole="radio" accessibilityState={{ selected: on }} onPress={() => onSelect(t.id)} style={[s.tenant, on && s.tenantOn]}>
                    <View style={[s.tIcon, on && s.tIconOn]}><Icon name="empresa" size={26} color={on ? C.blue : C.muted} /></View>
                    <View style={s.grow}><Text style={s.tName} numberOfLines={1}>{t.name}</Text><Text style={s.muted}>{t.role}</Text></View>
                    <View style={[s.radio, on && s.radioOn]}>{on && <View style={s.radioDot} />}</View>
                  </Pressable>
                );
              })}
              {!tenants.length && <Text style={s.muted}>Nenhuma empresa autorizada para este usuário.</Text>}
            </View>
            <Pressable accessibilityRole="button" onPress={onLogout} style={s.secondary}><Icon name="sair" size={22} color={C.text} /><Text style={s.secondaryText}>Sair da conta</Text></Pressable>
            <Pressable accessibilityRole="button" disabled={!tenantId} onPress={onEnter} style={[s.primary, !tenantId && { opacity: 0.5 }]}><Text style={s.primaryText}>Acessar Nexora</Text><Icon name="seta" size={24} color="white" /></Pressable>
          </View>

          <Text style={s.version}>{version}</Text>
          <Text style={s.slogan}>Monitore. Antecipe. Resolva.</Text>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const s = StyleSheet.create({
  app: { flex: 1, backgroundColor: C.bg }, scroll: { flexGrow: 1 },
  hero: { backgroundColor: C.navyDeep, paddingHorizontal: 22, paddingTop: 34, paddingBottom: 70, gap: 4 },
  eyebrow: { color: C.onNavyMuted, letterSpacing: 3, fontSize: 13, marginTop: 22 }, name: { color: 'white', fontSize: 28, fontWeight: '800' }, sub: { color: C.onNavyMuted, fontSize: 16 },
  cards: { marginTop: -48, paddingHorizontal: 14, gap: 14, paddingBottom: 22 },
  card: { backgroundColor: C.card, borderRadius: 22, padding: 16, borderWidth: 1, borderColor: C.border, gap: 14, shadowColor: '#0B2A55', shadowOpacity: 0.08, shadowRadius: 14, shadowOffset: { width: 0, height: 6 }, elevation: 3 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 14 }, grow: { flex: 1 },
  iconBox: { width: 62, height: 62, borderRadius: 16, backgroundColor: C.blueSoft, alignItems: 'center', justifyContent: 'center' },
  kicker: { color: C.muted, fontSize: 12, letterSpacing: 1.4, fontWeight: '700' }, title: { color: C.text, fontSize: 21, fontWeight: '800' }, muted: { color: C.muted, fontSize: 14, lineHeight: 20 },
  pill: { flexDirection: 'row', alignItems: 'center', gap: 7, backgroundColor: '#0F3326', borderRadius: 999, paddingHorizontal: 12, paddingVertical: 8, alignSelf: 'flex-start' }, dot: { width: 10, height: 10, borderRadius: 5, backgroundColor: '#16A34A' }, pillText: { color: '#86EFAC', fontWeight: '700', fontSize: 13 },
  divider: { height: 1, backgroundColor: C.border }, sep: { width: 1, height: 38, backgroundColor: C.border }, status: { color: C.text, fontSize: 16, fontWeight: '600' },
  list: { gap: 10 }, tenant: { flexDirection: 'row', alignItems: 'center', gap: 12, borderWidth: 1, borderColor: C.border, borderRadius: 16, padding: 12, minHeight: 64, backgroundColor: C.card }, tenantOn: { borderColor: C.blue, backgroundColor: C.blueSoft },
  tIcon: { width: 48, height: 48, borderRadius: 12, backgroundColor: '#16335C', alignItems: 'center', justifyContent: 'center' }, tIconOn: { backgroundColor: '#12305C' }, tName: { color: C.text, fontSize: 18, fontWeight: '800' },
  radio: { width: 28, height: 28, borderRadius: 14, borderWidth: 2, borderColor: C.border, alignItems: 'center', justifyContent: 'center' }, radioOn: { borderColor: C.blue }, radioDot: { width: 14, height: 14, borderRadius: 7, backgroundColor: C.blue },
  secondary: { minHeight: 52, borderRadius: 14, borderWidth: 1, borderColor: C.border, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 10, backgroundColor: C.card }, secondaryText: { color: C.text, fontWeight: '700', fontSize: 16 },
  primary: { minHeight: 56, borderRadius: 14, backgroundColor: C.blueStrong, alignItems: 'center', justifyContent: 'center', flexDirection: 'row', gap: 12 }, primaryText: { color: 'white', fontWeight: '800', fontSize: 18 },
  version: { textAlign: 'center', color: C.muted, fontSize: 14, marginTop: 4 }, slogan: { textAlign: 'center', color: '#9DB2CE', fontSize: 14 },
});
