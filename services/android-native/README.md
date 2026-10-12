# Nexora Android

Aplicativo Android nativo que abre o cliente web do Nexora em uma WebView.

- O botão **Configurações** altera e persiste a URL do servidor.
- Nesta compilação de teste, o padrão é `http://192.168.3.22:8090/m/`, o IP Wi-Fi atual da máquina.
- Em emulador Android, configure `http://10.0.2.2:8090/m/`.
- Em aparelho físico, se o IP mudar, configure `http://IP-DA-MAQUINA:8090/m/` e mantenha ambos na mesma rede.
- Quando a Oracle estiver disponível, substitua a URL pela URL HTTPS de produção.

HTTP sem TLS está liberado nesta versão exclusivamente para o teste local. Em produção, use HTTPS e restrinja tráfego em texto claro.
