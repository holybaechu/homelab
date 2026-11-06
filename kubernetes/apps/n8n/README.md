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

[gmail-hermes.json](gmail-hermes.json) uses only n8n's built-in **Gmail Trigger**
and **HTTP Request** nodes. Every five minutes, the trigger checks newly
received inbox mail, including messages already read on another device. Its
first scheduled check establishes a baseline without notifying old unread mail.
Only message/thread IDs go to Hermes; its bundled Google Workspace skill reads
the message and its existing memory/wiki/TickTick context informs the decision.
[Hermes's webhook configuration](../hermes/webhook.yaml) owns the judgment and
Korean notification format. `[SILENT]` suppresses unimportant messages; useful
alerts go to the existing Discord home channel and remain available for replies.
No custom watcher, classifier service, or Code node is involved.

The existing Hermes Gmail authorization is imported into n8n's native
`gmailOAuth2` credential once, using the official credential import command.
Subsequent token refreshes belong to n8n's encrypted credential store. For
reconnection, reuse the original Google OAuth client/authorization. Connecting
from n8n's own UI instead requires a Web OAuth client with its displayed callback
URL registered in Google Cloud; do not overwrite the working Hermes profile.

After restoring or creating an instance, import the two native credentials and
then run these commands inside the n8n container as the `node` user:

1. `n8n import:workflow --input=/path/to/gmail-hermes.json`
2. `n8n publish:workflow --id=gmailHermesTriage`
3. Restart n8n if the CLI says publication requires it, then check the published
   workflow and its first scheduled poll in the UI. Manual trigger tests can
   return existing mail; do not use them to test the new-mail baseline.

The HTTP node retries failed handoffs and sends the Gmail message ID as an
idempotency key. Hermes accepts asynchronously; HTTP acceptance does not prove
the later AI judgment or Discord delivery succeeded. Inspect Hermes when a
handoff succeeds but an expected alert is missing. Successful n8n payloads are
not retained; failed executions and Hermes tracing follow their native settings.

## Secrets and recovery

External Secrets supplies the shared token to both apps. At n8n startup, its
official `import:credentials` command reconciles only the named **Hermes webhook**
header credential. `X-Gitlab-Token` is Hermes's existing shared-token auth header;
no HMAC implementation is added. The webhook service is cluster-only. Secret
refresh uses the shared store/cache and interval; Reloader restarts the affected
apps so rotation reaches both the gateway and native n8n credential.

Keep the original `encryption_key` with a backup of the entire `n8n-data` volume.
Never replace or rotate that key alone: existing credentials become unreadable.
For a consistent SQLite backup, stop n8n before copying the volume, then restart
it; restore the volume and original 1Password key together. Removing this app
from the Flux apps layer prunes its PVC and can delete local-path data. Back it
up first and unpublish the Gmail workflow before removing either side.

[n8n hosting](https://docs.n8n.io/hosting/installation/docker/),
[Gmail Trigger](https://docs.n8n.io/integrations/builtin/trigger-nodes/n8n-nodes-base.gmailtrigger/),
[Server CLI](https://docs.n8n.io/hosting/cli-commands/).
