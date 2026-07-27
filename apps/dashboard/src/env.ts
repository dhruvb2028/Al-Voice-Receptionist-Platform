/**
 * Environment validation. Imported by next.config.ts so a missing or
 * malformed variable fails the build/boot instead of surfacing as a
 * runtime error mid-request.
 *
 * Server-only variables must never be referenced from client components;
 * only NEXT_PUBLIC_* values are exposed to the browser bundle.
 */
import { z } from "zod";

const serverSchema = z
  .object({
    NODE_ENV: z.enum(["development", "test", "production"]).default("development"),
    /**
     * Which environment this build is *for*. Distinct from NODE_ENV,
     * which `next build` always sets to "production" — including in CI,
     * where no deployment secrets exist. Only a real deploy sets this.
     */
    DEPLOY_ENV: z.enum(["development", "staging", "production"]).default("development"),
    /** Base URL of the FastAPI control plane. */
    API_BASE_URL: z.string().url().default("http://localhost:8000"),
    /** Clerk server key. Required when deploying; absent locally. */
    CLERK_SECRET_KEY: z.string().optional(),
    /** Uploads source maps at build time so traces are readable. */
    SENTRY_AUTH_TOKEN: z.string().optional(),
  })
  .superRefine((env, ctx) => {
    if (env.DEPLOY_ENV === "development") return;
    // Deploying without these ships a dashboard that either cannot
    // authenticate or points at localhost. Failing the deploy is far
    // cheaper than finding out from a client.
    if (env.API_BASE_URL.startsWith("http://localhost")) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["API_BASE_URL"],
        message: `must point at the deployed API when DEPLOY_ENV=${env.DEPLOY_ENV}`,
      });
    }
    if (!env.CLERK_SECRET_KEY) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        path: ["CLERK_SECRET_KEY"],
        message: `is required when DEPLOY_ENV=${env.DEPLOY_ENV}`,
      });
    }
  });

const clientSchema = z.object({
  NEXT_PUBLIC_APP_URL: z.string().url().default("http://localhost:3000"),
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: z.string().optional(),
  NEXT_PUBLIC_SENTRY_DSN: z.string().url().optional(),
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
  DEPLOY_ENV: process.env.DEPLOY_ENV,
  API_BASE_URL: process.env.API_BASE_URL,
  CLERK_SECRET_KEY: process.env.CLERK_SECRET_KEY,
  SENTRY_AUTH_TOKEN: process.env.SENTRY_AUTH_TOKEN,
});

export const clientEnv = validate(clientSchema, {
  NEXT_PUBLIC_APP_URL: process.env.NEXT_PUBLIC_APP_URL,
  NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
  NEXT_PUBLIC_SENTRY_DSN: process.env.NEXT_PUBLIC_SENTRY_DSN,
});
