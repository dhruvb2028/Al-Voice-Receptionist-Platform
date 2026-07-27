/** CSV export proxy — see the calls export route for the pattern. */
import { NextRequest, NextResponse } from "next/server";
import { proxyCsvExport } from "@/lib/csv-export";

export async function GET(request: NextRequest): Promise<NextResponse> {
  return proxyCsvExport(request, {
    apiPath: "/tenant/messages/export",
    filename: "messages.csv",
  });
}
