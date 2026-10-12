import React from 'react';
import { Image, StyleSheet, Text, View } from 'react-native';
import { C } from './theme';

// O simbolo e PROVISORIO (ver LEIA-ME do pacote de assets): substituir pelo ativo oficial aprovado.
const symbol = require('../assets/nexora-symbol-provisorio.png');

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <View style={s.row}>
      <Image source={symbol} style={compact ? s.symbolSmall : s.symbol} resizeMode="contain" accessibilityLabel="Nexora" />
      <View>
        <Text style={[s.word, compact && s.wordSmall]}>NEXORA</Text>
        {!compact && <Text style={s.tag}>Operação inteligente em escala</Text>}
      </View>
    </View>
  );
}

const s = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  symbol: { width: 52, height: 46, borderRadius: 8 }, symbolSmall: { width: 44, height: 39, borderRadius: 7 },
  word: { color: 'white', fontSize: 30, letterSpacing: 3, fontWeight: '300' }, wordSmall: { fontSize: 24, letterSpacing: 2.5 },
  tag: { color: C.onNavyMuted, fontSize: 12, marginTop: -2 },
});
