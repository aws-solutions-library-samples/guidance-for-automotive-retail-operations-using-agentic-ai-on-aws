/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Static export for S3+CloudFront hosting (set RETAIL_OPS_STATIC_EXPORT=1 at build).
  // All pages are client components reading the API at runtime, so a static
  // bundle works unchanged; left off for local `next dev`.
  ...(process.env.RETAIL_OPS_STATIC_EXPORT === "1"
    ? { output: "export", images: { unoptimized: true }, trailingSlash: true }
    : {}),
  // The console is a thin client over the deployed Retail Ops API (API Gateway). The
  // API base is read at runtime from NEXT_PUBLIC_RETAIL_OPS_API (or the ?api= query
  // param), so the same build points at any environment without rebuilding. Unset,
  // it targets the local dev API (`python3 -m app.services.devserver`, port 8080); deploy.sh
  // always passes the deployed API base explicitly.
  env: {
    NEXT_PUBLIC_RETAIL_OPS_API:
      process.env.NEXT_PUBLIC_RETAIL_OPS_API || "http://127.0.0.1:8080",
    // Plug-and-play feature flags (OFF by default; nothing renders unless set).
    NEXT_PUBLIC_ENABLE_QUICK: process.env.NEXT_PUBLIC_ENABLE_QUICK || "false",
    // Streaming floor path (spec R2): the Lambda Function URL written by
    // app/infra/deploy_streaming.py. Unset = the request/response REST path
    // stays the default; set = the advisor's objection check renders tokens as
    // they arrive over Server-Sent Events.
    // SSE floor path rides the main authenticated API (utterance-sse); "0" turns
    // it off and the plain request/response route is used instead.
    NEXT_PUBLIC_RETAIL_OPS_STREAM: process.env.NEXT_PUBLIC_RETAIL_OPS_STREAM || "1",
    // Cognito Hosted UI base — set it to turn on SSO (the OAuth code flow all
    // five apps share). The Hosted UI runs on its own app client of the pool.
    NEXT_PUBLIC_COGNITO_HOSTED_UI: process.env.NEXT_PUBLIC_COGNITO_HOSTED_UI || "",
    NEXT_PUBLIC_COGNITO_HOSTED_UI_CLIENT_ID: process.env.NEXT_PUBLIC_COGNITO_HOSTED_UI_CLIENT_ID || "",
    // External IdPs on the pool (comma-separated, e.g. "CorpDirectory"). Empty
    // means no SSO button — the Hosted UI would only show its own password form.
    NEXT_PUBLIC_SSO_IDPS: process.env.NEXT_PUBLIC_SSO_IDPS || "",
    // Cognito staff pool client id + region for real USER_PASSWORD_AUTH sign-in.
    NEXT_PUBLIC_COGNITO_CLIENT_ID: process.env.NEXT_PUBLIC_COGNITO_CLIENT_ID || "",
    NEXT_PUBLIC_COGNITO_REGION: process.env.NEXT_PUBLIC_COGNITO_REGION || "us-east-1",
  },
};

export default nextConfig;
