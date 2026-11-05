---
name: homelab-services
description: Find home files/photos, share links, and manage downloads.
---

# Homelab services

1. Choose the MCP tools for the task:

   | Need | Tools |
   | --- | --- |
   | Find, read or save files | `cloud_*`; list spaces first. OpenCloud holds originals. |
   | Share a file or shorten a URL | `zipline_*`; file links use `i.hchu.me`, short links use `l.hchu.me`. |
   | Find photos or curate albums | Immich tools; keep originals in Immich. |
   | Add, inspect or control a torrent | `qbt_*`; use `content_path` to locate completed files in OpenCloud's Public or Downloads space. |

2. Retrieve only relevant results or files. Read downloaded documents with the
   document/PDF tools. Publish private files through Zipline only when sharing is requested.

3. Continue across services as needed: save requested files to OpenCloud, record
   knowledge using the second-brain rules, and put requested follow-ups in TickTick.
   Preserve source URLs and IDs.

4. Verify saved files or albums before reporting success and return their actual
   URL or path. A started torrent remains pending. After processing or delivery,
   remove temporary downloads with `homelab_remove_working_file`.
