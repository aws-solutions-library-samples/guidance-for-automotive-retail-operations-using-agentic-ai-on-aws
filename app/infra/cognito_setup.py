#!/usr/bin/env python3
"""Provision the Retail Ops staff Cognito user pool (dev) and seed test users.

Creates, in the dev account:
  * User pool   retail-ops-<env>-staff  (username = PLAT_Users.user_id; email attribute)
  * App client  retail-ops-<env>-staff-web  (USER_PASSWORD_AUTH enabled, no secret — so a
                simple InitiateAuth can mint an IdToken for testing)
  * Groups      all 19 staff groups of spec §5.2 / §7.2 (from the persona matrix
                in app/services/common/personas.py), each seed user added to the
                group PLAT_Users lists for it
  * Seed users  every staff persona in SEED_USERS (the demo's sign-ins), each with a
                permanent password, so the deployed API can be exercised with a real JWT.

The authorizer maps the token's username claim straight to PLAT_Users.user_id,
so role + org-scope resolution is identical to the dev token — this only swaps
HOW identity is proven (verified Cognito JWT vs. plaintext dev token).

Dry-run by default; --apply to create; --delete to tear down; --print-config to
emit the pool/client ids other tools need.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

try:
    from app.infra import naming
except ImportError:  # run as a script from app/infra/
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app.infra import naming

ROOT = Path(__file__).resolve().parents[2]
REGION = "us-east-1"
POOL_NAME = naming.name("staff")
CLIENT_NAME = naming.name("staff-web")
QUICK_MCP_SECRET = naming.name("quick-mcp-oauth")  # noqa: S105 - a Secrets Manager secret name, not a credential
CONFIG_PATH = naming.config_path("cognito")

# Seed users: (user_id == username, role label for reference).
SEED_USERS = [
    ("U-US-00002", "dealer_advisor"),
    ("U-US-00007", "dealer_service"),
    ("U-US-00044", "oem_dm"),
    ("U-US-00001", "dealer_manager"),
    ("U-US-00050", "oem_cx"),
    ("U-US-00051", "admin_tenant"),
    # Fixed Ops personas (generator phase gen_service_staff, D-101)
    ("U-US-00053", "dealer_technician"),
    ("U-US-00055", "dealer_parts"),
    ("U-US-00056", "dealer_service_manager"),
    # Every staff persona the demo scripts sign in as gets a password login, not just a
    # dev-token link (docs/DEMO_WORKFLOWS.md, docs/presentation persona guides)
    ("U-US-00005", "dealer_bdc"),
    ("U-US-00006", "dealer_fni"),
    ("U-US-00008", "dealer_inventory"),
    ("U-US-00009", "dealer_delivery"),
    ("U-US-00045", "oem_regional"),
    ("U-US-00046", "oem_ci_planning"),
    ("U-US-00048", "oem_finance"),
    ("U-US-00049", "oem_marketing"),
]
def default_domain_prefix(pool_id: str) -> str:
    """The Hosted-UI domain prefix for a pool: `retail-ops-<env>-<pool id suffix>`,
    lower-cased. Cognito refuses a prefix that contains `aws`, `amazon` or
    `cognito`; when the random pool suffix happens to spell one, a hex digest of
    the pool id (which cannot) takes its place."""
    import hashlib
    prefix = naming.name(pool_id.split("_")[-1].lower())
    if any(w in prefix for w in naming.RESERVED_WORDS):
        prefix = naming.name(hashlib.sha256(pool_id.encode()).hexdigest()[:10])
    return prefix


def _seed_password() -> str:
    """Return the operator-supplied password used only for seeded dev users."""
    password = os.environ.get("RETAIL_OPS_SEED_PASSWORD", "")
    if not password:
        raise RuntimeError(
            "RETAIL_OPS_SEED_PASSWORD is required with --apply; set it in the environment "
            "or in the git-ignored deploy/config.env"
        )
    return password


def _staff_groups() -> tuple[str, ...]:
    import sys
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.services.common.personas import STAFF_GROUPS
    return STAFF_GROUPS


def ensure_groups(c, pool_id: str, apply: bool) -> list[str]:
    """Create any missing §5.2 staff group (idempotent)."""
    groups = _staff_groups()
    existing: set[str] = set()
    if pool_id:
        kwargs = {"UserPoolId": pool_id, "Limit": 60}
        while True:
            resp = c.list_groups(**kwargs)
            existing |= {g["GroupName"] for g in resp.get("Groups", [])}
            if not resp.get("NextToken"):
                break
            kwargs["NextToken"] = resp["NextToken"]
    for g in groups:
        if g in existing:
            continue
        if apply:
            c.create_group(UserPoolId=pool_id, GroupName=g, Description=f"Retail Ops persona {g} (spec §5.2)")
            print(f"  [group] created {g}")
        else:
            print(f"  [group] would create {g}")
    return list(groups)


def _emails() -> dict[str, str]:
    users = json.loads((ROOT / "sample-data" / "synthetic" / "us" / "tables" / "PLAT_Users.json").read_text())
    return {u["user_id"]: u.get("email") for u in users}


def find_pool(c) -> str | None:
    kwargs = {"MaxResults": 60}
    while True:
        resp = c.list_user_pools(**kwargs)
        for p in resp["UserPools"]:
            if p["Name"] == POOL_NAME:
                return p["Id"]
        if "NextToken" not in resp:
            return None
        kwargs["NextToken"] = resp["NextToken"]


def find_client(c, pool_id: str) -> str | None:
    resp = c.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)
    for cl in resp["UserPoolClients"]:
        if cl["ClientName"] == CLIENT_NAME:
            return cl["ClientId"]
    return None


def _check_self_signup_disabled(c, pool_id: str) -> None:
    """Fail on a reused pool that still allows public self sign-up.

    Deliberately not auto-fixed here: UpdateUserPool resets every setting it is not
    given, so a blind update would wipe the pool's other configuration.
    """
    cfg = c.describe_user_pool(UserPoolId=pool_id)["UserPool"].get("AdminCreateUserConfig") or {}
    if not cfg.get("AllowAdminCreateUserOnly"):
        raise SystemExit(
            f"user pool {pool_id} allows self sign-up (AllowAdminCreateUserOnly=false). "
            "Disable it (Cognito console > Sign-up > Self-service sign-up) and re-run."
        )


def provision(c, apply: bool) -> dict:
    pool_id = find_pool(c)
    if not apply:
        print(f"  [pool] {'exists ' + pool_id if pool_id else 'would create ' + POOL_NAME}")
        print(f"  [client] {'check' if pool_id else 'would create'} {CLIENT_NAME}")
        ensure_groups(c, pool_id, apply=False)
        for uid, role in SEED_USERS:
            print(f"  [user] would seed {uid} ({role}) and add to group {role}")
        if _FEDERATE and pool_id:
            ensure_federate(c, pool_id, apply=False)
        elif _FEDERATE:
            print("  [federate] would wire Hosted UI + OIDC IdP after pool creation")
        return {}

    seed_password = _seed_password()
    if not pool_id:
        pool = c.create_user_pool(
            PoolName=POOL_NAME,
            UsernameAttributes=[],  # username is the literal user_id, not email
            AutoVerifiedAttributes=[],
            Policies={"PasswordPolicy": {"MinimumLength": 8, "RequireUppercase": True,
                                         "RequireLowercase": True, "RequireNumbers": True,
                                         "RequireSymbols": True}},
            Schema=[{"Name": "email", "AttributeDataType": "String", "Mutable": True, "Required": False}],
            # Staff accounts are provisioned by this script only; public self sign-up
            # stays off (AWS AppSec "Cognito User Pool Self-Registration Enabled").
            AdminCreateUserConfig={"AllowAdminCreateUserOnly": True},
        )
        pool_id = pool["UserPool"]["Id"]
        print(f"  [pool] created {pool_id} (self sign-up disabled)")
    else:
        print(f"  [pool] reusing {pool_id}")
        _check_self_signup_disabled(c, pool_id)

    client_id = find_client(c, pool_id)
    if not client_id:
        cl = c.create_user_pool_client(
            UserPoolId=pool_id, ClientName=CLIENT_NAME, GenerateSecret=False,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
            **TOKEN_VALIDITY,
        )
        client_id = cl["UserPoolClient"]["ClientId"]
        print(f"  [client] created {client_id} {_validity_note()}")
    else:
        # Re-assert the mandated lifetimes: a client created before this policy
        # existed would otherwise keep Cognito's 30-day refresh default.
        c.update_user_pool_client(
            UserPoolId=pool_id, ClientId=client_id, ClientName=CLIENT_NAME,
            ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
            **TOKEN_VALIDITY,
        )
        print(f"  [client] reusing {client_id} {_validity_note()}")

    ensure_groups(c, pool_id, apply=True)
    emails = _emails()
    for uid, role in SEED_USERS:
        try:
            c.admin_create_user(
                UserPoolId=pool_id, Username=uid, MessageAction="SUPPRESS",
                UserAttributes=[{"Name": "email", "Value": emails.get(uid) or f"{uid}@example.com"}],
            )
            print(f"  [user] created {uid} ({role})")
        except c.exceptions.UsernameExistsException:
            print(f"  [user] exists {uid} ({role})")
        c.admin_set_user_password(UserPoolId=pool_id, Username=uid,
                                  Password=seed_password, Permanent=True)
        c.admin_add_user_to_group(UserPoolId=pool_id, Username=uid, GroupName=role)

    cfg = {"region": REGION, "user_pool_id": pool_id, "client_id": client_id,
           "issuer": f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}",
           "seed_users": [u for u, _ in SEED_USERS]}
    if _FEDERATE:
        cfg["federate"] = ensure_federate(c, pool_id, apply)
    cfg["quick_mcp"] = ensure_quick_mcp_client(c, pool_id)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    print(f"  [config] wrote {CONFIG_PATH.relative_to(ROOT)}")
    return cfg


def ensure_quick_mcp_client(c, pool_id: str) -> dict:
    """Machine client for Amazon Quick's MCP connector (docs/QUICK_BUILD.md §3b).

    Quick authenticates to the CUSTOM_JWT AgentCore Gateway with an OAuth
    client-credentials token, so the pool needs a resource server (`retail-ops-oem`,
    scope `read`), a client with a secret allowed to request that scope, and a
    Hosted-UI domain to serve /oauth2/token. The secret is stored in Secrets
    Manager (`retail-ops-<env>-quick-mcp-oauth`) — never in this config file — and the
    client id is written to cognito-config.json for deploy_gateway_quick.py."""
    import boto3
    rs_id, scope = "retail-ops-oem", "read"
    try:
        c.describe_resource_server(UserPoolId=pool_id, Identifier=rs_id)
        print(f"  [quick-mcp] resource server {rs_id} exists")
    except c.exceptions.ResourceNotFoundException:
        c.create_resource_server(UserPoolId=pool_id, Identifier=rs_id, Name="Retail Ops OEM read tools",
                                 Scopes=[{"ScopeName": scope, "ScopeDescription": "Read OEM accounts/alerts/programs via the Gateway"}])
        print(f"  [quick-mcp] created resource server {rs_id} (scope {scope})")
    client_name = "retail-ops-quick-mcp"
    existing = next((x for x in c.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]
                     if x["ClientName"] == client_name), None)
    if existing:
        cid = existing["ClientId"]
        print(f"  [quick-mcp] client {client_name} exists ({cid})")
    else:
        cl = c.create_user_pool_client(
            UserPoolId=pool_id, ClientName=client_name, GenerateSecret=True,
            AllowedOAuthFlows=["client_credentials"], AllowedOAuthFlowsUserPoolClient=True,
            AllowedOAuthScopes=[f"{rs_id}/{scope}"], SupportedIdentityProviders=["COGNITO"],
            **TOKEN_VALIDITY)["UserPoolClient"]
        cid = cl["ClientId"]
        sm = boto3.client("secretsmanager", region_name=REGION)
        secret_name = QUICK_MCP_SECRET
        body = json.dumps({"client_id": cid, "client_secret": cl["ClientSecret"],
                           "scope": f"{rs_id}/{scope}"})
        try:
            sm.create_secret(Name=secret_name, SecretString=body, Description="Quick MCP connector OAuth client")
        except sm.exceptions.ResourceExistsException:
            sm.put_secret_value(SecretId=secret_name, SecretString=body)
        print(f"  [quick-mcp] created client {cid}; secret in Secrets Manager {secret_name}")
    # the token endpoint needs a Hosted-UI domain (the --federate path also creates one)
    domain = c.describe_user_pool(UserPoolId=pool_id)["UserPool"].get("Domain")
    if not domain:
        domain = default_domain_prefix(pool_id)
        try:
            c.create_user_pool_domain(Domain=domain, UserPoolId=pool_id)
            print(f"  [quick-mcp] created hosted-UI domain {domain} (serves /oauth2/token)")
        except Exception as e:
            print(f"  [quick-mcp] domain not created ({type(e).__name__}); token URL unavailable until a domain exists")
            domain = None
    return {"resource_server": rs_id, "scope": f"{rs_id}/{scope}", "client_id": cid,
            "client_name": client_name, "secret_name": QUICK_MCP_SECRET,
            "token_url": f"https://{domain}.auth.{REGION}.amazoncognito.com/oauth2/token" if domain else None,
            "discovery_url": f"https://cognito-idp.{REGION}.amazonaws.com/{pool_id}/.well-known/openid-configuration"}


# set by main() from --federate; keeps provision()'s signature stable.
_FEDERATE = False


# --- the enterprise IdP federated sign-in (plug-and-play, opt-in) --------
# Wiring only; nothing runs unless --federate is passed. the enterprise IdP is an
# internal OIDC IdP: its client id/secret come from a Federate registration and
# are read from Secrets Manager (RETAIL_OPS_IDP_SECRET) or env, never hard-coded.
# With --federate we: ensure a Hosted UI domain, register the OIDC IdP, add a
# Hosted-UI OAuth app client whose supported IdP includes Federate, and map
# federated users to PLAT_Users groups via the pre-token-generation contract.
DEFAULT_IDP_NAME = "CorpDirectory"


def _console_url() -> str | None:
    """The deployed console's origin, if `deploy_console` has run."""
    p = naming.config_path("console")
    if not p.exists():
        return None
    try:
        return (json.loads(p.read_text()).get("url") or "").rstrip("/") or None
    except (ValueError, OSError):
        return None
HOSTED_UI_CLIENT_NAME = naming.name("staff-hostedui")

# Mandated short token lifetimes for any Cognito app client fronting an Amazon
# identity provider (AWS/SDO Security requirement attested at IdP registration).
# Cognito's own defaults are far longer (refresh 30 days), so these are set
# explicitly on EVERY client on every run — a client created before this was
# added gets corrected rather than left non-compliant.
#   refresh token : 10 hours
#   access token  : 10 minutes
#   ID token      : 1 hour
TOKEN_VALIDITY = dict(
    RefreshTokenValidity=10,
    AccessTokenValidity=10,
    IdTokenValidity=60,
    TokenValidityUnits={"RefreshToken": "hours", "AccessToken": "minutes",
                        "IdToken": "minutes"},
)


def _validity_note() -> str:
    u = TOKEN_VALIDITY["TokenValidityUnits"]
    return (f"(refresh {TOKEN_VALIDITY['RefreshTokenValidity']}{u['RefreshToken'][0]}, "
            f"access {TOKEN_VALIDITY['AccessTokenValidity']}{u['AccessToken'][:3]}, "
            f"id {TOKEN_VALIDITY['IdTokenValidity']}{u['IdToken'][:3]})")


def _idp_config() -> dict:
    """IdP settings from Secrets Manager (RETAIL_OPS_IDP_SECRET) or RETAIL_OPS_IDP_*
    env vars.

    Provider-agnostic on purpose. The pilot signs in through Amazon's internal
    IdP (the enterprise OIDC/SAML provider) and that IdP is removed before any external release,
    so every provider-specific value — name, protocol, issuer, metadata URL —
    is configuration rather than code:

      RETAIL_OPS_IDP_NAME        provider name on the pool      (default CorpDirectory)
      RETAIL_OPS_IDP_PROTOCOL    "oidc" | "saml"                (default oidc)
      OIDC:  client_id, client_secret, oidc_issuer, authorize_scopes
      SAML:  metadata_url  (or metadata_file)
    """
    import os
    sec: dict = {}
    sec_id = os.environ.get("RETAIL_OPS_IDP_SECRET")
    if sec_id:
        try:
            import boto3
            raw = boto3.client("secretsmanager", region_name=REGION).get_secret_value(SecretId=sec_id)
            sec = json.loads(raw["SecretString"])
        except Exception as e:
            print(f"  [idp] could not read secret {sec_id}: {e}")
    env = {"client_id": os.environ.get("RETAIL_OPS_IDP_CLIENT_ID", ""),
           "client_secret": os.environ.get("RETAIL_OPS_IDP_CLIENT_SECRET", ""),
           "oidc_issuer": os.environ.get("RETAIL_OPS_IDP_ISSUER", ""),
           "authorize_scopes": os.environ.get("RETAIL_OPS_IDP_SCOPES", ""),
           "sub_is_alias": os.environ.get("RETAIL_OPS_IDP_SUB_IS_ALIAS", "") not in ("", "0", "false", "False"),
           "metadata_url": os.environ.get("RETAIL_OPS_IDP_METADATA_URL", ""),
           "metadata_file": os.environ.get("RETAIL_OPS_IDP_METADATA_FILE", ""),
           "name": os.environ.get("RETAIL_OPS_IDP_NAME", DEFAULT_IDP_NAME),
           "protocol": os.environ.get("RETAIL_OPS_IDP_PROTOCOL", "oidc").lower()}
    # secret wins where it defines a value; env fills the rest
    return {**env, **{k: v for k, v in sec.items() if v}}


def _idp_definition(creds: dict) -> tuple[str, str, dict, dict] | None:
    """(name, ProviderType, ProviderDetails, AttributeMapping) for the configured
    IdP, or None when the credentials/metadata needed are not present.

    Attribute mapping is what lets a federated identity resolve to a PLAT_Users
    row: `username` carries the directory's subject and `email` the address the
    authorizer falls back to (see api/authorizer.py)."""
    name = creds.get("name") or DEFAULT_IDP_NAME
    proto = (creds.get("protocol") or "oidc").lower()
    if proto == "saml":
        details = {}
        if creds.get("metadata_url"):
            details = {"MetadataURL": creds["metadata_url"]}
        elif creds.get("metadata_file"):
            try:
                details = {"MetadataFile": Path(creds["metadata_file"]).read_text()}
            except OSError as e:
                print(f"  [idp] cannot read SAML metadata file: {e}")
                return None
        if not details:
            return None
        # SAML assertions carry claims as URIs; map the two the authorizer uses.
        mapping = {
            "username": creds.get("saml_username_claim")
                        or "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/nameidentifier",
            "email": creds.get("saml_email_claim")
                     or "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress",
        }
        return name, "SAML", details, mapping
    if not (creds.get("client_id") and creds.get("client_secret")):
        return None
    issuer = creds.get("oidc_issuer") or ""
    sub_is_alias = bool(creds.get("sub_is_alias"))
    # Federate publishes only 'openid' in scopes_supported and returns no email
    # claim (its id tokens carry sub + groups + amr). Default to the smallest
    # scope set that actually works there; let callers override per IdP.
    default_scopes = "openid" if sub_is_alias else "openid profile email"
    details = {"client_id": creds["client_id"], "client_secret": creds["client_secret"],
               "oidc_issuer": issuer,
               "authorize_scopes": creds.get("authorize_scopes") or default_scopes,
               "attributes_request_method": "GET"}
    # the IdP's `sub` is the employee's alias (login), so send it to the
    # Cognito `preferred_username` attribute: the authorizer reads that claim
    # and matches it against PLAT_Users.username. Federate's discovery document
    # only advertises sub/groups/amr, but a service profile may additionally
    # enable an uppercase EMAIL claim (per the Federate-Cognito guide) — map it
    # too so the authorizer's email fallback works when the profile has it; an
    # absent claim simply leaves the attribute empty. Other OIDC IdPs use the
    # standard lowercase `email`.
    mapping = ({"username": "sub", "preferred_username": "sub", "email": "EMAIL"}
               if sub_is_alias else {"username": "sub", "email": "email"})
    return name, "OIDC", details, mapping


def idp_redirect_uri(domain_prefix: str) -> str:
    """The redirect URI to register *with the IdP*.

    Not the console's /auth/callback — that is the app client's callback, used on
    the browser->Cognito leg. On the Cognito->IdP leg, Cognito collects the
    authorization code at its own `/oauth2/idpresponse` endpoint, so this is the
    value the IdP must allowlist."""
    return f"https://{domain_prefix}.auth.{REGION}.amazoncognito.com/oauth2/idpresponse"


def ensure_federate(c, pool_id: str, apply: bool) -> dict:
    """Provision the SSO surface: Hosted UI domain + OAuth client + the federated
    IdP (OIDC or SAML) described by `_idp_config()`.

    Opt-in (only called under --federate). If the IdP's credentials/metadata are
    absent, everything else is still wired and the missing input is reported, so
    enabling later is a secret drop + re-run. `--remove-idp` detaches the IdP
    again without touching the pool — the path for external release."""
    import os
    creds = _idp_config()
    idp = _idp_definition(creds)
    domain_prefix = os.environ.get("RETAIL_OPS_HOSTED_UI_DOMAIN") or default_domain_prefix(pool_id)
    # Default the OAuth redirect to the deployed console when it exists, so SSO
    # works on the hosted site without extra env; localhost stays registered too
    # so `npm run dev` can complete the same code flow.
    console_url = _console_url()
    callbacks = [c for c in (
        os.environ.get("RETAIL_OPS_IDP_CALLBACK"),
        f"{console_url}/auth/callback" if console_url else None,
        "http://localhost:3100/auth/callback",
    ) if c]
    logouts = [c for c in (
        os.environ.get("RETAIL_OPS_IDP_LOGOUT"),
        f"{console_url}/login" if console_url else None,
        "http://localhost:3100/login",
    ) if c]
    callback, logout = callbacks[0], logouts[0]
    idp_name = (creds.get("name") or DEFAULT_IDP_NAME) if idp is None else idp[0]
    existing_idps = {p["ProviderName"] for p in
                     c.list_identity_providers(UserPoolId=pool_id, MaxResults=20).get("Providers", [])}
    out = {"idp": idp_name, "idp_protocol": (creds.get("protocol") or "oidc").lower(),
           "domain_prefix": domain_prefix,
           "callback_url": callback, "logout_url": logout,
           "callback_urls": callbacks, "logout_urls": logouts,
           "idp_redirect_uri": idp_redirect_uri(domain_prefix),
           "credentials_present": idp is not None,
           "provider_present": idp_name in existing_idps}
    if not apply:
        print(f"  [idp] would ensure Hosted UI domain {domain_prefix}, "
              f"{out['idp_protocol'].upper()} IdP {idp_name}, Hosted-UI OAuth client "
              f"(inputs present: {out['credentials_present']}; "
              f"existing provider: {out['provider_present']})")
        print(f"  [idp] register this redirect URI with the IdP: {out['idp_redirect_uri']}")
        return out

    # 1) Hosted UI domain (idempotent).
    try:
        c.create_user_pool_domain(Domain=domain_prefix, UserPoolId=pool_id)
        print(f"  [federate] created Hosted UI domain {domain_prefix}")
    except c.exceptions.InvalidParameterException as e:
        print(f"  [federate] domain note: {e}")
    except Exception as e:
        if "already" in str(e).lower() or "exists" in str(e).lower():
            print(f"  [federate] domain {domain_prefix} exists")
        else:
            print(f"  [federate] domain note: {e}")
    out["hosted_ui"] = f"https://{domain_prefix}.auth.{REGION}.amazoncognito.com"

    # 2) The federated IdP (OIDC or SAML). When credentials are absent on a
    # later redeploy, preserve a provider that is already registered; removal is
    # an explicit --remove-idp operation, never a side effect of a missing env var.
    active_idp = idp_name if idp_name in existing_idps else None
    if idp is not None:
        name, ptype, details, mapping = idp
        try:
            c.create_identity_provider(UserPoolId=pool_id, ProviderName=name,
                                       ProviderType=ptype, ProviderDetails=details,
                                       AttributeMapping=mapping)
            print(f"  [idp] created {ptype} IdP {name}")
        except c.exceptions.DuplicateProviderException:
            c.update_identity_provider(UserPoolId=pool_id, ProviderName=name,
                                       ProviderDetails=details, AttributeMapping=mapping)
            print(f"  [idp] updated {ptype} IdP {name}")
        active_idp = name
        out["provider_present"] = True
    elif active_idp:
        print(f"  [idp] preserving existing {out['idp_protocol'].upper()} IdP {active_idp} "
              "(credentials not supplied on this run)")
    else:
        proto = out["idp_protocol"]
        need = ("RETAIL_OPS_IDP_METADATA_URL (or _FILE)" if proto == "saml"
                else "RETAIL_OPS_IDP_SECRET, or RETAIL_OPS_IDP_CLIENT_ID + _CLIENT_SECRET")
        print(f"  [idp] {proto.upper()} IdP SKIPPED — needs {need}; re-run --federate once set")

    # 3) Hosted-UI OAuth app client (native form + SSO button share this pool).
    supported = ["COGNITO"] + ([active_idp] if active_idp else [])
    existing = find_client(c, pool_id) if False else None
    for cl in c.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]:
        if cl["ClientName"] == HOSTED_UI_CLIENT_NAME:
            existing = cl["ClientId"]
    client_args = dict(
        UserPoolId=pool_id, ClientName=HOSTED_UI_CLIENT_NAME, GenerateSecret=False,
        ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH", "ALLOW_REFRESH_TOKEN_AUTH"],
        AllowedOAuthFlows=["code"], AllowedOAuthScopes=["openid", "email", "profile"],
        AllowedOAuthFlowsUserPoolClient=True, CallbackURLs=callbacks, LogoutURLs=logouts,
        SupportedIdentityProviders=supported,
        **TOKEN_VALIDITY)
    if existing:
        # GenerateSecret is create-only; UpdateUserPoolClient rejects it.
        c.update_user_pool_client(
            ClientId=existing,
            **{k: v for k, v in client_args.items() if k != "GenerateSecret"})
        out["hosted_ui_client_id"] = existing
        print(f"  [idp] updated Hosted-UI client {existing} {_validity_note()}")
    else:
        r = c.create_user_pool_client(**client_args)
        out["hosted_ui_client_id"] = r["UserPoolClient"]["ClientId"]
        print(f"  [idp] created Hosted-UI client {out['hosted_ui_client_id']}")
    return out


def remove_idp(c, apply: bool, name: str | None = None) -> int:
    """Detach a federated IdP from the pool, leaving the pool, its users and the
    Hosted UI intact.

    This is the externalization step: the pilot authenticates against Amazon's
    internal directory, which must come off before any external release. Removing
    the IdP drops it from the Hosted-UI client's supported providers, so the SSO
    button disappears from the console on the next `deploy.sh --only console` and
    username/password sign-in keeps working throughout.
    """
    pool_id = find_pool(c)
    if not pool_id:
        print("  no pool found"); return 1
    existing = [p["ProviderName"] for p in
                c.list_identity_providers(UserPoolId=pool_id, MaxResults=20).get("Providers", [])]
    targets = [name] if name else existing
    targets = [t for t in targets if t in existing]
    if not targets:
        print(f"  no federated IdP to remove (pool has: {existing or 'none'})")
        return 0
    if not apply:
        print(f"  [dry-run] would remove IdP(s) {targets} from {pool_id} and drop them "
              f"from {HOSTED_UI_CLIENT_NAME}'s supported providers")
        return 0
    # 1) take them off the Hosted-UI client first, so no sign-in can be routed to
    #    a provider that is about to disappear
    for cl in c.list_user_pool_clients(UserPoolId=pool_id, MaxResults=60)["UserPoolClients"]:
        if cl["ClientName"] != HOSTED_UI_CLIENT_NAME:
            continue
        desc = c.describe_user_pool_client(UserPoolId=pool_id, ClientId=cl["ClientId"])["UserPoolClient"]
        keep = [p for p in desc.get("SupportedIdentityProviders", []) if p not in targets] or ["COGNITO"]
        c.update_user_pool_client(
            UserPoolId=pool_id, ClientId=cl["ClientId"], ClientName=desc["ClientName"],
            ExplicitAuthFlows=desc.get("ExplicitAuthFlows", []),
            AllowedOAuthFlows=desc.get("AllowedOAuthFlows", []),
            AllowedOAuthScopes=desc.get("AllowedOAuthScopes", []),
            AllowedOAuthFlowsUserPoolClient=desc.get("AllowedOAuthFlowsUserPoolClient", False),
            CallbackURLs=desc.get("CallbackURLs", []), LogoutURLs=desc.get("LogoutURLs", []),
            SupportedIdentityProviders=keep)
        print(f"  [idp] {HOSTED_UI_CLIENT_NAME} now supports {keep}")
    # 2) delete the providers
    for t in targets:
        c.delete_identity_provider(UserPoolId=pool_id, ProviderName=t)
        print(f"  [idp] removed {t}")
    # 3) drop the stale block from the written config
    if CONFIG_PATH.exists():
        cfg = json.loads(CONFIG_PATH.read_text())
        if cfg.pop("federate", None) is not None:
            CONFIG_PATH.write_text(json.dumps(cfg, indent=2) + "\n")
    print("  next: deploy/deploy.sh --apply --only console   (rebuilds without the SSO button)")
    return 0


def delete(c) -> None:
    pool_id = find_pool(c)
    if not pool_id:
        print("  no pool to delete"); return
    # tidy federate artifacts first (best-effort)
    try:
        c.delete_identity_provider(UserPoolId=pool_id, ProviderName=DEFAULT_IDP_NAME)
    except Exception:
        pass
    c.delete_user_pool(UserPoolId=pool_id)
    print(f"  deleted pool {pool_id}")
    if CONFIG_PATH.exists():
        CONFIG_PATH.unlink()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Provision the Retail Ops staff Cognito pool (dev).")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--delete", action="store_true")
    ap.add_argument("--print-config", action="store_true")
    ap.add_argument("--federate", action="store_true",
                    help="also wire the federated IdP (OIDC/SAML) + Hosted UI sign-in (opt-in). "
                         "Provider is config: RETAIL_OPS_IDP_NAME / RETAIL_OPS_IDP_PROTOCOL")
    ap.add_argument("--remove-idp", nargs="?", const="", metavar="NAME",
                    help="detach the federated IdP (all, or just NAME) and drop it from the "
                         "Hosted-UI client. Use before external release; keeps pool + users.")
    args = ap.parse_args(argv)
    global _FEDERATE
    _FEDERATE = args.federate

    import boto3
    c = boto3.client("cognito-idp", region_name=REGION)
    acct = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    print(f"account={acct} region={REGION} pool={POOL_NAME}")

    if args.print_config:
        print(CONFIG_PATH.read_text() if CONFIG_PATH.exists() else "(no config yet)")
        return 0
    if args.delete:
        delete(c); return 0
    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}]")
    if args.remove_idp is not None:
        return remove_idp(c, args.apply, args.remove_idp or None)
    cfg = provision(c, args.apply)
    if cfg:
        print(f"[APPLY] done. pool={cfg['user_pool_id']} client={cfg['client_id']}")
    elif not args.apply:
        print("  (re-run with --apply to create)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
