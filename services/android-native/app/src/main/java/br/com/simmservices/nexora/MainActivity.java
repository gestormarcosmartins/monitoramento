package br.com.simmservices.nexora;

import android.app.Activity;
import android.app.AlertDialog;
import android.graphics.Color;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.webkit.CookieManager;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;

import java.net.URI;

public class MainActivity extends Activity {
    private static final String PREFS = "nexora_settings";
    private static final String SERVER_URL = "server_url";
    private static final String DEFAULT_URL = "http://192.168.3.22:8090/m/";

    private WebView webView;
    private TextView serverLabel;
    private ProgressBar progress;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        buildScreen();
        configureWebView();
        if (savedInstanceState == null) loadConfiguredServer();
        else webView.restoreState(savedInstanceState);
    }

    private int dp(int value) {
        return Math.round(value * getResources().getDisplayMetrics().density);
    }

    private void buildScreen() {
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.rgb(248, 250, 252));

        LinearLayout bar = new LinearLayout(this);
        bar.setGravity(Gravity.CENTER_VERTICAL);
        bar.setPadding(dp(16), dp(8), dp(8), dp(8));
        bar.setBackgroundColor(Color.rgb(15, 23, 42));

        LinearLayout titles = new LinearLayout(this);
        titles.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText("Nexora");
        title.setTextColor(Color.WHITE);
        title.setTextSize(19);
        title.setTypeface(null, 1);
        serverLabel = new TextView(this);
        serverLabel.setTextColor(Color.rgb(148, 163, 184));
        serverLabel.setTextSize(11);
        titles.addView(title);
        titles.addView(serverLabel);
        bar.addView(titles, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));

        Button refresh = new Button(this);
        refresh.setText("Atualizar");
        refresh.setOnClickListener(v -> webView.reload());
        bar.addView(refresh);

        Button settings = new Button(this);
        settings.setText("Configurações");
        settings.setOnClickListener(v -> showSettings());
        bar.addView(settings);

        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setMax(100);
        progress.setVisibility(View.GONE);

        webView = new WebView(this);
        root.addView(bar, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));
        root.addView(progress, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(3)));
        root.addView(webView, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, 0, 1));
        setContentView(root);
    }

    private void configureWebView() {
        webView.getSettings().setJavaScriptEnabled(true);
        webView.getSettings().setDomStorageEnabled(true);
        webView.getSettings().setDatabaseEnabled(true);
        webView.getSettings().setAllowFileAccess(false);
        webView.getSettings().setAllowContentAccess(false);
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true);
        webView.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView view, int newProgress) {
                progress.setProgress(newProgress);
                progress.setVisibility(newProgress < 100 ? View.VISIBLE : View.GONE);
            }
        });
        webView.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return false;
            }
            @Override public void onReceivedError(WebView view, WebResourceRequest request, android.webkit.WebResourceError error) {
                if (request.isForMainFrame()) Toast.makeText(MainActivity.this,
                    "Não foi possível acessar o servidor. Confira a URL em Configurações.", Toast.LENGTH_LONG).show();
            }
        });
    }

    private String configuredUrl() {
        return getSharedPreferences(PREFS, MODE_PRIVATE).getString(SERVER_URL, DEFAULT_URL);
    }

    private void loadConfiguredServer() {
        String url = configuredUrl();
        serverLabel.setText(url);
        webView.loadUrl(url);
    }

    private boolean validServerUrl(String value) {
        try {
            URI uri = URI.create(value);
            return ("http".equalsIgnoreCase(uri.getScheme()) || "https".equalsIgnoreCase(uri.getScheme()))
                && uri.getHost() != null;
        } catch (RuntimeException ex) {
            return false;
        }
    }

    private void showSettings() {
        final EditText input = new EditText(this);
        input.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        input.setSingleLine(true);
        input.setText(configuredUrl());
        input.setSelectAllOnFocus(true);
        int pad = dp(20);
        LinearLayout holder = new LinearLayout(this);
        holder.setPadding(pad, 0, pad, 0);
        holder.addView(input, new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT));

        AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("Configurações do servidor")
            .setMessage("Informe a URL completa do Nexora. No celular, use o IP da máquina na mesma rede, por exemplo: http://192.168.1.20:8090/m/")
            .setView(holder)
            .setNegativeButton("Cancelar", null)
            .setPositiveButton("Salvar", null)
            .create();
        dialog.setOnShowListener(v -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(button -> {
            String url = input.getText().toString().trim();
            if (!url.endsWith("/")) url += "/";
            if (!validServerUrl(url)) {
                input.setError("Use uma URL HTTP ou HTTPS válida");
                return;
            }
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(SERVER_URL, url).apply();
            dialog.dismiss();
            loadConfiguredServer();
        }));
        dialog.show();
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        webView.saveState(outState);
        super.onSaveInstanceState(outState);
    }
}
