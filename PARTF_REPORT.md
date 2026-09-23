# Part F — Arabic / RTL support (build report)

Scope as agreed in the earlier plan: auto-detect from phone locale with manual
override in Settings, ~50 strings, RTL via dir, Arabic Hijri via the same
Intl Umm al-Qura calendar from Part B. No strings left hardcoded-only in the
visible chrome; server-returned message bodies (e.g. exact-cap text from the
API) remain as the server sends them (English), noted as a deliberate limit.

## Detection (smart default)
- detectLang(): `Intl.DateTimeFormat().resolvedOptions().locale` first,
  `navigator.language` fallback; `/^ar/i` -> Arabic + rtl, otherwise English.
- Effective: localStorage `pt_lang` (manual override: 'ar'/'en' — set by the
  Settings select, 'auto' removes the key) ALWAYS wins over detection once set.
- <html lang="ar" dir="rtl"> applied by applyLang() at init and on change.

## Coverage
~50 keys per language (en/ar) driving: title/brand, status + permission card,
denial + city-search (placeholder + results), hero label, locate pill + stage
messages (locating / updating / approximate / retry), bottom nav, panel titles,
all settings fields incl. the long method note, ASR school + prayer options,
notify block, flag block, exact tier block (mark/replace + feedback), and the
six badge states. Static text via data-i18n; dynamic via t(). Gregorian date
uses the `ar` locale; Hijri uses `ar-u-ca-islamic-umalqura` (Arabic months +
Arabic-Indic numerals) with the EN tabular form as fallback; prayer-card name
order swaps so Arabic is primary in AR mode.

## Verified (real browser, real locale override)
Loaded with the browser's OS locale emulated as ar-AE (CDP
Emulation.setLocaleOverride) and NO manual toggle:
- lang/dir = ar/rtl; brand "أوقات الصلاة"; hero "الوقت المتبقي حتى";
  nav "الأوقات/القبلة/الإعدادات"; badge "دقيق — أوقات حقيقية من المصدر
  الرسمي" (state exact).
- Hijri: "10 ربيع الآخر 1448 هـ" — same Umm al-Qura day as Part B's fix.
- Greg: "الاثنين، 21 سبتمبر 2026".
- Prayer names: الفجر الشروق الظهر العصر المغرب العشاء.

English-locale load (no override): en/ltr with all English strings (default
path intact).

Manual override:
- Set en while Arabic → en/ltr; reload → still en/ltr (persisted).
- Set ar → ar/rtl; reload → still ar/rtl (persisted).
- Set auto → returns to detection (ar, while the emulated locale is ar-AE).

Note: the CDP locale override resets on navigation, so the ar test used
set-after-load + in-place reload. On a real device the phone's OS locale
applies from boot; no code difference.
