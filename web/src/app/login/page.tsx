"use client";

import React, { useState } from "react";
import Link from "next/link";
import { ArrowRight, Eye, EyeOff, Lock, Mail } from "lucide-react";
import Sidebar from "@/components/Sidebar";
import ParticleTerrain from "@/components/ParticleTerrain";
import { useIsDark } from "@/components/theme/useIsDark";
import "./login.css";

export default function LoginPage() {
  const isDark = useIsDark();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loadingProvider, setLoadingProvider] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const handleOAuthLogin = (provider: "github" | "aws") => {
    setErrorMsg(null);
    setLoadingProvider(provider);
    setTimeout(() => {
      setLoadingProvider(null);
      setSuccessMsg(
        provider === "github"
          ? "Redirecting to GitHub OAuth (read:user, repo:status)..."
          : "Redirecting to AWS Builder ID authentication..."
      );
    }, 600);
  };

  const handleCredentialsSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErrorMsg(null);
    setSuccessMsg(null);

    if (!email || !password) {
      setErrorMsg("Please provide both email and password.");
      return;
    }

    setLoadingProvider("credentials");
    setTimeout(() => {
      setLoadingProvider(null);
      setSuccessMsg("Signed in successfully. Launching sustainability dashboard...");
    }, 700);
  };

  return (
    <div className="relative min-h-screen overflow-hidden bg-page text-ink selection:bg-accent selection:text-on-accent">
      {/* Light theme sky. It comes first in the DOM, so everything else paints over it. */}
      <div aria-hidden="true" className="day-sky pointer-events-none fixed inset-0 dark:hidden" />

      {/* Night theme: the same drifting particle field as /home, plus the landing page's ambient glows.
          Mounted only at night: its pale dots would vanish on the day sky, and unmounting stops the WebGL loop. */}
      {isDark && <ParticleTerrain overlay />}
      <div aria-hidden="true" className="auth-vignette pointer-events-none fixed inset-0 z-[1] hidden dark:block" />
      <div className="pointer-events-none fixed -top-40 -left-40 hidden h-96 w-96 rounded-full bg-emerald-500/10 blur-3xl dark:block" />
      <div className="pointer-events-none fixed top-1/3 -right-40 hidden h-96 w-96 rounded-full bg-teal-500/10 blur-3xl dark:block" />

      {/* Same floating capsule navigation as the landing page */}
      <Sidebar />

      {/* Left padding clears the 50px capsule (14px offset + 50px width) */}
      <main className="relative z-10 flex min-h-screen items-center justify-center py-10 pl-[84px] pr-4">
        <div className="liquid-glass auth-card w-full max-w-[480px] rounded-[28px] px-6 py-8 sm:px-14 sm:py-14">
          <Link href="/" className="mb-8 flex items-center gap-3 transition-opacity hover:opacity-90">
            <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br from-emerald-300 to-teal-500 shadow-lg shadow-emerald-500/25">
              <svg className="h-5 w-5 text-black" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
              </svg>
            </div>
            <span className="text-xl font-bold tracking-tight text-ink">EcoAudit</span>
            <span className="hidden rounded-full border border-accent-line bg-accent-soft px-2.5 py-1 text-xs font-medium text-accent-strong sm:inline">
              AWS Environmental Hacks
            </span>
          </Link>

          <h1 className="text-[34px] font-normal leading-tight tracking-tight text-ink">Welcome back</h1>
          <p className="mt-1.5 mb-7 text-sm text-ink-2">Sign in to continue to EcoAudit</p>

          {/* Status alerts */}
          {errorMsg && (
            <div
              role="alert"
              className="mb-4 rounded-xl border border-red-500/30 bg-red-500/10 p-3 text-xs text-red-600 dark:text-red-400"
            >
              {errorMsg}
            </div>
          )}
          {successMsg && (
            <div
              role="status"
              className="mb-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs text-emerald-700 dark:text-emerald-300"
            >
              {successMsg}
            </div>
          )}

          <form onSubmit={handleCredentialsSubmit} className="space-y-3.5">
            <div className="relative">
              <Mail aria-hidden="true" strokeWidth={1.8} className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-ink-2" />
              <input
                id="email-input"
                type="email"
                required
                autoComplete="email"
                aria-label="Work email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Work email"
                className="auth-field h-[54px] w-full rounded-2xl pl-12 pr-4 text-sm text-ink placeholder:text-ink-2"
              />
            </div>

            <div className="relative">
              <Lock aria-hidden="true" strokeWidth={1.8} className="pointer-events-none absolute left-4 top-1/2 h-5 w-5 -translate-y-1/2 text-ink-2" />
              <input
                id="password-input"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="current-password"
                aria-label="Password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                className="auth-field h-[54px] w-full rounded-2xl pl-12 pr-12 text-sm text-ink placeholder:text-ink-2"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                className="absolute right-4 top-1/2 -translate-y-1/2 text-ink-2 transition-colors hover:text-ink"
              >
                {showPassword ? <EyeOff strokeWidth={1.8} className="h-5 w-5" /> : <Eye strokeWidth={1.8} className="h-5 w-5" />}
              </button>
            </div>

            <div className="flex justify-end">
              <a
                href="#forgot"
                onClick={(e) => {
                  e.preventDefault();
                  setErrorMsg("Password recovery link has been dispatched to your email if registered.");
                }}
                className="text-xs font-medium text-accent-strong hover:underline"
              >
                Forgot password?
              </a>
            </div>

            <button
              type="submit"
              disabled={loadingProvider !== null}
              className="auth-primary relative flex h-14 w-full items-center justify-center rounded-2xl text-base font-semibold active:scale-[0.99] disabled:opacity-60"
            >
              {loadingProvider === "credentials" ? "Signing in..." : "Sign in"}
              {loadingProvider !== "credentials" && (
                <ArrowRight aria-hidden="true" strokeWidth={1.8} className="absolute right-5 h-5 w-5" />
              )}
            </button>
          </form>

          {/* Divider */}
          <div className="my-6 flex items-center gap-4">
            <div className="h-px flex-1 bg-line" />
            <span className="text-xs font-medium text-ink-2">OR</span>
            <div className="h-px flex-1 bg-line" />
          </div>

          {/* Social / SSO Auth Buttons */}
          <div className="grid grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => handleOAuthLogin("github")}
              disabled={loadingProvider !== null}
              className="auth-field auth-provider flex h-[54px] items-center justify-center gap-2 whitespace-nowrap rounded-2xl px-2 text-sm font-medium sm:gap-2.5 sm:px-3 text-ink active:scale-[0.99] disabled:opacity-50"
            >
              {loadingProvider === "github" ? (
                <svg className="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              ) : (
                <svg className="h-6 w-6 fill-current" viewBox="0 0 24 24" aria-hidden="true">
                  <path fillRule="evenodd" clipRule="evenodd" d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z" />
                </svg>
              )}
              <span>GitHub</span>
            </button>

            <button
              type="button"
              onClick={() => handleOAuthLogin("aws")}
              disabled={loadingProvider !== null}
              className="auth-field auth-provider flex h-[54px] items-center justify-center gap-2 whitespace-nowrap rounded-2xl px-2 text-sm font-medium sm:gap-2.5 sm:px-3 text-ink active:scale-[0.99] disabled:opacity-50"
            >
              {loadingProvider === "aws" ? (
                <svg className="h-5 w-5 animate-spin" fill="none" viewBox="0 0 24 24">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8H4z" />
                </svg>
              ) : (
                <svg className="h-6 w-7" viewBox="0 0 28 24" aria-hidden="true">
                  <text x="14" y="12" textAnchor="middle" fontSize="11" fontWeight="700" fill="currentColor" fontFamily="var(--font-geist-sans), sans-serif">aws</text>
                  <path d="M3 16.5c6 4 16 4 22 0" fill="none" stroke="#ff9900" strokeWidth="1.8" strokeLinecap="round" />
                  <path d="M22.5 14.5l2.5 1.8-2.9.9" fill="none" stroke="#ff9900" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              )}
              <span>AWS SSO</span>
            </button>
          </div>

          {/* Footer note */}
          <p className="mt-6 flex flex-wrap items-center justify-center gap-x-1.5 text-center text-xs text-ink-3">
            New to EcoAudit?
            <a
              href="#signup"
              onClick={(e) => {
                e.preventDefault();
                handleOAuthLogin("github");
              }}
              className="inline-flex items-center gap-1 font-medium text-accent-strong hover:underline"
            >
              Connect with GitHub
              <ArrowRight aria-hidden="true" strokeWidth={1.8} className="h-3.5 w-3.5" />
            </a>
          </p>
        </div>
      </main>
    </div>
  );
}
