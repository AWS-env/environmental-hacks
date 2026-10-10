// Shared by the server layout (boot script) and ThemeToggle; no client-only code here.

export type Theme = "light" | "dark";

export const THEME_STORAGE_KEY = "theme";

/** The theme the page is showing (client only). */
export const currentTheme = (): Theme => (document.documentElement.dataset.theme === "dark" ? "dark" : "light");
export const SYSTEM_DARK = "(prefers-color-scheme: dark)";

// Runs inline at the top of <body>, before first paint: the stored choice, else the system setting.
export const THEME_BOOT_SCRIPT = `(function(){try{
var t=localStorage.getItem(${JSON.stringify(THEME_STORAGE_KEY)});
if(t!=="light"&&t!=="dark")t=matchMedia(${JSON.stringify(SYSTEM_DARK)}).matches?"dark":"light";
document.documentElement.dataset.theme=t;
}catch(e){}})();`;
