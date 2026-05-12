# Job Buddy PWA hardening report (plain English)

## What we changed
1. **Made the site installable** by adding a valid web app manifest.
2. **Added required app icons** (192x192, 512x512, and maskable 512x512).
3. **Registered a service worker only on HTTPS/localhost** so it is safe in production.
4. **Precached the app shell** (`/`, `/index.html`, `/offline.html`, manifest, icons).
5. **Added offline fallback** so navigation still works when internet is down.

## Files touched
- `index.html`
- `public/manifest.webmanifest`
- `public/service-worker.js`
- `public/offline.html`
- `public/assets/icons/*`

## Current validation in this environment
- Build command succeeds.
- Dist output includes manifest, service worker, offline page, and icons.
- Lighthouse automation is blocked here because Chrome is not installed in this container.

## Exact commands to finish validation on your machine
```bash
npm run build
npx vite preview --host 0.0.0.0 --port 4173
```
In a second terminal:
```bash
npx localtunnel --port 4173 --subdomain jobbuddy-pwa
```
Then run Lighthouse twice:
```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
npx lighthouse https://jobbuddy-pwa.loca.lt --only-categories=pwa,best-practices --output html --output json --output-path lighthouse-reports/$TS
```

## What cannot be solved with web code alone
- iOS install prompts and some install UX behaviors are controlled by Safari/iOS.
