from __future__ import annotations

import hashlib
import base64
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from app.social.providers.base import ProviderAccount, ProviderError, PublishResult

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
YOUTUBE_UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPES = (
    "openid email profile",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.upload",
)


class YouTubeProvider:
    slug = "youtube"

    @staticmethod
    def authorization_url(client_id: str, redirect_uri: str, state: str, code_verifier: str) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(code_verifier.encode()).digest()).decode().rstrip("=")
        return AUTH_URL + "?" + urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": " ".join(SCOPES), "state": state, "access_type": "offline",
            "include_granted_scopes": "true", "prompt": "consent",
            "code_challenge": challenge, "code_challenge_method": "S256",
        })

    @staticmethod
    def _post_token(data: dict[str, str]) -> dict[str, Any]:
        try:
            response = httpx.post(TOKEN_URL, data=data, timeout=httpx.Timeout(20, connect=5))
            if response.status_code >= 400:
                raise ProviderError("oauth_exchange_failed", "Google authorization could not be completed.")
            payload = response.json()
            if not isinstance(payload, dict) or not payload.get("access_token"):
                raise ProviderError("oauth_response_invalid", "Google returned an invalid authorization response.")
            return payload
        except httpx.TimeoutException as exc:
            raise ProviderError("oauth_timeout", "Google authorization timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("oauth_unavailable", "Google authorization is temporarily unavailable.", retryable=True) from exc

    def exchange_code(self, code: str, client_id: str, client_secret: str, redirect_uri: str,
                      code_verifier: str) -> dict[str, Any]:
        return self._post_token({
            "code": code, "client_id": client_id, "client_secret": client_secret,
            "redirect_uri": redirect_uri, "grant_type": "authorization_code",
            "code_verifier": code_verifier,
        })

    def refresh_token(self, refresh_token: str, client_id: str, client_secret: str) -> dict[str, Any]:
        return self._post_token({
            "refresh_token": refresh_token, "client_id": client_id,
            "client_secret": client_secret, "grant_type": "refresh_token",
        })

    def revoke_token(self, token: str) -> None:
        try:
            response = httpx.post("https://oauth2.googleapis.com/revoke", data={"token": token},
                                  timeout=httpx.Timeout(15, connect=4))
            if response.status_code >= 400:
                raise ProviderError("oauth_revoke_failed", "Google did not confirm permission revocation.")
        except httpx.HTTPError as exc:
            raise ProviderError("oauth_revoke_unavailable", "Google revocation is temporarily unavailable.", retryable=True) from exc

    @staticmethod
    def _get_json(url: str, token: str, params: dict[str, str] | None = None) -> dict[str, Any]:
        try:
            response = httpx.get(url, params=params, headers={"Authorization": f"Bearer {token}"},
                                 timeout=httpx.Timeout(20, connect=5))
            if response.status_code == 401:
                raise ProviderError("token_invalid", "Google authorization expired or was revoked.")
            if response.status_code == 429:
                raise ProviderError("provider_rate_limited", "Google rate limit reached.", retryable=True)
            if response.status_code >= 500:
                raise ProviderError("provider_unavailable", "Google publishing is temporarily unavailable.", retryable=True, uncertain=True)
            if response.status_code >= 400:
                raise ProviderError("provider_request_rejected", "Google rejected the publishing request.")
            result = response.json()
            if not isinstance(result, dict):
                raise ProviderError("provider_response_invalid", "Google returned an invalid response.")
            return result
        except httpx.TimeoutException as exc:
            raise ProviderError("provider_timeout", "Google did not respond in time; check publishing history before retrying.", uncertain=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("provider_unavailable", "Google publishing is temporarily unavailable.", retryable=True) from exc

    def accounts(self, access_token: str) -> list[ProviderAccount]:
        profile = self._get_json(USERINFO_URL, access_token)
        channels = self._get_json(f"{YOUTUBE_API}/channels", access_token,
                                  {"part": "snippet", "mine": "true", "maxResults": "50"})
        items = channels.get("items", [])
        if not items:
            raise ProviderError("youtube_channel_missing", "This Google account has no YouTube channel available to publish to.")
        accounts = []
        for channel in items:
            snippet = channel.get("snippet") or {}
            external_id = str(channel.get("id") or "")
            if not external_id:
                continue
            accounts.append(ProviderAccount(
                external_id=external_id,
                display_name=str(snippet.get("title") or profile.get("name") or "YouTube channel")[:255],
                username=str(snippet.get("customUrl"))[:255] if snippet.get("customUrl") else profile.get("email"),
                metadata={"thumbnail_url": (snippet.get("thumbnails") or {}).get("default", {}).get("url"),
                          "google_email": profile.get("email"), "google_subject": profile.get("sub")},
            ))
        if not accounts:
            raise ProviderError("youtube_channel_missing", "Google did not return a usable YouTube channel.")
        return accounts

    @staticmethod
    def validate(metadata: dict[str, Any], options: dict[str, Any]) -> None:
        title = str(metadata.get("title") or "").strip()
        description = str(metadata.get("description") or "")
        tags = metadata.get("hashtags") or metadata.get("tags") or []
        if not title or len(title) > 100:
            raise ProviderError("metadata_title_invalid", "YouTube titles must be between 1 and 100 characters.")
        if len(description) > 5000:
            raise ProviderError("metadata_description_invalid", "YouTube descriptions must be at most 5,000 characters.")
        if not isinstance(tags, list) or len(tags) > 500 or sum(len(str(tag).strip().lstrip("#")) for tag in tags) > 500:
            raise ProviderError("metadata_tags_invalid", "YouTube tags must fit within the API's combined 500-character limit.")
        privacy = options.get("privacy_status", "private")
        if privacy not in {"private", "unlisted", "public"}:
            raise ProviderError("privacy_option_invalid", "Choose private, unlisted, or public visibility.")
        if not isinstance(options.get("made_for_kids"), bool):
            raise ProviderError("made_for_kids_required", "Select whether this video is made for kids before publishing.")

    def publish(self, video_path: Path, metadata: dict[str, Any], options: dict[str, Any],
                access_token: str, *, request_key: str) -> PublishResult:
        self.validate(metadata, options)
        if not video_path.is_file() or video_path.suffix.lower() != ".mp4":
            raise ProviderError("video_file_invalid", "YouTube publishing requires an available MP4 clip.")
        title = str(metadata["title"]).strip()
        description = str(metadata.get("description") or "")
        hashtags = metadata.get("hashtags") or metadata.get("tags") or []
        tags = [str(tag).strip().lstrip("#")[:100] for tag in hashtags if str(tag).strip()]
        body = {"snippet": {"title": title, "description": description, "tags": tags,
                            "categoryId": str(options.get("category_id", "22"))},
                "status": {"privacyStatus": options.get("privacy_status", "private"),
                           "selfDeclaredMadeForKids": bool(options.get("made_for_kids", False))}}
        headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json",
                   "X-Upload-Content-Length": str(video_path.stat().st_size), "X-Upload-Content-Type": "video/mp4"}
        try:
            response = httpx.post(f"{YOUTUBE_UPLOAD}?uploadType=resumable&part=snippet,status", json=body,
                                  headers=headers, timeout=httpx.Timeout(30, connect=8))
            if response.status_code == 401:
                raise ProviderError("token_invalid", "Google authorization expired or was revoked.")
            if response.status_code == 429:
                raise ProviderError("provider_rate_limited", "YouTube rate limit reached.", retryable=True)
            if response.status_code >= 500:
                raise ProviderError("provider_unavailable", "YouTube is temporarily unavailable; result may need reconciliation.", uncertain=True)
            if response.status_code >= 400:
                raise ProviderError("provider_request_rejected", "YouTube rejected this upload request.")
            upload_url = response.headers.get("Location")
            if not upload_url or not upload_url.startswith("https://www.googleapis.com/upload/youtube/"):
                raise ProviderError("upload_session_invalid", "YouTube did not return a valid resumable upload session.")
            with video_path.open("rb") as video:
                uploaded = httpx.put(upload_url, content=video, headers={"Content-Type": "video/mp4"},
                                     timeout=httpx.Timeout(3600, connect=15))
            if uploaded.status_code == 401:
                raise ProviderError("token_invalid", "Google authorization expired during upload.", uncertain=True)
            if uploaded.status_code == 429:
                raise ProviderError("provider_rate_limited", "YouTube rate limit reached during upload.", retryable=True, uncertain=True)
            if uploaded.status_code >= 500:
                raise ProviderError("upload_result_uncertain", "YouTube did not confirm completion; inspect the channel before retrying.", uncertain=True)
            if uploaded.status_code >= 400:
                raise ProviderError("upload_rejected", "YouTube rejected the video upload.")
            payload = uploaded.json()
            video_id = str(payload.get("id") or "")
            if not video_id:
                raise ProviderError("upload_response_invalid", "YouTube did not return a video ID.", uncertain=True)
            return PublishResult(video_id, f"https://www.youtube.com/watch?v={video_id}", response.headers.get("X-GUploader-UploadID"))
        except ProviderError:
            raise
        except httpx.TimeoutException as exc:
            raise ProviderError("upload_result_uncertain", "YouTube upload timed out; inspect the channel before retrying.", uncertain=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("upload_result_uncertain", "YouTube upload status is uncertain; inspect the channel before retrying.", uncertain=True) from exc
