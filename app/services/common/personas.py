"""§5.2 — Persona matrix: role → home screen → screens it may open.

The single source for "every persona has a home" (acceptance #17) and for the
role-aware link resolver (§5.18.2). Screens are named by their console route
pattern; `{id}` segments are filled by the resolver. The console reads it through
the API (GET /me/home, GET /nav/resolve) rather than keeping a second copy.
"""
from __future__ import annotations

# Screen ids -> route templates (the app/screen inventory of §5.2.1, §5.9, §5.12,
# §5.16, §5.17, §5.20). Quick-embedded OEM views are routed through the OEM shell.
SCREENS: dict[str, str] = {
    # Dealer Console
    "dealer.cockpit": "/dealer/cockpit",
    "dealer.journeys": "/dealer/journeys",
    "dealer.journey": "/dealer/journeys?focus={id}",
    "dealer.discovery": "/dealer/discovery",
    "dealer.advisor": "/dealer/advisor/{id}",
    "dealer.customer360": "/dealer/customers/{id}",
    "dealer.assistant": "/dealer/assistant",
    "dealer.leads": "/dealer/leads",
    "dealer.service_lane": "/dealer/assistant/service",
    "dealer.fni": "/dealer/fni",
    "dealer.fni_deal": "/dealer/fni/{id}",
    "dealer.trade_in": "/dealer/trade-in",
    "dealer.inventory": "/dealer/inventory",
    "dealer.inventory_vin": "/dealer/inventory?vin={id}",
    "dealer.delivery": "/dealer/delivery",
    "dealer.campaigns": "/dealer/campaigns",
    "dealer.tasks": "/tasks",
    "dealer.task": "/tasks?focus={id}",
    # Service Desk (§5.20)
    "service.board": "/dealer/service",
    "service.my_ros": "/dealer/service/my-ros",
    "service.parts": "/dealer/service/parts",
    "service.cockpit": "/dealer/service/cockpit",
    "service.journey": "/dealer/service/{id}",
    "service.estimate": "/dealer/service/{id}/estimate",
    "service.inspect": "/dealer/service/{id}/inspect",
    "service.history": "/dealer/service/history/{id}",
    "service.book": "/dealer/service/book",
    "service.checkin": "/dealer/service/{id}/checkin",
    # OEM Workspace (Next.js shell; view content may be Quick-embedded)
    "oem.today": "/oem",
    "oem.region": "/oem/region",
    "oem.accounts": "/oem/accounts",
    "oem.account": "/oem/accounts/{id}",
    "oem.alerts": "/oem/alerts",
    "oem.alert": "/oem/alerts?focus={id}",
    "oem.scenario": "/oem/scenario",
    "oem.comms": "/oem/comms",
    "oem.reports": "/oem/reports",
    "oem.plan": "/oem/accounts/{id}",
    # Marketing Studio
    "marketing.campaigns": "/marketing/campaigns",
    "marketing.campaign": "/marketing/campaigns?focus={id}",
    "marketing.audiences": "/marketing/audiences",
    "marketing.journey_ops": "/marketing/journey-ops",
    # Admin
    "admin.home": "/admin",
    "admin.approvals": "/admin/approvals",
    "notifications": "/notifications",
    # Customer Web
    "customer.garage": "/garage",
    "customer.garage_service": "/garage/service/{id}",
    "customer.shop": "/shop",
    "customer.chat": "/shop/chat",
    # Shared
    "approvals": "/approvals",
}

_DEALER_ALL = frozenset(k for k in SCREENS if k.startswith(("dealer.", "service.")))
_OEM_ALL = frozenset(k for k in SCREENS if k.startswith("oem."))

# role -> (home screen, frozenset of screens it may open). Mirrors the §5.2 table.
PERSONAS: dict[str, tuple[str, frozenset[str]]] = {
    "dealer_advisor": ("dealer.journeys", frozenset({
        "dealer.journeys", "dealer.journey", "dealer.advisor", "dealer.discovery",
        "dealer.trade_in", "dealer.assistant", "dealer.customer360", "dealer.tasks",
        "dealer.task", "dealer.inventory_vin", "dealer.service_lane", "dealer.leads"})),
    "dealer_bdc": ("dealer.leads", frozenset({
        "dealer.leads", "dealer.assistant", "dealer.journeys", "dealer.journey",
        "dealer.advisor", "dealer.customer360", "dealer.tasks", "dealer.task"})),
    "dealer_fni": ("dealer.fni", frozenset({
        "dealer.fni", "dealer.fni_deal", "dealer.journeys", "dealer.journey",
        "dealer.delivery", "dealer.tasks", "dealer.task"})),
    "dealer_service": ("service.board", frozenset({
        "service.board", "service.journey", "service.estimate", "service.history",
        "service.book", "service.checkin", "dealer.service_lane", "dealer.trade_in", "dealer.assistant", "dealer.tasks",
        "dealer.task"})),
    "dealer_technician": ("service.my_ros", frozenset({
        "service.my_ros", "service.board", "service.journey", "service.inspect",
        "dealer.tasks", "dealer.task"})),
    "dealer_parts": ("service.parts", frozenset({
        "service.parts", "service.board", "service.journey", "dealer.inventory",
        "dealer.tasks", "dealer.task"})),
    "dealer_service_manager": ("service.cockpit", frozenset({
        k for k in SCREENS if k.startswith("service.")} | {
        "dealer.cockpit", "dealer.service_lane", "dealer.tasks", "dealer.task", "approvals"})),
    "dealer_inventory": ("dealer.inventory", frozenset({
        "dealer.inventory", "dealer.inventory_vin", "dealer.trade_in", "dealer.discovery",
        "dealer.cockpit", "dealer.tasks", "dealer.task"})),
    "dealer_delivery": ("dealer.delivery", frozenset({
        "dealer.delivery", "dealer.journeys", "dealer.journey", "dealer.tasks", "dealer.task"})),
    "dealer_manager": ("dealer.cockpit", _DEALER_ALL | {"approvals"}),
    "oem_dm": ("oem.today", _OEM_ALL | {"dealer.cockpit", "dealer.journeys", "dealer.journey",
                                        "approvals"}),
    "oem_regional": ("oem.region", _OEM_ALL | {"dealer.cockpit", "approvals"}),
    "oem_ci_planning": ("oem.accounts", frozenset({
        "oem.accounts", "oem.account", "oem.plan", "oem.scenario", "approvals"})),
    "oem_sales_ops": ("oem.comms", frozenset({"oem.comms", "oem.reports"})),
    "oem_finance": ("marketing.campaigns", frozenset({
        "marketing.campaigns", "marketing.campaign", "oem.accounts", "oem.account",
        "oem.plan", "approvals"})),
    "oem_marketing": ("marketing.campaigns", frozenset({
        "marketing.campaigns", "marketing.campaign", "marketing.audiences",
        "marketing.journey_ops"})),
    "oem_cx": ("marketing.journey_ops", frozenset({
        "marketing.journey_ops", "marketing.campaigns", "marketing.campaign"})),
    "admin_tenant": ("admin.home", frozenset({"admin.home", "admin.approvals", "approvals"})),
    "admin_dealer": ("admin.home", frozenset({"admin.home"})),
    "customer": ("customer.garage", frozenset({
        "customer.garage", "customer.garage_service", "customer.shop", "customer.chat"})),
}

# Every persona has the Notification Hub inbox (§5.18.4).
PERSONAS = {r: (h, frozenset(sc | {"notifications"})) for r, (h, sc) in PERSONAS.items()}

# The 19 assignable staff groups (§5.2 / §7.2): the Cognito groups and the
# Admin › Users group picker. The Administrator below is NOT one of them.
STAFF_GROUPS: tuple[str, ...] = tuple(r for r in PERSONAS if r != "customer")

# The Administrator (§5.2, §8.1, decided 2026-10-21): every staff module,
# tenant-wide. Granted only to the enterprise-IdP aliases listed in
# RETAIL_OPS_SSO_ADMINS (app/infra/sso_admins.py writes their PLAT_Users rows);
# it cannot be assigned in Admin › Users and is not a Cognito group. Its screens
# are the union of every staff group's screens, so no customer screen.
PLATFORM_ADMIN = "platform_admin"
PERSONAS[PLATFORM_ADMIN] = ("admin.home", frozenset().union(*(PERSONAS[g][1] for g in STAFF_GROUPS)))

# Every staff role a route may accept (the assignable groups + the Administrator).
STAFF_ROLES: frozenset[str] = frozenset(STAFF_GROUPS) | {PLATFORM_ADMIN}

# Human-friendly display names for each persona (role key -> label). Shown in the
# admin "View as" persona switcher and anywhere a role needs a readable name.
DISPLAY_NAMES: dict[str, str] = {
    "dealer_advisor": "Sales Advisor",
    "dealer_bdc": "BDC Agent",
    "dealer_fni": "F&I Manager",
    "dealer_inventory": "Inventory Manager",
    "dealer_delivery": "Delivery Coordinator",
    "dealer_manager": "General Manager",
    "dealer_service": "Service Advisor",
    "dealer_technician": "Service Technician",
    "dealer_parts": "Parts Specialist",
    "dealer_service_manager": "Service Manager",
    "oem_dm": "OEM District Manager",
    "oem_regional": "OEM Regional Lead",
    "oem_ci_planning": "OEM C&I Planner",
    "oem_sales_ops": "OEM Sales Ops",
    "oem_finance": "OEM Finance Partner",
    "oem_marketing": "OEM Marketing Lead",
    "oem_cx": "OEM CX Lead",
    "admin_tenant": "Tenant Admin",
    "admin_dealer": "Dealer Admin",
    PLATFORM_ADMIN: "Administrator",
    "customer": "Customer",
}


def display_name(role: str) -> str:
    """Readable label for a role; falls back to a title-cased key."""
    return DISPLAY_NAMES.get(role) or role.replace("_", " ").title()


def catalog(include_customer: bool = False) -> list[dict[str, str]]:
    """Persona list for the switcher: {role, name, home, url} per persona.
    The Administrator is never offered: nobody may "view as" it."""
    out = []
    for role in PERSONAS:
        if role == PLATFORM_ADMIN or (role == "customer" and not include_customer):
            continue
        screen = PERSONAS[role][0]
        out.append({"role": role, "name": display_name(role),
                    "home": screen, "url": route(screen)})
    return sorted(out, key=lambda p: p["name"])


class UnknownRole(KeyError):
    pass


def home(role: str) -> str:
    if role not in PERSONAS:
        raise UnknownRole(role)
    return PERSONAS[role][0]


def may_open(role: str, screen: str) -> bool:
    return role in PERSONAS and screen in PERSONAS[role][1]


def primary_role(groups: list[str] | None) -> str | None:
    """The first KNOWN group — it names the home screen and the display role."""
    for g in groups or []:
        if g in PERSONAS:
            return g
    return None


def screens_for(groups: list[str] | None, extra_screens: list[str] | None = None) -> frozenset[str]:
    """A user's screens = the UNION of every group's screen set, plus any
    per-user `extra_screens` (validated against the SCREENS catalog). Groups is
    a list so one person can hold two hats (e.g. F&I manager who also runs the
    service desk); unknown groups and unknown screens are ignored rather than
    failing authentication."""
    out: set[str] = set()
    for g in groups or []:
        if g in PERSONAS:
            out |= PERSONAS[g][1]
    for sc in extra_screens or []:
        if sc in SCREENS:
            out.add(sc)
    return frozenset(out)


def route(screen: str, resource_id: str | None = None) -> str:
    tmpl = SCREENS[screen]
    return tmpl.replace("{id}", resource_id or "") if "{id}" in tmpl else tmpl
