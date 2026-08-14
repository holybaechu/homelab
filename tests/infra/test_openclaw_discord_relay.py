import yaml

from tests.helpers import REPO_ROOT


ROLE_ROOT = REPO_ROOT / "infra/ansible/roles/openclaw_discord_relay"


def read(relative_path: str) -> str:
    return (ROLE_ROOT / relative_path).read_text(encoding="utf-8")


def test_isolated_discord_relay_role_uses_a_separate_identity_and_credentials():
    tasks = yaml.safe_load(read("tasks/main.yml"))
    contract = next(
        task
        for task in tasks
        if task["name"] == "Require the isolated Discord relay contract"
    )
    assertions = contract["ansible.builtin.assert"]["that"]

    assert "openclaw_discord_relay_enabled is boolean" in assertions
    assert "openclaw_discord_relay_user != openclaw_user" in assertions
    assert "openclaw_discord_relay_uid | int != 1001" in assertions
    assert "openclaw_discord_relay_gid | int != 1001" in assertions
    assert (
        "openclaw_discord_relay_routes_path == '/etc/openclaw/discord-relay/routes.json'"
        in assertions
    )
    assert (
        "openclaw_relay_core_hmac_path == openclaw_secret_root + '/discord_relay_core_hmac'"
        in assertions
    )
    assert (
        "openclaw_relay_ctf_hmac_path == openclaw_secret_root + '/discord_relay_ctf_hmac'"
        in assertions
    )
    assert "openclaw_gateway_port | int == 18789" in assertions
    assert "openclaw_ctf_gateway_port | int == 19789" in assertions

    bot_token_task = next(
        task
        for task in tasks
        if task["name"] == "Install the shared Discord bot token outside Git"
    )
    assert bot_token_task["ansible.builtin.copy"]["dest"] == (
        "{{ openclaw_discord_bot_token_path }}"
    )
    assert bot_token_task["ansible.builtin.copy"]["mode"] == "0600"
    assert bot_token_task["ansible.builtin.copy"]["owner"] == "root"
    assert bot_token_task["no_log"] is True
    assert bot_token_task["when"] == "openclaw_discord_relay_enabled | bool"

    route_copy = next(
        task
        for task in tasks
        if task["name"]
        == "Copy the private Discord relay route configuration outside the checkout"
    )
    assert route_copy["ansible.builtin.copy"]["src"] == (
        "{{ openclaw_setup_root }}/config/discord-relay-routes.json"
    )
    assert route_copy["ansible.builtin.copy"]["remote_src"] is True
    assert route_copy["ansible.builtin.copy"]["mode"] == "0640"
    assert route_copy["ansible.builtin.copy"]["owner"] == "root"
    assert route_copy["when"] == "openclaw_discord_relay_enabled | bool"


def test_discord_relay_unit_scopes_systemd_credentials_and_hardens_the_process():
    service = read("templates/openclaw-discord-relay.service.j2")

    for required in (
        "User={{ openclaw_discord_relay_user }}",
        "Group={{ openclaw_discord_relay_group }}",
        "Environment=NODE_PATH={{ openclaw_current_root }}/lib/node_modules/openclaw/node_modules",
        "Environment=OPENCLAW_DISCORD_BOT_TOKEN_FILE=%d/discord_bot_token",
        "Environment=OPENCLAW_RELAY_CORE_HMAC_FILE=%d/relay_core_hmac",
        "Environment=OPENCLAW_RELAY_CTF_HMAC_FILE=%d/relay_ctf_hmac",
        "Environment=OPENCLAW_RELAY_CORE_ENDPOINT=http://127.0.0.1:{{ openclaw_gateway_port }}/internal/discord-relay",
        "Environment=OPENCLAW_RELAY_CTF_ENDPOINT=http://127.0.0.1:{{ openclaw_ctf_gateway_port }}/internal/discord-relay",
        "LoadCredential=discord_bot_token:{{ openclaw_discord_bot_token_path }}",
        "LoadCredential=relay_core_hmac:{{ openclaw_relay_core_hmac_path }}",
        "LoadCredential=relay_ctf_hmac:{{ openclaw_relay_ctf_hmac_path }}",
        "NoNewPrivileges=true",
        "PrivateDevices=true",
        "PrivateTmp=true",
        "ProtectHome=true",
        "ProtectSystem=strict",
        "ProtectProc=invisible",
        "RestrictNamespaces=true",
        "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6",
        "CapabilityBoundingSet=",
        "InaccessiblePaths={{ openclaw_secret_root }}",
        "InaccessiblePaths=/srv/openclaw-ctf",
        "InaccessiblePaths={{ openclaw_ctf_home }}",
        "InaccessiblePaths={{ openclaw_ctf_state_root }}",
        "InaccessiblePaths={{ openclaw_ctf_cache_root }}",
        "InaccessiblePaths={{ openclaw_ctf_plugin_root }}",
        "InaccessiblePaths={{ openclaw_ctf_docker_cli_path | dirname }}",
        "InaccessiblePaths=/run/docker.sock",
        "InaccessiblePaths=/var/run/docker.sock",
    ):
        assert required in service

    assert "LoadCredential=openclaw_gateway_token:" not in service
    assert "LoadCredential=ctf_docker_" not in service
    assert "OPENCLAW_CTF_OPENAI_API_KEY_FILE" not in service
    assert "ctf_openai_api_key" not in service
    assert "DOCKER_HOST" not in service
    assert "DOCKER_SSH_COMMAND" not in service
    assert "OPENCLAW_GATEWAY_TOKEN_FILE" not in service


def test_discord_relay_process_only_routes_safe_guild_messages_to_fixed_hmac_endpoints():
    relay = read("files/openclaw-discord-relay.cjs")

    assert 'const WebSocket = require("ws");' in relay
    assert 'const DISCORD_GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json";' in relay
    assert 'payload.t !== "MESSAGE_CREATE"' in relay
    assert "!isSnowflake(message.guild_id) || !isSnowflake(message.channel_id)" in relay
    assert "message.author.bot || message.webhook_id" in relay
    assert "const target = routes.get(message.channel_id);" in relay
    assert "route.target !== \"core\" && route.target !== \"ctf\"" in relay
    assert "keys.length !== 1 || keys[0] !== \"target\"" in relay
    assert 'url.hostname !== "127.0.0.1"' in relay
    assert 'url.pathname !== "/internal/discord-relay"' in relay
    assert 'crypto.createHmac("sha256", token)' in relay
    assert '"x-openclaw-relay-signature": signature' in relay
    assert "normalizeAttachments(message.attachments)" in relay
    assert "allowed_mentions: { parse: [] }" in relay
    assert "new Blob([artifact.bytes], { type: \"application/zip\" })" in relay
    assert "DOCKER_HOST" not in relay
    assert "/var/run/docker.sock" not in relay
    assert "/srv/openclaw-ctf" not in relay


def test_ctf_attachment_and_zip_handoff_is_bounded_and_sticky_to_the_source_channel():
    relay = read("files/openclaw-discord-relay.cjs")

    # CTF turns may take several minutes, but one request cannot wait forever.
    assert "const REQUEST_TIMEOUT_MS = 10 * 60 * 1000;" in relay
    assert "const REST_TIMEOUT_MS = 30 * 1000;" in relay
    assert "const MAX_ATTACHMENT_COUNT = 10;" in relay
    assert "const MAX_PLUGIN_REQUEST_BYTES = 768 * 1024;" in relay
    assert "const MAX_PLUGIN_RESPONSE_BYTES = 35 * 1024 * 1024;" in relay
    assert "const MAX_ARTIFACT_BYTES = 25 * 1024 * 1024;" in relay
    assert "value.slice(0, MAX_ATTACHMENT_COUNT)" in relay
    assert "sanitizeDiscordAssetUrl(attachment.url)" in relay
    assert "DISCORD_ASSET_HOSTS" in relay
    assert "url.port ||" in relay
    assert "url.username ||" in relay
    assert "url.password" in relay

    # The relay passes only bounded metadata into CTF. It never writes inbound
    # challenge data or chooses a destination supplied by the plugin.
    assert "normalizeAttachments(message.attachments)" in relay
    assert "const channelId = inbound.message.channelId;" in relay
    assert "await sendPluginResponse(configuration.token, channelId, response);" in relay
    assert "const url = `${DISCORD_API_BASE_URL}/channels/${channelId}/messages`;" in relay
    assert "fs.writeFile" not in relay
    assert "openclaw_ctf_workspace_root" not in relay

    # Egress admits one safe, bounded ZIP only. A plugin cannot use the relay
    # to name arbitrary paths, send arbitrary bytes, or mention users/roles.
    for required in (
        "const ZIP_FILENAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,119}\\.zip$/i;",
        "if (!ZIP_FILENAME_RE.test(value.filename)",
        "if (value.base64.length > Math.ceil((MAX_ARTIFACT_BYTES * 4) / 3) + 4)",
        "bytes[0] !== 0x50 || bytes[1] !== 0x4b",
        "new Blob([artifact.bytes], { type: \"application/zip\" })",
        "allowed_mentions: { parse: [] }",
    ):
        assert required in relay


def test_relay_activation_exercises_the_bounded_zip_response_contract():
    tasks = yaml.safe_load(read("tasks/main.yml"))
    route_coverage = next(
        task
        for task in tasks
        if task["name"]
        == "Require Discord relay route coverage to match Gateway allowlists"
    )
    ctf_transfer_check = next(
        task
        for task in tasks
        if task["name"]
        == "Verify the staged CTF publish-tool handler before relay activation"
    )
    artifact_check = next(
        task
        for task in tasks
        if task["name"]
        == "Verify the bounded CTF ZIP artifact contract before relay activation"
    )

    coverage_command = route_coverage["ansible.builtin.command"]
    assert coverage_command["argv"][0] == "{{ openclaw_node_current_root }}/bin/node"
    assert coverage_command["argv"][-3:] == [
        "{{ openclaw_discord_relay_routes_path }}",
        "{{ openclaw_config_path }}",
        "{{ openclaw_ctf_gateway_source_config_path }}",
    ]
    for required in (
        "relay.parseRoutes",
        "openclaw-discord-relay-core",
        "openclaw-discord-relay-ctf",
        "assert.deepEqual",
    ):
        assert required in coverage_command["argv"][2]
    assert route_coverage["when"] == "openclaw_discord_relay_enabled | bool"
    assert route_coverage["no_log"] is True

    ctf_command = ctf_transfer_check["ansible.builtin.command"]
    assert ctf_command["argv"][0] == "{{ openclaw_node_current_root }}/bin/node"
    assert ctf_command["argv"][-2:] == [
        "{{ openclaw_ctf_plugin_root }}/openclaw.plugin.json",
        "{{ openclaw_ctf_plugin_root }}/index.js",
    ]
    for required in (
        "registerTool",
        "ctf_publish",
        "media/inbound",
        "runEmbeddedAgent",
        "exports",
    ):
        assert required in ctf_command["argv"][2]
    assert ctf_transfer_check["when"] == "openclaw_discord_relay_enabled | bool"
    assert ctf_transfer_check["no_log"] is True

    command = artifact_check["ansible.builtin.command"]
    assert command["argv"][0] == "{{ openclaw_node_current_root }}/bin/node"
    assert "relay.parsePluginResponse" in command["argv"][2]
    assert "ctf-result.zip" in command["argv"][2]
    assert "../escape.zip" in command["argv"][2]
    assert "not-a-zip" in command["argv"][2]
    assert artifact_check["when"] == "openclaw_discord_relay_enabled | bool"
    assert artifact_check["changed_when"] is False


def test_validation_keeps_ctf_transfer_data_out_of_the_relay_identity():
    validation = (REPO_ROOT / "infra/ansible/playbooks/validate.yml").read_text(
        encoding="utf-8"
    )

    for required in (
        "test -r '{{ openclaw_ctf_plugin_root }}/openclaw.plugin.json'",
        "grep -Fq 'ctf_publish' '{{ openclaw_ctf_plugin_root }}/openclaw.plugin.json'",
        "grep -Fq 'registerTool' '{{ openclaw_ctf_plugin_root }}/index.js'",
        "grep -Fq 'media/inbound' '{{ openclaw_ctf_plugin_root }}/index.js'",
        "grep -Fq 'runEmbeddedAgent' '{{ openclaw_ctf_plugin_root }}/index.js'",
        "grep -Fq 'exports' '{{ openclaw_ctf_plugin_root }}/index.js'",
        "InaccessiblePaths={{ openclaw_ctf_home }}",
        "InaccessiblePaths={{ openclaw_ctf_state_root }}",
        "InaccessiblePaths={{ openclaw_ctf_cache_root }}",
        "InaccessiblePaths={{ openclaw_ctf_plugin_root }}",
        "InaccessiblePaths={{ openclaw_ctf_docker_cli_path | dirname }}",
        "Environment=OPENCLAW_CTF_OPENAI_API_KEY_FILE=%d/ctf_openai_api_key",
        "LoadCredential=ctf_openai_api_key:",
        "! systemctl cat openclaw-gateway.service | grep -Fq 'ctf_openai_api_key'",
        "! systemctl cat openclaw-discord-relay.service | grep -Fq 'ctf_openai_api_key'",
        "! runuser -u '{{ openclaw_discord_relay_user }}' -- /usr/bin/test -r '{{ openclaw_ctf_workspace_root }}'",
        "! runuser -u '{{ openclaw_discord_relay_user }}' -- /usr/bin/test -r '{{ openclaw_ctf_state_root }}'",
        "! runuser -u '{{ openclaw_discord_relay_user }}' -- /usr/bin/test -r '{{ openclaw_ctf_plugin_root }}'",
        "! runuser -u '{{ openclaw_discord_relay_user }}' -- /usr/bin/test -x '{{ openclaw_ctf_docker_cli_path }}'",
    ):
        assert required in validation
