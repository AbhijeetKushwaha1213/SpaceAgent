/*
 * ThemeContext.jsx — First-Class Dark & Light Mode Support.
 *
 * Persists theme preference to localStorage and updates <html data-theme="...">.
 * Default is 'dark' (luxury deep-space theme), toggleable seamlessly to 'light'.
 */

import React, { createContext, useContext, useEffect, useState } from "react";

const ThemeContext = createContext({
  theme: "dark",
  toggleTheme: () => {},
  setTheme: () => {},
});

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(() => {
    if (typeof window === "undefined") return "dark";
    try {
      const saved = localStorage.getItem("sentinel_theme");
      if (saved === "light" || saved === "dark") return saved;
      if (window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches) {
        return "light";
      }
    } catch (e) {
      // ignore localStorage restrictions
    }
    return "dark";
  });

  useEffect(() => {
    if (typeof document !== "undefined") {
      document.documentElement.setAttribute("data-theme", theme);
      try {
        localStorage.setItem("sentinel_theme", theme);
      } catch (e) {}
    }
  }, [theme]);

  const toggleTheme = () => {
    setThemeState((prev) => (prev === "dark" ? "light" : "dark"));
  };

  const setTheme = (nextTheme) => {
    if (nextTheme === "dark" || nextTheme === "light") {
      setThemeState(nextTheme);
    }
  };

  return (
    <ThemeContext.Provider value={{ theme, toggleTheme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeContext);
}
