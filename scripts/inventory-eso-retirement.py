#!/usr/bin/env python3
"""Read-only administrator inventory. No credential values or raw errors are printed.

A passing inventory is NOT authentication, refresh, or rollback-closure approval.
Run before pruning; retain the printed metadata as the deletion allowlist.
"""
import json
import subprocess
import sys


TARGETS = {
    "cert-manager": {"cloudflare-dns"},
    "homelab": {
        "cloudflare-ddns", "hermes-browser", "hermes-coder-git", "hermes-discord",
        "hermes-immich", "hermes-langfuse", "hermes-obsidian", "hermes-services",
        "hermes-web", "hermes-webhook", "immich", "langfuse-env", "mysql-env",
        "n8n-env", "n8n-hermes-credentials", "qbittorrent-credentials", "zipline-env",
    },
    "identity": {"authentik-env", "authentik-postgresql", "headscale-oidc"},
    "networking": {"adguard-credentials", "headscale-gateway"},
}
ITEMS = {
    "cert-manager": {"cloudflare-connect": "34qgxb73mblpjwblihxkmu6uam"},
    "homelab": {
        "cloudflare-connect": "34qgxb73mblpjwblihxkmu6uam",
        "github-connect": "ioa2eqgh3qmg36ecdjvwx7bpo4",
        "hermes-runtime-connect": "rvmf543rf6sefjrqiu7csfa2ra",
        "immich-connect": "ltncwd5wyxwknf5xfqwxraqmtm",
        "langfuse-connect": "77j3ul6ntreertxl3bjkdko4h4",
        "mysql-connect": "awdei3gge3fnlyv3qyymied62u",
        "n8n-connect": "6esl52kqp2arngw72jrteqdbge",
        "obsidian-connect": "3galz5zqqwdrqoj2mjk4wcxfdy",
        "opencloud-connect": "kxywehnrosbeoxfby2ujyntdo4",
        "qbittorrent-connect": "ztl6gih3jqixgb5xufbv4xq4bi",
        "signing-connect": "fizdpxpyp5ommiouutzdlsgmnu",
        "zipline-connect": "ia7seg53btlz2t4xluwkzmfjly",
    },
    "identity": {
        "authentik-connect": "d7ginmnpodzvevvb472v26jvmq",
        "headscale-connect": "vyenqrf6ko2aficwt3dyaao2de",
        "immich-connect": "ltncwd5wyxwknf5xfqwxraqmtm",
        "langfuse-connect": "77j3ul6ntreertxl3bjkdko4h4",
        "zipline-connect": "ia7seg53btlz2t4xluwkzmfjly",
    },
    "networking": {
        "adguard-connect": "n2olk4p3ngumyuttl6b7itutba",
        "headscale-connect": "vyenqrf6ko2aficwt3dyaao2de",
    },
}
GROUPS = {"external-secrets.io", "generators.external-secrets.io"}
VAULT = "3ryhjdiydkomgh3oxfl5yrg5j4"


class ReadFailure(Exception):
    pass


def kubectl(*args):
    try:
        result = subprocess.run(["kubectl", *args], capture_output=True, timeout=60)
        if result.returncode:
            raise ReadFailure()
        return json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ReadFailure() from None


def old_references(value, namespace):
    """Inspect reference interfaces, not arbitrary strings or retained ESO templates."""
    found = set()
    if isinstance(value, dict):
        if value.get("kind") == "Secret" and value.get("name") in TARGETS.get(namespace, set()):
            found.add((namespace, value["name"]))
        for key, child in value.items():
            name = None
            if key in {"secretName", "existingSecret", "existingClaimSecret"}:
                name = child
            elif key in {"secretKeyRef", "secretRef", "apiTokenSecretRef",
                         "secret", "nodePublishSecretRef", "imagePullSecrets"}:
                if isinstance(child, dict):
                    name = child.get("name", child.get("secretName"))
                elif isinstance(child, list):
                    found.update((namespace, entry["name"]) for entry in child
                                 if isinstance(entry, dict) and
                                 entry.get("name") in TARGETS.get(namespace, set()))
            if isinstance(name, str) and name in TARGETS.get(namespace, set()):
                found.add((namespace, name))
            found.update(old_references(child, namespace))
    elif isinstance(value, list):
        for child in value:
            found.update(old_references(child, namespace))
    return found


def owner_matches(metadata, kind, uid, name):
    group = "onepassword.com/" if kind == "OnePasswordItem" else "external-secrets.io/"
    return not metadata.get("deletionTimestamp") and any(
        owner.get("kind") == kind and owner.get("uid") == uid
        and owner.get("name") == name and owner.get("apiVersion", "").startswith(group)
        and (kind == "OnePasswordItem" or owner.get("controller") is True)
        for owner in metadata.get("ownerReferences", []))


def main():
    failures = 0

    def check(ok, label):
        nonlocal failures
        print(("PASS " if ok else "FAIL ") + label)
        failures += not ok

    def read(label, *args):
        try:
            return kubectl(*args)
        except ReadFailure:
            check(False, label + " read-denied-or-failed")
            return None

    expected = {(ns, name) for ns, names in TARGETS.items() for name in names}
    live = set()
    groups = read("API discovery", "get", "--raw", "/apis")
    if groups is not None:
        available = {g["name"]: g for g in groups["groups"]}
        for group in sorted(GROUPS):
            definition = available.get(group)
            check(definition is not None, group + " installed")
            if not definition:
                continue
            resources = {}
            versions = [definition["preferredVersion"]] + definition["versions"]
            for version in dict.fromkeys(v["groupVersion"] for v in versions):
                discovery = read(group + " discovery", "get", "--raw", "/apis/" + version)
                if discovery is None:
                    continue
                for resource in discovery["resources"]:
                    if "/" not in resource["name"] and "list" in resource.get("verbs", []):
                        resources.setdefault(resource["name"], resource)
            for name, resource in sorted(resources.items()):
                args = ["get", name + "." + group, "-o", "json"]
                if resource["namespaced"]:
                    args.append("-A")
                inventory = read(group + "/" + name, *args)
                if inventory is None:
                    continue
                entries = inventory["items"]
                print(f"INFO {group}/{name} count={len(entries)}")
                for entry in entries:
                    meta = entry["metadata"]
                    ns = meta.get("namespace", "")
                    print(f"INFO instance {group}/{name} {ns}/{meta['name']}")
                    if group == "external-secrets.io" and name == "externalsecrets":
                        spec = entry["spec"]
                        target = spec.get("target", {}).get("name", meta["name"])
                        pair = (ns, target)
                        live.add(pair)
                        check(pair in expected, f"reviewed-target {ns}/{target}")
                        metadata = read("retained-owner", "get", "secret", target, "-n", ns,
                                        "-o", "jsonpath={.metadata}")
                        if metadata is not None:
                            check(owner_matches(metadata, "ExternalSecret", meta["uid"], meta["name"]),
                                  f"retained-owner {ns}/{target}")
                        policy = spec.get("target", {})
                        print(f"INFO policy {ns}/{target} creation={policy.get('creationPolicy', 'Owner')} "
                              f"deletion={policy.get('deletionPolicy', 'Retain')}")
                    else:
                        check(group == "external-secrets.io" and name == "clustersecretstores"
                              and meta["name"] == "onepassword", "reviewed-store-or-empty-kind")
        check(live == expected, f"retained-target-set actual={len(live)} expected={len(expected)}")

    for ns, items in ITEMS.items():
        for name, item_id in items.items():
            item = read("replacement-item", "get", "onepassworditem", name, "-n", ns, "-o", "json")
            metadata = read("replacement-owner", "get", "secret", name, "-n", ns,
                            "-o", "jsonpath={.metadata}")
            if item is not None and metadata is not None:
                check(item.get("spec", {}).get("itemPath") == f"vaults/{VAULT}/items/{item_id}"
                      and not item["metadata"].get("deletionTimestamp")
                      and [c.get("status") for c in item.get("status", {}).get("conditions", [])
                           if c.get("type") == "Ready"] == ["True"]
                      and owner_matches(metadata, "OnePasswordItem", item["metadata"]["uid"], name),
                      f"replacement-ready-path-owner {ns}/{name}")

    kinds = ["deployments", "statefulsets", "daemonsets", "replicasets", "replicationcontrollers",
             "pods", "jobs", "cronjobs", "ingresses", "certificates.cert-manager.io",
             "clusterissuers.cert-manager.io", "issuers.cert-manager.io",
             "helmreleases.helm.toolkit.fluxcd.io", "kustomizations.kustomize.toolkit.fluxcd.io"]
    for kind in kinds:
        data = read("consumers " + kind, "get", kind, "-A", "-o", "json")
        if data is None:
            continue
        for entry in data["items"]:
            # Finished Jobs/Pods and scaled-down ReplicaSets cannot consume a Secret.
            if entry["kind"] in {"Pod", "Job"} and (
                entry.get("status", {}).get("phase") in {"Succeeded", "Failed"}
                or entry.get("status", {}).get("completionTime")):
                continue
            if entry["kind"] == "ReplicaSet" and entry["spec"].get("replicas", 1) == 0:
                continue
            meta = entry["metadata"]
            ns = "cert-manager" if entry["kind"] == "ClusterIssuer" else meta.get("namespace", "")
            refs = old_references(entry.get("spec", {}), ns)
            for key, value in meta.get("annotations", {}).items():
                if key == "secret.reloader.stakater.com/reload":
                    refs.update((ns, n.strip()) for n in value.split(",")
                                if n.strip() in TARGETS.get(ns, set()))
            if refs:
                check(False, f"old-consumer {entry['kind']} {ns}/{meta['name']} count={len(refs)}")
        if kind == "clusterissuers.cert-manager.io":
            issuer = next((i for i in data["items"] if i["metadata"]["name"] == "letsencrypt"), {})
            solvers = issuer.get("spec", {}).get("acme", {}).get("solvers", [])
            check(any(s.get("dns01", {}).get("cloudflare", {}).get("apiTokenSecretRef") ==
                      {"name": "cloudflare-connect", "key": "credential"} for s in solvers),
                  "live-letsencrypt-projection (not DNS01 authentication)")

    chart_kinds = ["customresourcedefinitions", "deployments", "services", "serviceaccounts",
                   "roles", "rolebindings", "clusterroles", "clusterrolebindings",
                   "validatingwebhookconfigurations", "mutatingwebhookconfigurations"]
    for kind in chart_kinds:
        data = read("chart ownership " + kind, "get", kind, "-A", "-o", "json")
        if data is None:
            continue
        for entry in data["items"]:
            meta = entry["metadata"]
            annotations = meta.get("annotations", {})
            eso = (annotations.get("meta.helm.sh/release-name") == "external-secrets"
                   and annotations.get("meta.helm.sh/release-namespace") == "external-secrets")
            crd = entry["kind"] == "CustomResourceDefinition" and entry["spec"]["group"] in GROUPS
            if eso or crd:
                print(f"INFO retirement-resource {entry['kind']} {meta.get('namespace', '')}/{meta['name']}")
    print("INFO no-writes; authentication/refresh evidence and rollback closure remain separate gates")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
