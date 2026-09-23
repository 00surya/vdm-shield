# Enable verification email on Heroku

The website is deployed, but an SMTP sender must be configured before registration,
invitations and password recovery can deliver email. No email service is included
in the approved web/database hosting plan.

Open https://dashboard.heroku.com/apps/vdm-shield/settings and set the following
Config Vars privately. Do not put passwords or API keys in Git, chat, screenshots
or shell command arguments.

| Variable | Value |
| --- | --- |
| `SMTP_HOST` | Your provider's SMTP hostname |
| `SMTP_PORT` | `587` (STARTTLS) |
| `SMTP_USER` | Your provider's SMTP username |
| `SMTP_PASSWORD` | Your provider's SMTP credential |
| `MAIL_FROM` | An address your provider authorizes you to send from |

For example, [Resend SMTP](https://resend.com/docs/send-with-smtp) uses
`smtp.resend.com`, port `587`, username `resend`, and an API key as the password.
It requires a [verified domain that you own](https://resend.com/docs/dashboard/domains/introduction).
The Heroku `herokuapp.com` hostname is not a domain you own for email verification.
Other SMTP providers are also supported; use their documented STARTTLS settings.

Heroku restarts the application after Config Vars change. The registration and
password-reset forms reappear automatically once the required settings are present.
This detects configuration completeness; it does not prove that credentials,
sender verification or actual delivery work.

Then retry signup using an address you control, open the received verification
link, choose your password and sign in. If you already have a pending account,
sign in with the password you originally chose and request a new verification
email. Existing pending accounts are preserved; no manual verification bypass is
enabled. Perform a real delivery check only with an authorized recipient.
