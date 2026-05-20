# Lucid SaaS V1 Setup

This file documents the minimal SaaS and privacy setup for Lucid V1.

## Supabase Project

1. Create a Supabase project in a European region.
2. Open the Supabase SQL editor.
3. Run `supabase/schema.sql`.
4. Confirm Row Level Security is enabled on `public.profiles`.
5. Confirm users can only read and update their own profile.

## Supabase Auth URLs

For local development on port `8017`, configure Supabase Auth with:

```text
Site URL:
http://localhost:8017

Redirect URLs:
http://localhost:8017/auth.html
http://localhost:8017/auth.html?mode=callback
http://localhost:8017/auth.html?mode=reset
http://localhost:8017/lucid_web_app_v2_lucid.html
```

For production, add the equivalent `https://your-domain/...` URLs before launch.

Email confirmation behavior:

- If confirmation is enabled, signup creates the user/profile but Lucid shows `Check your email` until the email is confirmed.
- If confirmation is disabled, Supabase can return an active session immediately and Lucid redirects to the app.
- Password reset links should redirect to `auth.html?mode=reset`.

## Required Configuration

Frontend configuration needs only:

```text
SUPABASE_URL=
SUPABASE_ANON_KEY=
```

The Supabase anon key is public by design when Row Level Security is enabled.

Never expose:

```text
SUPABASE_SERVICE_ROLE_KEY
```

Do not commit `.env` or `.env.*` files.

The frontend must never use a `service_role` key. It should only use the public anon key with RLS enabled.

## Auth Flow Checklist

- Landing `Start free for 30 days` points to `auth.html?mode=signup`.
- Landing `Sign in` points to `auth.html?mode=login`.
- Signup shows `Create your Lucid account`.
- Login shows `Sign in to Lucid`.
- Forgot password uses Supabase password reset and shows `Check your email`.
- Reset password uses the Supabase recovery session and then redirects to the app.
- The app reads `profiles` only after `supabase.auth.getSession()` returns a user.
- The app queries `profiles` by `id = session.user.id`, never by email and never with a global select.
- If a profile is missing but the user is authenticated, the frontend may attempt a safe profile repair insert for that same user id.
- Trial access is based on `subscription_status` and `trial_end`.

If the app shows `permission denied for table profiles`, re-run `supabase/schema.sql` and confirm these grants/policies exist:

```sql
grant usage on schema public to anon, authenticated;
grant select, insert, update on public.profiles to authenticated;

create policy "Users can read own profile"
on public.profiles for select
to authenticated
using (auth.uid() = id);
```

Also confirm `profiles.id` exactly matches the UUID in Supabase Authentication -> Users.

## Local Static Frontend

Because the current frontend is static HTML, configuration can be injected in one of two ways:

```html
<script>
window.LUCID_SUPABASE_CONFIG = {
  url: "https://your-project.supabase.co",
  anonKey: "your-public-anon-key"
};
</script>
```

or temporarily for local browser testing:

```js
localStorage.setItem("LUCID_SUPABASE_URL", "https://your-project.supabase.co");
localStorage.setItem("LUCID_SUPABASE_ANON_KEY", "your-public-anon-key");
```

## Minimal Data Collected

Lucid V1 should collect only:

- email
- Supabase user id
- trial_start
- trial_end
- subscription_status
- created_at / updated_at timestamps

Lucid V1 should not collect:

- broker credentials
- portfolio data
- positions
- PnL
- account balances
- user trading history
- user API keys
- personal financial records

## Cookie And Tracking Position

Lucid V1 does not add marketing analytics, session replay, advertising pixels, or non-essential tracking.

No cookie banner is needed while only essential authentication/session storage is used.

If Google Analytics, Meta Pixel, Hotjar, session replay, marketing attribution, or similar tools are added later, consent requirements must be reviewed first.

## Account Deletion

V1 deletion is manual by contact request:

```text
privacy@lucidmacro.com
```

A self-serve account deletion flow should be added before broader public launch.

## Required Disclaimer

Use this wording consistently:

```text
Lucid is for macro understanding and education. It does not provide financial advice, investment recommendations, or trading signals.
```

## Remaining Before Wider Launch

- Confirm the Supabase project is hosted in an EU region.
- Replace the privacy contact email if needed.
- Add a self-serve account deletion flow.
- Add production-safe configuration injection.
- Review privacy policy and terms with a qualified legal professional.
- Add Stripe only after the trial/auth flow is validated.
