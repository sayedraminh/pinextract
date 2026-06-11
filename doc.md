# PinExtract Client Integration Guide

This document is for anyone building a client that wants to call the PinExtract server.

PinExtract is the backend server. Your app sends it a Pinterest URL, and the server returns the best available Pinterest image URL, usually the original-quality `i.pinimg.com/originals/...` file when Pinterest exposes it.

## Server URL

Local development server:

```text
http://127.0.0.1:8077
```

Run it locally:

```bash
PINEXTRACT_API_KEY=change-this-key python main.py
```

If you deploy the server, replace `http://127.0.0.1:8077` with your deployed API URL.

## Authentication

Every API endpoint requires an API key. Set the key on the server with:

```bash
PINEXTRACT_API_KEY=change-this-key python main.py
```

Clients should send the same key in the `X-API-Key` header:

```http
X-API-Key: change-this-key
```

For public browser apps, do not hardcode a private API key in shipped frontend code. Put the key on your own backend and call PinExtract from there. Sending the key from browser JavaScript is only suitable for local tools, internal dashboards, or trusted environments.

## Main Endpoint

Use this endpoint when your client needs the image URL and metadata:

```http
GET /api/extract?url={PINTEREST_URL}
```

Example:

```bash
curl "http://127.0.0.1:8077/api/extract?url=https%3A%2F%2Fpin.it%2F4vlUDenLv" \
  -H "X-API-Key: change-this-key"
```

The `url` query parameter must be URL encoded.

## Response

Successful response:

```json
{
  "source_url": "https://pin.it/4vlUDenLv",
  "resolved_url": "https://www.pinterest.com/pin/902268106602152457/...",
  "image_url": "https://i.pinimg.com/originals/9b/94/52/9b9452f783049b89c42a9a539f62fd0d.jpg",
  "content_type": "image/jpeg",
  "content_length": 59513,
  "title": "Pin title",
  "description": "Pin description",
  "candidate_count": 15
}
```

Use `image_url` as the final image URL in your client.

## Browser JavaScript Example

```html
<input id="pinUrl" value="https://pin.it/4vlUDenLv" />
<input id="apiKey" type="password" value="change-this-key" />
<button id="extract">Extract</button>
<img id="preview" alt="Pinterest image" />

<script>
  const apiBase = "http://127.0.0.1:8077";

  async function extractPinImage(pinUrl) {
    const params = new URLSearchParams({ url: pinUrl });
    const apiKey = document.querySelector("#apiKey").value;
    const response = await fetch(`${apiBase}/api/extract?${params}`, {
      headers: { "X-API-Key": apiKey },
    });
    const data = await response.json();

    if (!response.ok) {
      throw new Error(data.detail || "Could not extract Pinterest image.");
    }

    return data;
  }

  document.querySelector("#extract").addEventListener("click", async () => {
    const pinUrl = document.querySelector("#pinUrl").value;
    const result = await extractPinImage(pinUrl);
    document.querySelector("#preview").src = result.image_url;
  });
</script>
```

## React Example

```jsx
import { useState } from "react";

const API_BASE = "http://127.0.0.1:8077";
const API_KEY = "change-this-key";

export default function PinExtractor() {
  const [pinUrl, setPinUrl] = useState("https://pin.it/4vlUDenLv");
  const [imageUrl, setImageUrl] = useState("");
  const [error, setError] = useState("");

  async function handleExtract() {
    setError("");
    setImageUrl("");

    try {
      const params = new URLSearchParams({ url: pinUrl });
      const response = await fetch(`${API_BASE}/api/extract?${params}`, {
        headers: { "X-API-Key": API_KEY },
      });
      const data = await response.json();

      if (!response.ok) {
        throw new Error(data.detail || "Could not extract Pinterest image.");
      }

      setImageUrl(data.image_url);
    } catch (err) {
      setError(err.message);
    }
  }

  return (
    <div>
      <input value={pinUrl} onChange={(event) => setPinUrl(event.target.value)} />
      <button onClick={handleExtract}>Extract</button>
      {error && <p>{error}</p>}
      {imageUrl && <img src={imageUrl} alt="Pinterest result" />}
    </div>
  );
}
```

## POST Alternative

If your client prefers JSON bodies:

```http
POST /api/extract
Content-Type: application/json
```

```json
{
  "url": "https://pin.it/4vlUDenLv",
  "include_data": false
}
```

JavaScript:

```js
const response = await fetch("http://127.0.0.1:8077/api/extract", {
  method: "POST",
  headers: {
    "Content-Type": "application/json",
    "X-API-Key": "change-this-key",
  },
  body: JSON.stringify({
    url: "https://pin.it/4vlUDenLv",
    include_data: false,
  }),
});

const data = await response.json();
```

## Base64 Data URL

If your client needs the image embedded directly in the JSON response, pass `include_data=true`:

```text
GET /api/extract?url={PINTEREST_URL}&include_data=true
```

The response will include:

```json
{
  "image_data_base64": "...",
  "image_data_url": "data:image/jpeg;base64,..."
}
```

Then you can display it directly:

```js
imageElement.src = data.image_data_url;
```

Use this only when you actually need embedded data. For most clients, `image_url` is faster and smaller.

## Image Proxy / Download

Use this endpoint when your client wants the server to stream the image bytes:

```http
GET /api/image?url={PINTEREST_URL}
```

Preferred curl usage with the API key header:

```bash
curl -L "http://127.0.0.1:8077/api/image?url=https%3A%2F%2Fpin.it%2F4vlUDenLv" \
  -H "X-API-Key: change-this-key" \
  -o pin-image.jpg
```

Example download link:

```html
<a href="http://127.0.0.1:8077/api/image?url=https%3A%2F%2Fpin.it%2F4vlUDenLv&api_key=change-this-key" download>
  Download image
</a>
```

Use the query-string `api_key` form only when you cannot set headers, such as a plain browser download link. Prefer the `X-API-Key` header for normal API calls.

## Error Handling

Errors return JSON with a `detail` field:

```json
{
  "detail": "Only pin.it and pinterest.com URLs are supported."
}
```

Common status codes:

- `400`: invalid or unsupported URL
- `401`: invalid or missing API key
- `404`: no reachable image was found
- `502`: Pinterest or the image CDN returned an unexpected response
- `503`: `PINEXTRACT_API_KEY` is not set on the server
- `504`: request timed out

## Client Notes

- The server accepts only `pin.it` and `pinterest.com` URLs.
- The returned `image_url` points to `https://i.pinimg.com/...`.
- The server has CORS enabled, so browser clients can call it directly.
- For production, deploy this FastAPI app and set your client `API_BASE` to the deployed URL.
- Only download or redistribute images when you have the rights to do so.
