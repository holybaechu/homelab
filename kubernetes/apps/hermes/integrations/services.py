"""Service operations used only by the homelab MCP server."""

import asyncio
import hashlib
from pathlib import Path, PurePosixPath
import re
import tempfile
import uuid
from urllib.parse import quote, unquote, urlsplit, urlunsplit
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape

import httpx


CREDENTIALS = Path("/opt/homelab/credentials")
WORKSPACE = Path("/opt/data/workspace/homelab")
CLOUD = "https://cloud.hchu.me"
ZIPLINE = "http://zipline:3000"
COBALT = "http://cobalt:8080/api/"
DAV = "{DAV:}"
OC = "{http://owncloud.org/ns}"
PROPERTIES = ('<d:prop xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:resourcetype/>'
              '<d:getcontentlength/><d:getlastmodified/><d:getetag/><oc:fileid/><oc:privatelink/></d:prop>')


def credential(name: str) -> str:
    value = (CREDENTIALS / name).read_text().strip()
    if not value or "\n" in value or "\r" in value:
        raise ValueError(f"Missing or invalid {name}; check the hermes-services Secret")
    return value


def local_source(value: str) -> Path:
    path = Path(value).resolve(strict=True)
    roots = (Path("/opt/data/workspace"), Path("/opt/data/obsidian/vault"))
    if not path.is_file() or not any(path.is_relative_to(root) for root in roots):
        raise ValueError("Choose a regular file inside the Hermes workspace or Obsidian vault")
    return path


def remote_path(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or any(ord(c) < 32 for c in value):
        raise ValueError("Use a path relative to the selected OpenCloud space, without '..'")
    return "" if str(path) == "." else str(path)


def select_space(drives: list[dict], selector: str) -> dict:
    matches = [d for d in drives if d["id"] == selector or d["name"].casefold() == selector.casefold()]
    if selector == "personal":
        matches = [d for d in drives if d.get("driveType") == "personal"]
    if len(matches) != 1:
        raise ValueError("Choose an exact space name or ID from cloud_list_spaces")
    return matches[0]


def dav_url(space: dict, path: str = "") -> str:
    base = space["root"]["webDavUrl"]
    if urlsplit(base).scheme != "https" or urlsplit(base).netloc != urlsplit(CLOUD).netloc:
        raise ValueError("OpenCloud returned an unexpected WebDAV origin")
    return base.rstrip("/") + "/" + quote(remote_path(path), safe="/")


def dav_items(content: bytes, base: str) -> list[dict]:
    base_path = unquote(urlsplit(base).path).rstrip("/") + "/"
    items = []
    for response in ET.fromstring(content).findall(DAV + "response"):
        href = response.findtext(DAV + "href", "")
        path = unquote(urlsplit(href).path)
        if not path.startswith(base_path) or path.rstrip("/") == base_path.rstrip("/"):
            continue
        for propstat in response.findall(DAV + "propstat"):
            if " 200 " not in propstat.findtext(DAV + "status", ""):
                continue
            prop = propstat.find(DAV + "prop")
            if prop is None:
                continue
            relative = remote_path(path[len(base_path):].rstrip("/"))
            items.append({"path": relative, "name": PurePosixPath(relative).name,
                          "is_directory": prop.find(DAV + "resourcetype/" + DAV + "collection") is not None,
                          "bytes": int(prop.findtext(DAV + "getcontentlength", "0") or 0),
                          "modified": prop.findtext(DAV + "getlastmodified"),
                          "etag": prop.findtext(DAV + "getetag"),
                          "id": prop.findtext(OC + "fileid"), "web_url": prop.findtext(OC + "privatelink")})
    return sorted(items, key=lambda item: (not item["is_directory"], item["path"].casefold()))


def cobalt_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Cobalt returned an unsupported download URL")
    if parsed.netloc == "cobalt.hchu.me":
        return urlunsplit(("http", "cobalt:8080", parsed.path, parsed.query, ""))
    return value


def http_url(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
            or any(ord(c) < 32 for c in value)):
        raise ValueError("Provide an HTTP(S) URL without embedded credentials")
    return value


class Services:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http
        self._qbt = None
        self._qbt_lock = asyncio.Lock()

    def cloud_auth(self) -> httpx.BasicAuth:
        return httpx.BasicAuth(credential("OPENCLOUD_USERNAME"), credential("OPENCLOUD_APP_TOKEN"))

    async def drives(self) -> list[dict]:
        response = await self.http.get(CLOUD + "/graph/v1.0/drives", auth=self.cloud_auth())
        response.raise_for_status()
        return [d for d in response.json().get("value", []) if d.get("driveType") != "virtual"]

    async def cloud_list_spaces(self) -> dict:
        return {"spaces": [{"id": d["id"], "name": d["name"], "type": d["driveType"],
                            "web_url": d.get("webUrl")} for d in await self.drives()]}

    async def cloud_list_files(self, space: str, path: str, limit: int, offset: int) -> dict:
        drive = select_space(await self.drives(), space)
        response = await self.http.request("PROPFIND", dav_url(drive, path), auth=self.cloud_auth(),
                                           headers={"Depth": "1", "Content-Type": "application/xml"},
                                           content=f'<d:propfind xmlns:d="DAV:">{PROPERTIES}</d:propfind>')
        response.raise_for_status()
        items = dav_items(response.content, dav_url(drive))
        # WebDAV includes the requested directory itself; keep only its children.
        selected = remote_path(path)
        items = [item for item in items if item["path"] != selected]
        return {"space": drive["name"], "path": selected, "items": items[offset:offset + limit],
                "next_offset": offset + limit if offset + limit < len(items) else None}

    async def cloud_search_files(self, query: str, space: str, limit: int) -> dict:
        drive = select_space(await self.drives(), space)
        # OpenCloud 7.2 parses but ignores search offsets. Ask for a bounded page
        # and report truncation so callers refine their query instead of looping.
        body = ('<oc:search-files xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">'
                + PROPERTIES + '<oc:search><oc:pattern>' + escape(query)
                + f'</oc:pattern><oc:limit>{limit}</oc:limit></oc:search></oc:search-files>')
        response = await self.http.request("REPORT", dav_url(drive).rstrip("/"), auth=self.cloud_auth(),
                                           headers={"Content-Type": "application/xml"}, content=body)
        response.raise_for_status()
        items = dav_items(response.content, dav_url(drive))
        content_range = response.headers.get("Content-Range", "")
        total_match = re.search(r"/(\d+)$", content_range)
        total = int(total_match.group(1)) if total_match else None
        truncated = total > len(items) if total is not None else len(items) >= limit
        return {"space": drive["name"], "items": items, "total_matches": total,
                "truncated": truncated,
                "next_step": "Refine the query to narrow results" if truncated else None}

    async def download(self, url: str, filename: str, *, auth=None) -> dict:
        WORKSPACE.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="download-", dir=WORKSPACE))
        name = PurePosixPath(filename.replace("\\", "/")).name
        target = folder / (name if name not in {"", ".", ".."} else "download.bin")
        digest = hashlib.sha256()
        try:
            async with self.http.stream("GET", url, auth=auth, follow_redirects=True,
                                        timeout=httpx.Timeout(120, connect=10)) as response:
                response.raise_for_status()
                with target.open("xb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
                        digest.update(chunk)
        except BaseException:
            target.unlink(missing_ok=True)
            folder.rmdir()
            raise
        return {"local_path": str(target), "bytes": target.stat().st_size, "sha256": digest.hexdigest()}

    async def cloud_download_file(self, space: str, path: str) -> dict:
        path = remote_path(path)
        if not path:
            raise ValueError("Specify a file path from cloud_list_files or cloud_search_files")
        drive = select_space(await self.drives(), space)
        return {**await self.download(dav_url(drive, path), PurePosixPath(path).name, auth=self.cloud_auth()),
                "space": drive["name"], "source_path": path}

    async def cloud_upload_file(self, local_path: str, space: str, path: str) -> dict:
        source = local_source(local_path)
        path = remote_path(path)
        if not path:
            raise ValueError("Specify a destination filename inside the selected space")
        drive = select_space(await self.drives(), space)
        async def chunks():
            with source.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    yield chunk
        temporary = str(PurePosixPath(path).parent / (".hermes-upload-" + uuid.uuid4().hex))
        temporary_url = dav_url(drive, temporary)
        moved = False
        try:
            response = await self.http.put(temporary_url, auth=self.cloud_auth(), content=chunks(),
                                           headers={"Content-Length": str(source.stat().st_size)},
                                           timeout=httpx.Timeout(300, connect=10))
            response.raise_for_status()
            # This OpenCloud version ignores PUT If-None-Match:*. MOVE with
            # Overwrite:F enforces the conflict check on the server instead.
            response = await self.http.request("MOVE", temporary_url, auth=self.cloud_auth(),
                                               headers={"Destination": dav_url(drive, path), "Overwrite": "F"})
            response.raise_for_status()
            moved = True
        finally:
            if not moved:
                await self.http.delete(temporary_url, auth=self.cloud_auth())
        return {"space": drive["name"], "path": path, "bytes": source.stat().st_size,
                "etag": response.headers.get("etag")}

    async def zipline_upload_file(self, local_path: str) -> dict:
        source = local_source(local_path)
        with source.open("rb") as file:
            response = await self.http.post(ZIPLINE + "/api/upload", files={"file": (source.name, file)},
                                            headers={"Authorization": credential("ZIPLINE_TOKEN"),
                                                     "X-Zipline-Domain": "i.hchu.me"}, timeout=300)
        response.raise_for_status()
        return response.json()

    async def zipline_shorten_url(self, url: str, slug: str | None) -> dict:
        body = {"destination": http_url(url)}
        if slug:
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", slug):
                raise ValueError("Use 1–100 letters, numbers, underscores or hyphens for the slug")
            body["vanity"] = slug
        response = await self.http.post(ZIPLINE + "/api/user/urls", json=body,
                                        headers={"Authorization": credential("ZIPLINE_TOKEN"),
                                                 "X-Zipline-Domain": "l.hchu.me"})
        response.raise_for_status()
        return response.json()

    async def cobalt_download(self, url: str, audio_only: bool, quality: str, selection: int | None) -> dict:
        response = await self.http.post(COBALT, headers={"Accept": "application/json"},
                                       json={"url": http_url(url), "videoQuality": quality,
                                       "downloadMode": "audio" if audio_only else "auto",
                                       "localProcessing": "disabled", "alwaysProxy": True})
        result = response.json()
        if result.get("status") == "error":
            code = str(result.get("error", {}).get("code", "unknown"))
            if not re.fullmatch(r"[\w.]+", code):
                code = "unknown"
            raise ValueError(f"Cobalt could not process this source ({code}); try another supported URL")
        response.raise_for_status()
        if result.get("status") == "picker":
            items = result.get("picker", [])
            if selection is None:
                return {"status": "selection_required", "items": [
                    {"selection": i, "type": item.get("type")} for i, item in enumerate(items)],
                    "next_step": "Call cobalt_download with the same URL and a selection index"}
            if selection < 0 or selection >= len(items):
                raise ValueError("Choose a selection index returned by cobalt_download")
            result = {"status": "tunnel", "url": items[selection]["url"], "filename": "selected-media.bin"}
        if result.get("status") not in {"tunnel", "redirect"}:
            raise ValueError("Cobalt requires unsupported local processing for this URL; try audio_only or another source")
        return {**await self.download(cobalt_url(result["url"]), result.get("filename", "download.bin")),
                "source_url": url}

    async def qbt(self, action: str, **kwargs) -> dict:
        # The maintained library owns session authentication and API-version differences.
        async with self._qbt_lock:
            return await asyncio.to_thread(self._qbt_operation, action, **kwargs)

    def _qbt_operation(self, action: str, **kwargs) -> dict:
        import qbittorrentapi

        if self._qbt is None:
            self._qbt = qbittorrentapi.Client(host="http://qbittorrent:8080",
                        username=credential("QBITTORRENT_USERNAME"), password=credential("QBITTORRENT_PASSWORD"),
                        REQUESTS_ARGS={"timeout": (5, 30)})
        client = self._qbt
        if action == "status":
            info = client.transfer_info()
            return {"version": client.app_version(), "download_speed": info.dl_info_speed,
                    "upload_speed": info.up_info_speed, "connection": info.connection_status}
        if action == "list":
            items = client.torrents_info(status_filter=kwargs["status_filter"], limit=kwargs["limit"], offset=kwargs["offset"])
            return {"items": [{key: t.get(key) for key in (
                "hash", "name", "state", "progress", "dlspeed", "upspeed", "eta", "content_path",
            )} for t in items], "next_offset": kwargs["offset"] + len(items) if len(items) == kwargs["limit"] else None}
        if action == "add":
            url = kwargs["url"]
            if not url.startswith("magnet:?"):
                http_url(url)
            result = client.torrents_add(urls=url)
            if str(result).strip().lower() != "ok.":
                raise ValueError("qBittorrent rejected the torrent; verify its magnet link or torrent URL")
            return {"result": "accepted", "next_step": "Use qbt_list_torrents to verify progress and content_path"}
        torrent_hash = kwargs["torrent_hash"]
        if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", torrent_hash):
            raise ValueError("Choose a single torrent hash from qbt_list_torrents")
        operation = client.torrents_start if kwargs["running"] else client.torrents_stop
        operation(torrent_hashes=torrent_hash)
        return {"hash": torrent_hash, "running_requested": kwargs["running"]}


def remove_working_file(local_path: str) -> dict:
    path = Path(local_path).resolve(strict=True)
    if not path.is_relative_to(WORKSPACE.resolve()) or not path.is_file() or not path.parent.name.startswith("download-"):
        raise ValueError("Choose a temporary file returned by a homelab download tool")
    path.unlink()
    if not any(path.parent.iterdir()):
        path.parent.rmdir()
    return {"removed": local_path}
