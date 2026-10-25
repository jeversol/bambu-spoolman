import { revalidateTag } from "next/cache";

const backend = process.env.API_SERVER_URL || "http://127.0.0.1:50052";
const headers = { "Cache-Control": "no-store" };
type Context = { params: Promise<{ path: string[] }> };

async function forward(request: Request, context: Context) {
  const path = (await context.params).path.join("/");
  const allowed =
    request.method === "GET"
      ? /^(settings|spools(?:\/\d+)?)$/.test(path)
      : request.method === "PUT" && /^trays\/\d+$/.test(path);
  if (!allowed)
    return Response.json({ error: "Not found" }, { status: 404, headers });

  let body: string | undefined;
  if (request.method === "PUT") {
    const origin = request.headers.get("origin");
    if (origin) {
      try {
        if (new URL(origin).host !== request.headers.get("host")) {
          return Response.json(
            { error: "Cross-origin request rejected" },
            { status: 403, headers },
          );
        }
      } catch {
        return Response.json(
          { error: "Invalid origin" },
          { status: 403, headers },
        );
      }
    }
    if (
      request.headers.get("content-type")?.split(";")[0].trim() !==
      "application/json"
    ) {
      return Response.json(
        { error: "Expected application/json" },
        { status: 415, headers },
      );
    }
    const reader = request.body?.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    if (reader) {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        size += value.byteLength;
        if (size > 4096) {
          await reader.cancel();
          return Response.json(
            { error: "Request body too large" },
            { status: 413, headers },
          );
        }
        chunks.push(value);
      }
    }
    body = Buffer.concat(chunks).toString("utf8");
  }
  try {
    const response = await fetch(`${backend}/api/v1/${path}`, {
      method: request.method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(120_000),
    });
    if (request.method === "PUT" && response.ok) {
      revalidateTag("settings", { expire: 0 });
      revalidateTag("all-spools", { expire: 0 });
    }
    return new Response(response.body, {
      status: response.status,
      headers: { ...headers, "Content-Type": "application/json" },
    });
  } catch {
    return Response.json(
      { error: "Backend is unavailable" },
      { status: 502, headers },
    );
  }
}

export const GET = forward;
export const PUT = forward;
