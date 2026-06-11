from __future__ import annotations

import base64
import html
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator
from urllib.parse import urlparse

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

API_KEY_ENV = "PINEXTRACT_API_KEY"
DEFAULT_HOST = os.getenv("HOST", "127.0.0.1")
DEFAULT_PORT = int(os.getenv("PORT", "8077"))
MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_IMAGE_BYTES = 50 * 1024 * 1024
REQUEST_TIMEOUT = httpx.Timeout(20.0, connect=8.0)

HTML_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
IMAGE_HEADERS = {
    "User-Agent": HTML_HEADERS["User-Agent"],
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Referer": "https://www.pinterest.com/",
}

PINIMG_RE = re.compile(
    r"https://i\.pinimg\.com/[A-Za-z0-9_./%-]+?\.(?:jpe?g|png|webp|gif|avif)",
    re.IGNORECASE,
)
IMAGES_ORIG_RE = re.compile(
    r'"images_orig"\s*:\s*\{[^{}]*?"url"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
META_TAG_RE = re.compile(r"<meta\b[^>]*>", re.IGNORECASE)
META_ATTR_RE = re.compile(r"([:\w-]+)\s*=\s*(['\"])(.*?)\2", re.IGNORECASE | re.DOTALL)


class ExtractRequest(BaseModel):
    url: str = Field(..., description="Pinterest or pin.it URL")
    include_data: bool = Field(
        default=False,
        description="When true, include a base64 data URL in the JSON response.",
    )


@dataclass(frozen=True)
class ImageProbe:
    url: str
    content_type: str | None
    content_length: int | None


app = FastAPI(
    title="PinExtract",
    description="Extract the best available original-quality image from public Pinterest pins.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def test_page() -> FileResponse:
    return FileResponse(BASE_DIR / "test.html")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def require_api_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    api_key: str | None = Query(default=None, include_in_schema=False),
) -> None:
    expected_api_key = os.getenv(API_KEY_ENV)
    if not expected_api_key:
        raise HTTPException(
            status_code=503,
            detail=f"{API_KEY_ENV} is not set on the server.",
        )

    provided_api_key = x_api_key or api_key
    if not provided_api_key or not secrets.compare_digest(provided_api_key, expected_api_key):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


@app.get("/api/extract")
async def extract_get(
    url: str = Query(..., description="Pinterest or pin.it URL"),
    include_data: bool = Query(False, description="Include base64 data URL in response"),
    _api_key: None = Depends(require_api_key),
) -> dict[str, object]:
    return await extract_pin(url, include_data=include_data)


@app.post("/api/extract")
async def extract_post(
    payload: ExtractRequest,
    _api_key: None = Depends(require_api_key),
) -> dict[str, object]:
    return await extract_pin(payload.url, include_data=payload.include_data)


@app.get("/api/image")
async def image_proxy(
    url: str = Query(..., description="Pinterest or pin.it URL"),
    _api_key: None = Depends(require_api_key),
) -> StreamingResponse:
    result = await extract_pin(url, include_data=False)
    image_url = str(result["image_url"])
    content_type = str(result.get("content_type") or "application/octet-stream")
    filename = image_url.rsplit("/", 1)[-1].split("?", 1)[0] or "pin-image"

    return StreamingResponse(
        stream_remote_image(image_url),
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


async def extract_pin(source_url: str, include_data: bool = False) -> dict[str, object]:
    validate_pinterest_url(source_url)

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        page_html, resolved_url = await fetch_pinterest_html(client, source_url)
        candidates = extract_image_candidates(page_html)

        if not candidates:
            raise HTTPException(
                status_code=404,
                detail="No Pinterest image candidates were found on this page.",
            )

        probe = await choose_best_image(client, candidates)
        if probe is None:
            raise HTTPException(
                status_code=404,
                detail="Found Pinterest image candidates, but none were reachable.",
            )

        response: dict[str, object] = {
            "source_url": source_url,
            "resolved_url": resolved_url,
            "image_url": probe.url,
            "content_type": probe.content_type,
            "content_length": probe.content_length,
            "title": extract_first_meta(page_html, {"og:title", "twitter:title"}),
            "description": extract_first_meta(page_html, {"description"}),
            "candidate_count": len(candidates),
        }

        if include_data:
            image_bytes, content_type = await download_image(client, probe.url)
            response["content_type"] = content_type or probe.content_type
            response["content_length"] = len(image_bytes)
            response["image_data_base64"] = base64.b64encode(image_bytes).decode("ascii")
            response["image_data_url"] = (
                f"data:{response['content_type'] or 'application/octet-stream'};base64,"
                f"{response['image_data_base64']}"
            )

        return response


async def fetch_pinterest_html(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[str, str]:
    try:
        async with client.stream(
            "GET",
            url,
            headers=HTML_HEADERS,
            follow_redirects=True,
        ) as response:
            if not is_pinterest_page_url(str(response.url)):
                raise HTTPException(
                    status_code=400,
                    detail=f"Pinterest URL redirected outside Pinterest: {response.url}",
                )

            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Pinterest returned HTTP {response.status_code}.",
                )

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_HTML_BYTES:
                    raise HTTPException(
                        status_code=502,
                        detail="Pinterest HTML exceeded the configured response limit.",
                    )
                chunks.append(chunk)

            body = b"".join(chunks)
            encoding = response.encoding or "utf-8"
            return body.decode(encoding, errors="replace"), str(response.url)
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Timed out fetching Pinterest.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not fetch Pinterest: {exc}") from exc


def extract_image_candidates(page_html: str) -> list[str]:
    normalized = normalize_html_payload(page_html)
    candidates: list[str] = []

    for match in IMAGES_ORIG_RE.finditer(normalized):
        candidates.append(clean_image_url(match.group(1)))

    candidates.extend(
        clean_image_url(url)
        for url in extract_meta_values(normalized, {"og:image", "twitter:image"})
    )

    candidates.extend(clean_image_url(match.group(0)) for match in PINIMG_RE.finditer(normalized))

    seen: set[str] = set()
    unique: list[str] = []
    for url in candidates:
        if url and is_pinimg_url(url) and url not in seen:
            seen.add(url)
            unique.append(url)
    return unique


async def choose_best_image(
    client: httpx.AsyncClient,
    candidates: list[str],
) -> ImageProbe | None:
    for candidate in original_first_candidates(candidates):
        probe = await probe_image(client, candidate)
        if probe is not None:
            return probe
    return None


def original_first_candidates(candidates: list[str]) -> list[str]:
    ordered: list[str] = []

    for candidate in candidates:
        if "/originals/" in candidate:
            ordered.append(candidate)
        ordered.extend(original_variants(candidate))

    ordered.extend(sorted(candidates, key=image_size_score, reverse=True))

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in ordered:
        if candidate not in seen and is_pinimg_url(candidate):
            seen.add(candidate)
            unique.append(candidate)
    return unique


def original_variants(url: str) -> list[str]:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) < 5 or parts[0] == "originals":
        return []

    digest_parts = parts[-4:]
    filename = digest_parts[-1]
    stem = filename.rsplit(".", 1)[0]
    original_path_base = "/".join(["originals", *digest_parts[:3]])
    extensions = [filename.rsplit(".", 1)[-1].lower(), "jpg", "png", "webp", "jpeg"]

    variants: list[str] = []
    for extension in extensions:
        candidate = f"https://i.pinimg.com/{original_path_base}/{stem}.{extension}"
        if candidate not in variants:
            variants.append(candidate)
    return variants


async def probe_image(client: httpx.AsyncClient, url: str) -> ImageProbe | None:
    try:
        response = await client.head(
            url,
            headers=IMAGE_HEADERS,
            follow_redirects=True,
        )
        if response.status_code == 405:
            response = await client.get(
                url,
                headers={**IMAGE_HEADERS, "Range": "bytes=0-0"},
                follow_redirects=True,
            )
    except httpx.HTTPError:
        return None

    if response.status_code >= 400:
        return None

    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
    if not content_type.startswith("image/"):
        return None

    content_length_header = response.headers.get("content-length")
    content_length = (
        int(content_length_header)
        if content_length_header and content_length_header.isdigit()
        else None
    )
    return ImageProbe(url=str(response.url), content_type=content_type, content_length=content_length)


async def download_image(
    client: httpx.AsyncClient,
    url: str,
) -> tuple[bytes, str | None]:
    try:
        async with client.stream("GET", url, headers=IMAGE_HEADERS, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Image CDN returned HTTP {response.status_code}.",
                )

            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if content_type and not content_type.startswith("image/"):
                raise HTTPException(status_code=502, detail="Resolved URL did not return an image.")

            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Image is larger than the configured data response limit.",
                    )
                chunks.append(chunk)

            return b"".join(chunks), content_type or None
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Timed out downloading image.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Could not download image: {exc}") from exc


async def stream_remote_image(url: str) -> AsyncIterator[bytes]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        async with client.stream("GET", url, headers=IMAGE_HEADERS, follow_redirects=True) as response:
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=502,
                    detail=f"Image CDN returned HTTP {response.status_code}.",
                )
            async for chunk in response.aiter_bytes():
                yield chunk


def normalize_html_payload(page_html: str) -> str:
    normalized = html.unescape(page_html)
    normalized = normalized.replace("\\u002F", "/").replace("\\/", "/")
    return normalized


def clean_image_url(url: str) -> str:
    cleaned = html.unescape(url).replace("\\u002F", "/").replace("\\/", "/").strip()
    match = PINIMG_RE.search(cleaned)
    return match.group(0) if match else cleaned


def extract_first_meta(page_html: str, names: set[str]) -> str | None:
    values = extract_meta_values(page_html, names)
    return values[0] if values else None


def extract_meta_values(page_html: str, names: set[str]) -> list[str]:
    normalized = normalize_html_payload(page_html)
    wanted = {name.lower() for name in names}
    values: list[str] = []

    for tag in META_TAG_RE.finditer(normalized):
        attrs = {
            key.lower(): html.unescape(value).strip()
            for key, _quote, value in META_ATTR_RE.findall(tag.group(0))
        }
        markers = {attrs.get("property", "").lower(), attrs.get("name", "").lower()}
        content = attrs.get("content", "")
        if content and markers.intersection(wanted):
            values.append(content)

    return values


def image_size_score(url: str) -> int:
    if "/originals/" in url:
        return 1_000_000

    parsed = urlparse(url)
    first_part = parsed.path.strip("/").split("/", 1)[0]
    numbers = [int(number) for number in re.findall(r"\d+", first_part)]
    return max(numbers) if numbers else 0


def validate_pinterest_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(status_code=400, detail="URL must start with http:// or https://.")
    if not is_pinterest_page_url(url):
        raise HTTPException(
            status_code=400,
            detail="Only pin.it and pinterest.com URLs are supported.",
        )


def is_pinterest_page_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host == "pin.it" or host.endswith(".pin.it") or host == "pinterest.com" or host.endswith(".pinterest.com")


def is_pinimg_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and host == "i.pinimg.com"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host=DEFAULT_HOST, port=DEFAULT_PORT, reload=True)
