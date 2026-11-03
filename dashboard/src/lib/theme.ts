import { useCallback, useEffect, useState } from "react";

export type ThemeChoice = "light" | "dark" | "system";

function read(): ThemeChoice {
  try {
    const t = localStorage.getItem("theme");
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

export function isDark(choice: ThemeChoice, systemDark: boolean): boolean {
  return choice === "dark" || (choice === "system" && systemDark);
}

/** light / dark / system, remembered in the browser. The class on <html> swaps the colour tokens. */
export function useTheme() {
  const [choice, setChoice] = useState<ThemeChoice>(read);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => document.documentElement.classList.toggle("dark", isDark(choice, mq.matches));
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [choice]);

  const set = useCallback((next: ThemeChoice) => {
    try {
      localStorage.setItem("theme", next);
    } catch {
      /* private mode: the choice just will not be remembered */
    }
    setChoice(next);
  }, []);

  return { choice, set };
}
