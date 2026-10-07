// Supabase Edge Function: forwards Stripe API calls to api.stripe.com.
//
// Why: PythonAnywhere's free plan blocks api.stripe.com but allows
// *.supabase.co, so the store calls this function and it calls Stripe.
//
// Setup (Supabase dashboard, no CLI needed):
//   1. Edge Functions > Deploy a new function > Via Editor, name it
//      "stripe-relay", paste this file, Deploy.
//   2. Open the function > Details/Settings > turn OFF "Verify JWT"
//      (Stripe's own secret key is sent in the Authorization header).
//   3. Edge Functions > Secrets > add RELAY_SECRET = a long random string.
//   4. In the store's .env:
//      STRIPE_API_BASE=https://<project-ref>.supabase.co/functions/v1/stripe-relay/<RELAY_SECRET>
//
// The function only talks to api.stripe.com, only forwards /v1/ paths, and
// rejects any request without the right RELAY_SECRET in the URL.

const STRIPE = "https://api.stripe.com";
const FORWARD_HEADERS = [
  "authorization",
  "content-type",
  "idempotency-key",
  "stripe-version",
  "stripe-account",
  "user-agent",
  "x-stripe-client-user-agent",
];

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

Deno.serve(async (req: Request) => {
  const secret = Deno.env.get("RELAY_SECRET") ?? "";
  if (secret.length < 16) {
    return new Response("RELAY_SECRET is not set (min 16 characters)", { status: 500 });
  }

  // Path looks like /stripe-relay/<secret>/v1/checkout/sessions
  const url = new URL(req.url);
  const marker = "/stripe-relay/";
  const at = url.pathname.indexOf(marker);
  const rest = at === -1 ? "" : url.pathname.slice(at + marker.length);
  const slash = rest.indexOf("/");
  const given = slash === -1 ? rest : rest.slice(0, slash);
  const stripePath = slash === -1 ? "" : rest.slice(slash);

  if (!safeEqual(given, secret)) return new Response("Forbidden", { status: 403 });
  if (!stripePath.startsWith("/v1/")) return new Response("Not found", { status: 404 });

  const headers = new Headers();
  for (const name of FORWARD_HEADERS) {
    const value = req.headers.get(name);
    if (value) headers.set(name, value);
  }

  const upstream = await fetch(STRIPE + stripePath + url.search, {
    method: req.method,
    headers,
    body: req.method === "GET" || req.method === "HEAD" ? undefined : await req.arrayBuffer(),
  });

  const out = new Headers();
  for (const name of ["content-type", "request-id", "idempotency-key", "stripe-version", "stripe-should-retry"]) {
    const value = upstream.headers.get(name);
    if (value) out.set(name, value);
  }
  return new Response(upstream.body, { status: upstream.status, headers: out });
});
