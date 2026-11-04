# Temporary MySQL

Single MySQL 8.4 instance with local-path persistent storage. No backup is
configured. Binary logging is disabled for this temporary workload. TCP
connections require TLS, using a Let's Encrypt certificate renewed by cert-manager.

## Credentials

Create a Login item named `MySQL Temporary` in the `Homelab` 1Password vault:

- `username`: `temp`
- `password`: generated application password
- `root_password`: separate generated password, custom field outside any section

External Secrets reads these fields through the references in
`kubernetes/clusters/homelab/onepassword.yaml`. The application user has access to
the `temp` database. Root access is restricted to localhost inside the container.
Credentials initialize an empty data directory only: editing 1Password later does
not change existing MySQL accounts. Use SQL to rotate an existing account and
update 1Password to match.

## Connect

Public endpoint: `mysql.hchu.me:3306`, database `temp`, username `temp`.
The router must forward WAN TCP/3306 to `192.168.0.2:30306` (the MySQL NodePort).
Cloudflare DDNS maintains a DNS-only record; this is a native MySQL connection.
Use certificate and hostname verification in your client:

```sh
mysql --host=mysql.hchu.me --port=3306 --user=temp --password --database=temp --ssl-mode=VERIFY_IDENTITY
```

The client needs its operating system CA trust bundle configured (use `--ssl-ca`
if the client does not load it automatically).

Inside Kubernetes: `mysql.homelab.svc.cluster.local:3306`, database `temp`.

From your computer, keep this running:

```sh
kubectl -n homelab port-forward svc/mysql 3306:3306
```

Then connect to `127.0.0.1:3306`, database `temp`, with the application credentials
and TLS enabled (`--ssl-mode=REQUIRED` for this local tunnel).

## Remove after use

Export any data you need first. Remove `mysql` from the apps kustomization and the
three `MYSQL_*_REF` entries from the 1Password references, remove `mysql.${DOMAIN}`
from Cloudflare DDNS, then reconcile Flux. Remove the router forwarding rule and
the Cloudflare DNS record too (DDNS does not delete records on shutdown).
Flux pruning deletes the PVC and local-path data as well as the workload.
For a directly applied deployment, `kubectl delete -k kubernetes/apps/mysql` has
the same data deletion consequence. Delete the 1Password item separately.
