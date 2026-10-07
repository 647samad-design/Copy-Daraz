// Forwards every request on the Netlify domain to the Django backend and
// streams the response back. Configure BACKEND_URL in Netlify's
// environment variables, e.g. https://lumenmarket.pythonanywhere.com
import type { Context, Config } from "@netlify/edge-functions";

export default async (request: Request, context: Context) => {
  const backend = Netlify.env.get("BACKEND_URL");
  if (!backend) {
    return new Response(
      "Setup needed: add BACKEND_URL (your Django host, e.g. https://username.pythonanywhere.com) " +
        "in Netlify > Site configuration > Environment variables, then redeploy.",
      { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } },
    );
  }

  const incoming = new URL(request.url);
  const target = new URL(incoming.pathname + incoming.search, backend);

  const headers = new Headers(request.headers);
  headers.delete("host");
  // Tell Django which public domain the visitor used, and who the visitor is.
  // X-Forwarded-For is overwritten (not appended) so it can't be spoofed.
  headers.set("X-Forwarded-Host", incoming.host);
  headers.set("X-Forwarded-Proto", "https");
  headers.set("X-Forwarded-For", context.ip);

  const hasBody = !["GET", "HEAD"].includes(request.method);
  const response = await fetch(target, {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    redirect: "manual",
  });

  // Rewrite absolute redirects that point at the backend host.
  const location = response.headers.get("location");
  if (location) {
    const backendOrigin = new URL(backend).origin;
    if (location.startsWith(backendOrigin)) {
      const fixed = new Headers(response.headers);
      fixed.set("location", location.replace(backendOrigin, incoming.origin));
      return new Response(response.body, { status: response.status, headers: fixed });
    }
  }
  return response;
};

export const config: Config = { path: "/*" };
