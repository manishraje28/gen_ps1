# Sign in with Google (workspace login)

Without these settings the workspace runs on the **demo profile**. With them, the avatar menu shows
**Sign in with Google**, and each signed-in person gets their own tasks, approvals, memory and audit trail.

This uses a **Web application** OAuth client. It is separate from the Gmail desktop client in
`docs/integrations/GMAIL.md`; the live Gmail inbox is still the one demo account configured there.

## 1. Create the client (Google Cloud console, the same project as Gmail is fine)
1. **APIs & Services** > **Credentials** > **Create credentials** > **OAuth client ID**.
2. Application type: **Web application**. Name: `warrant-web`.
3. **Authorised redirect URIs**: add `http://localhost:8000/auth/google/callback`
   (add your deployed URL too if you host it, e.g. `https://<host>/auth/google/callback`).
4. Click **Create** and copy the client ID and secret.
5. While the app is in **Testing** mode, only the **test users** listed on the OAuth consent screen can sign in
   (up to 100). Add your teammates' and judges' Google addresses there.

## 2. Add to `.env`
```
GOOGLE_CLIENT_ID=...apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=...
# optional
WARRANT_ALLOWED_EMAILS=you@gmail.com,@yourcollege.edu
WARRANT_SECRET=<random string; otherwise one is generated into data/secret.key>
WARRANT_COOKIE_SECURE=1        # only behind HTTPS
```
Restart the server. Scopes requested: `openid email profile` only.

## Security
- The `state` parameter is random per login and checked against an HttpOnly cookie (blocks login CSRF).
- The ID token is verified with Google's public keys (signature, audience, issuer, expiry, verified email).
- The session cookie holds only the user id and an expiry, HMAC-SHA256 signed; tampering or expiry = demo profile.
- Every workspace query is scoped to the signed-in user; another user's session id returns 404.
- Sign-ins are written to the hash-chained audit log.
