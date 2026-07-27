/**
 * Shared CSV export proxy: attaches the Clerk session token server-side
 * and streams the API's CSV back as a download. The browser never sees
 * the API token or talks to the API directly.
 */
import "server-only";
import { auth } from "@clerk/nextjs/server";
import { NextRequest, NextResponse } from "next/server";
import { serverEnv } from "@/env";

export async function proxyCsvExport(
  request: NextRequest,
  { apiPath, filename }: { apiPath: string; filename: string },
): Promise<NextResponse> {
  if (!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY) {
    return new NextResponse("Not available.", { status: 401 });
  }
  const session = await auth();
  const token = await session.getToken();
  if (!token) {
    return new NextResponse("Sign in to continue.", { status: 401 });
  }

  const url = new URL(apiPath, serverEnv.API_BASE_URL);
  for (const [key, value] of request.nextUrl.searchParams.entries()) {
    if (value) url.searchParams.set(key, value);
  }

  const response = await fetch(url, {
    headers: { Authorization: `Bearer ${token}` },
    cache: "no-store",
  });
  if (!response.ok) {
    return new NextResponse("Export failed.", { status: response.status });
  }
  return new NextResponse(await response.text(), {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="${filename}"`,
    },
  });
}
