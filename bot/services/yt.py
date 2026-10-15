from __future__ import annotations
import copy
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional, TYPE_CHECKING

import requests
import yt_dlp

if TYPE_CHECKING:
    from bot import Bot

from bot.config.models import YtModel
from bot.player.enums import TrackType
from bot.player.track import Track
from bot.services import Service as _Service
from bot import errors, app_vars


class YtService(_Service):
    def __init__(self, bot: Bot, config: YtModel):
        self.bot = bot
        self.config = config
        self.name = "yt"
        self.hostnames = [
            "youtube.com",
            "www.youtube.com",
            "m.youtube.com",
            "youtu.be",
            "music.youtube.com",
        ]
        self.is_enabled = self.config.enabled
        self.error_message = ""
        self.warning_message = ""
        self.help = ""
        self.hidden = False
        self._max_retries = 2
        self._ydl_opts_base: Dict[str, Any] = {}

        # High-performance connection pool for instant searches
        self._http_session = requests.Session()
        self._http_session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        })

        # Fast In-Memory Search Cache (TTL: 30 minutes)
        self._search_cache: Dict[str, tuple[float, List[Track]]] = {}
        self._search_cache_lock = threading.Lock()
        self._search_cache_ttl = 1800.0

        # Stream Resolution Cache (TTL: 4 hours, avoids re-resolving streams for repeat/queued tracks)
        self._stream_cache: Dict[str, tuple[float, Track]] = {}
        self._stream_cache_lock = threading.Lock()
        self._stream_cache_ttl = 14400.0

    def initialize(self) -> None:
        self._ydl_opts_base = {
            "format": "bestaudio[ext=m4a]/bestaudio/best",
            "quiet": True,
            "no_warnings": True,
            "source_address": "0.0.0.0",
            "cachedir": os.path.join(app_vars.cache_dir, "yt-dlp"),
            "geo_bypass": True,
            "nocheckcertificate": True,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web", "ios", "mweb"],
                    "skip": ["translated_subs"],
                }
            },
        }
        ffmpeg_dir = app_vars.get_ffmpeg_dir()
        if ffmpeg_dir:
            self._ydl_opts_base["ffmpeg_location"] = ffmpeg_dir

        import shutil
        node_bin = shutil.which("node") or shutil.which("deno")
        if node_bin:
            runtime_name = "node" if "node" in node_bin.lower() else "deno"
            self._ydl_opts_base["js_runtimes"] = {runtime_name: {"path": node_bin}}

    def _pre_warm(self) -> None:
        """Pre-warms TCP/TLS connections to YouTube for instant response."""
        try:
            self._http_session.get("https://www.youtube.com/generate_204", timeout=3)
        except Exception:
            pass
        cookiefile = getattr(self.config, "cookiefile_path", "")
        if cookiefile:
            if not os.path.isabs(cookiefile):
                for base in [
                    app_vars.data_dir,
                    os.path.join(app_vars.data_dir, "data"),
                    app_vars.directory,
                    os.path.join(app_vars.directory, "data"),
                ]:
                    candidate = os.path.join(base, cookiefile)
                    if os.path.isfile(candidate):
                        cookiefile = candidate
                        break
            if os.path.isfile(cookiefile):
                self._ydl_opts_base["cookiefile"] = cookiefile
                logging.info(f"{self.name.upper()} Service: Cookie file found at {cookiefile}")
            else:
                logging.debug(f"{self.name.upper()} Service: Configured cookie file '{cookiefile}' not found.")
        else:
            # Check default locations in roaming user directory (%APPDATA%\TTMediaBot)
            for def_candidate in [
                os.path.join(app_vars.data_dir, "cookies.txt"),
                os.path.join(app_vars.data_dir, "data", "cookies.txt"),
                os.path.join(app_vars.directory, "data", "cookies.txt"),
            ]:
                if os.path.isfile(def_candidate):
                    self._ydl_opts_base["cookiefile"] = def_candidate
                    logging.info(f"{self.name.upper()} Service: Default cookie file found at {def_candidate}")
                    break

        cookie_path = self._ydl_opts_base.get("cookiefile")
        if cookie_path and os.path.isfile(cookie_path):
            import http.cookiejar
            try:
                cj = http.cookiejar.MozillaCookieJar(cookie_path)
                cj.load(ignore_discard=True, ignore_expires=True)
                self._http_session.cookies.update(cj)
                logging.info(f"{self.name.upper()} Service: Loaded cookies into Fast HTTP session from {cookie_path}")
            except Exception as e:
                logging.debug(f"Failed to load cookies into Fast HTTP session: {e}")

    @staticmethod
    def _extract_video_id(url: str) -> Optional[str]:
        if not url:
            return None
        import re
        patterns = [
            r"(?:v=|\/v\/|youtu\.be\/|embed\/|\/shorts\/|\/live\/)([a-zA-Z0-9_-]{11})",
            r"^([a-zA-Z0-9_-]{11})$",
        ]
        for p in patterns:
            m = re.search(p, url)
            if m:
                return m.group(1)
        return None

    def invalidate_cache(self, video_id: str = "", url: str = "") -> None:
        with self._stream_cache_lock:
            if video_id:
                self._stream_cache.pop(video_id, None)
            if url:
                self._stream_cache.pop(url, None)

    def _resolve_direct_innertube(
        self, video_id: str, source_url: str, info: Dict[str, Any]
    ) -> Optional[Track]:
        """
        Fast-Path Stream Resolution using direct YouTube InnerTube API.
        Attempts ANDROID_VR first (direct pure audio streams: itag 251 Opus / itag 140 M4A,
        bypassing video packets for 10x faster buffering and rock-solid playback under heavy CPU/network load).
        Falls back to standard ANDROID client (format 18) when needed.
        """
        clients_to_try = [
            (
                "ANDROID",
                "21.26.364",
                "com.google.android.youtube/21.26.364 (Linux; U; Android 11) gzip",
                30,
            ),
            (
                "ANDROID_VR",
                "1.60.19",
                "Mozilla/5.0 (Linux; Android 10; Quest 2) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                None,
            ),
        ]

        for client_name, client_version, user_agent, sdk_ver in clients_to_try:
            client_ctx: Dict[str, Any] = {
                "clientName": client_name,
                "clientVersion": client_version,
                "hl": "vi",
                "gl": "VN",
            }
            if sdk_ver:
                client_ctx["androidSdkVersion"] = sdk_ver

            payload = {
                "videoId": video_id,
                "context": {"client": client_ctx},
            }
            headers = {
                "User-Agent": user_agent,
                "Content-Type": "application/json",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            }

            try:
                resp = self._http_session.post(
                    "https://www.youtube.com/youtubei/v1/player",
                    json=payload,
                    headers=headers,
                    timeout=3.5,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                playability = data.get("playabilityStatus", {})
                if playability.get("status") != "OK":
                    logging.debug(
                        f"InnerTube ({client_name}) status '{playability.get('status')}' for {video_id}: {playability.get('reason')}"
                    )
                    continue

                streaming = data.get("streamingData", {})
                formats = streaming.get("adaptiveFormats", []) + streaming.get("formats", [])

                # 1. Prioritize dedicated Opus audio stream (itag 251 = webm/opus, ultra-lightweight & highest quality)
                direct_fmt = next((f for f in formats if f.get("itag") == 251 and "url" in f), None)
                # 2. Next, dedicated AAC audio stream (itag 140 = m4a)
                if not direct_fmt:
                    direct_fmt = next((f for f in formats if f.get("itag") == 140 and "url" in f), None)
                # 3. Next, any audio format with direct URL
                if not direct_fmt:
                    direct_fmt = next((f for f in formats if "url" in f and "audio" in f.get("mimeType", "")), None)
                # 4. Next, progressive format 18 (mp4 360p with stereo AAC audio)
                if not direct_fmt:
                    direct_fmt = next((f for f in formats if f.get("itag") == 18 and "url" in f), None)
                # 5. Fallback: Any format with direct URL
                if not direct_fmt:
                    direct_fmt = next((f for f in formats if "url" in f), None)

                if not direct_fmt or not direct_fmt.get("url"):
                    continue

                stream_url = direct_fmt["url"]
                mime = direct_fmt.get("mimeType", "")
                if "webm" in mime or "opus" in mime:
                    fmt_type = "webm"
                elif "mp4" in mime or "mp4a" in mime:
                    fmt_type = "mp4" if "video" in mime else "m4a"
                else:
                    fmt_type = "mp4"

                video_details = data.get("videoDetails", {})
                title = video_details.get("title") or info.get("title") or self.bot.translator.translate("Unknown Title")
                uploader = video_details.get("author") or info.get("uploader") or info.get("channel")
                full_title = f"{title} - {uploader}" if uploader and uploader not in title else title
                is_live = bool(video_details.get("isLiveContent"))

                track_extra = copy.deepcopy(info)
                track_extra.update({
                    "id": video_id,
                    "videoId": video_id,
                    "title": title,
                    "uploader": uploader,
                    "duration": int(video_details.get("lengthSeconds", 0)),
                    "view_count": int(video_details.get("viewCount", 0)),
                    "webpage_url": source_url,
                    "direct_innertube": True,
                    "innertube_client": client_name,
                    "selected_itag": direct_fmt.get("itag"),
                    "http_headers": {
                        "User-Agent": user_agent,
                        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
                    },
                })

                return Track(
                    service=self.name,
                    url=stream_url,
                    name=full_title,
                    format=fmt_type,
                    type=TrackType.Live if is_live else TrackType.Default,
                    extra_info=track_extra,
                    extracted_at=time.perf_counter(),
                )
            except Exception as e:
                logging.debug(f"Direct InnerTube ({client_name}) error for {video_id}: {e}")
                continue

        return None

    def _get_cached_search(self, key: str) -> Optional[List[Track]]:
        with self._search_cache_lock:
            cached = self._search_cache.get(key)
            if cached:
                ts, tracks = cached
                if time.perf_counter() - ts < self._search_cache_ttl:
                    return [copy.deepcopy(t) for t in tracks]
                else:
                    self._search_cache.pop(key, None)
        return None

    def _set_cached_search(self, key: str, tracks: List[Track]) -> None:
        if not tracks:
            return
        with self._search_cache_lock:
            if len(self._search_cache) > 300:
                oldest_key = min(self._search_cache.keys(), key=lambda k: self._search_cache[k][0])
                self._search_cache.pop(oldest_key, None)
            self._search_cache[key] = (time.perf_counter(), [copy.deepcopy(t) for t in tracks])

    def _search_fast_http(self, query: str, limit: int) -> List[Track]:
        """
        Ultra-fast direct YouTube search via pooled HTTP session.
        Typically completes in 200-500ms without initializing heavy extractors.
        """
        import json
        import re
        import urllib.parse

        url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
        try:
            r = self._http_session.get(url, timeout=5)
            if r.status_code != 200:
                return []

            tracks: List[Track] = []
            match = re.search(r"var ytInitialData\s*=\s*({.+?});</script>", r.text)
            if match:
                try:
                    data = json.loads(match.group(1))
                    sections = (
                        data.get("contents", {})
                        .get("twoColumnSearchResultsRenderer", {})
                        .get("primaryContents", {})
                        .get("sectionListRenderer", {})
                        .get("contents", [])
                    )
                    for sec in sections:
                        items = sec.get("itemSectionRenderer", {}).get("contents", [])
                        for item in items:
                            vr = item.get("videoRenderer")
                            if not vr:
                                continue
                            vid = vr.get("videoId")
                            title_runs = vr.get("title", {}).get("runs", [])
                            title = "".join(x.get("text", "") for x in title_runs) if title_runs else ""
                            channel_runs = vr.get("ownerText", {}).get("runs", [])
                            channel = "".join(x.get("text", "") for x in channel_runs) if channel_runs else ""
                            if vid and title:
                                full_title = f"{title} - {channel}" if channel and channel not in title else title
                                tracks.append(
                                    Track(
                                        service=self.name,
                                        url=f"https://www.youtube.com/watch?v={vid}",
                                        name=full_title,
                                        type=TrackType.Dynamic,
                                        extra_info={
                                            "id": vid,
                                            "videoId": vid,
                                            "title": title,
                                            "uploader": channel,
                                        },
                                    )
                                )
                                if len(tracks) >= limit:
                                    return tracks
                except Exception as ex:
                    logging.debug(f"ytInitialData parsing error: {ex}")

            if not tracks:
                titles = re.findall(r'"title":\{"runs":\[\{"text":"([^"]+)"\}\]', r.text)
                video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', r.text)
                seen_ids = set()
                for vid, tit in zip(video_ids, titles):
                    if vid in seen_ids:
                        continue
                    seen_ids.add(vid)
                    tracks.append(
                        Track(
                            service=self.name,
                            url=f"https://www.youtube.com/watch?v={vid}",
                            name=tit,
                            type=TrackType.Dynamic,
                            extra_info={"id": vid, "videoId": vid, "title": tit},
                        )
                    )
                    if len(tracks) >= limit:
                        break

            return tracks
        except Exception as e:
            logging.debug(f"Fast HTTP search error for '{query}': {e}")
            return []

    def search(self, query: str, limit: Optional[int] = None) -> List[Track]:
        if limit is None:
            limit = self.config.search_results or 1

        # Direct URL check
        if any(h in query for h in ["youtube.com", "youtu.be", "music.youtube.com"]):
            return self.get(query)

        clean_query = query.strip()
        cache_key = f"{clean_query.lower()}:{limit}"

        # 1. Instant cache response (0ms)
        cached = self._get_cached_search(cache_key)
        if cached:
            logging.info(f"YT search '{clean_query}' served from CACHE (0ms)")
            return cached

        # 2. Fast HTTP Search first (~200-500ms)
        tracks = self._search_fast_http(clean_query, limit)
        if tracks:
            self._set_cached_search(cache_key, tracks)
            return tracks

        # 3. Fallback to yt-dlp search if fast search yielded nothing
        search_query = f"ytsearch{limit + 5}:{clean_query}"
        opts = dict(self._ydl_opts_base)
        opts.update({
            "extract_flat": "in_playlist",
            "skip_download": True,
        })

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(search_query, download=False)
            entries = info.get("entries") or []
            for entry in entries:
                if not entry or entry.get("ie_key") == "YoutubeTab":
                    continue
                entry_url = entry.get("url") or ""
                if any(p in entry_url for p in ["/channel/", "/@", "/user/"]):
                    continue

                vid = entry.get("id") or entry.get("videoId")
                title = entry.get("title") or self.bot.translator.translate("Unknown Title")
                uploader = entry.get("uploader") or entry.get("channel")
                full_title = f"{title} - {uploader}" if uploader and uploader not in title else title
                url = entry_url if "watch?v=" in entry_url else f"https://www.youtube.com/watch?v={vid}"
                tracks.append(
                    Track(
                        service=self.name,
                        url=url,
                        name=full_title,
                        type=TrackType.Dynamic,
                        extra_info=entry,
                    )
                )
        except Exception as e:
            logging.warning(f"YT search via yt-dlp failed for '{clean_query}': {e}")

        # 4. Fallback for Vietnamese diacritics / accents
        if not tracks:
            import unicodedata
            nfkd = unicodedata.normalize('NFKD', clean_query)
            no_accents = ''.join([c for c in nfkd if not unicodedata.combining(c)]).replace('đ', 'd').replace('Đ', 'D')
            if no_accents != clean_query:
                logging.info(f"Retrying with unaccented query: '{no_accents}'")
                tracks = self._search_fast_http(no_accents, limit)
                if not tracks:
                    try:
                        search_query = f"ytsearch{limit + 5}:{no_accents}"
                        with yt_dlp.YoutubeDL(opts) as ydl:
                            info = ydl.extract_info(search_query, download=False)
                        entries = info.get("entries") or []
                        for entry in entries:
                            if not entry or entry.get("ie_key") == "YoutubeTab":
                                continue
                            vid = entry.get("id") or entry.get("videoId")
                            title = entry.get("title") or self.bot.translator.translate("Unknown Title")
                            uploader = entry.get("uploader") or entry.get("channel")
                            full_title = f"{title} - {uploader}" if uploader and uploader not in title else title
                            url = f"https://www.youtube.com/watch?v={vid}"
                            tracks.append(
                                Track(
                                    service=self.name,
                                    url=url,
                                    name=full_title,
                                    type=TrackType.Dynamic,
                                    extra_info=entry,
                                )
                            )
                            if len(tracks) >= limit:
                                break
                    except Exception as ex:
                        logging.warning(f"YT unaccented yt-dlp search failed: {ex}")

        if not tracks:
            raise errors.NothingFoundError()

        self._set_cached_search(cache_key, tracks)
        return tracks

    def get(
        self,
        url: str,
        extra_info: Optional[Dict[str, Any]] = None,
        process: bool = False,
    ) -> List[Track]:
        start_time = time.perf_counter()
        if not (url or extra_info):
            raise errors.InvalidArgumentError()

        info = dict(extra_info or {})
        video_id = info.get("videoId") or info.get("id") or info.get("contentId")

        if not process:
            # If extra_info has valid title and id, return dynamic track without network call
            if info.get("title") and (video_id or url):
                title = info.get("title")
                uploader = info.get("uploader") or info.get("channel")
                full_title = f"{title} - {uploader}" if uploader and uploader not in title else title
                source_url = info.get("webpage_url") or url or f"https://www.youtube.com/watch?v={video_id}"
                return [
                    Track(
                        service=self.name,
                        url=source_url,
                        name=full_title,
                        type=TrackType.Dynamic,
                        extra_info=info,
                    )
                ]

            lower_url = url.lower() if url else ""
            if (
                "list=" in lower_url
                or "/channel/" in lower_url
                or "/@" in lower_url
                or "/c/" in lower_url
                or "/user/" in lower_url
                or "/playlist" in lower_url
            ):
                opts = dict(self._ydl_opts_base)
                opts.update({
                    "extract_flat": "in_playlist",
                    "skip_download": True,
                })
                try:
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        pl_info = ydl.extract_info(url, download=False)
                    entries = pl_info.get("entries") or []
                    tracks: List[Track] = []
                    for entry in entries:
                        if not entry:
                            continue
                        e_id = entry.get("id")
                        e_title = entry.get("title") or self.bot.translator.translate("Unknown Title")
                        e_uploader = entry.get("uploader") or pl_info.get("uploader")
                        e_full = f"{e_title} - {e_uploader}" if e_uploader and e_uploader not in e_title else e_title
                        e_url = entry.get("url") or f"https://www.youtube.com/watch?v={e_id}"
                        tracks.append(
                            Track(
                                service=self.name,
                                url=e_url,
                                name=e_full,
                                type=TrackType.Dynamic,
                                extra_info=entry,
                            )
                        )
                    duration = (time.perf_counter() - start_time) * 1000
                    logging.info(f"YT Get (Playlist) finished in {duration:.2f}ms for {url} ({len(tracks)} tracks)")
                    return tracks
                except Exception as e:
                    logging.error(f"YT Playlist extraction failed: {e}")
                    raise errors.ServiceError(f"Playlist extraction failed: {e}") from e

            # Single video fallback
            return [
                Track(
                    service=self.name,
                    url=url,
                    name=self.bot.translator.translate("YouTube Track"),
                    type=TrackType.Dynamic,
                    extra_info=info,
                )
            ]

        # Process: Extract direct stream URL
        source_url = url or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        if not source_url:
            raise errors.ServiceError("No URL provided for stream extraction")

        video_id = video_id or self._extract_video_id(source_url)
        stream_cache_key = video_id or source_url
        with self._stream_cache_lock:
            cached_stream = self._stream_cache.get(stream_cache_key)
            if cached_stream:
                ts, cached_track = cached_stream
                if time.perf_counter() - ts < self._stream_cache_ttl:
                    duration = (time.perf_counter() - start_time) * 1000
                    logging.info(f"YT Get (Stream) resolved from STREAM CACHE in {duration:.2f}ms (0ms network) for {cached_track.name}")
                    return [copy.deepcopy(cached_track)]
                else:
                    self._stream_cache.pop(stream_cache_key, None)

        # FAST PATH (Tier 1): Direct YouTube InnerTube Android API (150 - 350ms)
        # Bypasses heavy webpage downloads & JS cipher evaluation for instant direct playback
        if video_id and not any(p in source_url.lower() for p in ["list=", "/channel/", "/@", "/live"]):
            fast_track = self._resolve_direct_innertube(video_id, source_url, info)
            if fast_track:
                duration = (time.perf_counter() - start_time) * 1000
                logging.info(
                    f"YT Get (Stream) resolved via Fast InnerTube in {duration:.2f}ms "
                    f"format={fast_track.format} for {fast_track.name}"
                )
                with self._stream_cache_lock:
                    if len(self._stream_cache) > 500:
                        oldest_key = min(self._stream_cache.keys(), key=lambda k: self._stream_cache[k][0])
                        self._stream_cache.pop(oldest_key, None)
                    self._stream_cache[stream_cache_key] = (time.perf_counter(), copy.deepcopy(fast_track))
                return [fast_track]

        # FALLBACK PATH (Tier 2): Full yt-dlp extractor
        opts = dict(self._ydl_opts_base)
        opts.update({
            "skip_download": True,
            "noplaylist": True,
            "check_formats": False,  # Skip redundant HTTP ping per format to save 1-2s RTT
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                extracted = ydl.extract_info(source_url, download=False)
        except Exception as e:
            logging.error(f"YT stream extraction failed for '{source_url}': {e}")
            raise errors.ServiceError(f"Failed to extract stream: {e}") from e

        stream_url = extracted.get("url")
        if not stream_url:
            raise errors.ServiceError("yt-dlp returned no stream URL for this video")

        title = extracted.get("title") or info.get("title") or self.bot.translator.translate("Unknown Title")
        uploader = extracted.get("uploader") or extracted.get("channel") or info.get("uploader")
        full_title = f"{title} - {uploader}" if uploader and uploader not in title else title
        fmt = extracted.get("ext") or "m4a"
        is_live = bool(extracted.get("is_live"))
        track_type = TrackType.Live if is_live else TrackType.Default

        duration = (time.perf_counter() - start_time) * 1000
        logging.info(f"YT Get (Stream) resolved in {duration:.2f}ms format={fmt} for {full_title}")

        resolved_track = Track(
            service=self.name,
            url=stream_url,
            name=full_title,
            format=fmt,
            type=track_type,
            extra_info=extracted,
            extracted_at=time.perf_counter(),
        )

        with self._stream_cache_lock:
            if len(self._stream_cache) > 500:
                oldest_key = min(self._stream_cache.keys(), key=lambda k: self._stream_cache[k][0])
                self._stream_cache.pop(oldest_key, None)
            self._stream_cache[stream_cache_key] = (time.perf_counter(), copy.deepcopy(resolved_track))

        return [resolved_track]

    def download(self, track: Track, file_path: str, video: bool = False) -> None:
        info = track.extra_info or {}
        video_id = info.get("videoId") or info.get("id") or info.get("contentId")
        source_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else track.url

        opts = dict(self._ydl_opts_base)
        ffmpeg_dir = app_vars.get_ffmpeg_dir()
        if ffmpeg_dir:
            opts["ffmpeg_location"] = ffmpeg_dir
        opts["outtmpl"] = file_path
        if video:
            opts["format"] = "bestvideo+bestaudio/best"
        else:
            opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"

        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([source_url])

    def get_related(self, video_id: str, count: int = 5) -> List[Track]:
        if not video_id:
            return []
        url = f"https://www.youtube.com/watch?v={video_id}&list=RD{video_id}"
        opts = dict(self._ydl_opts_base)
        opts.update({
            "extract_flat": "in_playlist",
            "skip_download": True,
            "playlist_items": f"2-{count + 1}",
        })
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            entries = info.get("entries") or []
            tracks: List[Track] = []
            for entry in entries:
                if not entry or entry.get("ie_key") == "YoutubeTab":
                    continue
                vid = entry.get("id") or entry.get("videoId")
                if not vid or vid == video_id:
                    continue
                title = entry.get("title") or self.bot.translator.translate("Unknown Title")
                uploader = entry.get("uploader") or entry.get("channel")
                full_title = f"{title} - {uploader}" if uploader and uploader not in title else title
                tracks.append(
                    Track(
                        service=self.name,
                        url=f"https://www.youtube.com/watch?v={vid}",
                        name=full_title,
                        type=TrackType.Dynamic,
                        extra_info=entry,
                    )
                )
            return tracks
        except Exception as e:
            logging.warning(f"Failed to fetch related tracks for {video_id}: {e}")
            return []

    def _fetch_autoplay_sync(self, video_id: str) -> bool:
        if not video_id or not hasattr(self.bot, "player"):
            return False
        related = self.get_related(video_id, count=5)
        if not related:
            return False

        existing_ids = set()
        for t in self.bot.player.track_list:
            info = getattr(t, "extra_info", None) or {}
            vid = info.get("id") or info.get("videoId")
            if vid:
                existing_ids.add(vid)

        added = 0
        for track in related:
            info = getattr(track, "extra_info", None) or {}
            vid = info.get("id") or info.get("videoId")
            if vid and vid not in existing_ids:
                self.bot.player.track_list.append(track)
                existing_ids.add(vid)
                added += 1

        if added > 0:
            logging.info(f"[Autoplay] Appended {added} related tracks to player track_list")
            # Immediately pre-resolve the direct m4a stream URL for the next track in this background thread
            try:
                next_idx = self.bot.player.track_index + 1
                if next_idx < len(self.bot.player.track_list):
                    next_t = self.bot.player.track_list[next_idx]
                    if not next_t._is_fetched:
                        logging.info(f"[Autoplay] Pre-resolving direct m4a stream for next track: {next_t.name}")
                        _ = next_t.url
                        logging.info(f"[Autoplay] Next track m4a stream ready: {next_t.name}")
            except Exception as e:
                logging.warning(f"[Autoplay] Error pre-resolving next track: {e}")

            # Schedule player prefetch for subsequent tracks
            if hasattr(self.bot.player, "_schedule_prefetch"):
                self.bot.player._schedule_prefetch()
            return True
        return False

    def _fetch_autoplay_async(self, video_id: str) -> None:
        if not video_id:
            return
        t = threading.Thread(
            target=self._fetch_autoplay_sync,
            args=(video_id,),
            daemon=True,
            name="YT_Autoplay_Fetcher",
        )
        t.start()
