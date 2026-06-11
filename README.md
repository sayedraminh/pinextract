# PinExtract

PinExtract is a small FastAPI server that extracts the best available image URL from a public Pinterest pin or `pin.it` short link. When Pinterest exposes an original CDN image, the API returns the `i.pinimg.com/originals/...` URL. It can also return a base64 data URL or stream the image through the server.

## Features

- Resolves `pin.it` short links and regular Pinterest pin URLs
- Prefers original-quality Pinterest CDN images
- Returns clean JSON metadata
- Optional base64/data URL output
- Image proxy endpoint for direct downloads
- Includes a local HTML test page

## Install

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

## Run

Create your local `.env` file:

```bash
cp .env.example .env
```

Edit `.env` and set your API key:

```text
PINEXTRACT_API_KEY=your-secret-key
HOST=127.0.0.1
PORT=8077
```

Start the server:

```bash
python main.py
```

The server starts at:

```text
http://127.0.0.1:8077/
```

`PINEXTRACT_API_KEY` is required in `.env`. API requests must send the same key in the `X-API-Key` header.

## API

Extract an image URL:

```bash
curl "http://127.0.0.1:8077/api/extract?url=https%3A%2F%2Fpin.it%2F4vlUDenLv" \
  -H "X-API-Key: your-secret-key"
```

Example response:

```json
{
  "source_url": "https://pin.it/4vlUDenLv",
  "resolved_url": "https://www.pinterest.com/pin/...",
  "image_url": "https://i.pinimg.com/originals/...",
  "content_type": "image/jpeg",
  "content_length": 59513,
  "title": "Pin title",
  "description": "Pin description",
  "candidate_count": 15
}
```

Include image data in the JSON:

```bash
curl "http://127.0.0.1:8077/api/extract?url=https%3A%2F%2Fpin.it%2F4vlUDenLv&include_data=true" \
  -H "X-API-Key: your-secret-key"
```

Stream the image through the server:

```bash
curl -L "http://127.0.0.1:8077/api/image?url=https%3A%2F%2Fpin.it%2F4vlUDenLv" \
  -H "X-API-Key: your-secret-key" \
  -o pin-image.jpg
```

For frontend/client usage, see [`doc.md`](doc.md).

## How It Works

Pinterest pages commonly include several image sizes such as `236x`, `474x`, `564x`, and `736x`. PinExtract looks for `images_orig` first, then tries to convert sized CDN URLs into `originals` URLs, verifies candidates against `i.pinimg.com`, and falls back to the largest reachable image if an original file is not available.

## Notes

PinExtract only works with public Pinterest pages that expose image metadata in the HTML response. It does not log in, bypass private content, or use an official Pinterest API. Only download or redistribute images when you have the rights to do so.

## License

MIT
