import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
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
      <body className="h-screen w-screen overflow-hidden flex bg-[#16211f] text-zinc-100 selection:bg-amber-400 selection:text-black relative font-sans">
        {/* Panoramic Clouds Sunset & Retro Halftone Background */}
        <div
          className="fixed inset-0 pointer-events-none z-0 bg-cover bg-center opacity-35 mix-blend-screen scale-105"
          style={{ backgroundImage: `url('/panoramic_clouds.jpg')` }}
        />

        {/* Vintage Dithered Pixel Art Clouds Texture on Far Left Edge */}
        <div
          className="fixed left-0 top-0 bottom-0 w-[240px] pointer-events-none z-0 opacity-40 mix-blend-luminosity bg-cover bg-left [mask-image:linear-gradient(to_right,rgba(0,0,0,1)_0%,rgba(0,0,0,0.6)_50%,transparent_100%)]"
          style={{ backgroundImage: `url('/pixel_clouds.jpg')` }}
        />

        {/* Atmospheric Dark Teal Vignette */}
        <div className="fixed inset-0 pointer-events-none z-0 bg-gradient-to-b from-[#16211f]/60 via-[#16211f]/30 to-[#16211f]/75" />

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
