# AL-ZOOL GOLD SNIPER — Web Access

This branch replaces the storefront entry point with a private login portal and owner-managed customer access.

Configure hosting environment variables:
- SUPABASE_URL
- SUPABASE_ANON_KEY
- SUPABASE_SERVICE_ROLE_KEY (server secret only; never expose it in browser code)
- OWNER_EMAIL (defaults to Meenosvn1@gmail.com)
- OWNER_PASSWORD (strong secret; creates the owner account if it does not exist)

The owner signs in, enters a client's existing email address, creates an account, downloads its generated password, and shares the credentials privately. The owner can suspend/reactivate or permanently delete client accounts. Deleted accounts are removed from Supabase Auth.

This portal currently implements login and customer administration only. It does not claim that MT5 trading is connected. The original AL-ZOOL MT5 engine repository and files are not modified. Disable public sign-ups in Supabase Auth.
