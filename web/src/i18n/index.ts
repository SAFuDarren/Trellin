import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import zhCN from "./zh-CN.json";

// English is the default; Chinese is complete (architecture decision E6).
export const LANGUAGES = { en: "English", "zh-CN": "简体中文" } as const;
export type Language = keyof typeof LANGUAGES;
const STORAGE_KEY = "research-multiplier-language";

function initialLanguage(): Language {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY);
    if (saved && saved in LANGUAGES) return saved as Language;
  } catch {
    // Storage can be unavailable; fall back to English.
  }
  return "en";
}

export function setLanguage(language: Language): void {
  void i18n.changeLanguage(language);
  document.documentElement.lang = language;
  try {
    window.localStorage.setItem(STORAGE_KEY, language);
  } catch {
    // The choice still applies to this page.
  }
}

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, "zh-CN": { translation: zhCN } },
  lng: initialLanguage(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});

export default i18n;
