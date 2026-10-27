# Connect Google Workspace sending accounts

Invoices use `accounting@limitlessmechanical.net`. Quotes use either
`redwine.david@limitlessmechanical.net` or `redwine.adrian@limitlessmechanical.net`.
These connections require the exact primary Google Workspace mailbox. An alias or
group address must first be reviewed separately; the CRM does not impersonate an
unverified From address.

## Google Cloud setup

1. In project `limitless-crm-512612`, Google Auth Platform, create a **dedicated Web
   application OAuth client** named `Limitless CRM Email`. Keep the existing IAP
   login client unchanged. Add this exact authorized redirect URI:

   `https://lms-crm-test-938549337192.us-central1.run.app/api/admin/email/oauth/callback`

2. Configure consent for `openid`, `userinfo.email`, and `gmail.send`. The CRM uses
   Gmail sending permission; it does not request inbox access or mail-settings
   permission. If the consent app is External and in Testing, add all three exact
   Workspace addresses as test users. Google testing refresh tokens may expire
   after seven days. Use an appropriate published/internal configuration for
   durable business use, following Google's verification and Workspace policies.

3. Download the dedicated client's JSON privately. In authenticated Cloud Shell,
   check out the private CRM repository, upload that JSON outside the repository,
   and run:

   `python3 deploy/configure-email.py /private/path/client.json --enable-sending`

   This stores the client and a token encryption key in Secret Manager, grants the
   existing runtime identity access to only those three secrets, enables Gmail
   API, and attaches secret references to the existing Cloud Run service. Review
   those access changes before running. Do not paste client secrets, refresh
   tokens, or the encryption key into chat or commit them to Git. The script does
   not change the CRM's preview mode or send mail. Keep the encryption-key secret
   stable; replacing it makes stored connections unreadable. An existing key is
   preserved when the script is rerun.

4. As CRM owner, open `/email-center`. Click **Connect** for each mailbox and have
   its user sign in and approve Google's sending permission. The CRM verifies the
   Google identity matches the requested sender, encrypts the offline token, and
   shows Connected. The connect flow expires after ten minutes and is bound to
   the browser that started it. OAuth requires a normal browser Google permits;
   an embedded browser may be rejected by Google.

## Send invoices

Complete the remittance address in Billing Desk company settings. Review and issue
the invoice. When ready for live delivery, turn off preview mode and save. Enter
the customer recipient, prepare the email, inspect the frozen attachment, then
process the queued message. The sender is always accounting. A preview from
before live mode is not a live send; prepare a new copy after reviewing it.

## Send quotes

Open Email accounts & quotes, select the quote, review its customer PDF, choose
David or Adrian, and enter the recipient. Prepare the quote email, review the
frozen attachment, and click **Send queued quote**. A changed quote cancels an old
queued copy and requires fresh preparation. In preview mode nothing is delivered.

Sent means Gmail returned a message ID, not proof of recipient delivery. An
uncertain send is never retried automatically: check that sender's Sent Mail,
then record Confirmed sent or Confirmed not sent with a reason. Repeated sends
require explicit resend selection. Disconnection stops future CRM use and erases
the stored token; users can also revoke app access in their Google account.

Official references:
- https://developers.google.com/identity/protocols/oauth2/web-server
- https://developers.google.com/workspace/gmail/api/auth/scopes

