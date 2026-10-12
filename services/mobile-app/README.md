# Nexora Mobile

Aplicativo React Native/Expo compartilhado entre Android e iOS.

## Funcionalidades iniciais

- servidor configurável (Oracle HTTPS ou IP local de desenvolvimento);
- login OIDC Authorization Code + PKCE;
- tokens no armazenamento seguro do sistema;
- seleção de Empresa respeitando as concessões do usuário;
- listagem, detalhe e criação de chamados;
- assumir atendimento, registrar interação e propor/finalizar solução;
- indicação explícita de sincronização pendente com o GLPI;
- limpeza de sessão e recuperação de configuração.

## Desenvolvimento

```powershell
npm install
npm run android
```

Para gerar os projetos nativos:

```powershell
npx expo prebuild
```

O APK pode ser compilado no Windows. A compilação e assinatura do IPA exigem macOS/Xcode ou EAS Build com uma conta Apple Developer.

O cliente OIDC `central-monitor` deve permitir o redirect URI `nexora://auth`. A configuração versionada do realm já contém esse endereço.
