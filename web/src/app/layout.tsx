import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import "./glass.css";
import Sidebar from "../components/Sidebar";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Kimi • Turn any repository into insights",
  description:
    "Understand, analyze, and work with your codebase using AI.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-screen overflow-hidden antialiased dark`}
    >
      <body className="h-screen w-screen overflow-hidden flex bg-[#0a0d0e] text-zinc-100 selection:bg-white selection:text-black relative font-sans">
        {/* Global Sleek Floating Capsule Sidebar */}
        <Sidebar />

        {/* Main Content Area Offset for Sidebar */}
        <div className="flex-1 h-screen overflow-hidden pl-[72px] relative z-10">
          {children}
        </div>
      </body>
    </html>
  );
}
