// Inicio de sesión con Microsoft Entra ID (login corporativo).
//
// Este módulo solo se carga cuando la API informa AUTH_MODO=entra. Usa el flujo
// de código de autorización con PKCE de MSAL: redirige a la página de inicio de
// sesión de la organización y, al volver, entrega una función que obtiene el
// token de acceso para llamar a la API (renovándolo en silencio cuando vence).

const MSAL = 'https://cdn.jsdelivr.net/npm/@azure/msal-browser@3.30.0/+esm';

/**
 * @param {{tenant_id: string, cliente_web: string, alcance: string}} config
 *        Datos públicos del registro de la aplicación, entregados por /api/salud.
 * @returns {Promise<() => Promise<string>>} función que devuelve un token vigente
 */
export async function iniciarSesion(config) {
  const { PublicClientApplication, InteractionRequiredAuthError } = await import(MSAL);
  const aplicacion = new PublicClientApplication({
    auth: {
      clientId: config.cliente_web,
      authority: `https://login.microsoftonline.com/${config.tenant_id}`,
      redirectUri: location.origin + location.pathname,
    },
    cache: { cacheLocation: 'sessionStorage' },
  });
  await aplicacion.initialize();
  const solicitud = { scopes: [config.alcance] };

  const retorno = await aplicacion.handleRedirectPromise();
  const cuenta = (retorno && retorno.account) || aplicacion.getAllAccounts()[0];
  if (!cuenta) {
    await aplicacion.loginRedirect(solicitud);
    return new Promise(() => {});          // la página está saliendo hacia el inicio de sesión
  }
  aplicacion.setActiveAccount(cuenta);

  return async function obtenerToken() {
    try {
      const resultado = await aplicacion.acquireTokenSilent({ ...solicitud, account: cuenta });
      return resultado.accessToken;
    } catch (error) {
      if (error instanceof InteractionRequiredAuthError) {
        await aplicacion.acquireTokenRedirect(solicitud);
        return new Promise(() => {});
      }
      throw error;
    }
  };
}
