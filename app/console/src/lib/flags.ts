// Plug-and-play feature flags. OFF by default — a release with these unset
// behaves exactly as before (no Dashboards screen, no Federate button).

export const quickEnabled = (): boolean =>
  process.env.NEXT_PUBLIC_ENABLE_QUICK === "true";

/** Human label for an IdP name, for the sign-in button. */
const IDP_LABELS: Record<string, string> = {
  CorpDirectory: "Corporate Directory",
};
export const idpLabel = (idp: string): string => IDP_LABELS[idp] || idp;

/** The EXTERNAL identity providers registered on the pool, e.g. ["CorpDirectory"].
 *
 *  This is what gates the SSO button, and it deliberately excludes Cognito's own
 *  user directory: with no external IdP the Hosted UI just renders its own
 *  username/password form, so an "SSO" button would send the user to a second
 *  password prompt for the same credentials they already have a form for. Real
 *  SSO means an external directory, so no external IdP => no button. */
export function ssoProviders(): string[] {
  return (process.env.NEXT_PUBLIC_SSO_IDPS || "")
    .split(",").map((s) => s.trim())
    .filter((s) => s && s.toUpperCase() !== "COGNITO");
}

/** True when a real external identity provider is wired behind the Hosted UI. */
export const ssoEnabled = (): boolean =>
  Boolean(process.env.NEXT_PUBLIC_COGNITO_HOSTED_UI) && ssoProviders().length > 0;

/** Hosted-UI authorize URL (OAuth code flow) pinned to one external identity
 *  provider, so the user lands on that directory's sign-in rather than on
 *  Cognito's own form. Returns null when SSO is not configured. */
export function ssoLoginUrl(idp: string): string | null {
  const base = process.env.NEXT_PUBLIC_COGNITO_HOSTED_UI;
  const clientId = process.env.NEXT_PUBLIC_COGNITO_HOSTED_UI_CLIENT_ID
    || process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID;
  if (!base || !clientId || !idp) return null;
  const redirect = typeof window !== "undefined"
    ? `${window.location.origin}/auth/callback`
    : "http://localhost:3100/auth/callback";
  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    scope: "openid email profile",
    redirect_uri: redirect,
  });
  params.set("identity_provider", idp);
  return `${base.replace(/\/$/, "")}/oauth2/authorize?${params.toString()}`;
}

/** The SSO buttons to render on the sign-in screen — one per external IdP.
 *  Empty when no external directory is wired, which is the signal to show only
 *  the username/password form. */
export function ssoOptions(): { idp: string; label: string; url: string }[] {
  return ssoProviders().flatMap((idp) => {
    const url = ssoLoginUrl(idp);
    return url ? [{ idp, label: idpLabel(idp), url }] : [];
  });
}
