# n8n

Self-hosted automation at `https://n8n.${DOMAIN}`, accessible through the LAN or
VPN. One official n8n container uses SQLite and keeps workflows, OAuth state,
and binary data on `n8n-data`. Login details and the credential encryption key
belong to the `n8n` item in the `Homelab` 1Password vault.

n8n's supported environment settings provision the owner account from that
item's `username` and `owner_password_hash`; sign in with its saved password.
Owner details are managed at startup rather than through the UI. When changing
the password, update its bcrypt hash in the same item before restarting n8n.

## Gmail attention triage

[gmail-hermes.json](gmail-hermes.json) uses only n8n's built-in **Gmail Trigger**,
**HTTP Request**, and **If** nodes. Every five minutes, the trigger checks newly
received inbox mail, including messages already read on another device. Its
first scheduled check establishes a baseline without notifying old unread mail.
The trigger reads decoded mail without downloading attachments. Perplexity's
`pplx-decider-v1.2` screens the sender, subject, date and plain-text body using
one Choice question. Only a valid `low_priority` answer with probability at
least 0.98 ends processing. This is a conservative initial cutoff, not an
accuracy guarantee. Important or uncertain answers, invalid responses and API
errors go to Hermes. Empty bodies, bodies over 20,000 characters, and MIME types
that may contain attachments bypass screening and go directly to Hermes.

Only message/thread IDs go to Hermes; its Google Workspace skill reads the
message and its memory/wiki/TickTick context informs the final decision.
[Hermes's webhook configuration](../hermes/webhook.yaml) owns the final judgment,
quiet-response format and Korean notifications to the existing Discord home
channel. No custom watcher, classifier service, or Code node is involved.

The existing Hermes Gmail authorization is imported into n8n's native
`gmailOAuth2` credential once, using the official credential import command.
Subsequent token refreshes belong to n8n's encrypted credential store. For
reconnection, reuse the original Google OAuth client/authorization. Connecting
from n8n's own UI instead requires a Web OAuth client with its displayed callback
URL registered in Google Cloud; do not overwrite the working Hermes profile.

After restoring or creating an instance, import the native credentials and
then run these commands inside the n8n container as the `node` user:

1. `n8n import:workflow --input=/path/to/gmail-hermes.json`
2. `n8n publish:workflow --id=gmailHermesTriage`
3. Restart n8n if the CLI says publication requires it, then check the published
   workflow and its first scheduled poll in the UI. Manual trigger tests can
   return existing mail; do not use them to test the new-mail baseline.

When updating this workflow, retain its current `staticData` and the trigger's
node name/ID so the next poll keeps its new-mail cursor. Export the current
workflow first, copy its `staticData` into the import file outside Git, then
import and publish that file. Keep the export for rollback until verification.

The Hermes HTTP node retries failed handoffs and sends the Gmail message ID as an
idempotency key. Hermes accepts asynchronously; HTTP acceptance does not prove
the later AI judgment or Discord delivery succeeded. Inspect Hermes when a
handoff succeeds but an expected alert is missing. Successful n8n payloads are
not retained; failed executions can contain decoded mail and model responses,
and Hermes tracing follows its native settings.

## Secrets and recovery

The official 1Password Operator supplies `n8n-connect`. Both the server and import
container explicitly select `encryption_key`, `username` and `owner_password_hash`;
the item's login password and webhook key are not exposed to either environment.
Reloader watches the replacement through these references. The obsolete
`n8n-env` and `n8n-hermes-credentials` targets are removed; recovering their former
contracts requires the [retirement recovery set](../../platform/onepassword/README.md#ordered-eso-retirement),
not just reverting Secret names. Do not delete a consumed OnePasswordItem.

The Operator supplies `n8n-connect/hermes_webhook_secret` to both apps. At n8n startup, its
official `import:credentials` command reconciles **Hermes webhook** and the native
**Perplexity Decider** API credential. `hermes-runtime-connect/perplexity-api-key`
reuses the existing authoritative runtime key; no key is embedded in the workflow
or environment.
`X-Gitlab-Token` is Hermes's existing shared-token auth header;
no HMAC implementation is added. The webhook service is cluster-only. Secret
refresh uses the Operator's six-hour poll; Reloader restarts the affected
apps so rotation reaches both the gateway and native n8n credentials.

Keep the original `encryption_key` with a backup of the entire `n8n-data` volume.
Never replace or rotate that key alone: existing credentials become unreadable.
For a consistent SQLite backup, stop n8n before copying the volume, then restart
it; restore the volume and original 1Password key together. Removing this app
from the Flux apps layer prunes its PVC and can delete local-path data. Back it
up first and unpublish the Gmail workflow before removing either side.

[n8n hosting](https://docs.n8n.io/hosting/installation/docker/),
[Gmail Trigger](https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.gmailtrigger/),
[Perplexity Decisions API](https://docs.perplexity.ai/docs/decisions/quickstart),
[Server CLI](https://docs.n8n.io/hosting/cli-commands/).
