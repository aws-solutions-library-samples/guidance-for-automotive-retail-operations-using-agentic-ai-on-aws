"""Platform integration interfaces (Tier C, spec §9A / AR-1/2/4).

Each capability the spec delegates to a managed AWS platform is defined here as
an INTERFACE with (a) a `local`/`sandbox` implementation that works today over
the seeded data, and (b) a documented swap point to the real service. The
selected implementation is chosen by INTEGRATION_MODE (env), so promoting to the
managed service is a config change, not a code rewrite — the anti-pattern the
spec's §2.2.2 composition rules warn against (stack leaking into logic).

  ranker        -> local rules ranker  | Amazon Personalize (Ranking recipe)
  knowledge     -> local keyword index | Amazon Bedrock Knowledge Bases / Quick Index
  outreach      -> SES/SNS (REAL here) | Amazon Connect (voice) when enabled
  compute       -> audited local calc  | AgentCore Code Interpreter

INTEGRATION_MODE=local (default) | aws
"""
