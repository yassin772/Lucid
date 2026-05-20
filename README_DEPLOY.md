# Lucid Vercel Preview Deploy

Lucid V1 is a static frontend. Vercel can deploy it without a framework migration or custom backend.

## What Vercel Serves

- `/` -> `lucid_landing.html`
- `/auth.html`
- `/lucid_web_app_v2_lucid.html`
- `/privacy.html`
- `/terms.html`
- `/api/lucid_payload.json`

## Required Vercel Environment Variables

Set these in Vercel Project Settings -> Environment Variables:

```text
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_ANON_KEY=your-public-anon-key
```

Only use the public anon key in Vercel frontend config.

Never add:

```text
SUPABASE_SERVICE_ROLE_KEY
```

## How Config Works

During `npm run build`, Vercel runs:

```text
node scripts/build_vercel_config.js
```

This writes:

```text
js/supabase_config.js
```

The file contains only the public Supabase URL and public anon key. It is ignored locally so `js/supabase_config.local.js` can keep working for localhost.

## Deploy Steps

1. Push the project to GitHub.
2. In Vercel, create a new project from the GitHub repository.
3. Keep framework preset as `Other` if Vercel does not detect one.
4. Build command: `npm run build`.
5. Output directory: `.`.
6. Add `SUPABASE_URL` and `SUPABASE_ANON_KEY`.
7. Deploy.
8. Open the generated preview URL.

## Supabase Auth URLs

In Supabase -> Authentication -> URL Configuration, add the deployed Vercel URL.

For example, if the Vercel URL is:

```text
https://lucidmacro.vercel.app
```

Use:

```text
Site URL:
https://lucidmacro.vercel.app

Redirect URLs:
https://lucidmacro.vercel.app/auth.html
https://lucidmacro.vercel.app/auth.html?mode=callback
https://lucidmacro.vercel.app/auth.html?mode=reset
https://lucidmacro.vercel.app/lucid_web_app_v2_lucid.html
```

Keep localhost URLs too while testing locally:

```text
http://localhost:8017
http://localhost:8017/auth.html
http://localhost:8017/auth.html?mode=callback
http://localhost:8017/auth.html?mode=reset
http://localhost:8017/lucid_web_app_v2_lucid.html
```

## Preview QA Checklist

- Landing loads over HTTPS.
- `Start free for 30 days` opens signup.
- Signup creates a Supabase user and profile.
- Login opens the app if trial is active.
- Logout returns to login.
- Refresh keeps the session.
- Forgot password sends an email.
- Reset password returns to `auth.html?mode=reset`.
- Expired trial shows the calm expired state.
- `/api/lucid_payload.json` loads.
- `/privacy.html` and `/terms.html` load.

## Mobile QA

Test the Vercel preview on:

- iPhone Safari
- Android Chrome

Check:

- auth keyboard behavior
- no horizontal overflow
- readable auth card
- app sidebar/header behavior
- currency cards
- pair cards
- landing footer

## Later

- Add a custom domain.
- Add production Supabase redirect URLs for the custom domain.
- Add Stripe only after private user validation.
- Add privacy-friendly analytics only after consent requirements are reviewed.
