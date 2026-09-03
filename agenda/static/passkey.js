/* Chaves de acesso (Face ID, Touch ID, Windows Hello).
 *
 * O navegador faz o trabalho pesado: a chave privada nasce e morre dentro do
 * aparelho, e a biometria só destrava o aparelho — nada disso passa por aqui
 * nem chega ao servidor. Este arquivo só converte base64url ↔ ArrayBuffer,
 * que é a parte chata da API do WebAuthn.
 */
(function () {
  'use strict';

  const suportado = () =>
    window.PublicKeyCredential !== undefined &&
    typeof navigator.credentials?.create === 'function';

  const b64d = (s) => {
    const base = s.replace(/-/g, '+').replace(/_/g, '/');
    const bin = atob(base + '='.repeat((4 - (base.length % 4)) % 4));
    return Uint8Array.from(bin, (c) => c.charCodeAt(0));
  };

  const b64e = (buffer) => {
    const bytes = new Uint8Array(buffer);
    let bin = '';
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  };

  const csrf = () =>
    document.querySelector('meta[name="csrf-token"]')?.content || '';

  async function post(url, body) {
    const r = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf() },
      body: JSON.stringify(body || {}),
    });
    return { ok: r.ok, dados: await r.json().catch(() => ({})) };
  }

  function paraNavegador(opcoes) {
    const o = { ...opcoes, challenge: b64d(opcoes.challenge) };
    if (o.user) o.user = { ...o.user, id: b64d(o.user.id) };
    for (const lista of ['excludeCredentials', 'allowCredentials']) {
      if (Array.isArray(o[lista])) {
        o[lista] = o[lista].map((c) => ({ ...c, id: b64d(c.id) }));
      }
    }
    return o;
  }

  function paraServidor(cred) {
    const r = cred.response;
    const saida = {
      id: cred.id,
      rawId: b64e(cred.rawId),
      type: cred.type,
      clientExtensionResults: cred.getClientExtensionResults?.() || {},
      response: { clientDataJSON: b64e(r.clientDataJSON) },
    };
    if (r.attestationObject) {
      saida.response.attestationObject = b64e(r.attestationObject);
      if (r.getTransports) saida.response.transports = r.getTransports();
    } else {
      saida.response.authenticatorData = b64e(r.authenticatorData);
      saida.response.signature = b64e(r.signature);
      if (r.userHandle) saida.response.userHandle = b64e(r.userHandle);
    }
    return saida;
  }

  function avisar(texto) {
    if (typeof window.toast === 'function') window.toast(texto);
    else alert(texto);
  }

  // --- Cadastrar uma chave (dentro da conta) ------------------------------ //
  async function cadastrar(botao) {
    if (!suportado()) {
      avisar('Este navegador não trabalha com chave de acesso.');
      return;
    }
    botao.disabled = true;
    try {
      const inicio = await post('/api/passkey/cadastro/opcoes');
      if (!inicio.ok) throw new Error(inicio.dados.erro || 'falhou');

      const cred = await navigator.credentials.create({
        publicKey: paraNavegador(inicio.dados.options),
      });
      if (!cred) throw new Error('cancelado');

      const fim = await post('/api/passkey/cadastro', {
        credential: paraServidor(cred),
        label: navigator.platform || 'Este aparelho',
      });
      if (!fim.ok) throw new Error(fim.dados.erro || 'falhou');
      avisar('Pronto. Da próxima vez é só o rosto ou a digital.');
      setTimeout(() => window.location.reload(), 900);
    } catch (e) {
      // NotAllowedError = a pessoa cancelou. Não é erro, é escolha.
      if (e.name !== 'NotAllowedError' && e.message !== 'cancelado') {
        avisar('Não consegui cadastrar essa chave.');
      }
    } finally {
      botao.disabled = false;
    }
  }

  // --- Entrar com a chave (tela de login) --------------------------------- //
  async function entrar(botao) {
    if (!suportado()) {
      avisar('Este navegador não trabalha com chave de acesso.');
      return;
    }
    botao.disabled = true;
    try {
      const inicio = await post('/api/passkey/login/opcoes');
      if (!inicio.ok) throw new Error('falhou');

      const cred = await navigator.credentials.get({
        publicKey: paraNavegador(inicio.dados.options),
      });
      if (!cred) throw new Error('cancelado');

      const fim = await post('/api/passkey/login', {
        credential: paraServidor(cred),
        next: new URLSearchParams(window.location.search).get('next') || '',
      });
      if (!fim.ok) throw new Error(fim.dados.erro || 'falhou');
      window.location.href = fim.dados.redirect || '/hoje';
    } catch (e) {
      if (e.name !== 'NotAllowedError' && e.message !== 'cancelado') {
        avisar('Não consegui entrar com essa chave.');
      }
    } finally {
      botao.disabled = false;
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    // O botão só aparece se o aparelho realmente suportar — botão que não
    // funciona é pior que botão nenhum.
    if (!suportado()) {
      document.querySelectorAll('[data-passkey]').forEach((el) => el.remove());
      return;
    }
    document.querySelectorAll('[data-passkey="cadastrar"]').forEach((b) =>
      b.addEventListener('click', () => cadastrar(b))
    );
    document.querySelectorAll('[data-passkey="entrar"]').forEach((b) =>
      b.addEventListener('click', () => entrar(b))
    );
  });
})();
