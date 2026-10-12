import React from 'react';
import Svg, { Circle, Ellipse, Path, Rect } from 'react-native-svg';

export type IconName = 'inicio' | 'chamados' | 'novo' | 'mais' | 'servidor' | 'nuvem' | 'empresa' | 'sair' | 'seta' | 'monitoramento' | 'noc';

// Icones do pacote Nexora_Mobile_Assets (viewBox 24x24, traco 1.8). "seta" e complementar.
export function Icon({ name, color = '#2563EB', size = 24 }: { name: IconName; color?: string; size?: number }) {
  const common = { stroke: color, strokeWidth: 1.8, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const, fill: 'none' };
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {name === 'inicio' && <Path {...common} d="m3 10 9-7 9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z" />}
      {name === 'chamados' && <Path {...common} d="M4 14v-3a8 8 0 0 1 16 0v3M4 14H2v6h5v-6H4M20 14h2v6h-5v-6h3M20 20c0 2-3 2-6 2" />}
      {name === 'novo' && <><Circle {...common} cx={12} cy={12} r={9} /><Path {...common} d="M12 7v10M7 12h10" /></>}
      {name === 'mais' && <><Circle {...common} cx={5} cy={12} r={1} /><Circle {...common} cx={12} cy={12} r={1} /><Circle {...common} cx={19} cy={12} r={1} /></>}
      {name === 'servidor' && <><Ellipse {...common} cx={12} cy={5} rx={8} ry={3} /><Path {...common} d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" /><Path {...common} d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>}
      {name === 'nuvem' && <Path {...common} d="M20 17a4 4 0 0 0-.7-7.9 7 7 0 0 0-13.5 1.4A4 4 0 0 0 7 18h12" />}
      {name === 'empresa' && <><Rect {...common} x={3} y={3} width={12} height={18} rx={1} /><Path {...common} d="M15 9h6v12H3M7 7h1M11 7h1M7 11h1M11 11h1M7 15h1M11 15h1M17 13h1M17 17h1" /></>}
      {name === 'sair' && <Path {...common} d="M10 17l5-5-5-5M15 12H3M12 3h6a3 3 0 0 1 3 3v12a3 3 0 0 1-3 3h-6" />}
      {name === 'seta' && <Path {...common} d="M5 12h14M13 6l6 6-6 6" />}
      {name === 'monitoramento' && <><Rect {...common} x={2} y={3} width={20} height={14} rx={2} /><Path {...common} d="M8 21h8M12 17v4" /><Path {...common} d="M5 10h3l2-3 3 6 2-3h4" /></>}
      {name === 'noc' && <><Rect {...common} x={3} y={4} width={7} height={7} rx={1.5} /><Rect {...common} x={14} y={4} width={7} height={7} rx={1.5} /><Rect {...common} x={3} y={14} width={7} height={7} rx={1.5} /><Path {...common} d="M14 17.5h3l1.5-2.5 2 5" /></>}
    </Svg>
  );
}
