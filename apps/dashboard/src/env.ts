/**
 * Environment validation. Imported by next.config.ts so a missing or
 * malformed variable fails the build/boot instead of surfacing as a
 * runtime error mid-request.
 *
 * Server-only variables must never be referenced from client components;
 * only NEXT_PUBLIC_* values are exposed to the browser bundle.
 */
import { z } from "zod";

const serverSchema = z.object({
  NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
  /** Base URL of the FastAPI control plane. */
  API_BASE_URL: z.string().url().default("http://localhost:8000"),
});

const clientSchema = z.object({
  NEXT_PUBLIC_APP_URL: z.string().url().default("http://localhost:3000"),
});

function validate<T extends z.ZodTypeAny>(schema: T, values: Record<string, string | undefined>): z.infer<T> {
  // Treat empty strings as unset so defaults apply (docker build args and
  // CI can pass "" without bypassing validation defaults).
  const cleaned = Object.fromEntries(
    Object.entries(values).filter(([, v]) => v !== undefined && v !== ""),
  );
  const parsed = schema.safeParse(cleaned);
  if (!parsed.success) {
    const issues = parsed.error.issues
      .map((issue) => `  ${issue.path.join(".")}: ${issue.message}`)
      .join("\n");
    throw new Error(`Invalid environment configuration:\n${issues}`);
  }
  return parsed.data;
}

export const serverEnv = validate(serverSchema, {
  NODE_ENV: process.env.NODE_ENV,
  API_BASE_URL: process.env.API_BASE_URL,
});

export const clientEnv = validate(clientSchema, {
  NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
});
