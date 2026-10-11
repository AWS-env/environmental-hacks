import Sidebar from "@/components/Sidebar";

export default function HomeLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  // Always dark: the particle field is drawn for a night sky. data-theme keeps the sidebar's colours in step.
  return (
    <div data-theme="dark" className="h-screen w-screen overflow-hidden flex bg-[#0a0d0e] text-zinc-100 selection:bg-white selection:text-black relative font-sans">
      {/* Global Sleek Floating Capsule Sidebar */}
      <Sidebar />

      {/* Main Content Area Offset for Sidebar */}
      <div className="flex-1 h-screen overflow-hidden pl-[72px] relative z-10">
        {children}
      </div>
    </div>
  );
}
